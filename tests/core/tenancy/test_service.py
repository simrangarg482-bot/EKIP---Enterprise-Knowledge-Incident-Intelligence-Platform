"""Tests for `app.core.tenancy.service` -- Milestone 10's envelope-encryption
addition to `register_connector`, plus the integration-gaps pass's
`create_organization` optional-actor/audit addition, `accept_invitation`
hardening, and `register_connector`'s new project-scoped permission check.
Not a full test suite for `core.tenancy.service` (no test infrastructure for
this module existed before the first of these additions). Monkeypatches
`repository.*` functions (capturing what they were actually called with),
the same "monkeypatch the module-level dependency" style used throughout
this test suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.tenancy import service as tenancy_service
from app.core.tenancy.schemas import ConnectorConfigCreate, OrganizationCreate, SSOConfigurationCreate
from app.shared.schemas import ActorKind, Identity
from app.shared.security import decrypt_secret, get_kms


def _admin(organization_id: uuid.UUID) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=frozenset({"tenancy:manage"}),
    )


class _FakeConnectorConfigRow:
    def __init__(self, **kwargs: object) -> None:
        now = datetime.now(timezone.utc)
        self.id = uuid.uuid4()
        self.organization_id = kwargs["organization_id"]
        self.source = kwargs["source"]
        self.credential_ref = kwargs["credential_ref"]
        self.project_id = kwargs.get("project_id")
        self.config = kwargs.get("config") or {}
        self.status = "connecting"
        self.last_synced_at = None
        self.created_at = now
        self.updated_at = now


@pytest.mark.asyncio
async def test_register_connector_encrypts_credential_before_storing(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _admin(organization_id)
    plaintext_credential = "xoxb-11725744885042-fake-slack-bot-token"
    captured: dict[str, object] = {}

    async def fake_insert_connector_config(session, **kwargs):
        captured.update(kwargs)
        return _FakeConnectorConfigRow(**kwargs)

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(
        tenancy_service.repository, "insert_connector_config", fake_insert_connector_config
    )
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    result = await tenancy_service.register_connector(
        None,
        actor,
        organization_id,
        ConnectorConfigCreate(source="slack", credential_ref=plaintext_credential),
    )

    stored_credential_ref = captured["credential_ref"]
    assert stored_credential_ref != plaintext_credential
    assert plaintext_credential not in stored_credential_ref

    # The stored value is a real, working envelope -- round-trips back to
    # the original plaintext via the same KMS `ingestion.service` uses.
    assert decrypt_secret(get_kms(), stored_credential_ref) == plaintext_credential

    # The returned read-model reflects whatever was actually persisted
    # (the encrypted value), not the plaintext the caller submitted --
    # matches `ConnectorConfig.credential_ref`'s own documented contract.
    assert result.credential_ref == stored_credential_ref


@pytest.mark.asyncio
async def test_update_connector_sync_status_threads_config_patch_through(monkeypatch) -> None:
    """`config_patch` (ingestion's persisted cross-sync resume token, see
    `app.ingestion.service._execute_ingestion_job`) must reach `repository.
    update_connector_config_sync_status` unchanged -- the actual JSONB
    shallow-merge is a one-line operation inside that repository function
    itself (no test infra for direct repository calls exists in this test
    file; every test here monkeypatches at the `repository.*` boundary).
    """
    organization_id = uuid.uuid4()
    actor = _admin(organization_id)
    connector_config_id = uuid.uuid4()
    existing_row = _FakeConnectorConfigRow(
        organization_id=organization_id, source="sharepoint", credential_ref="encrypted-ref"
    )
    existing_row.id = connector_config_id
    captured: dict[str, object] = {}

    async def fake_get_connector_config_by_id(session, config_id):
        assert config_id == connector_config_id
        return existing_row

    async def fake_update_connector_config_sync_status(session, config_id, **kwargs):
        captured.update(kwargs)
        return existing_row

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(
        tenancy_service.repository,
        "get_connector_config_by_id",
        fake_get_connector_config_by_id,
    )
    monkeypatch.setattr(
        tenancy_service.repository,
        "update_connector_config_sync_status",
        fake_update_connector_config_sync_status,
    )
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    await tenancy_service.update_connector_sync_status(
        None,
        actor,
        organization_id,
        connector_config_id,
        status="active",
        config_patch={"_resume_token": '{"site-1": "https://example.com/delta"}'},
    )

    assert captured["config_patch"] == {"_resume_token": '{"site-1": "https://example.com/delta"}'}


class _FakeOrganizationRow:
    def __init__(self, organization_id: uuid.UUID) -> None:
        self.id = organization_id
        self.slug = "acme"


class _FakeSSOConfigurationRow:
    def __init__(self, organization_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        self.id = uuid.uuid4()
        self.organization_id = organization_id
        self.provider = "okta"
        self.protocol = "oidc"
        self.issuer_url = "https://acme.okta.com"
        self.client_id = "client-123"
        self.client_secret_ref = "encrypted-ref"
        self.created_at = now
        self.updated_at = now


@pytest.mark.asyncio
async def test_configure_sso_encrypts_client_secret_before_storing(monkeypatch) -> None:
    """Confirmed bug fix (2026-08 audit "C3"): `configure_sso` used to
    persist `data.client_secret_ref` unchanged -- every organization's real
    OIDC client secret sat in the database as plaintext. This mirrors
    `test_register_connector_encrypts_credential_before_storing` exactly:
    the same encrypt-at-write pattern, applied here for the first time to
    SSO secrets.
    """
    organization_id = uuid.uuid4()
    actor = _admin(organization_id)
    plaintext_secret = "real-entra-id-client-secret-value"
    captured: dict[str, object] = {}

    async def fake_get_sso_configuration_by_organization_id(session, org_id):
        return None  # not already configured

    async def fake_insert_sso_configuration(session, **kwargs):
        captured.update(kwargs)
        return _FakeSSOConfigurationRow(organization_id)

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(
        tenancy_service.repository,
        "get_sso_configuration_by_organization_id",
        fake_get_sso_configuration_by_organization_id,
    )
    monkeypatch.setattr(
        tenancy_service.repository, "insert_sso_configuration", fake_insert_sso_configuration
    )
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    await tenancy_service.configure_sso(
        None,
        actor,
        organization_id,
        SSOConfigurationCreate(
            provider="okta",
            issuer_url="https://acme.okta.com",
            client_id="client-123",
            client_secret_ref=plaintext_secret,
        ),
    )

    stored_client_secret_ref = captured["client_secret_ref"]
    assert stored_client_secret_ref != plaintext_secret
    assert plaintext_secret not in stored_client_secret_ref

    # A real, working envelope -- round-trips back to the original
    # plaintext via the same KMS `core.auth.service._resolve_client_secret`
    # uses.
    assert decrypt_secret(get_kms(), stored_client_secret_ref) == plaintext_secret


@pytest.mark.asyncio
async def test_get_organization_sso_config_sets_tenant_context_before_reading_sso_row(
    monkeypatch,
) -> None:
    """Milestone 10 RLS note: `organizations` isn't RLS-protected (no bypass
    needed for the slug lookup), but `sso_configurations` is -- this test
    asserts `set_tenant_context` runs after the org is resolved by slug and
    before the `sso_configurations` read.
    """
    organization_id = uuid.uuid4()
    org_row = _FakeOrganizationRow(organization_id)
    sso_row = _FakeSSOConfigurationRow(organization_id)
    call_order: list[str] = []

    async def fake_get_organization_by_slug(session, slug):
        call_order.append("get_organization_by_slug")
        assert slug == "acme"
        return org_row

    async def fake_set_tenant_context(session, org_id) -> None:
        call_order.append("set_tenant_context")
        assert org_id == organization_id

    async def fake_get_sso_configuration_by_organization_id(session, org_id):
        call_order.append("get_sso_configuration_by_organization_id")
        assert org_id == organization_id
        return sso_row

    monkeypatch.setattr(
        tenancy_service.repository, "get_organization_by_slug", fake_get_organization_by_slug
    )
    monkeypatch.setattr(tenancy_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(
        tenancy_service.repository,
        "get_sso_configuration_by_organization_id",
        fake_get_sso_configuration_by_organization_id,
    )

    result = await tenancy_service.get_organization_sso_config(None, "acme")

    assert result.organization_id == organization_id
    assert call_order == [
        "get_organization_by_slug",
        "set_tenant_context",
        "get_sso_configuration_by_organization_id",
    ]


@pytest.mark.asyncio
async def test_evaluate_provisioning_sets_tenant_context_before_any_query(monkeypatch) -> None:
    """`evaluate_provisioning` already receives `organization_id` as a
    parameter (unlike `get_organization_sso_config`, which has to discover
    it) -- Milestone 10 note: `set_tenant_context` must run before its first
    RLS-protected query (`invitations`), not after.
    """
    organization_id = uuid.uuid4()
    call_order: list[str] = []

    async def fake_set_tenant_context(session, org_id) -> None:
        call_order.append("set_tenant_context")
        assert org_id == organization_id

    async def fake_get_pending_invitation(session, org_id, email):
        call_order.append("get_pending_invitation")
        return None

    async def fake_get_active_rules_by_type(session, org_id, rule_type):
        call_order.append(f"get_active_rules_by_type:{rule_type}")
        return []

    monkeypatch.setattr(tenancy_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(
        tenancy_service.repository, "get_pending_invitation", fake_get_pending_invitation
    )
    monkeypatch.setattr(
        tenancy_service.repository, "get_active_rules_by_type", fake_get_active_rules_by_type
    )

    decision = await tenancy_service.evaluate_provisioning(
        None, organization_id=organization_id, email="new.hire@acme.com"
    )

    assert decision.allowed is False
    assert call_order[0] == "set_tenant_context"
    assert "get_pending_invitation" in call_order


# --- create_organization (optional actor + audit) -----------------------------


class _FakeOrgRow:
    def __init__(self, *, name: str, slug: str) -> None:
        now = datetime.now(timezone.utc)
        self.id = uuid.uuid4()
        self.name = name
        self.slug = slug
        self.status = "onboarding"
        self.created_at = now
        self.updated_at = now


@pytest.mark.asyncio
async def test_create_organization_without_actor_records_no_audit_event(monkeypatch) -> None:
    """Existing callers with no `Identity` available at all
    (`scripts/seed_test_organization.py`, `scripts/test_milestone6.py`) must
    keep working unchanged: omitting `actor` must not call
    `record_audit_event` at all (there is nothing to attribute it to).
    """
    audit_calls: list[dict[str, object]] = []

    async def fake_get_organization_by_slug(session, slug):
        return None

    async def fake_insert_organization(session, *, name, slug):
        return _FakeOrgRow(name=name, slug=slug)

    async def fake_set_tenant_context(session, org_id) -> None:
        return None

    async def fake_insert_project(session, **kwargs):
        return None

    async def fake_record_audit_event(session, actor, **kwargs):
        audit_calls.append(kwargs)

    monkeypatch.setattr(
        tenancy_service.repository, "get_organization_by_slug", fake_get_organization_by_slug
    )
    monkeypatch.setattr(tenancy_service.repository, "insert_organization", fake_insert_organization)
    monkeypatch.setattr(tenancy_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(tenancy_service.repository, "insert_project", fake_insert_project)
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    result = await tenancy_service.create_organization(
        None, OrganizationCreate(name="Acme", slug="acme")
    )

    assert result.slug == "acme"
    assert audit_calls == []


@pytest.mark.asyncio
async def test_create_organization_with_actor_records_audit_event(monkeypatch) -> None:
    """A caller that already has an `Identity` (the REST `POST /organizations`
    endpoint) gets a real audit event, attributed to that actor.
    """
    organization_id = uuid.uuid4()
    actor = _admin(organization_id)
    audit_calls: list[dict[str, object]] = []

    async def fake_get_organization_by_slug(session, slug):
        return None

    async def fake_insert_organization(session, *, name, slug):
        return _FakeOrgRow(name=name, slug=slug)

    async def fake_set_tenant_context(session, org_id) -> None:
        return None

    async def fake_insert_project(session, **kwargs):
        return None

    async def fake_record_audit_event(session, event_actor, **kwargs):
        assert event_actor is actor
        audit_calls.append(kwargs)

    monkeypatch.setattr(
        tenancy_service.repository, "get_organization_by_slug", fake_get_organization_by_slug
    )
    monkeypatch.setattr(tenancy_service.repository, "insert_organization", fake_insert_organization)
    monkeypatch.setattr(tenancy_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(tenancy_service.repository, "insert_project", fake_insert_project)
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    result = await tenancy_service.create_organization(
        None, OrganizationCreate(name="Acme", slug="acme"), actor=actor
    )

    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "organization.create"
    assert audit_calls[0]["resource_id"] == result.id


@pytest.mark.asyncio
async def test_create_organization_sets_tenant_context_before_inserting_project(
    monkeypatch,
) -> None:
    """Confirmed bug (2026-08 audit "C2"): `projects` is RLS-protected
    (`FORCE ROW LEVEL SECURITY`, keyed on `app.current_organization_id`), but
    `create_organization` used to insert the default "General" project
    without ever calling `set_tenant_context` first. Under real RLS
    enforcement that insert should be rejected outright (the GUC would still
    be unset/stale, never equal to the brand-new organization's id). This
    asserts the ordering: `insert_organization` -> `set_tenant_context` ->
    `insert_project` -- and that the audit write (if `actor` is given) also
    lands after the GUC is set, since it happens later in the same
    transaction.
    """
    organization_id = uuid.uuid4()
    actor = _admin(organization_id)
    call_order: list[str] = []

    async def fake_get_organization_by_slug(session, slug):
        return None

    async def fake_insert_organization(session, *, name, slug):
        call_order.append("insert_organization")
        return _FakeOrgRow(name=name, slug=slug)

    async def fake_set_tenant_context(session, org_id) -> None:
        call_order.append("set_tenant_context")

    async def fake_insert_project(session, **kwargs):
        call_order.append("insert_project")
        assert "set_tenant_context" in call_order, (
            "insert_project (RLS-protected) ran before set_tenant_context"
        )
        return None

    async def fake_record_audit_event(session, event_actor, **kwargs):
        call_order.append("record_audit_event")

    monkeypatch.setattr(
        tenancy_service.repository, "get_organization_by_slug", fake_get_organization_by_slug
    )
    monkeypatch.setattr(tenancy_service.repository, "insert_organization", fake_insert_organization)
    monkeypatch.setattr(tenancy_service, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(tenancy_service.repository, "insert_project", fake_insert_project)
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    await tenancy_service.create_organization(
        None, OrganizationCreate(name="Acme", slug="acme"), actor=actor
    )

    assert call_order == [
        "insert_organization",
        "set_tenant_context",
        "insert_project",
        "record_audit_event",
    ]


# --- accept_invitation hardening -----------------------------------------------


class _FakeInvitationRow:
    def __init__(self, *, status: str, expires_at: datetime) -> None:
        self.id = uuid.uuid4()
        self.status = status
        self.expires_at = expires_at


@pytest.mark.asyncio
async def test_accept_invitation_raises_not_found_for_unknown_id(monkeypatch) -> None:
    async def fake_get_invitation_by_id(session, invitation_id):
        return None

    monkeypatch.setattr(
        tenancy_service.repository, "get_invitation_by_id", fake_get_invitation_by_id
    )

    with pytest.raises(NotFoundError):
        await tenancy_service.accept_invitation(None, uuid.uuid4())


@pytest.mark.asyncio
async def test_accept_invitation_raises_conflict_when_not_pending(monkeypatch) -> None:
    row = _FakeInvitationRow(
        status="accepted", expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )

    async def fake_get_invitation_by_id(session, invitation_id):
        return row

    monkeypatch.setattr(
        tenancy_service.repository, "get_invitation_by_id", fake_get_invitation_by_id
    )

    with pytest.raises(ConflictError):
        await tenancy_service.accept_invitation(None, row.id)


@pytest.mark.asyncio
async def test_accept_invitation_raises_conflict_when_expired(monkeypatch) -> None:
    row = _FakeInvitationRow(
        status="pending", expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    async def fake_get_invitation_by_id(session, invitation_id):
        return row

    monkeypatch.setattr(
        tenancy_service.repository, "get_invitation_by_id", fake_get_invitation_by_id
    )

    with pytest.raises(ConflictError):
        await tenancy_service.accept_invitation(None, row.id)


@pytest.mark.asyncio
async def test_accept_invitation_succeeds_for_pending_unexpired_invitation(monkeypatch) -> None:
    row = _FakeInvitationRow(
        status="pending", expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    updates: list[dict[str, object]] = []

    async def fake_get_invitation_by_id(session, invitation_id):
        return row

    async def fake_update_invitation_status(session, invitation_id, **kwargs):
        updates.append({"invitation_id": invitation_id, **kwargs})

    monkeypatch.setattr(
        tenancy_service.repository, "get_invitation_by_id", fake_get_invitation_by_id
    )
    monkeypatch.setattr(
        tenancy_service.repository, "update_invitation_status", fake_update_invitation_status
    )

    await tenancy_service.accept_invitation(None, row.id)

    assert updates == [{"invitation_id": row.id, "status": "accepted", "accepted_at": updates[0]["accepted_at"]}]


# --- register_connector project-scoped permission -----------------------------


@pytest.mark.asyncio
async def test_register_connector_with_project_id_checks_project_scoped_permission(
    monkeypatch,
) -> None:
    """A caller granted `tenancy:manage` only on a *different* project must
    be denied when registering a connector scoped to this one -- confirms
    `register_connector` now checks the permission against `data.project_id`,
    not just the organization as a whole.
    """
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={other_project_id: frozenset({"tenancy:manage"})},
    )

    class _FakeProjectRow:
        def __init__(self) -> None:
            self.id = project_id
            self.organization_id = organization_id

    async def fake_get_project_by_id(session, project_id_arg):
        return _FakeProjectRow()

    monkeypatch.setattr(tenancy_service.repository, "get_project_by_id", fake_get_project_by_id)

    with pytest.raises(PermissionDeniedError):
        await tenancy_service.register_connector(
            None,
            actor,
            organization_id,
            ConnectorConfigCreate(source="slack", credential_ref="xoxb-token", project_id=project_id),
        )


@pytest.mark.asyncio
async def test_register_connector_with_project_id_succeeds_with_project_scoped_permission(
    monkeypatch,
) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={project_id: frozenset({"tenancy:manage"})},
    )

    class _FakeProjectRow:
        def __init__(self) -> None:
            self.id = project_id
            self.organization_id = organization_id

    async def fake_get_project_by_id(session, project_id_arg):
        return _FakeProjectRow()

    async def fake_insert_connector_config(session, **kwargs):
        return _FakeConnectorConfigRow(**kwargs)

    async def fake_record_audit_event(session, actor_arg, **kwargs):
        return None

    monkeypatch.setattr(tenancy_service.repository, "get_project_by_id", fake_get_project_by_id)
    monkeypatch.setattr(
        tenancy_service.repository, "insert_connector_config", fake_insert_connector_config
    )
    monkeypatch.setattr(tenancy_service, "record_audit_event", fake_record_audit_event)

    result = await tenancy_service.register_connector(
        None,
        actor,
        organization_id,
        ConnectorConfigCreate(source="slack", credential_ref="xoxb-token", project_id=project_id),
    )

    assert result.project_id == project_id


@pytest.mark.asyncio
async def test_register_connector_without_project_id_still_requires_org_level_permission(
    monkeypatch,
) -> None:
    """An org-wide connector (`project_id=None`) has no narrower scope to
    check against, so it must fall back to the plain org-level
    `tenancy:manage` check -- an actor with only a project-scoped grant (and
    none at the org level) must still be denied.
    """
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={project_id: frozenset({"tenancy:manage"})},
    )

    with pytest.raises(PermissionDeniedError):
        await tenancy_service.register_connector(
            None,
            actor,
            organization_id,
            ConnectorConfigCreate(source="slack", credential_ref="xoxb-token"),
        )
