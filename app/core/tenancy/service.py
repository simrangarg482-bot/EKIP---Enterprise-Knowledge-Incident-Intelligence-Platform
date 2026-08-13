"""Public interface for core/tenancy -- organizations, projects, SSO
configuration, connector configuration.

Owned by: core/tenancy. This module is the authority on "what organization/
project does this belong to, and what's connected to it" (PROJECT_PLAN.md
section 9.2). Business rules and ORM->Pydantic mapping live here; raw SQL
lives in repository.py; the wire/HTTP concerns live in the future api/ layer.

Tenant isolation (PROJECT_PLAN.md section 3.7): every function below that
takes an `organization_id` argument also takes the calling `actor: Identity`
and verifies `actor.organization_id == organization_id` before doing anything
else -- there is no operation here that lets an authenticated caller read or
write another organization's tenancy data, matching the "no admin override
query path that skips it" rule. `create_organization` is the sole exception,
since an organization does not exist yet at the moment it is being created --
see its docstring.

Authorization: mutating operations (`create_project`, `register_connector`,
`configure_sso`) additionally require the `tenancy:manage` permission via
core/users's `require_permission`, and record an audit event via
core/audit's `record_audit_event` -- the same cross-submodule dependency
pattern documented for core/incidents (PROJECT_PLAN.md section 9.4). Seeding
`tenancy:manage` into the platform's fixed permission catalog is a data
migration concern, not something this module manages.

Milestone 10 addition (PROJECT_PLAN.md section 12.5): `register_connector`
depends on `shared/security` to envelope-encrypt a connector's plaintext
credential before persisting it -- the first real caller of that module. See
`register_connector`'s own docstring for the encrypt-at-write/decrypt-at-read
split with `ingestion.service`. `configure_sso` does the same for
`SSOConfigurationCreate.client_secret_ref` (2026-08 audit "C3" fix), with
`core.auth.service._resolve_client_secret` as its decrypt-at-read
counterpart.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit_event
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.tenancy import repository
from app.database.session import set_tenant_context
from app.core.tenancy.schemas import (
    AccessRule,
    AccessRuleCreate,
    ConnectorConfig,
    ConnectorConfigCreate,
    Invitation,
    InvitationCreate,
    Organization,
    OrganizationCreate,
    Project,
    ProjectCreate,
    ProvisioningDecision,
    SSOConfiguration,
    SSOConfigurationCreate,
)
from app.core.users import repository as users_repository
from app.core.users.service import require_permission, require_project_permission
from app.shared.config.logging import get_logger
from app.shared.schemas import Identity
from app.shared.security import encrypt_secret, get_kms

logger = get_logger(__name__)

_MANAGE_PERMISSION = "tenancy:manage"
# Applied when InvitationCreate.expires_at is omitted -- not yet a Settings
# field, same accepted gap as core/auth/service.py's _REFRESH_TOKEN_LIFETIME.
_DEFAULT_INVITATION_LIFETIME = timedelta(days=14)


def _ensure_same_organization(actor: Identity, organization_id: uuid.UUID) -> None:
    """Tenant-isolation guard: deny any operation scoped to an organization
    other than the caller's own (PROJECT_PLAN.md section 3.7).

    Deliberately a `PermissionDeniedError`, not a `NotFoundError` -- consistent
    with `core.users.service.require_permission`'s existing convention for
    authorization failures elsewhere in the codebase, rather than introducing
    a second denial style for this module alone.
    """
    if actor.organization_id != organization_id:
        logger.warning(
            "tenancy_cross_organization_denied",
            actor=actor.audit_tag,
            actor_organization_id=str(actor.organization_id),
            requested_organization_id=str(organization_id),
        )
        raise PermissionDeniedError(
            "Cannot access another organization's data.",
            error_code="tenancy.cross_organization_denied",
            detail={"organization_id": str(organization_id)},
        )


# --- Organizations -----------------------------------------------------------


async def create_organization(
    session: AsyncSession, data: OrganizationCreate, actor: Identity | None = None
) -> Organization:
    """Create a new organization together with its mandatory default project.

    `actor` is optional and defaults to `None`: an organization does not
    exist yet at the moment it is created, so there is no valid
    organization-scoped Identity to *require* one from (Identity.
    organization_id is mandatory per ENGINEERING_DECISIONS.md #004) -- who/
    what is allowed to call this (public self-serve signup vs. an internal
    admin/sales tool) is still not pinned down anywhere in the docs. `actor`
    exists purely so a caller that *does* already have one (the REST
    `POST /organizations` endpoint, reached by an already-authenticated
    identity creating an additional organization) can have the creation
    audited under a real actor rather than silently going unaudited; no
    permission check is added here, since one still isn't specified. Existing
    callers with no `Identity` available at all (`scripts/seed_test_
    organization.py`, `scripts/test_milestone6.py`) keep working unchanged by
    omitting it, in which case no audit event is recorded (nothing to
    attribute it to).

    Auto-creates the "General" default project in the same transaction
    (PROJECT_PLAN.md section 3.2: every organization has at least one project,
    so every incident/document has a uniform `project_id` even for customers
    who never create a second one).

    Raises ConflictError if `data.slug` is already taken. Note: this is a
    pre-check, not a database-constraint-driven retry -- a race between two
    concurrent signups for the same slug is a known, accepted gap (the
    `slug` column's own uniqueness constraint is the final backstop against
    actually storing a duplicate, it just wouldn't surface as this clean an
    error in that narrow race window).

    Milestone 10 RLS note: `organizations` itself needs no GUC set --
    deliberately excluded from RLS (see the RLS migration's own docstring;
    same reasoning `get_organization_sso_config` documents for its own
    `get_organization_by_slug` lookup). `projects` (and, if `actor` is given,
    `audit_logs` via `record_audit_event` below) *are* RLS-protected,
    though, so `set_tenant_context` must be called the moment `org_row.id`
    is known -- the same "set it immediately once the id is known, before
    the very next RLS-protected query" pattern `get_organization_sso_config`
    already follows. `SET LOCAL` scopes this to the current transaction, so
    setting it once here also covers the later `record_audit_event` write in
    the same transaction; no second call is needed.
    """
    existing = await repository.get_organization_by_slug(session, data.slug)
    if existing is not None:
        raise ConflictError(
            "An organization with this slug already exists.",
            error_code="organization.slug_taken",
            detail={"slug": data.slug},
        )

    org_row = await repository.insert_organization(
        session, name=data.name, slug=data.slug
    )
    await set_tenant_context(session, org_row.id)
    await repository.insert_project(
        session, organization_id=org_row.id, name="General", is_default=True
    )

    if actor is not None:
        await record_audit_event(
            session,
            actor,
            action="organization.create",
            resource_type="organization",
            resource_id=org_row.id,
            metadata={"slug": org_row.slug},
        )

    logger.info(
        "organization_created", organization_id=str(org_row.id), slug=org_row.slug
    )
    return Organization.model_validate(org_row)


async def get_organization(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> Organization:
    """Fetch one organization. Raises NotFoundError if it doesn't exist."""
    _ensure_same_organization(actor, organization_id)

    row = await repository.get_organization_by_id(session, organization_id)
    if row is None:
        raise NotFoundError(
            "Organization not found.",
            error_code="organization.not_found",
            detail={"organization_id": str(organization_id)},
        )
    return Organization.model_validate(row)


async def list_organizations(session: AsyncSession) -> list[Organization]:
    """Return every organization in the system, unscoped.

    No `actor` parameter, by design -- like `get_organization_sso_config`,
    this is a deliberate exception to "every operation takes an actor and is
    checked against it," not an oversight. The only legitimate caller is a
    scheduled, system-internal job that must iterate every tenant by
    definition (`app.agents.workers.tasks`'s Knowledge Gap Agent cron,
    Milestone 9 -- mirroring `app.ingestion.workers.tasks.
    scheduled_reconciliation`'s identical precedent of calling a repository-
    level, unscoped listing directly from a worker task rather than through
    a normal actor-scoped service call). There is no narrower organization
    to scope this to when the whole point of the call is "every
    organization" -- nothing about this function is reachable from REST or
    MCP, where an actor is always available and this would be the wrong
    tool.
    """
    rows = await repository.list_organizations(session)
    return [Organization.model_validate(row) for row in rows]


async def get_organization_sso_config(
    session: AsyncSession, org_slug: str
) -> SSOConfiguration:
    """Resolve an organization's SSO configuration by its login-URL slug.

    Called *before* any Identity exists -- this is the very first step of the
    SSO login flow (PROJECT_PLAN.md section 3.3, section 11.1:
    `GET /o/{org-slug}/login`), so unlike every other function in this module
    it takes no `actor` and performs no tenant-isolation check: the slug
    itself is the only thing identifying which organization the employee is
    trying to log into.

    Raises NotFoundError if the slug doesn't resolve to an organization, or if
    the organization exists but has no SSO configured yet (an organization
    mid-onboarding, before an IT Admin has connected an IdP).

    Milestone 10 RLS note: `get_organization_by_slug` needs no bypass --
    `organizations` itself is deliberately excluded from RLS (see the RLS
    migration's own docstring), so this lookup succeeds unrestricted before
    `organization_id` is even known. The very next query, though
    (`sso_configurations`, which *is* RLS-protected), does need the GUC set
    first -- done immediately below, the moment `org_row.id` is known.
    """
    org_row = await repository.get_organization_by_slug(session, org_slug)
    if org_row is None:
        raise NotFoundError(
            "Organization not found.",
            error_code="organization.not_found",
            detail={"slug": org_slug},
        )

    await set_tenant_context(session, org_row.id)
    sso_row = await repository.get_sso_configuration_by_organization_id(
        session, org_row.id
    )
    if sso_row is None:
        raise NotFoundError(
            "This organization has not configured SSO yet.",
            error_code="sso_configuration.not_found",
            detail={"organization_id": str(org_row.id)},
        )
    return SSOConfiguration.model_validate(sso_row)


async def configure_sso(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: SSOConfigurationCreate,
) -> SSOConfiguration:
    """Configure `organization_id`'s SSO provider for the first time.

    Raises ConflictError if SSO is already configured -- replacing an
    existing configuration is a distinct, not-yet-built operation (see
    repository.insert_sso_configuration's docstring), not silently handled
    here as an upsert.

    `data.client_secret_ref` (the plaintext OIDC client secret a caller
    submits at setup time) is envelope-encrypted (`app.shared.security`,
    PROJECT_PLAN.md section 12.5) before it is ever persisted -- the same
    encrypt-at-write pattern `register_connector` already uses for connector
    credentials, applied here for the first time to SSO secrets. Only the
    encrypted envelope is stored; `core.auth.service._resolve_client_secret`
    is the sole place that decrypts it back, immediately before an OIDC
    token exchange needs it.
    """
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    existing = await repository.get_sso_configuration_by_organization_id(
        session, organization_id
    )
    if existing is not None:
        raise ConflictError(
            "SSO is already configured for this organization.",
            error_code="sso_configuration.already_exists",
            detail={"organization_id": str(organization_id)},
        )

    encrypted_client_secret_ref = encrypt_secret(get_kms(), data.client_secret_ref)
    row = await repository.insert_sso_configuration(
        session,
        organization_id=organization_id,
        provider=data.provider,
        protocol=data.protocol,
        issuer_url=data.issuer_url,
        client_id=data.client_id,
        client_secret_ref=encrypted_client_secret_ref,
    )
    await record_audit_event(
        session,
        actor,
        action="sso_configuration.configure",
        resource_type="sso_configuration",
        resource_id=row.id,
        metadata={"organization_id": str(organization_id), "provider": data.provider},
    )
    return SSOConfiguration.model_validate(row)


# --- Projects ----------------------------------------------------------------


async def list_projects(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> list[Project]:
    """Return every project belonging to `organization_id`."""
    _ensure_same_organization(actor, organization_id)

    rows = await repository.list_projects(session, organization_id)
    return [Project.model_validate(row) for row in rows]


async def get_default_project(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> Project:
    """Fetch `organization_id`'s auto-created default project.

    Used by callers (core/incidents, when a caller omits `project_id` on
    incident creation) that need "the project" for an organization that
    hasn't bothered creating more than one (PROJECT_PLAN.md section 3.2).
    Raises NotFoundError in the pathological case where an organization
    somehow has none -- should be unreachable given `create_organization`
    always creates one alongside the organization itself, but not re-derived
    or assumed here.
    """
    _ensure_same_organization(actor, organization_id)

    row = await repository.get_default_project(session, organization_id)
    if row is None:
        raise NotFoundError(
            "This organization has no default project.",
            error_code="project.default_missing",
            detail={"organization_id": str(organization_id)},
        )
    return Project.model_validate(row)


async def create_project(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: ProjectCreate,
) -> Project:
    """Create a new project within `organization_id`."""
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    row = await repository.insert_project(
        session,
        organization_id=organization_id,
        name=data.name,
        is_default=data.is_default,
    )
    await record_audit_event(
        session,
        actor,
        action="project.create",
        resource_type="project",
        resource_id=row.id,
        metadata={"organization_id": str(organization_id), "name": data.name},
    )
    return Project.model_validate(row)


# --- Connector configuration ----------------------------------------------------


async def register_connector(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: ConnectorConfigCreate,
) -> ConnectorConfig:
    """Register a new (organization, external tool) connection.

    If `data.project_id` is given, verifies that project actually belongs to
    `organization_id` -- without this check, a caller could otherwise scope a
    connector to a project belonging to a *different* organization, which
    would be a tenant-isolation leak at write time rather than read time.
    Once validated, the `tenancy:manage` check is itself narrowed to that
    project (`require_project_permission`) rather than the organization as a
    whole -- a user granted `tenancy:manage` only on one project should not
    thereby be able to register a connector scoped to a different project in
    the same organization. A connector with no `project_id` (org-wide) still
    requires the plain org-level permission, since there is no narrower scope
    to check it against.

    `data.credential_ref` (the plaintext credential a caller submits -- e.g.
    a Slack bot token, a Jira API token pair) is envelope-encrypted (§12.5,
    `app.shared.security`) before it is ever persisted; only the encrypted
    envelope is stored, and the plaintext value is never logged or written
    to `connector_configs` directly. `ingestion.service._execute_ingestion_
    job` is the sole place that decrypts it back, immediately before a
    connector's `authenticate()` needs it -- see that function's own
    docstring.
    """
    _ensure_same_organization(actor, organization_id)

    if data.project_id is not None:
        project_row = await repository.get_project_by_id(session, data.project_id)
        if project_row is None or project_row.organization_id != organization_id:
            raise ValidationError(
                "project_id does not belong to this organization.",
                error_code="connector_config.invalid_project",
                detail={
                    "organization_id": str(organization_id),
                    "project_id": str(data.project_id),
                },
            )
        require_project_permission(actor, data.project_id, _MANAGE_PERMISSION)
    else:
        require_permission(actor, _MANAGE_PERMISSION)

    encrypted_credential_ref = encrypt_secret(get_kms(), data.credential_ref)
    row = await repository.insert_connector_config(
        session,
        organization_id=organization_id,
        source=data.source,
        credential_ref=encrypted_credential_ref,
        project_id=data.project_id,
        config=data.config,
    )
    await record_audit_event(
        session,
        actor,
        action="connector_config.register",
        resource_type="connector_config",
        resource_id=row.id,
        metadata={"organization_id": str(organization_id), "source": data.source},
    )
    return ConnectorConfig.model_validate(row)


async def list_connectors(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> list[ConnectorConfig]:
    """Return every connector configuration belonging to `organization_id`."""
    _ensure_same_organization(actor, organization_id)

    rows = await repository.list_connector_configs(session, organization_id)
    return [ConnectorConfig.model_validate(row) for row in rows]


async def get_connector(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    connector_config_id: uuid.UUID,
) -> ConnectorConfig:
    """Fetch one connector configuration, enforcing the same ownership and
    `tenancy:manage` permission check `register_connector` applies at write
    time -- the read-then-act counterpart needed by `POST /tenancy/
    connectors/{id}/sync` (the API layer enqueues the actual ingestion job
    itself, via its own injected `arq` pool; this function only answers
    "does this connector exist, belong to this organization, and may `actor`
    act on it," the same shape `update_connector_sync_status` already
    establishes for its own ownership check).
    """
    _ensure_same_organization(actor, organization_id)

    row = await repository.get_connector_config_by_id(session, connector_config_id)
    if row is None or row.organization_id != organization_id:
        raise NotFoundError(
            "Connector configuration not found.",
            error_code="connector_config.not_found",
            detail={"connector_config_id": str(connector_config_id)},
        )

    if row.project_id is not None:
        require_project_permission(actor, row.project_id, _MANAGE_PERMISSION)
    else:
        require_permission(actor, _MANAGE_PERMISSION)

    return ConnectorConfig.model_validate(row)


async def update_connector_sync_status(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    connector_config_id: uuid.UUID,
    *,
    status: str,
    last_synced_at: datetime | None = None,
    config_patch: dict | None = None,
) -> ConnectorConfig:
    """Record a connector's sync outcome (PROJECT_PLAN.md section 4.5:
    "job status is tracked explicitly since the caller and worker no longer
    share a call stack") -- ingestion's new consumer of this module
    (app/ingestion/service.py, task #12).

    `config_patch`, when given, is shallow-merged into the connector's
    existing `config` JSONB rather than replacing it -- ingestion's own
    caller uses this to persist a cross-sync resume token (`FetchResult.
    resume_token`, see that field's docstring) under a reserved `
    "_resume_token"` key without disturbing the admin-supplied keys
    (`site_ids`/`channels`/...) already living in the same JSONB blob.

    Deliberately NOT gated by `require_permission(_MANAGE_PERMISSION)`,
    unlike `register_connector`/`configure_sso`: this is a system-triggered
    completion step reporting the outcome of a job that was already
    legitimately running -- ingestion's worker calls this as
    `Identity.for_agent("ingestion_worker", organization_id)`, not on behalf
    of a human requesting a new privileged action. This mirrors
    `core.incidents.service.create_postmortem`'s reasoning for why
    persisting an already-triggered background result isn't itself
    permission-gated. `_ensure_same_organization` still applies
    unconditionally: that's a structural tenant-isolation invariant, not a
    business permission, and holds regardless of who or what is calling.
    """
    _ensure_same_organization(actor, organization_id)

    existing = await repository.get_connector_config_by_id(session, connector_config_id)
    if existing is None or existing.organization_id != organization_id:
        raise NotFoundError(
            "Connector configuration not found.",
            error_code="connector_config.not_found",
            detail={"connector_config_id": str(connector_config_id)},
        )

    row = await repository.update_connector_config_sync_status(
        session,
        connector_config_id,
        status=status,
        last_synced_at=last_synced_at,
        config_patch=config_patch,
    )
    if row is None:
        raise RuntimeError("Connector configuration disappeared mid-update.")  # unreachable: fetched above

    await record_audit_event(
        session,
        actor,
        action="connector_config.sync_status_update",
        resource_type="connector_config",
        resource_id=connector_config_id,
        metadata={"status": status},
    )
    return ConnectorConfig.model_validate(row)


# --- Organization access rules (domain / group auto-join) --------------------


async def _resolve_role_id(session: AsyncSession, role_name: str) -> uuid.UUID:
    """Resolve a role name (as supplied on `AccessRuleCreate`/`InvitationCreate`)
    to its id, raising a clean domain error rather than letting a bad name
    surface as a foreign-key violation at insert time.
    """
    role = await users_repository.get_role_by_name(session, role_name)
    if role is None:
        raise ValidationError(
            "Unknown role name.",
            error_code="role.not_found",
            detail={"role": role_name},
        )
    return role.id


async def create_access_rule(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: AccessRuleCreate,
) -> AccessRule:
    """Create a domain/group auto-join rule for `organization_id`."""
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    role_id = await _resolve_role_id(session, data.grants_role)
    row = await repository.insert_access_rule(
        session,
        organization_id=organization_id,
        rule_type=data.rule_type,
        value=data.value,
        grants_role_id=role_id,
        is_active=data.is_active,
    )
    await record_audit_event(
        session,
        actor,
        action="access_rule.create",
        resource_type="organization_access_rule",
        resource_id=row.id,
        metadata={
            "organization_id": str(organization_id),
            "rule_type": data.rule_type,
            "value": data.value,
        },
    )
    return AccessRule.model_validate(row)


async def list_access_rules(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> list[AccessRule]:
    """Return every access rule (active or not) belonging to `organization_id`."""
    _ensure_same_organization(actor, organization_id)

    rows = await repository.list_access_rules(session, organization_id)
    return [AccessRule.model_validate(row) for row in rows]


async def deactivate_access_rule(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> AccessRule:
    """Suspend an access rule without deleting it.

    Verifies the rule actually belongs to `organization_id` before touching
    it -- without this check, a caller could deactivate another
    organization's rule by guessing/enumerating its id, a write-time
    tenant-isolation leak of the same shape `register_connector` already
    guards against for `project_id`.
    """
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    rule_row = await repository.get_access_rule_by_id(session, rule_id)
    if rule_row is None or rule_row.organization_id != organization_id:
        raise NotFoundError(
            "Access rule not found.",
            error_code="access_rule.not_found",
            detail={"organization_id": str(organization_id), "rule_id": str(rule_id)},
        )

    row = await repository.deactivate_access_rule(session, rule_id)
    await record_audit_event(
        session,
        actor,
        action="access_rule.deactivate",
        resource_type="organization_access_rule",
        resource_id=rule_id,
        metadata={"organization_id": str(organization_id)},
    )
    return AccessRule.model_validate(row)


# --- Invitations ---------------------------------------------------------------


async def create_invitation(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: InvitationCreate,
) -> Invitation:
    """Invite `data.email` to join `organization_id`.

    Requires `actor.user_id` to be set -- only a `USER`-kind identity (an
    actual admin) can send an invitation, since `invitations.invited_by` is a
    required reference to a `users` row; a service/agent identity has none.
    Raises ConflictError if a pending invitation for this email already
    exists (the partial unique index's application-level counterpart, for a
    clean domain error instead of a raw integrity-error surface).
    """
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    if actor.user_id is None:
        raise ValidationError(
            "Only a user identity can send invitations.",
            error_code="invitation.invalid_actor",
        )

    existing = await repository.get_pending_invitation(session, organization_id, data.email)
    if existing is not None:
        raise ConflictError(
            "An invitation is already pending for this email.",
            error_code="invitation.already_pending",
            detail={"organization_id": str(organization_id), "email": data.email},
        )

    role_id = await _resolve_role_id(session, data.grants_role)
    expires_at = data.expires_at or (datetime.now(timezone.utc) + _DEFAULT_INVITATION_LIFETIME)

    row = await repository.insert_invitation(
        session,
        organization_id=organization_id,
        email=data.email,
        grants_role_id=role_id,
        invited_by=actor.user_id,
        expires_at=expires_at,
    )
    await record_audit_event(
        session,
        actor,
        action="invitation.create",
        resource_type="invitation",
        resource_id=row.id,
        metadata={"organization_id": str(organization_id), "email": data.email},
    )
    return Invitation.model_validate(row)


async def list_invitations(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> list[Invitation]:
    """Return every invitation belonging to `organization_id`, newest first."""
    _ensure_same_organization(actor, organization_id)

    rows = await repository.list_invitations(session, organization_id)
    return [Invitation.model_validate(row) for row in rows]


async def revoke_invitation(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> Invitation:
    """Revoke a pending invitation before it's accepted or expires.

    Raises ConflictError if the invitation is not currently `"pending"` --
    an already-accepted or already-expired/revoked invitation is a closed
    state machine, mirroring the `status`-transition discipline already used
    for postmortems (DATABASE_DESIGN.md).
    """
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _MANAGE_PERMISSION)

    invitation_row = await repository.get_invitation_by_id(session, invitation_id)
    if invitation_row is None or invitation_row.organization_id != organization_id:
        raise NotFoundError(
            "Invitation not found.",
            error_code="invitation.not_found",
            detail={"organization_id": str(organization_id), "invitation_id": str(invitation_id)},
        )
    if invitation_row.status != "pending":
        raise ConflictError(
            "Only a pending invitation can be revoked.",
            error_code="invitation.not_pending",
            detail={"status": invitation_row.status},
        )

    row = await repository.update_invitation_status(session, invitation_id, status="revoked")
    await record_audit_event(
        session,
        actor,
        action="invitation.revoke",
        resource_type="invitation",
        resource_id=invitation_id,
        metadata={"organization_id": str(organization_id)},
    )
    return Invitation.model_validate(row)


async def accept_invitation(session: AsyncSession, invitation_id: uuid.UUID) -> None:
    """Mark an invitation accepted.

    Called by core/auth only after the invited user has actually been
    created/linked -- kept as a separate, explicit step from
    `evaluate_provisioning` (which only decides whether provisioning is
    *allowed*), even though both happen inside the same database transaction
    as the rest of SSO login completion. No `actor`: this runs as part of the
    same pre-session login flow as `evaluate_provisioning`, not as an
    admin-facing action.

    Guards added for its second caller, `POST /invitations/{invitation_id}/
    accept` (a REST entry point added alongside this comment, for a caller
    that -- unlike `evaluate_provisioning` -- has not already verified the
    invitation is pending and unexpired): raises NotFoundError for an unknown
    id, and ConflictError if the invitation is not currently `"pending"` (already
    accepted/revoked) or has passed its `expires_at`. `evaluate_provisioning`
    only ever passes an id it just confirmed satisfies both conditions, so
    these checks are a no-op, defense-in-depth addition for that call path,
    not a behavior change for it.

    Known limitation, stated plainly: `invitations` has no separate secret
    token column (`Invitation`'s schema exposes only its opaque `id`) --
    unlike a real "click this emailed link" flow, possessing this id alone is
    sufficient to accept here, with no proof the caller controls the invited
    email address. Adding a real single-use secret token is a schema/
    migration change, out of scope for wiring up the REST surface this
    guards; the same limitation already applies to how `create_invitation`
    itself is delivered (no email-sending exists in this codebase at all --
    see docs/USER_TESTING_GUIDE.md section 3).
    """
    invitation = await repository.get_invitation_by_id(session, invitation_id)
    if invitation is None:
        raise NotFoundError(
            "Invitation not found.",
            error_code="invitation.not_found",
            detail={"invitation_id": str(invitation_id)},
        )
    if invitation.status != "pending":
        raise ConflictError(
            "Only a pending invitation can be accepted.",
            error_code="invitation.not_pending",
            detail={"status": invitation.status},
        )
    if invitation.expires_at <= datetime.now(timezone.utc):
        raise ConflictError(
            "This invitation has expired.",
            error_code="invitation.expired",
            detail={"invitation_id": str(invitation_id)},
        )

    await repository.update_invitation_status(
        session, invitation_id, status="accepted", accepted_at=datetime.now(timezone.utc)
    )


# --- Provisioning policy evaluation ----------------------------------------------


async def evaluate_provisioning(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    groups: Sequence[str] = (),
) -> ProvisioningDecision:
    """Decide whether a verified SSO login may provision a user in
    `organization_id`, and which role it should receive if so.

    No `actor` parameter: this runs mid-login, before any session/Identity
    exists yet (same precedent as `get_organization_sso_config`) -- it is
    core/auth's job to call this, never the reverse, keeping "is this
    authentication valid" and "is this login authorized to provision an
    account" as two distinct steps (this migration's whole point).

    Precedence, most to least specific:
      1. A pending, unexpired invitation for this exact `email`.
      2. An active `domain` rule matching the email's domain.
      3. An active `group` rule matching one of `groups` (only checked if the
         IdP actually sent a groups claim -- an empty `groups` sequence
         means "no group claim available," not "matches no group," and
         group rules are simply skipped rather than treated as a denial
         signal on their own).
      4. Otherwise, denied.

    Milestone 10 RLS note: unlike `get_organization_sso_config`, this
    function is *handed* `organization_id` directly by its caller (core/auth,
    which resolved it via `get_organization_sso_config`'s own slug lookup
    earlier in the same login transaction) rather than discovering it itself
    -- so the GUC is set unconditionally at the top, before the first
    RLS-protected query (`invitations`) runs. Since `set_tenant_context` uses
    `SET LOCAL` (transaction-scoped, not connection-scoped), and SSO login
    completion runs `get_organization_sso_config` -> `evaluate_provisioning`
    -> `accept_invitation` inside one shared transaction, this same call also
    covers `accept_invitation`'s later `invitations` update on this session
    -- no separate wiring needed there.
    """
    await set_tenant_context(session, organization_id)
    now = datetime.now(timezone.utc)

    invitation = await repository.get_pending_invitation(session, organization_id, email)
    if invitation is not None:
        if invitation.expires_at > now:
            return ProvisioningDecision(
                allowed=True,
                grants_role_id=invitation.grants_role_id,
                matched_invitation_id=invitation.id,
                reason="invitation_match",
            )
        # Past-due but still "pending": lazily mark it expired now that
        # we've noticed (get_pending_invitation's docstring -- no sweep job
        # exists yet), then fall through to the coarser rule checks below.
        await repository.update_invitation_status(session, invitation.id, status="expired")
        logger.info(
            "invitation_expired",
            invitation_id=str(invitation.id),
            organization_id=str(organization_id),
        )

    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if domain:
        for rule in await repository.get_active_rules_by_type(session, organization_id, "domain"):
            if rule.value.lower() == domain:
                return ProvisioningDecision(
                    allowed=True, grants_role_id=rule.grants_role_id, reason="domain_match"
                )

    if groups:
        normalized_groups = {group.lower() for group in groups}
        for rule in await repository.get_active_rules_by_type(session, organization_id, "group"):
            if rule.value.lower() in normalized_groups:
                return ProvisioningDecision(
                    allowed=True, grants_role_id=rule.grants_role_id, reason="group_match"
                )

    logger.info(
        "provisioning_denied", organization_id=str(organization_id), email=email
    )
    return ProvisioningDecision(allowed=False, reason="no_matching_policy")
