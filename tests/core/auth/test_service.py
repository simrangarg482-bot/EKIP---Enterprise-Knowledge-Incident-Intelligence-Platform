"""Tests for `app.core.auth.service.refresh`/`logout`'s Milestone 10 RLS
wiring -- not a full test suite for `core.auth.service` (no test
infrastructure for that module existed before this addition). Each starts
from a bare, client-presented refresh-token hash with no `Identity`/org
context yet, so both must resolve the owning organization via the
RLS-bypassing `resolve_refresh_token_organization_id` lookup and call
`set_tenant_context` before the real, RLS-scoped `get_refresh_token_by_hash`
query runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth import service as auth_service
from app.core.auth.schemas import RefreshRequest, SessionTokens
from app.core.exceptions import PermissionDeniedError
from app.shared.security import encrypt_secret, get_kms


class _FakeRefreshTokenRow:
    def __init__(self, *, organization_id: uuid.UUID, revoked_at=None) -> None:
        now = datetime.now(timezone.utc)
        self.id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.organization_id = organization_id
        self.family_id = uuid.uuid4()
        self.revoked_at = revoked_at
        self.expires_at = now + timedelta(days=30)


@pytest.mark.asyncio
async def test_refresh_raises_invalid_token_when_hash_unresolvable(monkeypatch) -> None:
    """No row matches this token hash at all -- the RLS-bypassing resolver
    itself returns None, so `refresh` must fail the same way it always has
    (invalid token), never reach `set_tenant_context` or the real query.
    """
    tenant_context_calls: list[uuid.UUID] = []

    async def fake_resolve(session, token_hash):
        return None

    async def fake_set_tenant_context(session, org_id) -> None:
        tenant_context_calls.append(org_id)

    monkeypatch.setattr(auth_service.repository, "resolve_refresh_token_organization_id", fake_resolve)
    monkeypatch.setattr(auth_service, "set_tenant_context", fake_set_tenant_context)

    with pytest.raises(PermissionDeniedError):
        await auth_service.refresh(None, RefreshRequest(refresh_token="bogus-token"))

    assert tenant_context_calls == []


@pytest.mark.asyncio
async def test_refresh_sets_tenant_context_before_reading_full_token_row(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    token_row = _FakeRefreshTokenRow(organization_id=organization_id)
    call_order: list[str] = []

    async def fake_resolve(session, token_hash):
        call_order.append("resolve_org_id")
        return organization_id

    async def fake_set_tenant_context(session, org_id) -> None:
        call_order.append("set_tenant_context")
        assert org_id == organization_id

    async def fake_get_refresh_token_by_hash(session, token_hash):
        call_order.append("get_refresh_token_by_hash")
        return token_row

    async def fake_revoke_refresh_token(session, refresh_token_id, *, revoked_at) -> None:
        return None

    async def fake_issue_session(session, *, user_id, organization_id, family_id):
        call_order.append("issue_session")
        return SessionTokens(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            token_type="bearer",
            expires_in=900,
        )

    monkeypatch.setattr(auth_service.repository, "resolve_refresh_token_organization_id", fake_resolve)
    monkeypatch.setattr(auth_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(
        auth_service.repository, "get_refresh_token_by_hash", fake_get_refresh_token_by_hash
    )
    monkeypatch.setattr(auth_service.repository, "revoke_refresh_token", fake_revoke_refresh_token)
    monkeypatch.setattr(auth_service, "_issue_session", fake_issue_session)

    result = await auth_service.refresh(None, RefreshRequest(refresh_token="a-valid-token"))

    assert result.access_token == "new-access-token"
    assert call_order == [
        "resolve_org_id",
        "set_tenant_context",
        "get_refresh_token_by_hash",
        "issue_session",
    ]


@pytest.mark.asyncio
async def test_logout_is_a_no_op_when_hash_unresolvable(monkeypatch) -> None:
    async def fake_resolve(session, token_hash):
        return None

    monkeypatch.setattr(auth_service.repository, "resolve_refresh_token_organization_id", fake_resolve)

    # Should not raise -- logout is documented as idempotent even for a
    # token that resolves to nothing.
    await auth_service.logout(None, RefreshRequest(refresh_token="bogus-token"))


@pytest.mark.asyncio
async def test_logout_sets_tenant_context_before_revoking(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    token_row = _FakeRefreshTokenRow(organization_id=organization_id)
    call_order: list[str] = []

    async def fake_resolve(session, token_hash):
        call_order.append("resolve_org_id")
        return organization_id

    async def fake_set_tenant_context(session, org_id) -> None:
        call_order.append("set_tenant_context")
        assert org_id == organization_id

    async def fake_get_refresh_token_by_hash(session, token_hash):
        call_order.append("get_refresh_token_by_hash")
        return token_row

    async def fake_revoke_refresh_token(session, refresh_token_id, *, revoked_at) -> None:
        call_order.append("revoke_refresh_token")
        assert refresh_token_id == token_row.id

    monkeypatch.setattr(auth_service.repository, "resolve_refresh_token_organization_id", fake_resolve)
    monkeypatch.setattr(auth_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(
        auth_service.repository, "get_refresh_token_by_hash", fake_get_refresh_token_by_hash
    )
    monkeypatch.setattr(auth_service.repository, "revoke_refresh_token", fake_revoke_refresh_token)

    await auth_service.logout(None, RefreshRequest(refresh_token="a-valid-token"))

    assert call_order == [
        "resolve_org_id",
        "set_tenant_context",
        "get_refresh_token_by_hash",
        "revoke_refresh_token",
    ]


@pytest.mark.asyncio
async def test_revoke_all_sessions_scopes_by_user_and_organization(monkeypatch) -> None:
    """`revoke_all_sessions` ("logout everywhere") must delegate to the
    org-scoped repository call with the exact `user_id`/`organization_id`
    it was given, and return the revoked-session count as-is -- this is
    what both `POST /auth/logout-all` (self) and
    `POST /users/{user_id}/logout-all` (admin, on someone else's behalf)
    rely on.
    """
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def fake_revoke_all_for_user(session, passed_user_id, passed_organization_id, *, revoked_at):
        captured["user_id"] = passed_user_id
        captured["organization_id"] = passed_organization_id
        captured["revoked_at"] = revoked_at
        return 4

    monkeypatch.setattr(auth_service.repository, "revoke_all_for_user", fake_revoke_all_for_user)

    result = await auth_service.revoke_all_sessions(None, user_id, organization_id)

    assert result == 4
    assert captured["user_id"] == user_id
    assert captured["organization_id"] == organization_id
    assert captured["revoked_at"] is not None


# --- _resolve_client_secret (2026-08 audit "C3") -----------------------------


def test_resolve_client_secret_decrypts_a_real_envelope() -> None:
    """Confirmed bug fix: `_resolve_client_secret` used to return
    `client_secret_ref` unchanged, treating it as if it were already the
    plaintext secret. `core.tenancy.service.configure_sso` now always
    stores an envelope-encrypted blob (`app.shared.security.encrypt_secret`)
    there -- this proves the round trip actually works, using the real KMS
    envelope helpers (the same ones `configure_sso` itself calls), not a
    mock standing in for them.
    """
    plaintext_secret = "super-secret-oidc-client-secret"
    encrypted = encrypt_secret(get_kms(), plaintext_secret)

    # The stored value must not itself be (or contain) the plaintext --
    # otherwise this "encryption" would be decorative.
    assert encrypted != plaintext_secret
    assert plaintext_secret not in encrypted

    resolved = auth_service._resolve_client_secret(encrypted)

    assert resolved == plaintext_secret


def test_resolve_client_secret_rejects_a_plaintext_value_that_is_not_an_envelope() -> None:
    """Guards against silently regressing back to "treat client_secret_ref
    as already-plaintext": a raw, non-JSON string (what every
    `SSOConfiguration.client_secret_ref` looked like before this fix) must
    fail loudly (the same discipline `decrypt_secret`'s own docstring
    describes), not be quietly accepted as a usable secret.
    """
    with pytest.raises(Exception):
        auth_service._resolve_client_secret("this-is-not-an-encrypted-envelope")
