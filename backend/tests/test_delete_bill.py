import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend import main


def make_request(method="POST", headers=None):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [
            (name.lower().encode(), value.encode())
            for name, value in (headers or {}).items()
        ],
        "server": ("testserver", 443),
        "client": ("testclient", 123),
    }
    return Request(scope)


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, deleted_id):
        self.deleted_id = deleted_id
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params
        row = {"id": self.deleted_id} if self.deleted_id == params[0] else None
        return FakeCursor(row)


class DeleteBillTests(unittest.TestCase):
    def test_accepts_positive_integer_bill_id(self):
        self.assertEqual(main.BillDelete.model_validate({"bill_id": 12}).bill_id, 12)

    def test_normalizes_positive_decimal_string_bill_id(self):
        self.assertEqual(main.BillDelete.model_validate({"bill_id": "12"}).bill_id, 12)

    def test_rejects_missing_and_invalid_bill_ids(self):
        invalid_payloads = (
            {},
            {"bill_id": ""},
            {"bill_id": "abc"},
            {"bill_id": "1.5"},
            {"bill_id": "0"},
            {"bill_id": "-1"},
            {"bill_id": 0},
            {"bill_id": -1},
            {"bill_id": 1.5},
            {"bill_id": True},
            {"bill_id": None},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    main.BillDelete.model_validate(payload)

    def test_post_endpoint_deletes_only_the_normalized_id(self):
        connection = FakeConnection(deleted_id=12)

        with patch.object(main, "connect_db", return_value=connection):
            result = main.delete_bill_from_body(
                main.BillDelete.model_validate({"bill_id": "12"}),
                None,
            )

        self.assertEqual(result, {"message": "Bill deleted successfully"})
        self.assertEqual(connection.query, "DELETE FROM bills WHERE id = %s RETURNING id")
        self.assertEqual(connection.params, (12,))

    def test_post_endpoint_returns_404_when_bill_does_not_exist(self):
        connection = FakeConnection(deleted_id=None)

        with patch.object(main, "connect_db", return_value=connection):
            with self.assertRaises(HTTPException) as error:
                main.delete_bill_from_body(
                    main.BillDelete.model_validate({"bill_id": 12}),
                    None,
                )

        self.assertEqual(error.exception.status_code, 404)

    def test_api_key_dependency_rejects_missing_or_invalid_credentials(self):
        os.environ["BILLBUDDY_API_KEY"] = "test-api-key"
        os.environ.pop("BILLBUDDY_SESSION_SECRET", None)

        for token in (None, "incorrect-api-key"):
            with self.subTest(token=token):
                with self.assertRaises(HTTPException) as error:
                    main.verify_api_key(
                        request=make_request(),
                        x_billbuddy_token=token,
                        billbuddy_session=None,
                    )
                self.assertEqual(error.exception.status_code, 401)

    def test_post_endpoint_declares_the_existing_auth_dependency(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/bills/delete"
            and "POST" in getattr(route, "methods", set())
        )
        self.assertIn(
            main.verify_api_key,
            [dependency.call for dependency in route.dependant.dependencies],
        )

    def test_existing_path_delete_behavior_uses_shared_helper(self):
        connection = FakeConnection(deleted_id=12)

        with patch.object(main, "connect_db", return_value=connection):
            result = main.delete_bill(12, None)

        self.assertEqual(result, {"message": "Bill deleted successfully"})
        self.assertEqual(connection.params, (12,))


if __name__ == "__main__":
    unittest.main()
