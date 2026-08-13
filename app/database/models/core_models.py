"""SQLAlchemy models for tables owned by core/.

Owned by: database/ (ARCHITECTURE.md section 3 -- database/ holds every
table's definition, but per DATABASE_DESIGN.md's ownership convention, only
core/'s repository.py files are permitted to write to these tables; other
modules read through core/'s public interface, never by importing these
models directly).

Tables here match DATABASE_DESIGN.md's "core/ -- owned tables" section:
users, roles, permissions, role_permissions, user_roles, incidents,
incident_timeline, postmortems, audit_logs.

Multi-tenancy (PROJECT_PLAN.md section 3): every table that stores data
belonging to a specific company carries `organization_id`. Deliberate
exception -- `users`: a user is a global person record, not pinned to one
company. Which company a person belongs to, and with what role, is recorded
on `UserRole` instead (organization_id lives there, not here) -- this is what
lets one person hold a different role in two different companies
(PROJECT_PLAN.md section 3.5), which a column directly on `users` could not
express. `roles` and `permissions` also stay global: they are a fixed,
platform-defined catalog shared by every company (PROJECT_PLAN.md section
3.5), not something each company customizes.

Foreign keys to `organizations.id` / `projects.id` use RESTRICT, matching
this file's existing convention (see Postmortem.incident_id) of defaulting
to RESTRICT so deleting a company can never silently cascade-delete its
incident/audit history -- that has to be a deliberate, separate operation,
not a side effect.

Requires the pgcrypto extension (for gen_random_uuid()) -- enabled in the
first Alembic migration, not here.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class User(Base):
    """A person who can authenticate and act within EKIP.

    Deliberately has no `organization_id` -- see the module docstring.
    A user's company membership(s) and role(s) live on `UserRole`.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Set only for an account created via email/password signup
    #: (`core.auth.service.signup`) -- an SSO-provisioned user has no local
    #: credential at all, so this stays `NULL` for them; `login_with_password`
    #: treats a `NULL` hash as "not a password-auth account" and rejects the
    #: same generic way it rejects a wrong password (no user enumeration).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Role(Base):
    """A named role, e.g. `engineer` or `incident_commander`.

    A fixed, platform-wide catalog shared by every company -- not customer-
    editable in the MVP (PROJECT_PLAN.md section 3.5). Grants zero or more
    `Permission`s via `RolePermission`; assigned to a user *within one
    company* via `UserRole`.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Permission(Base):
    """A single grantable capability, e.g. `incident:write`.

    Also a fixed, platform-wide catalog. Permission codes are the vocabulary
    `core/users`'s `authorize()` checks against -- the same codes apply
    whether the caller entered via REST or MCP (ARCHITECTURE.md section 6).
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(Base):
    """Join table: role <-> permission. Composite PK, per DATABASE_DESIGN.md.

    Catalog-level (role grants permission), not scoped to a company -- the
    company-specific fact is "this user holds this role here," which is
    `UserRole`'s job, not this table's.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    """Join table: a user holds a role *within one specific company*.

    This is where multi-tenancy actually attaches to identity
    (PROJECT_PLAN.md section 3.5): the composite key is now
    `(user_id, organization_id, role_id)`, not just `(user_id, role_id)` --
    so the same person can hold different roles in different companies. FK to
    `organizations.id` uses RESTRICT, matching this file's convention: a
    company's role assignments should never silently vanish as a side effect
    of some other delete.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        Index("ix_user_roles_organization_id", "organization_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )


class Incident(Base):
    """A single incident record -- the transactional heart of `core/`.

    Now carries both `organization_id` (which company) and `project_id`
    (which team within that company) -- per PROJECT_PLAN.md section 3.2,
    every incident belongs to exactly one project, even for small companies
    that never bother creating more than the auto-created default one.

    Indexes are re-shaped so `organization_id` leads every composite index:
    every real query filters by company first (PROJECT_PLAN.md section 3.7 --
    tenant isolation is enforced at the query level, not applied afterward),
    so a leading `organization_id` column serves both "just this company" and
    "this company plus a status/severity/recency filter" queries without
    needing a separate single-column index as well.
    """

    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_org_status", "organization_id", "status"),
        Index("ix_incidents_org_severity", "organization_id", "severity"),
        Index(
            "ix_incidents_org_created_at_desc",
            "organization_id",
            "created_at",
            postgresql_using="btree",
        ),
        # Note: DATABASE_DESIGN.md also calls for an optional GIN trigram
        # index on title/description "if lexical incident search is needed
        # independent of the vector store" -- deferred, since that's a
        # conditional/future need, not a day-one requirement. Add via a
        # dedicated migration (`CREATE EXTENSION pg_trgm; CREATE INDEX ...
        # USING gin (title gin_trgm_ops)`) if/when that need materializes.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # open/investigating/resolved/closed
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    owner_team: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    timeline: Mapped[list["IncidentTimeline"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class IncidentTimeline(Base):
    """One chronological entry (note, status change, evidence) on an incident.

    Carries its own `organization_id` (matching its parent incident's)
    rather than relying on a join back to `incidents` -- this is a defense-
    in-depth choice (PROJECT_PLAN.md section 3.1): a database-level Row-Level
    Security policy on this table can check `organization_id` directly,
    without needing to reach into another table to enforce isolation.
    """

    __tablename__ = "incident_timeline"
    __table_args__ = (
        Index("ix_incident_timeline_incident_occurred", "incident_id", "occurred_at"),
        Index("ix_incident_timeline_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Tagged string, e.g. "user:<id>" or "agent:<agent_name>" -- deliberately
    # not an FK; see DATABASE_DESIGN.md's rationale (human-vs-AI authorship
    # must stay unambiguous at the query level, and agents have no users row).
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped["Incident"] = relationship(back_populates="timeline")


class Postmortem(Base):
    """A postmortem report tied to one incident, gated by human review.

    Carries its own `organization_id` for the same reason as
    `IncidentTimeline` -- a direct column an RLS policy can check, rather
    than a join back to `incidents`.
    """

    __tablename__ = "postmortems"
    __table_args__ = (
        Index("ix_postmortems_incident_id", "incident_id"),
        Index("ix_postmortems_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)  # draft/in_review/approved/published
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # "agent:postmortem_agent" or "user:<id>" -- same human/AI tagging
    # convention as incident_timeline.actor, per DATABASE_DESIGN.md.
    generated_by: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    """Append-only. No updates, no deletes, ever -- enforced by convention in
    core/audit/'s repository.py (the only module permitted to write here),
    not by anything at the ORM level.

    `organization_id` is nullable, unlike every other table here -- flagged
    as an open item, not an oversight: it's not yet decided whether a future
    platform-admin action (one not scoped to any single company) needs to
    write an audit row with no company attached. Every audit event core/
    currently produces (incident.create, postmortem.approve, etc.) *is*
    company-scoped, so this may end up NOT NULL once that question is
    settled -- see PROJECT_PLAN.md section 12.7 for the related open item.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_org_occurred_at_desc", "organization_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Mapped as `event_metadata` in Python because `metadata` is reserved by
    # SQLAlchemy's declarative Base; the actual Postgres column is still
    # named `metadata`, matching DATABASE_DESIGN.md's schema at the SQL level.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )