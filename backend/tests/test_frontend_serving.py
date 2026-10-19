import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import main


async def request_app(path, method="GET", headers=None):
    raw_path = path.encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "query_string": b"",
        "headers": [
            (name.lower().encode(), value.encode())
            for name, value in (headers or {}).items()
        ],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            await asyncio.Future()
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

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
    return response_start["status"], dict(response_start["headers"]), response_body


class FrontendServingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.frontend_dist = Path(self.temp_dir.name)
        (self.frontend_dist / "assets").mkdir()
        (self.frontend_dist / "index.html").write_text(
            "<!doctype html><html>BillBuddy</html>",
            encoding="utf-8",
        )
        (self.frontend_dist / "assets" / "app.js").write_text(
            "console.log('app')",
            encoding="utf-8",
        )
        self.dist_patch = patch.object(
            main,
            "FRONTEND_DIST_DIR",
            self.frontend_dist,
        )
        self.dist_patch.start()

    def tearDown(self):
        self.dist_patch.stop()
        self.temp_dir.cleanup()

    def test_root_serves_frontend_index(self):
        status, headers, body = asyncio.run(
            request_app("/", headers={"Accept": "text/html"})
        )

        self.assertEqual(status, 200)
        self.assertIn(b"text/html", headers[b"content-type"])
        self.assertIn(b"BillBuddy", body)

    def test_root_preserves_api_json_for_non_html_requests(self):
        status, headers, body = asyncio.run(
            request_app("/", headers={"Accept": "application/json"})
        )

        self.assertEqual(status, 200)
        self.assertIn(b"application/json", headers[b"content-type"])
        self.assertEqual(json.loads(body), {"message": "BillBuddy API is running"})

    def test_static_asset_is_served_from_build_directory(self):
        status, headers, body = asyncio.run(request_app("/assets/app.js"))

        self.assertEqual(status, 200)
        self.assertIn(b"javascript", headers[b"content-type"])
        self.assertEqual(body, b"console.log('app')")

    def test_unknown_frontend_route_uses_spa_index(self):
        status, _, body = asyncio.run(request_app("/dashboard/settings"))

        self.assertEqual(status, 200)
        self.assertIn(b"BillBuddy", body)

    def test_missing_asset_does_not_use_spa_fallback(self):
        status, _, body = asyncio.run(request_app("/assets/missing.js"))

        self.assertEqual(status, 404)
        self.assertNotIn(b"BillBuddy", body)

    def test_unknown_api_route_does_not_use_spa_fallback(self):
        status, _, body = asyncio.run(request_app("/voice/unknown"))

        self.assertEqual(status, 404)
        self.assertNotIn(b"BillBuddy", body)

    def test_existing_health_endpoint_remains_json(self):
        status, headers, body = asyncio.run(request_app("/health"))

        self.assertEqual(status, 200)
        self.assertIn(b"application/json", headers[b"content-type"])
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_health_check_fails_when_frontend_build_is_missing(self):
        empty_dir = tempfile.TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        with patch.object(main, "FRONTEND_DIST_DIR", Path(empty_dir.name)):
            status, _, body = asyncio.run(request_app("/health"))

        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(body),
            {"detail": "Frontend assets are not available"},
        )

    def test_bill_endpoint_keeps_authentication_and_is_not_spa(self):
        with patch.dict(os.environ, {"BILLBUDDY_API_KEY": "local-test-key"}):
            status, _, body = asyncio.run(request_app("/bills"))

        self.assertEqual(status, 401)
        self.assertNotIn(b"BillBuddy", body)

    def test_path_traversal_cannot_escape_build_directory(self):
        status, _, body = asyncio.run(request_app("/../outside.html"))

        self.assertEqual(status, 404)
        self.assertNotIn(b"BillBuddy", body)


if __name__ == "__main__":
    unittest.main()
