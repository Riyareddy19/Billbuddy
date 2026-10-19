import unittest
from unittest.mock import patch

from backend import main


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row.copy()
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params):
        self.queries.append((query, params))
        if query.startswith("UPDATE bills SET"):
            self.row["paid"] = params[0]
            return FakeCursor(self.row)
        return FakeCursor(self.row if params[0] == self.row["id"] else None)


class MarkBillPaidTests(unittest.TestCase):
    def setUp(self):
        self.bill = {
            "id": 12,
            "name": "Internet",
            "category": "Utilities",
            "amount": 999.0,
            "due_date": main.date(2026, 10, 25),
            "paid": False,
        }

    def test_patch_bill_paid_returns_updated_bill(self):
        connection = FakeConnection(self.bill)
        changes = main.BillUpdate.model_validate({"paid": True})

        with patch.object(main, "connect_db", return_value=connection):
            result = main.update_bill(12, changes, None)

        self.assertEqual(result["id"], 12)
        self.assertIs(result["paid"], True)
        self.assertEqual(result["due_date"], "2026-10-25")
        self.assertEqual(
            connection.queries,
            [
                ("SELECT * FROM bills WHERE id = %s", (12,)),
                ("UPDATE bills SET paid = %s WHERE id = %s", [True, 12]),
                ("SELECT * FROM bills WHERE id = %s", (12,)),
            ],
        )

    def test_paid_update_endpoint_requires_existing_api_auth_dependency(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/bills/{bill_id}"
            and "PATCH" in getattr(route, "methods", set())
        )

        self.assertIn(
            main.verify_api_key,
            [dependency.call for dependency in route.dependant.dependencies],
        )


if __name__ == "__main__":
    unittest.main()
