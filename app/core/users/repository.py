"""Persistence for core/users -- users, roles, permissions.

Owned by: core/users. Pure data access: each function issues one query and
returns ORM rows or plain scalar values; identity assembly and authorization
decisions live in service.py.

The two resolution queries (`get_role_names`, `get_permission_codes`) are the
performance-relevant heart of RBAC: they let the service resolve an identity
in two small indexed queries once per request, so every downstream
`authorize()` is then a pure in-memory set check with no further DB hits.

Multi-tenancy (PROJECT_PLAN.md section 3.5): role assignment is scoped per
organization -- `UserRole`'s primary key is `(user_id, organization_id,
role_id)`, not just `(user_id, role_id)`, because the same person can hold
different roles in different companies. Both resolution queries below filter
by `organization_id` as well as `user_id` accordingly: omitting that filter
would resolve (and leak) a user's roles/permissions across every organization
they belong to, not just the one their session is scoped to.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.core_models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.database.models.tenancy_models import Project, ProjectMembership

#: Every permission code a self-service signup's bootstrap "admin" role
#: grants -- the same fixed list `scripts/seed_test_organization.py`'s
#: dev-only bootstrap has always used (kept in sync by hand; both exist to
#: grant "everything this app currently checks," not a design each should
#: independently decide). Not a `Settings` field: this is what "admin"
#: *means* in this codebase today, not a deployment-time configuration.
ADMIN_PERMISSION_CODES: Sequence[str] = (
    "tenancy:manage",
    "incident:write",
    "postmortem:write",
    "postmortem:approve",
    "knowledge:review",
    "observability:read",
)


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Fetch a single user by primary key, or None if absent."""
    return await session.get(User, user_id)


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    """Fetch a single user by their unique email, or None if absent.

    Used by core/auth during credential resolution (login looks a user up by
    email before issuing a token).
    """
    stmt = select(User).where(User.email == email)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_role_names(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> Sequence[str]:
    """Return the names of the roles assigned to `user_id` *within
    `organization_id`*.

    Joins `user_roles -> roles`, filtered by both `user_id` and
    `organization_id` -- a role assignment held in a different organization
    must never be returned (PROJECT_PLAN.md section 3.5). Empty sequence if
    the user has no roles in this organization.
    """
    stmt = (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            UserRole.organization_id == organization_id,
        )
        .order_by(Role.name)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def insert_user(session: AsyncSession, *, email: str, display_name: str) -> User:
    """Create a new user row and return it with server defaults populated.

    Called only after a decision to allow provisioning has already been made
    elsewhere (core/tenancy's `evaluate_provisioning`) -- this function
    performs no authorization or policy check of its own; core/users manages
    identity/roles, not who may join an organization.
    """
    row = User(email=email, display_name=display_name)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    """Fetch a single role by its unique name, or None if absent.

    Used by core/tenancy to resolve a role *name* (e.g. `"engineer"`, as
    supplied in an `AccessRuleCreate`/`InvitationCreate` request) into the
    `grants_role_id` actually stored -- a read-only reference-data lookup, not
    a tenant-scoped query, since the `roles` catalog itself is global
    (DATABASE_DESIGN.md).
    """
    stmt = select(Role).where(Role.name == name)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_role(
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role_id: uuid.UUID,
) -> UserRole | None:
    """Fetch one (user, organization, role) assignment row, or None if it
    doesn't exist -- lets callers make role assignment idempotent by checking
    first, rather than relying on catching an integrity error.
    """
    return await session.get(UserRole, (user_id, organization_id, role_id))


async def insert_user_role(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role_id: uuid.UUID,
) -> None:
    """Assign `role_id` to `user_id` within `organization_id`.

    Not idempotent by itself -- inserting a duplicate composite key raises an
    integrity error. Callers are expected to check `get_user_role` first
    (the same "check, then act" pattern used elsewhere in this module, e.g.
    `resolve_identity`'s `is_active` check) rather than this function
    silently swallowing a conflict.
    """
    session.add(UserRole(user_id=user_id, organization_id=organization_id, role_id=role_id))
    await session.flush()


async def get_permission_codes(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> set[str]:
    """Return the flattened set of permission codes granted to `user_id`
    *within `organization_id`*.

    Joins `user_roles -> role_permissions -> permissions`, filtered by both
    `user_id` and `organization_id`, and de-duplicates: two roles granting the
    same permission collapse to one code. This set is what the service loads
    into `Identity.permissions` -- scoped to a single organization, never
    merged across every organization a user might belong to.
    """
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(
            UserRole.user_id == user_id,
            UserRole.organization_id == organization_id,
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def get_project_permission_map(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> dict[uuid.UUID, frozenset[str]]:
    """Return `user_id`'s permission codes, grouped by project, for every
    project *within `organization_id`* they hold a `project_memberships` row
    for (PROJECT_PLAN.md section 3.6's project-level authorization tier --
    `Identity.project_permissions`).

    Joins `project_memberships -> projects -> role_permissions -> permissions`,
    filtered by `user_id` and `projects.organization_id` (not
    `project_memberships.organization_id` -- that column doesn't exist;
    `project_memberships` has no organization_id of its own, only a
    `project_id`, so scoping to this organization has to go through
    `projects` the same way `core.tenancy.service.register_connector`
    already validates a submitted `project_id` against its owning
    organization). A project the user has no membership row for is simply
    absent from the returned dict -- `Identity.has_permission`'s own
    docstring already treats "no entry for this project" as "fall back to
    org-level `permissions`", so there is no need to return an empty
    frozenset placeholder for every project in the organization.

    One query, grouped in Python rather than issued once per project: this
    mirrors `get_permission_codes`'s single-query-per-identity-resolution
    shape, so populating `Identity.project_permissions` in `resolve_identity`
    costs exactly one more indexed query, not N.
    """
    stmt = (
        select(ProjectMembership.project_id, Permission.code)
        .join(Project, Project.id == ProjectMembership.project_id)
        .join(RolePermission, RolePermission.role_id == ProjectMembership.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            ProjectMembership.user_id == user_id,
            Project.organization_id == organization_id,
        )
        .distinct()
    )
    result = await session.execute(stmt)

    grouped: dict[uuid.UUID, set[str]] = {}
    for project_id, code in result.all():
        grouped.setdefault(project_id, set()).add(code)
    return {project_id: frozenset(codes) for project_id, codes in grouped.items()}


async def update_password_hash(session: AsyncSession, *, user_id: uuid.UUID, password_hash: str) -> None:
    """Set (or replace) `user_id`'s local password credential.

    Used only by `core.auth.service.signup` -- keeps the "only core/users's
    repository writes `users` rows" discipline intact rather than having
    core/auth reach into this table directly.
    """
    row = await session.get(User, user_id)
    if row is None:
        raise ValueError(f"update_password_hash: no such user {user_id}")  # unreachable in practice
    row.password_hash = password_hash
    await session.flush()


async def get_first_organization_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """Return one organization `user_id` holds a role in, or `None` if they
    hold none anywhere.

    Used by `core.auth.service.login_with_password`: a password-auth account
    is created by `signup` with exactly one role assignment (in the
    organization signup itself created), so "first" is unambiguous today --
    this does not attempt to support a password-auth user who has since
    joined a second organization some other way, which isn't a flow this
    codebase builds yet (see `core.auth.service.signup`'s own docstring).
    """
    stmt = select(UserRole.organization_id).where(UserRole.user_id == user_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_permissions(session: AsyncSession, codes: Sequence[str]) -> list[Permission]:
    """Fetch existing `Permission` rows for `codes`, inserting any missing
    ones -- the same "populate the fixed catalog if it isn't there yet"
    behavior `scripts/seed_test_organization.py`'s dev bootstrap already
    relies on, now reusable by real signup instead of only a dev script.
    """
    result = await session.execute(select(Permission).where(Permission.code.in_(codes)))
    existing = {row.code: row for row in result.scalars().all()}

    permissions = []
    for code in codes:
        if code in existing:
            permissions.append(existing[code])
            continue
        permission = Permission(code=code, description=f"Seeded by signup bootstrap: {code}")
        session.add(permission)
        permissions.append(permission)
    await session.flush()
    return permissions


async def get_or_create_role_by_name(session: AsyncSession, name: str, *, description: str) -> Role:
    """Fetch the `Role` named `name`, creating it (with `description`) if it
    doesn't exist yet. Idempotent -- safe to call on every signup, not just
    the first.
    """
    existing = await get_role_by_name(session, name)
    if existing is not None:
        return existing

    role = Role(name=name, description=description)
    session.add(role)
    await session.flush()
    return role


async def grant_permissions_to_role(
    session: AsyncSession, *, role_id: uuid.UUID, permissions: Sequence[Permission]
) -> None:
    """Grant every permission in `permissions` to `role_id`, skipping any
    already granted -- idempotent, mirroring `insert_user_role`'s sibling
    `get_user_role`-then-`insert_user_role` pattern rather than relying on a
    database-constraint-driven retry.
    """
    granted = await session.execute(
        select(RolePermission.permission_id).where(RolePermission.role_id == role_id)
    )
    already_granted = set(granted.scalars().all())
    for permission in permissions:
        if permission.id not in already_granted:
            session.add(RolePermission(role_id=role_id, permission_id=permission.id))
    await session.flush()
