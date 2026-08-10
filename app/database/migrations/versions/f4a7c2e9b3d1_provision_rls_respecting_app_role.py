"""provision a dedicated, non-superuser/non-bypassrls application role

Revision ID: f4a7c2e9b3d1
Revises: e3f6a1b8d4c9
Create Date: 2026-08-08 00:00:00.000000

Confirmed/strongly-suspected finding (2026-08 audit "C2";
EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md recommendation #2): this project's
`DATABASE_URL` has, in every environment observed so far, connected as
`neondb_owner` -- Neon's default database-owner role for a freshly-created
project. Migration c7d4e8f19a2b's `FORCE ROW LEVEL SECURITY` makes RLS apply
even to a table's *owner*, but that has no effect on a role carrying the
`BYPASSRLS` attribute, and none at all on an actual Postgres superuser --
Postgres checks superuser/BYPASSRLS before it ever consults FORCE. Whether
`neondb_owner` specifically carries `BYPASSRLS` cannot be determined from
application code alone; this migration does not assume an answer either way.
`scripts/verify_rls_enforcement.py` (added alongside this migration) runs a
live, read-only-transaction behavioral check against whichever role the
application is actually configured to connect as, and should be run both
before and after this migration is adopted.

This migration provisions `ekip_app`, a role deliberately given none of the
attributes that would let it bypass RLS (`NOSUPERUSER NOBYPASSRLS NOCREATEDB
NOCREATEROLE`), with exactly the privileges the application needs: ordinary
DML (`SELECT`/`INSERT`/`UPDATE`/`DELETE`) on every table in `public`, matching
grants on sequences, and (implicitly -- Postgres grants `EXECUTE` on new
functions to `PUBLIC` by default, and nothing in this codebase's migrations
revokes that) the ability to call the narrow `SECURITY DEFINER` bypass
functions from migration d2e5f8a3c1b6. Those functions are unaffected by
which role calls them either way: `SECURITY DEFINER` always runs with the
privileges of the function's *owner*, never the caller's, so switching the
application's connection role does not change their behavior. `ALTER DEFAULT
PRIVILEGES` grants below make this automatic for tables/sequences/functions
created by *future* migrations too (run as the schema-owning role), so this
does not need to be re-run every time a new table is added.

Provisioning this role is necessary but not sufficient for the fix to take
effect: an operator must still update the deployed `DATABASE_URL` secret to
connect as `ekip_app` (with the same password supplied to this migration)
instead of `neondb_owner`/whichever role currently owns the schema. That is a
deployment-secret change outside what any migration can perform by itself,
and is deliberately left as an explicit manual step -- see
EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md's updated recommendation #2 for the
full rollout checklist.

Password handling: role-DDL statements (`CREATE ROLE ... PASSWORD`) cannot
take a bind parameter -- the same constraint `app.database.session.
set_tenant_context`'s docstring explains for `SET LOCAL` -- and hardcoding a
real password into a checked-in migration file would itself be a
secret-handling regression this fix is trying to close, not repeat. This
migration therefore reads `EKIP_APP_ROLE_PASSWORD` from the environment *at
migration-run time* and fails loudly if it is unset, rather than silently
falling back to a guessable default -- the same "loud failure over a silent
insecure default" discipline `app.shared.security.kms.
LocalKeyManagementService`'s own KEK-length check already follows.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a7c2e9b3d1'
down_revision: str | None = 'e3f6a1b8d4c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "ekip_app"
_PASSWORD_ENV_VAR = "EKIP_APP_ROLE_PASSWORD"


def _escape_sql_literal(value: str) -> str:
    """Escape a value for interpolation into a single-quoted SQL string
    literal. Role-DDL statements can't use bind parameters (see module
    docstring), so this is the one place a value must be interpolated
    directly -- doubling embedded single quotes is the standard SQL
    escaping rule for that case.
    """
    return value.replace("'", "''")


def _current_database(bind) -> str:
    return bind.execute(sa.text("SELECT current_database()")).scalar_one()


def upgrade() -> None:
    password = os.environ.get(_PASSWORD_ENV_VAR)
    if not password:
        raise RuntimeError(
            f"{_PASSWORD_ENV_VAR} must be set in the environment running this "
            f"migration (e.g. `{_PASSWORD_ENV_VAR}=... alembic upgrade head`) -- "
            f"refusing to create the '{_APP_ROLE}' role with a hardcoded or "
            "empty password."
        )
    escaped_password = _escape_sql_literal(password)

    bind = op.get_bind()
    db_name = _current_database(bind)

    # CREATE ROLE has no `IF NOT EXISTS` clause -- re-running this migration
    # against a database that already has the role (e.g. a re-applied
    # deployment) must update its password/attributes, not fail.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                CREATE ROLE {_APP_ROLE}
                    WITH LOGIN
                    PASSWORD '{escaped_password}'
                    NOSUPERUSER
                    NOBYPASSRLS
                    NOCREATEDB
                    NOCREATEROLE
                    NOREPLICATION;
            ELSE
                ALTER ROLE {_APP_ROLE}
                    WITH LOGIN
                    PASSWORD '{escaped_password}'
                    NOSUPERUSER
                    NOBYPASSRLS
                    NOCREATEDB
                    NOCREATEROLE
                    NOREPLICATION;
            END IF;
        END
        $$;
        """
    )

    op.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {_APP_ROLE}')
    op.execute(f'GRANT USAGE ON SCHEMA public TO {_APP_ROLE}')
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}'
    )
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}')
    op.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {_APP_ROLE}')

    # Cover tables/sequences/functions added by migrations that run after
    # this one, without needing a follow-up migration every time.
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE}'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT EXECUTE ON FUNCTIONS TO {_APP_ROLE}'
    )


def downgrade() -> None:
    bind = op.get_bind()
    db_name = _current_database(bind)

    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE}'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE}'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'REVOKE EXECUTE ON FUNCTIONS FROM {_APP_ROLE}'
    )
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE}')
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE}')
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {_APP_ROLE}')
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE}')
    op.execute(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM {_APP_ROLE}')
    op.execute(f'DROP ROLE IF EXISTS {_APP_ROLE}')
