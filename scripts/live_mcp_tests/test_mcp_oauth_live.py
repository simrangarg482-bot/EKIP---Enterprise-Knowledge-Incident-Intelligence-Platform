"""Live integration tests for `app.mcp.oauth` -- the OAuth 2.1 bridge that
lets an OAuth-only MCP client (Claude's "Add custom connector" UI) obtain and
refresh a real EKIP access token. See `app/mcp/oauth/provider.py`'s module
docstring for the full design.

Every test here goes over real HTTP to the same separately-running
`scripts/run_mcp_server.py` process `test_mcp_live.py` targets -- these are
the plain OAuth endpoints (`/register`, `/authorize`, `/ekip/oauth/authorize`,
`/token`), not the MCP JSON-RPC transport, so a plain `httpx` client is used
instead of the `mcp` client SDK.

RUN
    python scripts/run_mcp_server.py         # terminal 1
    pytest scripts/live_mcp_tests/test_mcp_oauth_live.py -v -s   # terminal 2
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


@pytest.fixture()
def oauth_base(mcp_server: str) -> str:
    """`mcp_server` is the `/mcp` JSON-RPC URL; OAuth endpoints live one
    level up, at the server's plain root."""
    return mcp_server.removesuffix("/mcp")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register_client(client: httpx.Client) -> tuple[str, str]:
    response = client.post(
        "/register",
        json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "ekip-oauth-live-test",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["client_id"], body["redirect_uris"][0]


def _begin_authorize(client: httpx.Client, *, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    return response.headers["location"]


class TestOAuthDiscovery:
    def test_protected_resource_metadata_is_served(self, oauth_base: str) -> None:
        response = httpx.get(f"{oauth_base}/.well-known/oauth-protected-resource", timeout=15)
        assert response.status_code == 200
        body = response.json()
        assert body["resource"].rstrip("/") == oauth_base.rstrip("/") or "authorization_servers" in body
        print(f"PASS: protected resource metadata served -- {body}")

    def test_authorization_server_metadata_is_served(self, oauth_base: str) -> None:
        response = httpx.get(f"{oauth_base}/.well-known/oauth-authorization-server", timeout=15)
        assert response.status_code == 200
        body = response.json()
        for endpoint in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            assert endpoint in body, f"missing {endpoint} in authorization server metadata: {body}"
        assert "S256" in body.get("code_challenge_methods_supported", [])
        print(f"PASS: authorization server metadata served -- endpoints: "
              f"{[k for k in body if k.endswith('_endpoint')]}")

    def test_unauthenticated_mcp_request_gets_401_with_oauth_challenge(self, mcp_server: str) -> None:
        """Confirms the MCP endpoint is a real OAuth-protected resource: an
        unauthenticated request must get a `401` carrying a `WWW-Authenticate`
        header pointing at protected-resource metadata -- this is exactly
        what makes an OAuth-aware client (Claude included) attempt discovery
        instead of silently proceeding unauthenticated.
        """
        response = httpx.post(
            mcp_server,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            },
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=15,
        )
        assert response.status_code == 401, response.text
        challenge = response.headers.get("www-authenticate", "")
        assert "resource_metadata=" in challenge, f"missing resource_metadata in WWW-Authenticate: {challenge}"
        print(f"PASS: unauthenticated MCP request rejected with OAuth challenge -- {challenge}")


class TestOAuthAuthorizationCodeFlow:
    def test_dynamic_client_registration_succeeds(self, oauth_base: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, _ = _register_client(client)
        assert client_id
        print(f"PASS: dynamic client registration issued client_id={client_id}")

    def test_authorize_redirects_to_ekip_confirmation_page(self, oauth_base: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            _, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s1"
            )
        assert location.startswith("/ekip/oauth/authorize"), location
        print(f"PASS: /authorize redirected to EKIP's own confirmation page -- {location}")

    def test_confirmation_page_rejects_invalid_ekip_token(self, oauth_base: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            _, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s2"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]

            response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": "not-a-real-jwt"},
                follow_redirects=False,
            )
        # Rejected tokens re-render the form (200) with an error, never a redirect with a `code`.
        assert response.status_code == 200, response.text
        assert "could not be verified" in response.text.lower()
        print("PASS: confirmation page rejected an invalid EKIP token without issuing a code")

    def test_full_authorization_code_flow_with_a_real_ekip_token(self, oauth_base: str, access_token: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            verifier, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s3"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]

            confirm_response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": access_token},
                follow_redirects=False,
            )
            assert confirm_response.status_code == 302, confirm_response.text
            callback = parse_qs(urlparse(confirm_response.headers["location"]).query)
            assert callback["state"][0] == "s3"
            code = callback["code"][0]

            token_response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            assert token_response.status_code == 200, token_response.text
            tokens = token_response.json()
            assert tokens["token_type"] == "Bearer"
            assert tokens["access_token"]
            assert tokens["refresh_token"]

            # A used code must not be exchangeable a second time.
            replay_response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            assert replay_response.status_code >= 400, "a used authorization code was accepted a second time"

        print("PASS: full DCR -> authorize -> confirm -> PKCE token exchange succeeded end-to-end; code replay rejected")

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_confirmation_submissions_never_500(
        self, oauth_base: str, access_token: str
    ) -> None:
        """Regression test for the exact production failure: submitting the
        `/ekip/oauth/authorize` confirmation form twice for the same
        `flow_id` (a real browser double-click, or a retried request) used
        to raise an uncaught `KeyError` on the second `del self._pending_
        flows[flow_id]` -- surfaced to Claude as a bare "Internal Server
        Error" / "Couldn't register with ekip's sign-in service". Fired
        concurrently here (`asyncio.gather`) to force the actual race, not
        just a sequential double-POST that the old code might happen to
        serialize around.
        """
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            _, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="race"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]

        async def submit() -> httpx.Response:
            async with httpx.AsyncClient(base_url=oauth_base, timeout=15, follow_redirects=False) as ac:
                return await ac.post(
                    "/ekip/oauth/authorize", data={"flow_id": flow_id, "ekip_access_token": access_token}
                )

        responses = await asyncio.gather(*(submit() for _ in range(5)))
        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, f"a concurrent duplicate submission 500'd -- statuses: {statuses}"
        assert statuses.count(302) == 1, f"expected exactly one submission to win with a 302, got: {statuses}"
        assert all(s in (302, 400) for s in statuses), f"unexpected status among duplicates: {statuses}"
        print(f"PASS: 5 concurrent duplicate confirmation submissions -- exactly one 302, rest 400, no 500 -- {statuses}")

    def test_wrong_pkce_verifier_is_rejected(self, oauth_base: str, access_token: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            _correct_verifier, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s4"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]
            confirm_response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": access_token},
                follow_redirects=False,
            )
            code = parse_qs(urlparse(confirm_response.headers["location"]).query)["code"][0]

            wrong_verifier, _ = _pkce_pair()
            response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": wrong_verifier,
                },
            )
        assert response.status_code >= 400, "a mismatched PKCE code_verifier was accepted"
        print("PASS: mismatched PKCE code_verifier rejected")

    @pytest.mark.asyncio
    async def test_oauth_issued_access_token_authenticates_a_real_tool_call(
        self, oauth_base: str, mcp_server: str, access_token: str
    ) -> None:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            verifier, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s5"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]
            confirm_response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": access_token},
                follow_redirects=False,
            )
            code = parse_qs(urlparse(confirm_response.headers["location"]).query)["code"][0]
            token_response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            oauth_access_token = token_response.json()["access_token"]
            oauth_refresh_token = token_response.json()["refresh_token"]

        headers = {"Authorization": f"Bearer {oauth_access_token}"}
        async with httpx.AsyncClient(headers=headers, timeout=60.0) as http:
            async with streamable_http_client(mcp_server, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "search_similar_incidents", {"description": "checkout returning 500 errors"}
                    )

        assert not result.is_error, f"tool call with OAuth-issued token failed: {result.content}"
        print(
            "PASS: OAuth-issued access token authenticated a real MCP tool call "
            f"(refresh_token also issued: {bool(oauth_refresh_token)})"
        )

    def test_refresh_token_grant_issues_a_new_working_token(self, oauth_base: str, access_token: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            client_id, redirect_uri = _register_client(client)
            verifier, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="s6"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]
            confirm_response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": access_token},
                follow_redirects=False,
            )
            code = parse_qs(urlparse(confirm_response.headers["location"]).query)["code"][0]
            token_response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            first_refresh_token = token_response.json()["refresh_token"]

            refresh_response = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": first_refresh_token,
                    "client_id": client_id,
                },
            )
        assert refresh_response.status_code == 200, refresh_response.text
        refreshed = refresh_response.json()
        assert refreshed["access_token"]
        assert refreshed["refresh_token"]
        print("PASS: refresh_token grant issued a new access/refresh token pair")

    def test_refresh_with_unknown_client_id_is_rejected(self, oauth_base: str) -> None:
        """Regression test for the exact real-world failure this project's
        persistent client registry (`app.core.mcp_oauth`) was built to fix:
        Claude presenting a `client_id` the server has never seen (before the
        fix, this happened whenever the in-memory registry had been cleared
        by a restart; now it should only ever happen for a `client_id` that
        was never really registered at all -- either way, the server's
        answer must be a clean `invalid_client`, never a crash).
        """
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            response = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": "does-not-matter-client-is-unknown",
                    "client_id": "00000000-0000-0000-0000-000000000000",
                },
            )
        assert response.status_code == 401, response.text
        assert response.json().get("error") == "invalid_client"
        print("PASS: refresh_token grant with an unknown client_id rejected with invalid_client")

    def test_refresh_with_wrong_client_secret_is_rejected(self, oauth_base: str, access_token: str) -> None:
        with httpx.Client(base_url=oauth_base, timeout=15) as client:
            reg = client.post(
                "/register",
                json={
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                    "client_name": "wrong-secret-test",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                },
            ).json()
            client_id, redirect_uri = reg["client_id"], reg["redirect_uris"][0]
            assert reg["client_secret"], "expected a client_secret for a client_secret_post registration"

            verifier, challenge = _pkce_pair()
            location = _begin_authorize(
                client, client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state="wrong-secret"
            )
            flow_id = parse_qs(urlparse(location).query)["flow_id"][0]
            confirm_response = client.post(
                "/ekip/oauth/authorize",
                data={"flow_id": flow_id, "ekip_access_token": access_token},
                follow_redirects=False,
            )
            code = parse_qs(urlparse(confirm_response.headers["location"]).query)["code"][0]

            response = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": "definitely-the-wrong-secret",
                    "code_verifier": verifier,
                },
            )
        assert response.status_code == 401, response.text
        print("PASS: authorization_code exchange with the wrong client_secret rejected")
