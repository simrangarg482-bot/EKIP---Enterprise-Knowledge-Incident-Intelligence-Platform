"""Shared fixtures for EKIP's live MCP server integration tests.

WHY THESE LIVE UNDER `scripts/`, NOT `tests/`
    `pyproject.toml` sets `testpaths = ["tests"]`, so a bare `pytest` would
    auto-collect anything under `tests/`. These tests need a REAL running MCP
    server process, a real database, a real `OPENAI_API_KEY` (tool calls
    invoke the actual agent graph), and they cost real money per run --
    auto-collecting them would make an ordinary `pytest` run hang or fail on
    any machine without that environment. Same placement decision, and the
    same reasoning, as `scripts/live_connector_tests/`.

    Run them explicitly:

        python scripts/run_mcp_server.py          # in one terminal
        pytest scripts/live_mcp_tests/ -v -s      # in another

WHAT "LIVE" MEANS HERE
    These are not in-process calls into `app.mcp`. Every assertion below
    goes over real HTTP to a separately-running server process, through the
    real MCP streamable-HTTP transport, using the official `mcp` client
    SDK's `ClientSession`. That is the only way to exercise what a real MCP
    client (Claude Desktop, an IDE plugin) actually does: transport framing,
    the `initialize` handshake, bearer-token extraction from request
    headers, and JSON-RPC tool dispatch. An in-process test skips all of it.

SKIPPING RATHER THAN FAILING
    If no server is reachable, every test SKIPS with an explicit instruction
    instead of failing -- a missing local process is an environment
    condition, not a defect in the code under test. Same convention as the
    connector suites' credential gating.
"""

from __future__ import annotations

import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HARNESS_DIR = _PROJECT_ROOT / "tests" / "ingestion_retrieval"
for _path in (_PROJECT_ROOT, _HARNESS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest  # noqa: E402

import config as harness_config  # noqa: E402  (tests/ingestion_retrieval/config.py)
import utils as harness_utils  # noqa: E402  (tests/ingestion_retrieval/utils.py)

from app.shared.config.settings import get_settings  # noqa: E402

#: Where `scripts/run_mcp_server.py` binds by default -- derived from the
#: same `Settings.mcp_port` (`MCP_PORT` env var) it reads, so the two never
#: drift out of sync. `EKIP_MCP_URL` (the `mcp_url` fixture below) still
#: overrides this outright for pointing the suite at a non-default host/URL
#: entirely (e.g. a remote/ngrok-fronted server).
DEFAULT_MCP_URL = f"http://127.0.0.1:{get_settings().mcp_port}/mcp"


@pytest.fixture(autouse=True)
async def _fresh_engine_pool_per_event_loop():
    """Drop the app engine's pooled connections before each async test.

    Identical reasoning to `scripts/live_connector_tests/conftest.py`'s
    fixture of the same name: the session-scoped bootstrap below runs on the
    harness's own persistent loop, while `pytest-asyncio` gives each test a
    fresh loop, and an asyncpg connection is permanently bound to the loop
    that opened it. Without this, the first test that touches the database
    borrows a connection from a dead loop and raises "Task got Future
    attached to a different loop".
    """
    from app.database.session import engine

    await engine.dispose()
    yield


@pytest.fixture(scope="session")
def cfg() -> harness_config.Config:
    return harness_config.load_config()


@pytest.fixture(scope="session")
def mcp_url() -> str:
    import os

    return os.environ.get("EKIP_MCP_URL", DEFAULT_MCP_URL)


@pytest.fixture(scope="session")
def mcp_server(mcp_url: str) -> str:
    """Skip the whole suite unless an MCP server is actually accepting TCP
    connections at `mcp_url`. A plain socket connect, deliberately: an HTTP
    probe against `/mcp` without a session would get a protocol-level error
    that says nothing about whether the process is up.
    """
    parsed = urlparse(mcp_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as exc:
        pytest.skip(
            f"No MCP server reachable at {mcp_url} ({exc}). Start one with "
            "`python scripts/run_mcp_server.py`, or point EKIP_MCP_URL at a running instance."
        )
    return mcp_url


@pytest.fixture(scope="session")
def identity(cfg: harness_config.Config) -> dict:
    """One real, bootstrapped organization + admin, and a freshly minted
    access token, shared across the suite.

    The token is minted through EKIP's own real auth service (no REST
    self-signup endpoint exists) -- see
    `tests/ingestion_retrieval/utils.py`'s module docstring. Minted per run
    rather than read from configuration because these tokens are
    short-lived; a pasted one would expire between runs.
    """
    return harness_utils.bootstrap_admin_sync(
        org_name=cfg.org_name,
        org_slug=cfg.org_slug,
        email=cfg.admin_email,
        display_name=cfg.admin_display_name,
    )


@pytest.fixture(scope="session")
def access_token(identity: dict) -> str:
    return identity["access_token"]


@pytest.fixture(scope="session")
def organization_id(identity: dict) -> uuid.UUID:
    return uuid.UUID(identity["organization_id"])
