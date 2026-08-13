"""Pydantic contracts for core/auth.

Owned by: core/auth. Local to this submodule (PROJECT_STRUCTURE.md), same
pattern as core/tenancy/schemas.py and core/users/schemas.py.

Supersedes API_DESIGN.md's original `POST /auth/login` / `AskRequest`-style
single-tenant shapes: per PROJECT_PLAN.md's opening note, anything touching
authentication is governed by this document's section 3.3-3.4, not the older
one. The flow modeled here is OIDC Authorization Code + PKCE, provider-
agnostic across Entra ID / Okta / Auth0 / Google Workspace (section 3.3) --
one shape handles all four, since all four speak OIDC.

Two round trips, two schema pairs:
  1. Begin login (`SSOAuthorizationRedirect`) -> employee's browser goes to
     the IdP -> IdP redirects back with a code (`SSOCallbackRequest`).
  2. `complete_sso_login` returns `SessionTokens`; `refresh` takes a
     `RefreshRequest` and also returns `SessionTokens`; `verify_access_token`
     returns `TokenClaims`.

PKCE mechanics: `code_verifier` is generated when login begins and must be
supplied again at the callback step to complete the token exchange with the
IdP. This module treats it as an opaque string the caller (the future api/
layer) is responsible for stashing server-side keyed by `state` across the
redirect round trip -- core/auth does not persist it itself, since it is only
meaningful for the duration of one in-flight login attempt, not something
that needs a database row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SSOAuthorizationRedirect(BaseModel):
    """Result of beginning an SSO login for one organization.

    `authorization_url` is where the employee's browser should be redirected
    (PROJECT_PLAN.md section 3.3, step 3) -- the IdP's OIDC authorize
    endpoint, pre-filled with the organization's `client_id` (from its
    `sso_configurations` row, resolved via core/tenancy) and the PKCE
    challenge derived from `code_verifier`. `state` is an opaque anti-CSRF
    token the callback must echo back unchanged.
    """

    model_config = ConfigDict(frozen=True)

    authorization_url: str
    state: str
    code_verifier: str


class SSOCallbackRequest(BaseModel):
    """Input to `complete_sso_login`, once the IdP has redirected back.

    `org_slug` re-identifies which organization's SSO configuration to
    exchange the code against (the callback URL is
    `/o/{org-slug}/callback`, PROJECT_PLAN.md section 11.1); `state` and
    `code_verifier` are the values the caller stashed from the matching
    `SSOAuthorizationRedirect` and must supply unchanged to complete PKCE.
    """

    org_slug: str
    code: str
    state: str
    code_verifier: str


class SessionTokens(BaseModel):
    """EKIP's own signed session, issued after a successful login or refresh
    (PROJECT_PLAN.md section 3.4).

    `access_token` is the short-lived JWT verified on every request (by
    `verify_access_token`, and used identically by the REST API and MCP per
    section 7.4); `refresh_token` is the longer-lived credential used to
    obtain a new `access_token` without repeating the full SSO round trip.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until access_token expires, from issuance


class RefreshRequest(BaseModel):
    """Input to `refresh` -- exchange a still-valid refresh token for a new
    `SessionTokens` pair.
    """

    refresh_token: str


class SignupRequest(BaseModel):
    """Input to `signup` -- self-service email/password account creation.

    Always creates a brand-new organization alongside the user (there is no
    "join an existing organization via signup" flow yet -- see `signup`'s
    own docstring); `organization_slug` follows `OrganizationCreate.slug`'s
    exact URL-safe pattern, since it becomes that organization's real slug.
    """

    email: str
    password: str = Field(min_length=8)
    display_name: str
    organization_name: str
    organization_slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=1, max_length=63)


class LoginRequest(BaseModel):
    """Input to `login_with_password`."""

    email: str
    password: str


class LogoutAllResponse(BaseModel):
    """Response for `POST /auth/logout-all` and `POST /users/{user_id}/
    logout-all` -- "logout everywhere" (`revoke_all_sessions`).

    `revoked_session_count` is `revoke_all_sessions`'s own return value (the
    number of `refresh_tokens` rows it revoked), included alongside the
    human-readable `message` so a caller can tell "nothing to revoke" (0)
    apart from "some sessions really were revoked" without parsing prose.

    Important, and stated here rather than only in the endpoint docstring:
    this revokes refresh tokens, not already-issued access tokens.
    `core.auth.service.verify_access_token` is stateless (pure JWT signature/
    expiry check, no database lookup) -- an access token issued before this
    call remains valid until its own `exp` (bounded by `settings.
    jwt_expiry_minutes`), even after every refresh token is revoked. "Logged
    out everywhere" therefore means "no session can be *refreshed* past this
    point," not "every existing access token stops working immediately."
    """

    model_config = ConfigDict(frozen=True)

    message: str
    revoked_session_count: int


class TokenClaims(BaseModel):
    """The verified, decoded claims of an access token -- the output of
    `verify_access_token`.

    This is intentionally smaller than `shared.schemas.Identity`: it is the
    raw, transport-level claim set (who, which organization, when it expires)
    with no roles/permissions resolved yet. Turning this into a full
    `Identity` is `core.users.service.resolve_identity`'s job -- core/auth
    answers "whose token is this and is it valid," not "what can they do"
    (mirrors the existing division of labor already documented on
    core/users/service.resolve_identity).
    """

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    organization_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime


class VerifiedIdPClaims(BaseModel):
    """The verified claims extracted from an IdP's ID token, once
    `_exchange_code_for_claims` has actually completed signature
    verification (see that function's NOT YET IMPLEMENTED docstring).

    Distinct from `TokenClaims` (EKIP's own access token claims): this is
    what the *IdP* asserts about the user -- subject, email, display name,
    and group memberships if the provider sends them -- before EKIP has
    decided anything about provisioning. This is the boundary of core/auth's
    responsibility in the SSO-provisioning-policy design
    (ENGINEERING_DECISIONS.md): "verify OIDC authentication, validate token
    claims, extract identity information." Deciding whether these claims are
    *allowed* to provision an account in a given organization is
    core/tenancy's job (`evaluate_provisioning`), not this schema's or
    core/auth's.

    `groups` defaults to empty, not `None`: an IdP that doesn't send a groups
    claim at all is indistinguishable, from core/auth's point of view, from
    one that sent an empty group list -- both simply mean "no group-based
    provisioning signal available for this login."
    """

    model_config = ConfigDict(frozen=True)

    sub: str
    email: str
    name: str | None = None
    groups: tuple[str, ...] = ()
