"""One-time local/dev bootstrap script: creates a fake test organization, a
permission catalog + "admin" role granting every permission code the app
actually checks, a fake test user, assigns that role, and mints a real,
normally-signed access token for that user -- so you can exercise every REST
endpoint through Swagger UI (`/docs`) without needing a real company, real
employees, or a real SSO identity provider account.

Why this is necessary: authentication in this codebase is SSO-only by
design (core/auth/service.py's own module docstring -- there is no
username/password login at all), and there is no seed data anywhere for
organizations/roles/permissions (nothing populates those tables until
something like this script runs). Setting up a real OIDC provider (Entra
ID/Okta/Auth0/Google Workspace) purely to click through a login flow for
local testing is disproportionate for trying the app out. This script
instead calls the exact same building blocks a real SSO login would
eventually call (`core.tenancy.service.create_organization`,
`core.users.service.get_or_create_user`/`assign_role`), plus
`core.auth.service`'s private `_issue_access_token` directly (skipping only
the IdP round-trip) -- the resulting token is a real, normally-signed JWT
that every REST endpoint accepts identically to one minted through a real
login. There is no separate "test mode" in the app itself; this script just
skips the identity-provider handshake.

Safe to re-run: every step is idempotent (existing organization/permissions/
role/user/role-assignment are reused, not duplicated), so running this again
just mints a fresh access token once your first one expires
(`jwt_expiry_minutes`, default 60).

Run: python scripts/seed_test_organization.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.auth.service import _issue_access_token
from app.core.exceptions import ConflictError
from app.core.tenancy import repository as tenancy_repository
from app.core.tenancy import service as tenancy_service
from app.core.tenancy.schemas import Organization, OrganizationCreate
from app.core.users import service as users_service
from app.database.models.core_models import Permission, Role, RolePermission
from app.database.session import session_scope, set_tenant_context
from app.shared.config.logging import configure_logging
from app.shared.config.tracing import configure_tracing

configure_logging()
configure_tracing()

# Every permission code actually checked anywhere in the app today (grep for
# `require_permission(actor, "...")` / `_..._PERMISSION = "..."` across app/).
# There is no seed migration for these -- nothing exists in `permissions`
# until a script like this one creates it.
_ALL_PERMISSION_CODES = [
    "tenancy:manage",
    "incident:write",
    "incident:read",  # 2026-08 audit "H4"
    "postmortem:write",
    "postmortem:approve",
    "postmortem:read",  # 2026-08 audit "H4"
    "knowledge:review",
    "observability:read",
]

_TEST_ORG_NAME = "Test Org"
_TEST_ORG_SLUG = "test-org"
_TEST_USER_EMAIL = "student@test-org.example"
_TEST_USER_DISPLAY_NAME = "Test Student"
_ADMIN_ROLE_NAME = "admin"


async def _get_or_create_organization(session) -> Organization:
    try:
        organization = await tenancy_service.create_organization(
            session, OrganizationCreate(name=_TEST_ORG_NAME, slug=_TEST_ORG_SLUG)
        )
        print(f"Created organization: {organization.id} ({organization.slug})")
        return organization
    except ConflictError:
        org_row = await tenancy_repository.get_organization_by_slug(session, _TEST_ORG_SLUG)
        if org_row is None:
            raise  # genuinely unexpected -- the slug conflict said it exists
        organization = Organization.model_validate(org_row)
        print(f"Organization already exists: {organization.id} ({organization.slug})")
        return organization


async def _get_or_create_permissions(session, codes: list[str]) -> list[Permission]:
    result = await session.execute(select(Permission).where(Permission.code.in_(codes)))
    existing = {row.code: row for row in result.scalars().all()}

    permissions = []
    for code in codes:
        if code in existing:
            permissions.append(existing[code])
            continue
        permission = Permission(code=code, description=f"Seeded for local testing: {code}")
        session.add(permission)
        permissions.append(permission)
    await session.flush()
    return permissions


async def _get_or_create_admin_role(session, permissions: list[Permission]) -> Role:
    result = await session.execute(select(Role).where(Role.name == _ADMIN_ROLE_NAME))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(
            name=_ADMIN_ROLE_NAME,
            description="Seeded local-testing role granting every permission code.",
        )
        session.add(role)
        await session.flush()

    granted = await session.execute(
        select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
    )
    already_granted = set(granted.scalars().all())
    for permission in permissions:
        if permission.id not in already_granted:
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await session.flush()
    return role


async def main() -> None:
    async with session_scope() as session:
        organization = await _get_or_create_organization(session)

        permissions = await _get_or_create_permissions(session, _ALL_PERMISSION_CODES)
        role = await _get_or_create_admin_role(session, permissions)
        print(f"Admin role ready: {role.id} (granting {len(permissions)} permissions)")

        user_id = await users_service.get_or_create_user(
            session, email=_TEST_USER_EMAIL, display_name=_TEST_USER_DISPLAY_NAME
        )
        print(f"Test user ready: {user_id} ({_TEST_USER_EMAIL})")

        # `user_roles` is RLS-protected (Milestone 10) -- this insert needs
        # the session's tenant GUC set first, the same way every real
        # request-handling chokepoint (app.api.deps.get_current_identity,
        # app.mcp.dispatch.run_mcp_tool) sets it after resolving an Identity.
        # This script has no Identity yet -- that's what it's bootstrapping
        # -- so it sets the GUC directly using the organization id already
        # created/resolved above.
        await set_tenant_context(session, organization.id)
        await users_service.assign_role(
            session, user_id=user_id, organization_id=organization.id, role_id=role.id
        )
        print("Assigned admin role to test user in test organization.")

        access_token, issued_at, expires_at = _issue_access_token(user_id, organization.id)

    print("\n--- Access token -- paste into Swagger UI's Authorize button as: Bearer <token> ---")
    print(access_token)
    print(f"\nIssued at:  {issued_at.isoformat()}")
    print(f"Expires at: {expires_at.isoformat()}")
    print(f"\nOrganization ID: {organization.id}")
    print(f"User ID:         {user_id}")


if __name__ == "__main__":
    asyncio.run(main())
