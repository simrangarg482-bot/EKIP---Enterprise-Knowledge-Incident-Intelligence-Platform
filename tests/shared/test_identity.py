"""Tests for `app.shared.schemas.identity.Identity.resolve_search_scope`
(2026-08 audit "C1" fix): the single, sanctioned place a caller turns
`Identity.project_permissions` into the `(project_ids, permission_codes)`
pair a `retrieval.schemas.SearchFilters` construction needs.

See `tests/agents/retrieval/test_node.py`, `tests/agents/investigation/
test_evidence.py`, and `tests/agents/test_service.py` for regression tests
proving every affected retrieval/search call site actually uses this method
(i.e. that the leak this method closes is closed end-to-end, not just that
the method itself computes the right value in isolation).
"""

from __future__ import annotations

import uuid

from app.shared.schemas import ActorKind, Identity


def _actor(
    *,
    permissions: frozenset[str] = frozenset(),
    project_permissions: dict[uuid.UUID, frozenset[str]] | None = None,
) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        permissions=permissions,
        project_permissions=project_permissions or {},
    )


def test_no_project_permissions_returns_unrestricted_scope() -> None:
    """The pre-existing, common-case behavior (org-level permissions only,
    no `project_memberships` row) must be preserved exactly: `project_ids
    =None` ("no restriction"), `permission_codes` unchanged.
    """
    actor = _actor(permissions=frozenset({"observability:read", "incident:write"}))

    project_ids, permission_codes = actor.resolve_search_scope()

    assert project_ids is None
    assert permission_codes == actor.permissions


def test_single_project_membership_restricts_to_that_project() -> None:
    project_id = uuid.uuid4()
    actor = _actor(
        permissions=frozenset({"observability:read"}),
        project_permissions={project_id: frozenset({"incident:write"})},
    )

    project_ids, permission_codes = actor.resolve_search_scope()

    assert project_ids == [project_id]
    assert permission_codes == frozenset({"observability:read", "incident:write"})


def test_multiple_project_memberships_restrict_to_exactly_those_projects() -> None:
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    other_project = uuid.uuid4()  # no membership row -- must never be included
    actor = _actor(
        project_permissions={
            project_a: frozenset({"incident:write"}),
            project_b: frozenset({"postmortem:write", "postmortem:approve"}),
        }
    )

    project_ids, permission_codes = actor.resolve_search_scope()

    assert set(project_ids or []) == {project_a, project_b}
    assert other_project not in (project_ids or [])
    assert permission_codes == frozenset(
        {"incident:write", "postmortem:write", "postmortem:approve"}
    )


def test_project_scoped_codes_are_merged_not_substituted() -> None:
    """A permission granted only at the org level, and one granted only via
    a project role, must both be present in the merged set -- restricting
    `project_ids` must not also silently narrow which ACL-gated content is
    visible within the projects the actor *can* see.
    """
    project_id = uuid.uuid4()
    actor = _actor(
        permissions=frozenset({"observability:read"}),
        project_permissions={project_id: frozenset({"knowledge:review"})},
    )

    _, permission_codes = actor.resolve_search_scope()

    assert "observability:read" in permission_codes
    assert "knowledge:review" in permission_codes


def test_empty_project_permission_set_for_a_project_contributes_nothing() -> None:
    """A membership row with zero granted permission codes (an edge case,
    e.g. a role with no permissions attached) must not blow up the merge --
    `frozenset.union()` over an empty set is a no-op, not an error.
    """
    project_id = uuid.uuid4()
    actor = _actor(
        permissions=frozenset({"observability:read"}),
        project_permissions={project_id: frozenset()},
    )

    project_ids, permission_codes = actor.resolve_search_scope()

    assert project_ids == [project_id]
    assert permission_codes == frozenset({"observability:read"})
