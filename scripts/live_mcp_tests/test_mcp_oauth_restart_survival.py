"""The critical regression test for `app.core.mcp_oauth` / `app.mcp.oauth.
provider`'s persistent client registry: does a registered OAuth client
actually survive a REAL MCP server process restart, not just a fresh
in-process object?

Runs against a throwaway `scripts/run_mcp_server.py` subprocess on its own
ephemeral port (never the shared dev server on `MCP_PORT`/8001 other live
tests and any real Claude connector use) -- killed and restarted mid-test,
which is the whole point: a real process boundary, not a simulated one.

RUN
    pytest scripts/live_mcp_tests/test_mcp_oauth_restart_survival.py -v -s
    (spawns and tears down its own server subprocess; nothing else needs to
    be running first, unlike the other live_mcp_tests files.)
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RESTART_TEST_PORT = 8099
_BASE_URL = f"http://127.0.0.1:{_RESTART_TEST_PORT}"


def _wait_for_port(port: int, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.3)
    raise TimeoutError(f"Nothing started listening on 127.0.0.1:{port} within {timeout}s")


def _wait_for_port_free(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                pass
        except OSError:
            return
        time.sleep(0.3)
    raise TimeoutError(f"127.0.0.1:{port} was still accepting connections after {timeout}s")


def _spawn_server() -> subprocess.Popen:
    env = {
        **os.environ,
        "MCP_PORT": str(_RESTART_TEST_PORT),
        # Isolated from the shared dev server's ngrok-facing value on purpose
        # -- this subprocess is only ever reached over loopback.
        "MCP_PUBLIC_BASE_URL": _BASE_URL,
    }
    # Redirected to a file, NOT `subprocess.PIPE`: this app's SQLAlchemy
    # logging is verbose enough to fill an unread pipe's OS buffer, which
    # would deadlock the child the moment it blocks trying to write past a
    # full pipe that nothing here ever drains.
    log_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=f"_mcp_restart_test_{os.getpid()}_{time.monotonic_ns()}.log", delete=False
    )
    proc = subprocess.Popen(
        [sys.executable, "scripts/run_mcp_server.py"],
        cwd=str(_PROJECT_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    proc._restart_test_log_path = log_file.name  # stashed for failure diagnostics below
    log_file.close()
    try:
        _wait_for_port(_RESTART_TEST_PORT)
    except TimeoutError:
        with open(log_file.name, encoding="utf-8", errors="replace") as f:
            raise TimeoutError(f"server subprocess never bound; its log:\n{f.read()}") from None
    return proc


def _kill_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    _wait_for_port_free(_RESTART_TEST_PORT)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest.mark.asyncio
async def test_registered_client_survives_a_real_server_process_restart(access_token: str) -> None:
    """1. register a client against process A
    2. complete authorize+token to obtain a refresh_token bound to it
    3. kill process A entirely (real OS process termination)
    4. start process B, a genuinely fresh Python process/interpreter
    5. present the SAME client_id/client_secret to process B's /token
       (refresh_token grant) -- must succeed, proving `get_client()` found
       it via the database, not a memory that no longer exists
    6. use the resulting access_token against a real /mcp tool call on
       process B -- must succeed end-to-end
    """
    proc_a = _spawn_server()
    try:
        with httpx.Client(base_url=_BASE_URL, timeout=15, follow_redirects=False) as client:
            reg = client.post(
                "/register",
                json={
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                    "client_name": "restart-survival-test",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                },
            ).json()
            client_id = reg["client_id"]
            client_secret = reg["client_secret"]
            redirect_uri = reg["redirect_uris"][0]
            assert client_secret, "expected a client_secret for a client_secret_post registration"

            verifier, challenge = _pkce_pair()
            authorize_resp = client.get(
                "/authorize",
                params={
                    "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
                    "code_challenge": challenge, "code_challenge_method": "S256", "state": "restart-test",
                },
            )
            flow_id = parse_qs(urlparse(authorize_resp.headers["location"]).query)["flow_id"][0]

            confirm_resp = client.post(
                "/ekip/oauth/authorize", data={"flow_id": flow_id, "ekip_access_token": access_token}
            )
            code = parse_qs(urlparse(confirm_resp.headers["location"]).query)["code"][0]

            token_resp = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                    "client_id": client_id, "client_secret": client_secret, "code_verifier": verifier,
                },
            )
            assert token_resp.status_code == 200, token_resp.text
            refresh_token = token_resp.json()["refresh_token"]

        print(f"PASS: registered {client_id!r} and obtained a refresh_token against process A "
              f"(pid={proc_a.pid})")
    finally:
        _kill_server(proc_a)
    print(f"PASS: process A (pid={proc_a.pid}) killed; port {_RESTART_TEST_PORT} confirmed free")

    proc_b = _spawn_server()
    try:
        assert proc_b.pid != proc_a.pid, "test setup bug: expected a genuinely new process"

        with httpx.Client(base_url=_BASE_URL, timeout=15, follow_redirects=False) as client:
            refresh_resp = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token", "refresh_token": refresh_token,
                    "client_id": client_id, "client_secret": client_secret,
                },
            )
            assert refresh_resp.status_code == 200, (
                f"refresh failed against the NEW process (pid={proc_b.pid}) -- the client "
                f"registration did not survive the restart: {refresh_resp.text}"
            )
            new_access_token = refresh_resp.json()["access_token"]
            print(f"PASS: refresh_token grant succeeded against process B (pid={proc_b.pid}) -- "
                  f"client_id {client_id!r} was found via persistent storage, not memory")

        mcp_headers = {"Authorization": f"Bearer {new_access_token}"}
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        }
        mcp_resp = httpx.post(
            f"{_BASE_URL}/mcp", json=body, headers={
                **mcp_headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            }, timeout=15,
        )
        assert mcp_resp.status_code == 200, f"MCP initialize failed on process B: {mcp_resp.text}"
        print(f"PASS: MCP initialize succeeded on process B using the token from the post-restart refresh")
    finally:
        _kill_server(proc_b)


@pytest.mark.asyncio
async def test_duplicate_client_registration_is_idempotent() -> None:
    """RFC 7591 always mints a fresh `client_id` per registration, so a true
    "same client_id twice" case cannot happen through the public `/register`
    endpoint -- the realistic version of "duplicate registration" is a
    client retrying a registration call whose response it never saw (the
    request actually succeeded server-side). This exercises `core.mcp_oauth.
    service.register_oauth_client` directly with the identical `client_info`
    twice: it must not raise, and the second call's data must win cleanly
    (see `repository.upsert_oauth_client`'s docstring for why this is an
    upsert, not a plain insert).
    """
    import uuid

    from app.core.mcp_oauth import service as mcp_oauth_service
    from app.database.session import session_scope

    client_id = f"duplicate-registration-test-{uuid.uuid4()}"
    client_info = {
        "client_id": client_id,
        "client_secret": "first-secret",
        "client_secret_expires_at": 0,
        "client_id_issued_at": 1700000000,
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": "ekip",
        "client_name": "duplicate-test-v1",
    }

    async with session_scope() as session:
        await mcp_oauth_service.register_oauth_client(session, client_info)

    # Same client_id, registered again (simulating a retried DCR call) --
    # must not raise an IntegrityError, and must overwrite cleanly.
    updated_info = {**client_info, "client_secret": "second-secret", "client_name": "duplicate-test-v2"}
    async with session_scope() as session:
        await mcp_oauth_service.register_oauth_client(session, updated_info)

    async with session_scope() as session:
        data = await mcp_oauth_service.get_registered_client(session, client_id)

    assert data is not None
    assert data["client_secret"] == "second-secret", "second registration should win, not be ignored or conflict"
    assert data["client_name"] == "duplicate-test-v2"
    print(f"PASS: duplicate registration of {client_id!r} was idempotent -- second write won cleanly")
