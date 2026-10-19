import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import main


def make_request(method="POST", headers=None, body=b""):
    request_headers = {
        name.lower().encode(): value.encode()
        for name, value in (headers or {}).items()
    }
    request_headers.setdefault(b"content-length", str(len(body)).encode())
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": list(request_headers.items()),
        "server": ("testserver", 443),
        "client": ("testclient", 123),
    }
    return Request(scope, receive=receive)


class FakeBolnaResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class VoiceSessionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com"},
        )
        self.environment.start()
        with main._SECURITY_STATE_LOCK:
            main._VOICE_CAPABILITIES.clear()
            main._RATE_LIMITS.clear()
        self.session = {
            "run_id": "run-123",
            "agent_id": "agent-456",
            "sip_username": "ephemeral-user",
            "sip_password": "ephemeral-password",
            "sip_domain": "sip.example.test",
            "wss_url": "wss://sip.example.test",
            "sip_register": False,
            "expires_in": 120,
            "ice_servers": [{"urls": "stun:stun.example.test"}],
        }

    def tearDown(self):
        self.environment.stop()

    def test_mints_session_for_configured_agent_and_returns_documented_fields(self):
        with (
            patch.dict(
                os.environ,
                {
                    "BOLNA_API_KEY": "bn-private-test-key",
                    "BOLNA_AGENT_ID": "agent-456",
                },
            ),
            patch.object(
                main,
                "urlopen",
                return_value=FakeBolnaResponse(self.session),
            ) as urlopen,
        ):
            result = main.mint_bolna_session()

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.bolna.ai/web-call/freeswitch-session",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer bn-private-test-key")
        self.assertEqual(json.loads(request.data), {"agent_id": "agent-456"})
        self.assertEqual(result.model_dump(), self.session)

    def test_missing_server_credentials_returns_service_unavailable(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaises(HTTPException) as error,
        ):
            main.mint_bolna_session()

        self.assertEqual(error.exception.status_code, 503)

    def test_rejects_session_for_a_different_agent(self):
        session = {**self.session, "agent_id": "unexpected-agent"}

        with (
            patch.dict(
                os.environ,
                {
                    "BOLNA_API_KEY": "bn-private-test-key",
                    "BOLNA_AGENT_ID": "agent-456",
                },
            ),
            patch.object(
                main,
                "urlopen",
                return_value=FakeBolnaResponse(session),
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.mint_bolna_session()

        self.assertEqual(error.exception.status_code, 502)

    def test_voice_session_route_does_not_use_dashboard_cookie_auth(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/voice/session"
            and "POST" in getattr(route, "methods", set())
        )

        self.assertNotIn(
            main.verify_api_key,
            [dependency.call for dependency in route.dependant.dependencies],
        )

    def test_voice_session_route_rejects_request_bodies(self):
        token = main.issue_voice_capability("https://app.example.com")
        request = make_request(
            headers={
                "origin": "https://app.example.com",
                "content-type": "application/json",
            },
            body=b"unexpected",
        )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                main.create_voice_session(request, f"Bearer {token}")
            )

        self.assertEqual(error.exception.status_code, 413)

    def test_valid_capability_mints_one_voice_session(self):
        token = main.issue_voice_capability("https://app.example.com")
        request = make_request(
            headers={
                "origin": "https://app.example.com",
                "content-type": "application/json",
            }
        )
        expected = main.BolnaWebCallSession.model_validate(self.session)
        with patch.object(main, "mint_bolna_session", return_value=expected) as mint:
            result = asyncio.run(
                main.create_voice_session(request, f"Bearer {token}")
            )

        self.assertEqual(result, expected)
        mint.assert_called_once_with()

    def test_expired_capability_is_rejected(self):
        token = main.issue_voice_capability("https://app.example.com")
        digest = main.hashlib.sha256(token.encode()).hexdigest()
        with main._SECURITY_STATE_LOCK:
            origin, scope, _ = main._VOICE_CAPABILITIES[digest]
            main._VOICE_CAPABILITIES[digest] = (origin, scope, 0)

        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                main.create_voice_session(
                    make_request(
                        headers={
                            "origin": "https://app.example.com",
                            "content-type": "application/json",
                        }
                    ),
                    f"Bearer {token}",
                )
            )

        self.assertEqual(error.exception.status_code, 401)

    def test_malformed_and_missing_capabilities_are_rejected(self):
        for authorization in ("Bearer malformed token", None):
            with self.subTest(authorization=authorization):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        main.create_voice_session(
                            make_request(
                                headers={
                                    "origin": "https://app.example.com",
                                    "content-type": "application/json",
                                }
                            ),
                            authorization,
                        )
                    )
                self.assertEqual(error.exception.status_code, 401)

    def test_wrong_scope_capability_is_rejected(self):
        token = main.secrets.token_urlsafe(32)
        digest = main.hashlib.sha256(token.encode()).hexdigest()
        with main._SECURITY_STATE_LOCK:
            main._VOICE_CAPABILITIES[digest] = (
                "https://app.example.com",
                "bills:write",
                time.time() + 120,
            )

        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                main.create_voice_session(
                    make_request(
                        headers={
                            "origin": "https://app.example.com",
                            "content-type": "application/json",
                        }
                    ),
                    f"Bearer {token}",
                )
            )

        self.assertEqual(error.exception.status_code, 401)

    def test_capability_is_bound_to_its_issuing_origin(self):
        token = main.issue_voice_capability("https://app.example.com")
        with patch.dict(
            os.environ,
            {
                "BILLBUDDY_FRONTEND_ORIGINS":
                    "https://app.example.com,https://other.example.com"
            },
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    main.create_voice_session(
                        make_request(
                            headers={
                                "origin": "https://other.example.com",
                                "content-type": "application/json",
                            }
                        ),
                        f"Bearer {token}",
                    )
                )

        self.assertEqual(error.exception.status_code, 401)

    def test_capability_is_single_use(self):
        token = main.issue_voice_capability("https://app.example.com")
        with patch.object(
            main,
            "mint_bolna_session",
            return_value=main.BolnaWebCallSession.model_validate(self.session),
        ):
            asyncio.run(
                main.create_voice_session(
                    make_request(
                        headers={
                            "origin": "https://app.example.com",
                            "content-type": "application/json",
                        }
                    ),
                    f"Bearer {token}",
                )
            )
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    main.create_voice_session(
                        make_request(
                            headers={
                                "origin": "https://app.example.com",
                                "content-type": "application/json",
                            }
                        ),
                        f"Bearer {token}",
                    )
                )

        self.assertEqual(error.exception.status_code, 401)

    def test_voice_capability_cannot_authenticate_bill_api(self):
        token = main.issue_voice_capability("https://app.example.com")
        with (
            patch.dict(os.environ, {"BILLBUDDY_API_KEY": "server-only-key"}),
            self.assertRaises(HTTPException) as error,
        ):
            main.verify_api_key(
                request=make_request(
                    headers={
                        "origin": "https://app.example.com",
                        "authorization": f"Bearer {token}",
                    }
                ),
                x_billbuddy_token=None,
                billbuddy_session=None,
            )

        self.assertEqual(error.exception.status_code, 401)

    def test_voice_session_requires_json_content_type(self):
        token = main.issue_voice_capability("https://app.example.com")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                main.create_voice_session(
                    make_request(headers={"origin": "https://app.example.com"}),
                    f"Bearer {token}",
                )
            )

        self.assertEqual(error.exception.status_code, 415)

    def test_rate_limit_cleanup_preserves_the_other_actions_window(self):
        with main._SECURITY_STATE_LOCK:
            main._RATE_LIMITS[("dashboard-login", "testclient")] = (
                main.LOGIN_RATE_WINDOW_SECONDS,
                [time.monotonic() - 120],
            )

        main.enforce_rate_limit(
            make_request(),
            action="voice-session",
            limit=main.VOICE_RATE_LIMIT,
            window_seconds=main.VOICE_RATE_WINDOW_SECONDS,
        )

        self.assertIn(("dashboard-login", "testclient"), main._RATE_LIMITS)

    def test_cookie_auth_requires_exact_allowed_origin_for_mutations(self):
        request = make_request(headers={"origin": "https://attacker.example"})
        with (
            patch.dict(
                os.environ,
                {
                    "BILLBUDDY_API_KEY": "test-api-key",
                    "BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com",
                },
            ),
            patch.object(main, "verify_dashboard_session", return_value=True),
            self.assertRaises(HTTPException) as error,
        ):
            main.verify_api_key(
                request=request,
                x_billbuddy_token=None,
                billbuddy_session="valid-session",
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_cookie_auth_allows_mutations_from_exact_frontend_origin(self):
        with (
            patch.dict(
                os.environ,
                {
                    "BILLBUDDY_API_KEY": "test-api-key",
                    "BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com",
                    "BILLBUDDY_SESSION_SECRET": "test-session-secret",
                },
            ),
            patch.object(main, "verify_dashboard_session", return_value=True),
        ):
            csrf_token = main.create_dashboard_csrf_token(
                "valid-session",
                "test-session-secret",
            )
            self.assertIsNone(
                main.verify_api_key(
                    request=make_request(
                        headers={
                            "origin": "https://app.example.com",
                            "x-billbuddy-csrf": csrf_token,
                        }
                    ),
                    x_billbuddy_token=None,
                    billbuddy_session="valid-session",
                )
            )

    def test_cookie_auth_allows_safe_requests_without_origin(self):
        request = make_request(method="GET")
        with (
            patch.dict(
                os.environ,
                {
                    "BILLBUDDY_API_KEY": "test-api-key",
                    "BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com",
                },
            ),
            patch.object(main, "verify_dashboard_session", return_value=True),
        ):
            self.assertIsNone(
                main.verify_api_key(
                    request=request,
                    x_billbuddy_token=None,
                    billbuddy_session="valid-session",
                )
            )

    def test_api_key_authentication_does_not_require_browser_origin(self):
        request = make_request()
        with patch.dict(
            os.environ,
            {"BILLBUDDY_API_KEY": "test-api-key"},
        ):
            self.assertIsNone(
                main.verify_api_key(
                    request=request,
                    x_billbuddy_token="test-api-key",
                    billbuddy_session=None,
                )
            )

    def test_login_and_voice_session_reject_untrusted_origins(self):
        with (
            patch.dict(
                os.environ,
                {"BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com"},
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.require_frontend_origin(
                make_request(headers={"origin": "https://evil.example"})
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_cookie_auth_mutation_requires_valid_session_bound_csrf_token(self):
        with (
            patch.dict(
                os.environ,
                {
                    "BILLBUDDY_API_KEY": "test-api-key",
                    "BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com",
                    "BILLBUDDY_SESSION_SECRET": "test-session-secret",
                },
            ),
            patch.object(main, "verify_dashboard_session", return_value=True),
            self.assertRaises(HTTPException) as error,
        ):
            main.verify_api_key(
                request=make_request(
                    headers={"origin": "https://app.example.com"}
                ),
                x_billbuddy_token=None,
                billbuddy_session="valid-session",
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_login_rejects_untrusted_origin_before_authentication(self):
        with (
            patch.dict(
                os.environ,
                {"BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com"},
            ),
            self.assertRaises(HTTPException) as error,
        ):
            asyncio.run(
                main.dashboard_login(
                    make_request(headers={"origin": "https://attacker.example"}),
                    main.Response(),
                )
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_login_returns_session_bound_csrf_token_with_secure_cookie(self):
        response = main.Response()
        with patch.dict(
            os.environ,
            {
                "BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com",
                "BILLBUDDY_DASHBOARD_PASSWORD": "test-password",
                "BILLBUDDY_SESSION_SECRET": "test-session-secret",
                "BILLBUDDY_COOKIE_SECURE": "true",
                "BILLBUDDY_COOKIE_SAMESITE": "lax",
            },
        ):
            result = asyncio.run(
                main.dashboard_login(
                    make_request(
                        headers={
                            "origin": "https://app.example.com",
                            "content-type": "application/json",
                        },
                        body=b'{"password":"test-password"}',
                    ),
                    response,
                )
            )

        cookie = response.headers["set-cookie"]
        session = cookie.split(";", maxsplit=1)[0].split("=", maxsplit=1)[1]
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["voice_capability"])
        self.assertEqual(
            result["voice_capability_expires_in"],
            main.VOICE_CAPABILITY_TTL_SECONDS,
        )
        self.assertEqual(
            result["csrf_token"],
            main.create_dashboard_csrf_token(session, "test-session-secret"),
        )
        self.assertIn("httponly", cookie.lower())
        self.assertIn("secure", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        self.assertNotIn("domain=", cookie.lower())

    def test_authenticated_session_returns_uncached_csrf_token(self):
        session = main.create_dashboard_session(
            2_000_000_000,
            "test-session-secret",
        )
        response = main.Response()
        with patch.dict(
            os.environ,
            {"BILLBUDDY_SESSION_SECRET": "test-session-secret"},
        ):
            result = main.dashboard_session_status(
                response,
                billbuddy_session=session,
            )

        self.assertTrue(result["authenticated"])
        self.assertEqual(
            result["csrf_token"],
            main.create_dashboard_csrf_token(session, "test-session-secret"),
        )
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_logout_rejects_untrusted_origin(self):
        with (
            patch.dict(
                os.environ,
                {"BILLBUDDY_FRONTEND_ORIGINS": "https://app.example.com"},
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.dashboard_logout(
                make_request(headers={"origin": "https://attacker.example"}),
                main.Response(),
            )

        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
