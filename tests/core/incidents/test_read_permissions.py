"""Regression tests for the 2026-08 audit "H4" fix: `get_incident`,
`list_incidents`, `get_timeline`, and `get_postmortem` previously checked
only same-organization membership (`_ensure_same_organization`) -- any
identity with zero role assignments at all (a real, supported state; see
`core.users.service.resolve_identity`'s own docstring) could still read
every incident/postmortem in the organization. This file proves the new
`incident:read`/`postmortem:read` gates actually deny that unauthorized-but-
same-org caller, while an authorized one (org-level OR project-scoped grant)
still gets the exact same data back as before.

Uses real `Identity` objects with controlled `permissions`/`project_permissions`
sets -- not a mocked `require_permission` -- so these tests exercise the real
`Identity.has_permission` logic end to end, the same "fake only the true I/O
boundary, run the real authorization logic" approach `tests/agents/
retrieval/test_node.py` already established for `resolve_search_scope`.
Only `core.incidents.repository`'s DB calls are monkeypatched (no test
infrastructure/DB fixture exists for this module -- see `tests/core/incidents/
test_service.py`'s own docstring on why fakes, not a real database, are used
here).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.exceptions import PermissionDeniedError
from app.core.incidents import service as incidents_service
from app.core.incidents.schemas import Incident, IncidentFilter, Postmortem, TimelineEntry
from app.shared.schemas import ActorKind, Identity


def _actor(
    organization_id: uuid.UUID,
    *,
    permissions: frozenset[str] = frozenset(),
    project_permissions: dict[uuid.UUID, frozenset[str]] | None = None,
) -> Identity:
    """An identity with `permissions=frozenset()` and no `project_permissions`
    (the defaults) models exactly the "real, supported state" H4's issue text
    calls out: a user who exists and belongs to the organization, but holds
    no role assignments granting any permission at all.
    """
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=permissions,
        project_permissions=project_permissions or {},
    )


def _incident_row(organization_id: uuid.UUID, project_id: uuid.UUID, **overrides: object) -> Incident:
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        title="Checkout service down",
        description="Elevated 500s on /checkout since 14:02 UTC.",
        status="investigating",
        severity="high",
        owner_team=None,
        reported_by=uuid.uuid4(),
        resolved_at=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _timeline_row(organization_id: uuid.UUID, incident_id: uuid.UUID, **overrides: object) -> TimelineEntry:
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        organization_id=organization_id,
        incident_id=incident_id,
        event_type="note",
        event_data={"note": "Rolled back the last deploy."},
        actor="user:abc123",
        occurred_at=now,
    )
    defaults.update(overrides)
    return TimelineEntry(**defaults)


def _postmortem_row(organization_id: uuid.UUID, incident_id: uuid.UUID, **overrides: object) -> Postmortem:
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        organization_id=organization_id,
        incident_id=incident_id,
        status="approved",
        root_cause="A connection-pool exhaustion bug introduced in the last deploy.",
        action_items=[],
        generated_by="agent:postmortem_agent",
        reviewed_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Postmortem(**defaults)


# --- get_incident ----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_incident_denies_actor_with_no_role_assignments(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_incident_by_id(session, iid):
        return row

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(organization_id)  # zero permissions -- no role assignments at all

    with pytest.raises(PermissionDeniedError) as exc_info:
        await incidents_service.get_incident(None, actor, organization_id, incident_id)
    assert exc_info.value.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_get_incident_allows_actor_with_org_level_incident_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_incident_by_id(session, iid):
        return row

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(organization_id, permissions=frozenset({"incident:read"}))

    result = await incidents_service.get_incident(None, actor, organization_id, incident_id)

    assert result.id == incident_id


@pytest.mark.asyncio
async def test_get_incident_allows_actor_with_project_scoped_incident_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_incident_by_id(session, iid):
        return row

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(
        organization_id, project_permissions={project_id: frozenset({"incident:read"})}
    )

    result = await incidents_service.get_incident(None, actor, organization_id, incident_id)

    assert result.id == incident_id


@pytest.mark.asyncio
async def test_get_incident_denies_actor_whose_project_scoped_role_lacks_read(monkeypatch) -> None:
    """A project-scoped grant that does not include `incident:read` for this
    exact project must not fall back to the org-level set -- even though the
    actor also holds `incident:read` at the org level -- matching
    `Identity.has_permission`'s own documented "an explicit project override
    does not also consult the org-level set" behavior (a project-scoped
    'viewer'-without-incidents role must actually be able to restrict).
    """
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_incident_by_id(session, iid):
        return row

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(
        organization_id,
        permissions=frozenset({"incident:read"}),
        project_permissions={project_id: frozenset({"incident:write"})},
    )

    with pytest.raises(PermissionDeniedError):
        await incidents_service.get_incident(None, actor, organization_id, incident_id)


@pytest.mark.asyncio
async def test_get_incident_still_enforces_cross_organization_isolation(monkeypatch) -> None:
    """H4's new gate must not weaken the pre-existing tenant-isolation check
    -- a same-permission actor from a different organization is still
    denied, and denied for the cross-organization reason specifically
    (checked before the new permission gate even runs, since the incident
    row is never even fetched for a foreign organization_id).
    """
    organization_id = uuid.uuid4()
    foreign_organization_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    actor = _actor(foreign_organization_id, permissions=frozenset({"incident:read"}))

    with pytest.raises(PermissionDeniedError) as exc_info:
        await incidents_service.get_incident(None, actor, organization_id, incident_id)
    assert exc_info.value.error_code == "incident.cross_organization_denied"


# --- list_incidents ----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_incidents_denies_actor_with_no_role_assignments(monkeypatch) -> None:
    organization_id = uuid.uuid4()

    async def fake_list_incidents(session, org_id, query):
        return [_incident_row(organization_id, uuid.uuid4())]

    monkeypatch.setattr(incidents_service.repository, "list_incidents", fake_list_incidents)

    actor = _actor(organization_id)

    with pytest.raises(PermissionDeniedError):
        await incidents_service.list_incidents(None, actor, organization_id, IncidentFilter())


@pytest.mark.asyncio
async def test_list_incidents_allows_actor_with_org_level_incident_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    row = _incident_row(organization_id, uuid.uuid4())

    async def fake_list_incidents(session, org_id, query):
        assert org_id == organization_id
        return [row]

    monkeypatch.setattr(incidents_service.repository, "list_incidents", fake_list_incidents)

    actor = _actor(organization_id, permissions=frozenset({"incident:read"}))

    result = await incidents_service.list_incidents(None, actor, organization_id, IncidentFilter())

    assert [incident.id for incident in result] == [row.id]


@pytest.mark.asyncio
async def test_list_incidents_project_scoped_grant_alone_is_not_sufficient(monkeypatch) -> None:
    """`list_incidents` has no per-project filter (`IncidentFilter` carries
    no `project_id`) -- it lists across every project in the organization at
    once, so it is gated at the org level only. A project-scoped-only grant
    (no org-level `incident:read`) must not satisfy an org-level check --
    `require_permission` with no `project_id` argument only ever consults
    `actor.permissions`.
    """
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()

    async def fake_list_incidents(session, org_id, query):
        return [_incident_row(organization_id, project_id)]

    monkeypatch.setattr(incidents_service.repository, "list_incidents", fake_list_incidents)

    actor = _actor(
        organization_id, project_permissions={project_id: frozenset({"incident:read"})}
    )

    with pytest.raises(PermissionDeniedError):
        await incidents_service.list_incidents(None, actor, organization_id, IncidentFilter())


# --- get_timeline ----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_timeline_denies_actor_with_no_role_assignments(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    incident_row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    async def fake_list_timeline_entries(session, iid):
        return [_timeline_row(organization_id, incident_id)]

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)
    monkeypatch.setattr(
        incidents_service.repository, "list_timeline_entries", fake_list_timeline_entries
    )

    actor = _actor(organization_id)

    with pytest.raises(PermissionDeniedError):
        await incidents_service.get_timeline(None, actor, organization_id, incident_id)


@pytest.mark.asyncio
async def test_get_timeline_allows_actor_with_project_scoped_incident_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    incident_row = _incident_row(organization_id, project_id, id=incident_id)
    timeline_row = _timeline_row(organization_id, incident_id)

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    async def fake_list_timeline_entries(session, iid):
        assert iid == incident_id
        return [timeline_row]

    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)
    monkeypatch.setattr(
        incidents_service.repository, "list_timeline_entries", fake_list_timeline_entries
    )

    actor = _actor(
        organization_id, project_permissions={project_id: frozenset({"incident:read"})}
    )

    result = await incidents_service.get_timeline(None, actor, organization_id, incident_id)

    assert [entry.id for entry in result] == [timeline_row.id]


# --- get_postmortem ----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_postmortem_denies_actor_with_no_role_assignments(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    postmortem_id = uuid.uuid4()
    postmortem_row = _postmortem_row(organization_id, incident_id, id=postmortem_id)
    incident_row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_postmortem_by_id(session, pid):
        return postmortem_row

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    monkeypatch.setattr(
        incidents_service.repository, "get_postmortem_by_id", fake_get_postmortem_by_id
    )
    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(organization_id)

    with pytest.raises(PermissionDeniedError):
        await incidents_service.get_postmortem(None, actor, organization_id, postmortem_id)


@pytest.mark.asyncio
async def test_get_postmortem_allows_actor_with_org_level_postmortem_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    postmortem_id = uuid.uuid4()
    postmortem_row = _postmortem_row(organization_id, incident_id, id=postmortem_id)
    incident_row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_postmortem_by_id(session, pid):
        return postmortem_row

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    monkeypatch.setattr(
        incidents_service.repository, "get_postmortem_by_id", fake_get_postmortem_by_id
    )
    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(organization_id, permissions=frozenset({"postmortem:read"}))

    result = await incidents_service.get_postmortem(None, actor, organization_id, postmortem_id)

    assert result.id == postmortem_id


@pytest.mark.asyncio
async def test_get_postmortem_allows_actor_with_project_scoped_postmortem_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    postmortem_id = uuid.uuid4()
    postmortem_row = _postmortem_row(organization_id, incident_id, id=postmortem_id)
    incident_row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_postmortem_by_id(session, pid):
        return postmortem_row

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    monkeypatch.setattr(
        incidents_service.repository, "get_postmortem_by_id", fake_get_postmortem_by_id
    )
    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(
        organization_id, project_permissions={project_id: frozenset({"postmortem:read"})}
    )

    result = await incidents_service.get_postmortem(None, actor, organization_id, postmortem_id)

    assert result.id == postmortem_id


@pytest.mark.asyncio
async def test_get_postmortem_denies_actor_whose_project_scoped_role_lacks_read(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    postmortem_id = uuid.uuid4()
    postmortem_row = _postmortem_row(organization_id, incident_id, id=postmortem_id)
    incident_row = _incident_row(organization_id, project_id, id=incident_id)

    async def fake_get_postmortem_by_id(session, pid):
        return postmortem_row

    async def fake_get_incident_by_id(session, iid):
        return incident_row

    monkeypatch.setattr(
        incidents_service.repository, "get_postmortem_by_id", fake_get_postmortem_by_id
    )
    monkeypatch.setattr(incidents_service.repository, "get_incident_by_id", fake_get_incident_by_id)

    actor = _actor(
        organization_id,
        permissions=frozenset({"postmortem:read"}),
        project_permissions={project_id: frozenset({"incident:write"})},
    )

    with pytest.raises(PermissionDeniedError):
        await incidents_service.get_postmortem(None, actor, organization_id, postmortem_id)


@pytest.mark.asyncio
async def test_get_postmortem_still_enforces_cross_organization_isolation() -> None:
    organization_id = uuid.uuid4()
    foreign_organization_id = uuid.uuid4()
    postmortem_id = uuid.uuid4()
    actor = _actor(foreign_organization_id, permissions=frozenset({"postmortem:read"}))

    with pytest.raises(PermissionDeniedError) as exc_info:
        await incidents_service.get_postmortem(None, actor, organization_id, postmortem_id)
    assert exc_info.value.error_code == "incident.cross_organization_denied"
