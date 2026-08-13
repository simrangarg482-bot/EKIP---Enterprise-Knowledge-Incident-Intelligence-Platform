"""Tenancy administration routers (PROJECT_PLAN.md section 9.2 / API_DESIGN.md
section 1's "tenancy" resource group).

Owned by: app/api. Thin pass-through to `app.core.tenancy.service` -- no
business logic beyond request/response translation (ARCHITECTURE.md
section 6), matching every other router in this package.

Two `APIRouter` instances live in this one file:

  - `router` (prefix `/tenancy`): the original connector-registration surface
    (Milestone 10). Every endpoint here operates on the caller's own
    `actor.organization_id` -- there is no `{organization_id}` path
    parameter, the same "no org override query path" convention this
    router's own history already established (`_ensure_same_organization`
    would reject a mismatch anyway; not exposing the parameter at all is
    simpler and removes the possibility of ever passing a different one by
    mistake).

  - `admin_router` (no prefix): the previously-missing REST surface for
    everything else already implemented in `core.tenancy.service` --
    organizations, projects, SSO configuration, access rules, and
    invitations -- none of which had a REST or MCP entry point before this.
    Unlike `router` above, these endpoints DO take an explicit
    `{organization_id}` path parameter (matching how this surface was
    specified): `core.tenancy.service`'s own `_ensure_same_organization`
    guard still runs on every call, so supplying a path parameter here is a
    convenience for the caller (and lets the URL read as a normal nested
    REST resource), not a widening of what's actually reachable -- a caller
    can still only ever operate on their own organization, exactly as
    `router`'s connector endpoints do implicitly.

  Two deliberate scoping decisions worth stating plainly:
    - `GET /organizations` does **not** call `core.tenancy.service.
      list_organizations` -- that function is explicitly documented (see its
      own docstring) as an unscoped, cross-tenant listing meant only for a
      system-internal cron job, never for an authenticated REST caller.
      Exposing it here would leak every organization in the system to any
      authenticated user. Instead, this endpoint returns a single-element
      list containing only the caller's own organization (via
      `get_organization`), which is the only organization-listing semantic
      that makes sense for a caller whose `Identity` is itself scoped to
      exactly one organization.
    - `POST /invitations/{invitation_id}/accept` is deliberately
      unauthenticated (no `CurrentIdentity` dependency), matching
      `core.tenancy.service.accept_invitation`'s own pre-login signature --
      the same function core/auth calls mid-SSO-login, before any session
      exists. The path parameter is named `invitation_id` here (the
      invitation's real, only identifier) rather than a separate secret
      "token": `invitations` has no dedicated single-use token column today
      (see `accept_invitation`'s own docstring for this limitation, and
      `docs/USER_TESTING_GUIDE.md` section 3 for why no invitation email
      exists to deliver one through in the first place). Treat an
      invitation id as sensitive until a real token column exists.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import ArqPool, CurrentIdentity, DbSession
from app.core.tenancy import service as tenancy_service
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
    SSOConfiguration,
    SSOConfigurationCreate,
)

router = APIRouter(prefix="/tenancy", tags=["tenancy"])


@router.post("/connectors", response_model=ConnectorConfig, status_code=201)
async def register_connector(
    data: ConnectorConfigCreate, actor: CurrentIdentity, session: DbSession
) -> ConnectorConfig:
    return await tenancy_service.register_connector(session, actor, actor.organization_id, data)


@router.get("/connectors", response_model=list[ConnectorConfig])
async def list_connectors(actor: CurrentIdentity, session: DbSession) -> list[ConnectorConfig]:
    return await tenancy_service.list_connectors(session, actor, actor.organization_id)


@router.post("/connectors/{connector_config_id}/sync", status_code=202)
async def sync_connector(
    connector_config_id: uuid.UUID, actor: CurrentIdentity, session: DbSession, arq_pool: ArqPool
) -> dict[str, str]:
    """Trigger an on-demand ingestion sync for one connector.

    Previously, `run_ingestion_job`/`reindex` (`app.ingestion.service`) had
    no REST or MCP caller at all -- reachable only from `scheduled_
    reconciliation`'s hourly cron pass (`app.ingestion.workers.tasks`). This
    is the missing producer side of that same, already-real consumer: it
    enqueues the exact same `run_ingestion_job_task` the cron job enqueues
    (`app.ingestion.workers.tasks`), onto the same `arq` queue the
    already-running worker process consumes -- no ingestion logic is
    duplicated here, only the trigger.

    `tenancy_service.get_connector` enforces ownership and `tenancy:manage`
    before anything is enqueued, so this cannot be used to trigger a sync
    for a connector outside the caller's own organization/project scope.
    Returns `202` immediately -- the sync itself runs asynchronously in the
    worker process; poll `GET /tenancy/connectors` and watch `last_synced_at`
    /`status` to see it complete.
    """
    connector = await tenancy_service.get_connector(
        session, actor, actor.organization_id, connector_config_id
    )
    await arq_pool.enqueue_job("run_ingestion_job_task", str(connector.id))
    return {"status": "enqueued", "connector_config_id": str(connector.id)}


admin_router = APIRouter(tags=["tenancy-admin"])


# --- Organizations -----------------------------------------------------------


@admin_router.post("/organizations", response_model=Organization, status_code=201)
async def create_organization(
    data: OrganizationCreate, actor: CurrentIdentity, session: DbSession
) -> Organization:
    return await tenancy_service.create_organization(session, data, actor=actor)


@admin_router.get("/organizations", response_model=list[Organization])
async def list_organizations(actor: CurrentIdentity, session: DbSession) -> list[Organization]:
    """Return the caller's own organization as a single-element list -- see
    this module's docstring for why this does not call the unscoped
    `core.tenancy.service.list_organizations`.
    """
    organization = await tenancy_service.get_organization(session, actor, actor.organization_id)
    return [organization]


@admin_router.get("/organizations/{organization_id}", response_model=Organization)
async def get_organization(
    organization_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> Organization:
    return await tenancy_service.get_organization(session, actor, organization_id)


# --- Projects ------------------------------------------------------------------


@admin_router.post(
    "/organizations/{organization_id}/projects", response_model=Project, status_code=201
)
async def create_project(
    organization_id: uuid.UUID, data: ProjectCreate, actor: CurrentIdentity, session: DbSession
) -> Project:
    return await tenancy_service.create_project(session, actor, organization_id, data)


@admin_router.get("/organizations/{organization_id}/projects", response_model=list[Project])
async def list_projects(
    organization_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> list[Project]:
    return await tenancy_service.list_projects(session, actor, organization_id)


# --- SSO -------------------------------------------------------------------------


@admin_router.post(
    "/organizations/{organization_id}/sso/configure",
    response_model=SSOConfiguration,
    status_code=201,
)
async def configure_sso(
    organization_id: uuid.UUID,
    data: SSOConfigurationCreate,
    actor: CurrentIdentity,
    session: DbSession,
) -> SSOConfiguration:
    return await tenancy_service.configure_sso(session, actor, organization_id, data)


# --- Access rules ----------------------------------------------------------------


@admin_router.post(
    "/organizations/{organization_id}/access-rules", response_model=AccessRule, status_code=201
)
async def create_access_rule(
    organization_id: uuid.UUID,
    data: AccessRuleCreate,
    actor: CurrentIdentity,
    session: DbSession,
) -> AccessRule:
    return await tenancy_service.create_access_rule(session, actor, organization_id, data)


@admin_router.get(
    "/organizations/{organization_id}/access-rules", response_model=list[AccessRule]
)
async def list_access_rules(
    organization_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> list[AccessRule]:
    return await tenancy_service.list_access_rules(session, actor, organization_id)


@admin_router.patch("/access-rules/{rule_id}/deactivate", response_model=AccessRule)
async def deactivate_access_rule(
    rule_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> AccessRule:
    """No `{organization_id}` path parameter -- like `router`'s connector
    endpoints, this always operates on the caller's own organization
    (`core.tenancy.service.deactivate_access_rule` already verifies the rule
    belongs to it and 404s otherwise).
    """
    return await tenancy_service.deactivate_access_rule(
        session, actor, actor.organization_id, rule_id
    )


# --- Invitations -----------------------------------------------------------------


@admin_router.post(
    "/organizations/{organization_id}/invitations", response_model=Invitation, status_code=201
)
async def create_invitation(
    organization_id: uuid.UUID,
    data: InvitationCreate,
    actor: CurrentIdentity,
    session: DbSession,
) -> Invitation:
    return await tenancy_service.create_invitation(session, actor, organization_id, data)


@admin_router.get(
    "/organizations/{organization_id}/invitations", response_model=list[Invitation]
)
async def list_invitations(
    organization_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> list[Invitation]:
    return await tenancy_service.list_invitations(session, actor, organization_id)


@admin_router.post("/invitations/{invitation_id}/accept", status_code=204)
async def accept_invitation(invitation_id: uuid.UUID, session: DbSession) -> None:
    """Deliberately unauthenticated -- see this module's docstring."""
    await tenancy_service.accept_invitation(session, invitation_id)


@admin_router.post("/invitations/{invitation_id}/revoke", response_model=Invitation)
async def revoke_invitation(
    invitation_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> Invitation:
    """No `{organization_id}` path parameter -- same reasoning as
    `deactivate_access_rule` above.
    """
    return await tenancy_service.revoke_invitation(
        session, actor, actor.organization_id, invitation_id
    )
