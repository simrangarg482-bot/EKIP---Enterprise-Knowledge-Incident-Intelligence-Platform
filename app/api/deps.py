"""FastAPI dependencies for app/api -- database sessions and identity
resolution.

Owned by: app/api. Mirrors app/mcp/auth.py's exact two-call identity
resolution pattern (`verify_access_token` then `resolve_identity`); the only
difference is where the `AsyncSession` comes from. MCP obtains one via an
injected `session_factory` because app.mcp may not import app.database at
all (pyproject.toml's "mcp never touches database or retrieval directly"
contract). app/api has no such restriction and uses
`app.database.session.get_db_session` directly, exactly as that function's
own docstring anticipates ("the future REST layer's session dependency needs
zero new code").

`_bearer_scheme` (added after Milestone 10): a plain `Header()` parameter
(what `get_current_identity` actually reads the token from, via
`_extract_bearer_token`) is invisible to FastAPI's OpenAPI generation as a
"security scheme" -- Swagger UI renders it as an ordinary per-operation text
field rather than the standard global "Authorize" padlock, and at least one
Swagger UI version has a real bug where a value typed into that kind of
field (an `Optional[str]` header parameter) never actually gets attached to
the outgoing request, despite showing as filled in the form. Declaring
`HTTPBearer` as an additional, otherwise-unused dependency fixes both
problems: FastAPI detects any `SecurityBase` dependency in the graph (nested
or not) and registers it in the OpenAPI `components.securitySchemes`, which
gives Swagger UI a real "Authorize" button that reliably attaches
`Authorization: Bearer <token>` to every subsequent request for every
operation depending on `CurrentIdentity`. It does not change the actual
authentication logic at all: there is still only one `Authorization` header
on the wire, and `_extract_bearer_token`/`authorization: Header()` below are
completely unchanged -- this is additive, not a replacement, specifically so
no existing behavior or test needs to change.
"""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import service as auth_service
from app.core.exceptions import PermissionDeniedError
from app.core.users import service as users_service
from app.database.session import get_db_session, set_tenant_context
from app.shared.schemas import Identity

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_arq_pool(request: Request) -> ArqRedis:
    """The one `arq` Redis pool `app.api.main`'s lifespan opened at startup
    -- for enqueueing jobs onto the same queue `app.ingestion.workers.main`'s
    worker process consumes (`POST /tenancy/connectors/{id}/sync` is the
    first, and so far only, caller).
    """
    return request.app.state.arq_pool


ArqPool = Annotated[ArqRedis, Depends(get_arq_pool)]

# `auto_error=False`: a missing/malformed header must still surface as our
# own `PermissionDeniedError` (via `_extract_bearer_token`), not FastAPI's
# default bare 403 -- see this module's docstring for why this dependency
# exists at all despite not being read anywhere below.
_bearer_scheme = HTTPBearer(auto_error=False)


def _extract_bearer_token(authorization: str | None) -> str:
    """Pull the raw token out of an `Authorization: Bearer <token>` header.

    Raises `PermissionDeniedError` (not a raw, unmapped 401) for a
    missing/malformed header, so the single `EKIPError` -> HTTP handler
    registered in app/api/errors.py handles this the same way as every other
    authentication/authorization failure, rather than this dependency having
    to construct its own HTTPException.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionDeniedError(
            "Missing or malformed Authorization header.",
            error_code="auth.missing_bearer_token",
        )
    return authorization.split(" ", 1)[1].strip()


async def get_current_identity(
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    _credentials: Annotated[object | None, Security(_bearer_scheme)] = None,
) -> Identity:
    """Resolve the caller's `Identity` from a bearer access token.

    Deliberately the same two-step composition as
    `app.mcp.auth.resolve_mcp_identity` -- REST and MCP authenticate
    identically, per ARCHITECTURE.md section 6 / API_DESIGN.md's "Identity is
    threaded through every call" convention, so access control never differs
    by entry point.

    `_credentials` is intentionally never read: it exists purely so this
    dependency's `HTTPBearer` sub-dependency is visible to FastAPI's OpenAPI
    generation (giving Swagger UI a working "Authorize" button) -- see this
    module's docstring. The actual token still comes from `authorization`
    below, unchanged; both parameters read the same underlying HTTP header,
    since a client (Swagger UI included) only ever sends one `Authorization`
    header regardless of how many ways this function declares interest in it.

    Also sets this request's Postgres session-local tenant GUC (Milestone
    10's RLS backstop, `app.database.session.set_tenant_context`) once
    `organization_id` is known, so every downstream query issued on this
    same `session` for the rest of the request is covered by the RLS
    policies without any further code change needed at each call site.
    `users_service.resolve_identity` itself already sets this GUC internally
    before this line runs (it has to -- its own `user_roles` lookup is
    RLS-protected too, and needs the GUC set before *it* can query anything;
    see that function's own docstring), so this call is intentionally
    redundant/idempotent -- a second, cheap `set_config` call -- rather than
    the only place this happens: this line's role is to guarantee the GUC is
    set for this identity even for a hypothetical future `Identity` built
    some other way (e.g. a service-account/agent identity that never goes
    through `resolve_identity` at all).
    """
    token = _extract_bearer_token(authorization)
    claims = auth_service.verify_access_token(token)
    identity = await users_service.resolve_identity(session, claims.user_id, claims.organization_id)
    await set_tenant_context(session, identity.organization_id)
    return identity


CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]
