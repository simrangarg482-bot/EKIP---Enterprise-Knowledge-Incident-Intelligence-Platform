"""The `Identity` value object threaded through every cross-module call.

Owned by: shared/ (ARCHITECTURE.md section 3). This is the single most
load-bearing shared type in the system: API_DESIGN.md section 2 threads
`Identity` through every `core/` and `agents/` public call precisely so that
access control is identical regardless of entry point -- the same
`authorize()` check runs whether a request arrived over REST or MCP
(ARCHITECTURE.md section 6).

It is deliberately transport-agnostic: it carries *who* the caller is and
*what* they're allowed to do, with no knowledge of JWTs, MCP tokens, or HTTP.
Resolving a raw credential into an `Identity` is core/auth/'s job; everything
downstream just receives the resolved object.

Multi-tenancy (PROJECT_PLAN.md section 3.4-3.6): an `Identity` is scoped to
exactly one organization -- there is no global, org-less identity in this
system. `organization_id` is the claim every session token carries (section
3.4) and the value every downstream query filters by (section 3.7); making it
a required field here, rather than an optional add-on, is what makes
"resolve this user's identity" and "resolve this user's identity within this
organization" the same operation -- there is no other kind to accidentally
fall back to.

`project_permissions` is the extension point for the second, finer-grained
authorization tier (section 3.6): a project-scoped role can override the
identity's org-level `permissions` for a specific project. `core/users/
service.py`'s `resolve_identity` populates it from `project_memberships` for
every identity resolution, so it defaults to empty only for callers who hold
no project-scoped membership row at all (today, most callers -- there is no
API yet to create a `project_memberships` row; see that table's own
docstring). `resolve_search_scope()` below is the sanctioned way for a
retrieval/search caller to turn this into a `project_ids` restriction: a
caller with no project-scoped grants may still search every project they can
see via their org-level `permissions` (the pre-existing, unrestricted
default), but a caller who *does* hold one or more project-scoped grants is
restricted to exactly those projects -- holding an org-level permission is
no longer, on its own, sufficient to see a project the caller has no
membership row for.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ActorKind(str, Enum):
    """Distinguishes human users from non-human callers.

    Mirrors the human-vs-AI authorship distinction that DATABASE_DESIGN.md
    keeps unambiguous at the data layer (the tagged `actor` strings on
    incident_timeline / postmortems / audit_logs). `Identity.audit_tag`
    produces exactly those strings, so the identity that performed an action
    and the string recorded against it never drift apart.
    """

    USER = "user"
    SERVICE = "service"
    AGENT = "agent"


class Identity(BaseModel):
    """A resolved caller, scoped to exactly one organization. Immutable once
    constructed.

    `permissions` is the flattened set of org-level permission codes granted
    via the caller's roles *within `organization_id`* (resolved once by
    core/users/), so downstream `authorize()` checks are a pure set-membership
    test with no further database access. `project_permissions` holds any
    project-scoped overrides, keyed by project id (section 3.6) -- a project
    absent from that mapping simply has no override.
    """

    # Frozen: an identity is a fact about who is calling, resolved once at the
    # boundary; nothing downstream should be able to mutate it (e.g. quietly
    # grant itself a permission mid-request).
    model_config = ConfigDict(frozen=True)

    kind: ActorKind
    # The stable principal id: a stringified user UUID, a service name, or an
    # agent name -- whatever uniquely identifies this caller of `kind`.
    subject: str
    # The organization this identity is scoped to. Required, not optional:
    # per PROJECT_PLAN.md section 3.4/3.7, a session -- and therefore every
    # resolved Identity -- belongs to exactly one organization; there is no
    # "global" Identity to construct without one.
    organization_id: uuid.UUID
    # Populated only when kind == USER; None for services/agents that have no
    # `users` row (consistent with DATABASE_DESIGN.md's rationale for tagged
    # actors rather than strict FKs).
    user_id: uuid.UUID | None = None
    display_name: str | None = None
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = Field(default_factory=frozenset)
    # Project-scoped permission overrides (PROJECT_PLAN.md section 3.6):
    # project_id -> permission codes granted *for that project*. Populated by
    # `resolve_identity`; empty here only for callers with no project
    # membership row (see the module docstring).
    project_permissions: dict[uuid.UUID, frozenset[str]] = Field(default_factory=dict)

    @property
    def audit_tag(self) -> str:
        """The `actor` string recorded in incident_timeline / audit_logs / etc.

        e.g. ``user:3f2a...`` or ``agent:postmortem_agent``. Single source of
        that format, so it stays consistent everywhere it is written.
        """
        return f"{self.kind.value}:{self.subject}"

    def has_permission(
        self, permission_code: str, project_id: uuid.UUID | None = None
    ) -> bool:
        """Pure, database-free permission check against the pre-resolved
        set(s).

        With no `project_id`, checks the org-level `permissions` set. With a
        `project_id` that has an explicit override in `project_permissions`,
        checks *only* that project-scoped set -- it does not also fall back to
        `permissions`, since a project override that's intentionally more
        restrictive (e.g. a `viewer`-only project role) must actually be able
        to restrict. A `project_id` with no override falls back to the
        org-level set, per PROJECT_PLAN.md section 3.6.
        """
        if project_id is not None and project_id in self.project_permissions:
            return permission_code in self.project_permissions[project_id]
        return permission_code in self.permissions

    def resolve_search_scope(self) -> tuple[list[uuid.UUID] | None, frozenset[str]]:
        """Resolve `(project_ids, permission_codes)` for a
        `retrieval.schemas.SearchFilters` construction.

        This is the sanctioned place a retrieval/search caller (agents/*)
        turns `project_permissions` into the hard project-level restriction
        `SearchFilters.project_ids` expects -- `retrieval/`'s own module
        docstring is explicit that resolving "which projects can this caller
        see" from an `Identity` is not retrieval's job, so that resolution
        has to happen here, on the object that actually carries the data,
        rather than being re-derived ad hoc at each call site.

        With no project-scoped grants at all, returns `(None, self.
        permissions)` -- `SearchFilters.project_ids=None` means "no
        project-level restriction," preserving the existing, unrestricted
        behavior for the common case of a caller with only org-level
        permissions. With one or more project-scoped grants, returns
        `(list(self.project_permissions.keys()), merged_permissions)`:
        search is restricted to *exactly* the projects the caller has a
        membership row for, and `permission_codes` is widened to include
        every project-scoped permission code alongside the org-level ones
        (safe to widen here, never a leak, because it is always applied
        together with the now-restricted `project_ids` in the same query --
        a permission code granted only for project B cannot surface a
        project A chunk).
        """
        if not self.project_permissions:
            return None, self.permissions
        project_ids = list(self.project_permissions.keys())
        merged_permissions = self.permissions.union(*self.project_permissions.values())
        return project_ids, merged_permissions

    @classmethod  # qki agents bohot saare hai
    def for_agent(cls, agent_name: str, organization_id: uuid.UUID) -> "Identity":
        """Convenience constructor for internal agent-initiated calls.

        Agents act with no interactive user (AGENT_WORKFLOWS.md), but still
        act *within* one organization's scope -- e.g. the organization that
        owns the incident being triaged -- so `organization_id` is required
        here too, keeping agent identity construction consistent with every
        other `Identity` in the system rather than a special-cased exception.
        """
        return cls(
            kind=ActorKind.AGENT, subject=agent_name, organization_id=organization_id
        )
