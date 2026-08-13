"""Auth router -- SSO/PKCE login, refresh, logout, and identity lookup.

Owned by: app/api. Wraps core/auth/service.py's real OIDC Authorization
Code + PKCE flow. API_DESIGN.md section 1's `/auth/login`
(username+password exchange) / `/auth/refresh` / `/auth/me` table predates
that flow's implementation; `/auth/refresh` and `/auth/me` are preserved
as-is (they still match what core/auth exposes), but `/auth/login` becomes
a two-step redirect flow (`/auth/{org_slug}/login` then `/auth/callback`),
matching what core/auth actually implements rather than the older sketch.

PKCE note: `code_verifier` is generated server-side by `begin_sso_login` and
returned directly in `SSOAuthorizationRedirect` to the caller (a public
client, e.g. a browser SPA) -- per the OAuth2 PKCE spec for public clients,
it is the *caller's* job to stash it (e.g. sessionStorage keyed by `state`)
across the redirect round-trip and resupply it verbatim in
`SSOCallbackRequest`. This router does no server-side state->code_verifier
storage of its own; there is nothing to store, since the schema already
requires the caller to send it back.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentIdentity, DbSession
from app.core.audit.service import record_audit_event
from app.core.auth import service as auth_service
from app.core.auth.schemas import (
    LoginRequest,
    LogoutAllResponse,
    RefreshRequest,
    SessionTokens,
    SignupRequest,
    SSOAuthorizationRedirect,
    SSOCallbackRequest,
)
from app.core.exceptions import ValidationError
from app.core.users import service as users_service
from app.core.users.schemas import UserProfile

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/{org_slug}/login", response_model=SSOAuthorizationRedirect)
async def begin_login(
    org_slug: str, redirect_uri: str, session: DbSession
) -> SSOAuthorizationRedirect:
    """Start an SSO login. `redirect_uri` is supplied by the caller (its own
    callback URL) -- core/auth does not hardcode or guess it (see
    `begin_sso_login`'s docstring).
    """
    return await auth_service.begin_sso_login(session, org_slug, redirect_uri=redirect_uri)


@router.post("/callback", response_model=SessionTokens)
async def complete_login(
    data: SSOCallbackRequest, redirect_uri: str, session: DbSession
) -> SessionTokens:
    """Complete an SSO login. `redirect_uri` must be identical to the one
    used in `begin_login`, per the OAuth2 spec.
    """
    return await auth_service.complete_sso_login(session, data, redirect_uri=redirect_uri)


@router.post("/signup", response_model=SessionTokens, status_code=status.HTTP_201_CREATED)
async def signup(data: SignupRequest, session: DbSession) -> SessionTokens:
    """Self-service email/password account creation -- a parallel path
    alongside the SSO flow above, not a replacement for it. See
    `auth_service.signup`'s docstring for exactly what it does and does not
    support (always a brand-new organization; no join-existing-org flow).
    """
    return await auth_service.signup(session, data)


@router.post("/login", response_model=SessionTokens)
async def login(data: LoginRequest, session: DbSession) -> SessionTokens:
    """Email/password login, counterpart to `signup`."""
    return await auth_service.login_with_password(session, data)


@router.post("/refresh", response_model=SessionTokens)
async def refresh_session(data: RefreshRequest, session: DbSession) -> SessionTokens:
    return await auth_service.refresh(session, data)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_session(data: RefreshRequest, session: DbSession) -> Response:
    await auth_service.logout(session, data)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all_sessions(actor: CurrentIdentity, session: DbSession) -> LogoutAllResponse:
    """"Logout everywhere" -- revoke every one of the caller's own sessions
    (`core.auth.service.revoke_all_sessions`) within their own organization.

    Requires `actor.user_id` -- like `GET /auth/me`, a service/agent identity
    has no sessions of its own to revoke, so it gets a clean `ValidationError`
    rather than calling `revoke_all_sessions` with a nonsensical id. See
    `LogoutAllResponse`'s own docstring for exactly what "logged out
    everywhere" does and does not guarantee about a still-live access token.
    """
    if actor.user_id is None:
        raise ValidationError(
            "Only a user identity has sessions to revoke.", error_code="user.no_profile"
        )
    revoked_count = await auth_service.revoke_all_sessions(
        session, actor.user_id, actor.organization_id
    )
    await record_audit_event(
        session,
        actor,
        action="user.logout_all_sessions",
        resource_type="user",
        resource_id=actor.user_id,
        metadata={"revoked_session_count": revoked_count},
    )
    return LogoutAllResponse(
        message="Successfully logged out from all sessions",
        revoked_session_count=revoked_count,
    )


@router.get("/me", response_model=UserProfile)
async def get_me(actor: CurrentIdentity, session: DbSession) -> UserProfile:
    """Resolve the current identity's full profile (API_DESIGN.md:
    `GET /auth/me`). Requires `actor.user_id` -- a service/agent identity
    calling this (there is no legitimate reason one would, since only a
    human logs in via this router) gets a clean `ValidationError` rather
    than an obscure attribute failure inside `get_user_profile`.
    """
    if actor.user_id is None:
        raise ValidationError(
            "Only a user identity has a profile.", error_code="user.no_profile"
        )
    return await users_service.get_user_profile(session, actor.user_id, actor.organization_id)
