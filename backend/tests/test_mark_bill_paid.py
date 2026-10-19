import asyncio
import json
import os
import unittest
from unittest.mock import patch

from backend import main


async def request_app(path, method="PATCH", headers=None, body=b""):
    raw_path = path.encode()
    request_headers = {
        name.lower().encode(): value.encode()
        for name, value in (headers or {}).items()
    }
    if body:
        request_headers.setdefault(b"content-length", str(len(body)).encode())
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": raw_path,
        "query_string": b"",
        "headers": list(request_headers.items()),
        "server": ("testserver", 443),
        "client": ("testclient", 123),
    }
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            await asyncio.Future()
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await main.app(scope, receive, send)
    response_start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return (
        response_start["status"],
        dict(response_start["headers"]),
        response_body,
    )


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
        with (
            patch.dict(os.environ, {"BILLBUDDY_API_KEY": "unit-test-key"}),
            patch.object(main, "connect_db", return_value=connection),
        ):
            status, headers, body = asyncio.run(
                request_app(
                    "/bills/12",
                    headers={
                        "X-BillBuddy-Token": "unit-test-key",
                        "Content-Type": "application/json",
                    },
                    body=b'{"paid":true}',
                )
            )

        self.assertEqual(status, 200)
        self.assertIn(b"application/json", headers[b"content-type"])
        result = json.loads(body)
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

    def test_patch_rejects_invalid_bill_id(self):
        with patch.dict(os.environ, {"BILLBUDDY_API_KEY": "unit-test-key"}):
            status, _, body = asyncio.run(
                request_app(
                    "/bills/not-an-integer",
                    headers={"X-BillBuddy-Token": "unit-test-key"},
                    body=b'{"paid":true}',
                )
            )

        self.assertEqual(status, 422)
        self.assertIn(b"detail", body)

    def test_patch_rejects_missing_or_invalid_api_key(self):
        for token in (None, "wrong-key"):
            with self.subTest(token_present=token is not None):
                headers = {"Content-Type": "application/json"}
                if token is not None:
                    headers["X-BillBuddy-Token"] = token
                with patch.dict(
                    os.environ,
                    {"BILLBUDDY_API_KEY": "unit-test-key"},
                ):
                    status, _, body = asyncio.run(
                        request_app(
                            "/bills/12",
                            headers=headers,
                            body=b'{"paid":true}',
                        )
                    )

                self.assertEqual(status, 401)
                self.assertEqual(
                    json.loads(body),
                    {"detail": "Invalid or missing authentication"},
                )

    def test_patch_rejects_incorrectly_formatted_body(self):
        with patch.dict(os.environ, {"BILLBUDDY_API_KEY": "unit-test-key"}):
            status, _, body = asyncio.run(
                request_app(
                    "/bills/12",
                    headers={
                        "X-BillBuddy-Token": "unit-test-key",
                        "Content-Type": "application/json",
                    },
                    body=b'{"paid":"not-a-boolean"}',
                )
            )

        self.assertEqual(status, 422)
        self.assertIn(b"detail", body)

    def test_post_to_mark_paid_path_is_not_supported(self):
        with patch.dict(os.environ, {"BILLBUDDY_API_KEY": "unit-test-key"}):
            status, _, _ = asyncio.run(
                request_app(
                    "/bills/12",
                    method="POST",
                    headers={"X-BillBuddy-Token": "unit-test-key"},
                    body=b'{"paid":true}',
                )
            )

        self.assertEqual(status, 405)

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
