"""The one place in this whole harness that does NOT go through the REST
API. Read this docstring before using anything in this module.

WHY THIS FILE HAS TO EXIST (a real, verified gap in EKIP today, not an
assumption): calling `POST /organizations` -- and every other tenancy-admin
endpoint -- requires an already-authenticated `CurrentIdentity`
(`app/api/deps.py`). EKIP has no anonymous, public self-registration
endpoint at all. Worse: even once an organization is created,
`core.tenancy.service.create_organization` does not grant the calling actor
(or anyone) any role in the new organization -- nobody has `tenancy:manage`
there, so `configure_sso`/`create_invitation`/`create_access_rule` are all
*also* unreachable via REST immediately after registration. And there is
no REST or MCP endpoint anywhere in this codebase that creates a `Role`,
creates a `Permission`, or grants a permission to a role -- confirmed by
reading `app/core/users/service.py` and every router under `app/api/
routers/` directly; the only code in the whole repository that ever does
this is `scripts/seed_test_organization.py`, which does it with raw ORM
access for one hardcoded test organization.

In short: a genuinely brand-new EKIP customer, with zero prior identity in
the system, cannot self-service their way to a first administrator account
through any API this project currently exposes. Someone -- a human
operator, or a script exactly like this one -- has to seed that first
identity directly against the database, the same way
`scripts/seed_test_organization.py` already does for local dev testing.

This module generalizes that exact, existing, unmodified pattern (same
imports, same functions: `core.tenancy.service.create_organization`,
`core.users.service.get_or_create_user` / `assign_role`,
`core.auth.service._issue_session`) so this harness can mint a real,
normally-signed session (access token AND a real, persisted refresh token)
for any number of named personas, each with a distinct role/permission set,
instead of the seed script's single hardcoded "admin" user. Every token
minted this way is indistinguishable, to every REST/MCP endpoint that
verifies it, from one issued through a genuine SSO login -- it is real
output of the project's own signing code, not a forged double.

Once this gap is closed by a real product feature (a genuine self-service
signup endpoint, and a role/permission-management API), this whole module
becomes unnecessary and `01_register_org.py` / `02_bootstrap_org_admin.py`
can be rewritten to call it directly instead.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.auth.schemas import SessionTokens  # noqa: E402
from app.core.auth.service import _issue_session  # noqa: E402
from app.core.exceptions import ConflictError  # noqa: E402
from app.core.tenancy import repository as tenancy_repository  # noqa: E402
from app.core.tenancy import service as tenancy_service  # noqa: E402
from app.core.tenancy.schemas import Organization, OrganizationCreate  # noqa: E402
from app.core.users import service as users_service  # noqa: E402
from app.database.models.core_models import Permission, Role, RolePermission  # noqa: E402
from app.database.session import engine, session_scope, set_tenant_context  # noqa: E402

# The complete, real permission catalog -- every code actually checked
# anywhere in the app today (grep for `require_permission(actor, "..."`) /
# `_..._PERMISSION = "..."` across app/). There is no seed migration for
# these; nothing exists in the `permissions` table until a script like this
# one (or `seed_test_organization.py`) creates it.
ALL_PERMISSION_CODES = [
    "tenancy:manage",
    "incident:write",
    "incident:read",  # 2026-08 audit "H4"
    "postmortem:write",
    "postmortem:approve",
    "postmortem:read",  # 2026-08 audit "H4"
    "knowledge:review",
    "observability:read",
]

# Five realistic personas, matching the task's requested roster, each with a
# distinct permission set chosen specifically so permission-matrix testing
# has real allow/deny contrast between them. `incident:read`/`postmortem:read`
# (2026-08 audit "H4") are added here for every persona that could already
# read incidents/postmortems before that fix (which was every persona except
# a hypothetical bare zero-permission identity, since the read was previously
# unconditional) -- this harness's job is to model realistic, already-
# functioning personas, not to exercise the new gate's deny path (see
# `tests/core/incidents/test_service.py` for that).
PERSONAS: dict[str, list[str]] = {
    "admin": list(ALL_PERMISSION_CODES),
    "security_engineer": [
        "incident:write",
        "incident:read",
        "postmortem:write",
        "postmortem:read",
        "observability:read",
    ],
    "developer": ["incident:write", "incident:read"],
    "manager": [
        "postmortem:approve",
        "postmortem:read",
        "incident:read",
        "knowledge:review",
        "observability:read",
    ],
    "read_only": ["observability:read", "incident:read", "postmortem:read"],
}


async def get_or_create_organization(session, name: str, slug: str) -> Organization:
    """Create the organization via the project's real service function
    (this is the same function `POST /organizations` calls -- see
    01_register_org.py for the REST-facing version of this same operation).
    Idempotent: re-running against an already-created slug returns the
    existing organization instead of erroring.
    """
    try:
        return await tenancy_service.create_organization(session, OrganizationCreate(name=name, slug=slug))
    except ConflictError:
        row = await tenancy_repository.get_organization_by_slug(session, slug)
        if row is None:
            raise
        return Organization.model_validate(row)


async def _ensure_permissions(session, codes: Iterable[str]) -> dict[str, Permission]:
    codes = list(codes)
    result = await session.execute(select(Permission).where(Permission.code.in_(codes)))
    existing = {row.code: row for row in result.scalars().all()}
    for code in codes:
        if code not in existing:
            row = Permission(code=code, description=f"Seeded by realworld_onboarding harness: {code}")
            session.add(row)
            existing[code] = row
    await session.flush()
    return existing


async def ensure_role(session, name: str, permission_codes: Iterable[str]) -> Role:
    """Get-or-create a role by name and make sure it grants exactly the
    given permission codes (adds any missing grants; never removes one that
    already exists, so re-running this is always safe).
    """
    permissions = await _ensure_permissions(session, permission_codes)

    result = await session.execute(select(Role).where(Role.name == name))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(name=name, description=f"Seeded by realworld_onboarding harness: {name}")
        session.add(role)
        await session.flush()

    granted = await session.execute(select(RolePermission.permission_id).where(RolePermission.role_id == role.id))
    already_granted = set(granted.scalars().all())
    for permission in permissions.values():
        if permission.id not in already_granted:
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await session.flush()
    return role


async def ensure_all_persona_roles(session) -> dict[str, Role]:
    """Get-or-create all five roles in `PERSONAS` in one call."""
    return {name: await ensure_role(session, name, codes) for name, codes in PERSONAS.items()}


async def ensure_user_with_role(
    session, *, email: str, display_name: str, organization_id: uuid.UUID, role_id: uuid.UUID
) -> uuid.UUID:
    """Get-or-create a user and make sure they hold `role_id` in
    `organization_id`. Sets the RLS tenant GUC first (`user_roles` is
    RLS-protected as of Milestone 10) -- this script has no `Identity` yet
    (it's bootstrapping one), so it sets the GUC directly using the already-
    known `organization_id`, exactly like `seed_test_organization.py` does.
    """
    user_id = await users_service.get_or_create_user(session, email=email, display_name=display_name)
    await set_tenant_context(session, organization_id)
    await users_service.assign_role(session, user_id=user_id, organization_id=organization_id, role_id=role_id)
    return user_id


async def issue_real_session(session, *, user_id: uuid.UUID, organization_id: uuid.UUID) -> SessionTokens:
    """Mint a real access token + a real, persisted refresh token for
    `user_id`, using the project's own `_issue_session` -- identical output
    to what a genuine SSO login produces. A fresh `family_id` is used, same
    as a first-time login (as opposed to `refresh`, which carries the
    family forward).
    """
    return await _issue_session(session, user_id=user_id, organization_id=organization_id, family_id=uuid.uuid4())


async def bootstrap_persona(
    *, organization_name: str, organization_slug: str, persona: str, email: str, display_name: str
) -> dict:
    """One-shot convenience: ensure the organization exists, ensure the RBAC
    catalog + all five persona roles exist, ensure this specific user holds
    `persona`'s role in that organization, and mint them a real session.

    Returns a plain dict (not a dataclass) so callers can `json.dumps` it
    straight into `common.state` without a custom encoder.
    """
    if persona not in PERSONAS:
        raise ValueError(f"Unknown persona {persona!r}. Known personas: {sorted(PERSONAS)}")

    async with session_scope() as session:
        organization = await get_or_create_organization(session, organization_name, organization_slug)
        roles = await ensure_all_persona_roles(session)
        role = roles[persona]
        user_id = await ensure_user_with_role(
            session, email=email, display_name=display_name, organization_id=organization.id, role_id=role.id
        )
        tokens = await issue_real_session(session, user_id=user_id, organization_id=organization.id)

    return {
        "organization_id": str(organization.id),
        "organization_slug": organization.slug,
        "user_id": str(user_id),
        "email": email,
        "persona": persona,
        "role_id": str(role.id),
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_in": tokens.expires_in,
    }


async def set_user_active(user_id: uuid.UUID, *, is_active: bool) -> None:
    """Flip a user's `is_active` flag directly (there is no REST/MCP API
    to deactivate a user account anywhere in this codebase -- confirmed
    by reading every router; this is another instance of the same class
    of gap this module's own docstring describes for role/permission
    management). Used only by `09_negative_tests.py` to construct a
    disposable "disabled user" fixture and confirm
    `core.users.service.resolve_identity` really does reject them
    (`PermissionDeniedError`, error_code "user.inactive") -- never used
    against a real customer's data.
    """
    from app.database.models.core_models import User

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError(f"No such user: {user_id}")
        user.is_active = is_active


_shared_loop: asyncio.AbstractEventLoop | None = None


def _run(coro):
    """Run `coro` on ONE persistent event loop, reused for the lifetime of
    this process -- deliberately NOT `asyncio.run(...)` per call.

    Why this matters: `app.database.session`'s async engine is a
    module-level singleton (imported once, cached in `sys.modules`), and
    its pooled `asyncpg` connections are bound to whichever event loop was
    running when they were opened. `asyncio.run()` creates a brand-new
    loop and destroys it again on every call -- call it more than once in
    one process (exactly what happens here, since every numbered script in
    this harness that seeds more than one persona, and the master
    orchestrator across every stage, calls `bootstrap_persona_sync`/
    `set_user_active_sync` repeatedly) and the pool eventually tries to
    reuse or close a connection tied to an already-closed loop, raising
    "RuntimeError: Event loop is closed" -- intermittently, depending on
    which pooled connection happens to get recycled when. Sharing one loop
    for every async DB call this harness ever makes in a given process
    keeps the engine's connections valid for as long as that process runs.
    """
    global _shared_loop
    if _shared_loop is None or _shared_loop.is_closed():
        _shared_loop = asyncio.new_event_loop()
    return _shared_loop.run_until_complete(coro)


def bootstrap_persona_sync(
    *, organization_name: str, organization_slug: str, persona: str, email: str, display_name: str
) -> dict:
    """Synchronous wrapper around `bootstrap_persona`, for the (otherwise
    fully synchronous, `httpx.Client`-based) numbered scripts in this
    harness to call without needing to be `async def` themselves. Safe to
    call any number of times in one process -- see `_run`'s docstring.
    """
    return _run(
        bootstrap_persona(
            organization_name=organization_name,
            organization_slug=organization_slug,
            persona=persona,
            email=email,
            display_name=display_name,
        )
    )


def set_user_active_sync(user_id: uuid.UUID, *, is_active: bool) -> None:
    """Synchronous wrapper around `set_user_active`. Uses the same shared
    event loop as `bootstrap_persona_sync` (see `_run`'s docstring) --
    `09_negative_tests.py` calls this AFTER several `bootstrap_persona_sync`
    calls in the same process, so it must reuse the same loop rather than
    starting a fresh one, for exactly the same reason.
    """
    _run(set_user_active(user_id, is_active=is_active))


def dispose_shared_loop() -> None:
    """Optional, best-effort cleanup: dispose of the project's async
    engine's connection pool and close this harness's shared event loop.

    Not needed for correctness -- every PASS/FAIL result is already final
    by the time anything calls this. It only prevents cosmetic noise
    (asyncpg/SQLAlchemy sometimes print a "Task was destroyed but it is
    pending" or "Event loop is closed" warning during Python's own
    interpreter-shutdown garbage collection if pooled connections are left
    open when the process exits) that could otherwise look, at a glance,
    like the same bug this module just fixed. Safe to call even if nothing
    in this module ever ran (no-op). `99_master_e2e.py` calls this once, at
    the very end, after its combined summary has already printed; the
    individual numbered scripts don't bother, since a short-lived
    single-stage process exiting a moment later is harmless either way.
    """
    global _shared_loop
    if _shared_loop is not None and not _shared_loop.is_closed():
        _shared_loop.run_until_complete(engine.dispose())
        _shared_loop.close()
    _shared_loop = None
