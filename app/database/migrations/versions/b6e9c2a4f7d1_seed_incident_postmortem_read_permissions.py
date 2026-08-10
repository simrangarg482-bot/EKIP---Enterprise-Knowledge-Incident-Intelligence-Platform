"""seed incident:read / postmortem:read permissions and backfill every
existing role

Revision ID: b6e9c2a4f7d1
Revises: f4a7c2e9b3d1
Create Date: 2026-08-08 00:00:00.000000

Confirmed gap (2026-08 audit "H4"; see `app.core.incidents.service`'s module
docstring for the corresponding code change): `get_incident`, `list_incidents`,
`get_timeline`, and `get_postmortem` previously checked only same-organization
membership (`_ensure_same_organization`) -- any identity with zero role
assignments at all (a real, supported state per `core.users.service.
resolve_identity`'s own docstring) could still read every incident and
postmortem in the organization, including sensitive root-cause/evidence
detail. `observability:read`/`knowledge:review` already gate other read
surfaces in this same codebase, so `incident:read`/`postmortem:read` close the
one resource group that was missing the pattern.

This migration is the reason the code change is safe to deploy rather than an
instant lockout: per `app.database.models.core_models`'s own module docstring,
`permissions`/`roles`/`role_permissions` are a "fixed, platform-wide catalog"
in intent, but in practice **no migration before this one has ever seeded or
backfilled them** -- every row in those three tables today exists only because
some operator's bootstrap script (`scripts/seed_test_organization.py`,
`scripts/realworld_onboarding/common/bootstrap.py`) inserted it directly.
Permission checks are purely dynamic against whatever `role_permissions` rows
already exist at request time (`core.users.service.resolve_identity` ->
`repository.get_permission_codes`) -- there is no "permission code doesn't
exist yet" special case, and no default/backfill mechanism other than this
kind of migration. Concretely: the instant `get_incident` starts calling
`require_project_permission(actor, project_id, "incident:read")`, *every*
identity resolved from *every* existing role -- there is no fixed "admin"/
"member" role name this codebase can special-case, since roles are created ad
hoc per company (see `bootstrap.py`'s own module docstring) -- would fail that
check unless a `role_permissions` row for it already exists.

The fix: insert both new `Permission` rows (idempotent on `code`), then grant
them to **every role that already exists**, not a curated subset -- this is
the only backward-compatible choice available, since preserving "whatever
read access an existing identity already had" is this migration's entire
purpose, and there is no data anywhere that distinguishes "a role that should
keep incident/postmortem read access" from "a role that shouldn't" (that
distinction did not exist before this migration, because the read was
unconditional). A company wanting a genuinely read-restricted role from this
point forward creates one without these grants, going forward -- this
migration only protects already-provisioned data, it does not weaken the new
gate's ability to restrict *future* roles.

`permissions`/`roles`/`role_permissions` are excluded from Row-Level Security
(`c7d4e8f19a2b_milestone_10_row_level_security.py`'s own "genuinely global
catalogs" list) -- no `set_tenant_context`/GUC call is needed here, matching
that migration's own treatment of these three tables.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6e9c2a4f7d1'
down_revision: str | None = 'f4a7c2e9b3d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_PERMISSIONS: tuple[tuple[str, str], ...] = (
    (
        "incident:read",
        "Read an incident, its timeline, and its postmortem (2026-08 audit 'H4').",
    ),
    (
        "postmortem:read",
        "Read a postmortem report (2026-08 audit 'H4').",
    ),
)
_NEW_PERMISSION_CODES = tuple(code for code, _description in _NEW_PERMISSIONS)


def upgrade() -> None:
    bind = op.get_bind()

    # `permissions.id` has a `gen_random_uuid()` server default -- no need to
    # supply one here. `ON CONFLICT (code) DO NOTHING` makes this safe to
    # re-run against a database that already has these codes (e.g. a
    # re-applied deployment), matching the idempotency discipline
    # `f4a7c2e9b3d1`'s role-provisioning `DO $$ ... IF NOT EXISTS` block
    # already established for this same "may run more than once" concern.
    for code, description in _NEW_PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (code, description) "
                "VALUES (:code, :description) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )

    # Grant both new codes to EVERY existing role -- see module docstring for
    # why this (not a curated subset) is the only backward-compatible choice:
    # preserving already-provisioned identities' pre-existing, unconditional
    # read access is this migration's entire purpose.
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r "
            "CROSS JOIN permissions p "
            "WHERE p.code IN :codes "
            "ON CONFLICT DO NOTHING"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(_NEW_PERMISSION_CODES)},
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN ("
            "SELECT id FROM permissions WHERE code IN :codes"
            ")"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(_NEW_PERMISSION_CODES)},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list(_NEW_PERMISSION_CODES)},
    )
