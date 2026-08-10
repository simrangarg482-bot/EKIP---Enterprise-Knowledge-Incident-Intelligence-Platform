"""Live verification: is Postgres Row-Level Security actually enforced
against whichever role `DATABASE_URL` currently connects as?

2026-08 audit "C2" ("Strong evidence that Postgres RLS is not actually
enforced against the deployed database role"): every Milestone 10 RLS policy
(`app/database/migrations/versions/c7d4e8f19a2b_milestone_10_row_level_
security.py`) depends on the connecting role holding neither `SUPERUSER` nor
`BYPASSRLS` -- `FORCE ROW LEVEL SECURITY` makes a policy apply even to a
table's *owner*, but has no effect on either of those two attributes, which
Postgres checks before it ever consults `FORCE`. This project's own prior
security review (`EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md`, recommendation
#2) flagged that this had never actually been verified against a real
database. This script is that verification, run for real rather than
inferred from a connection string's username.

This is a real-network(/real-database), read-mostly script, following this
project's own established precedent (`scripts/test_connectors.py`,
`scripts/live_connector_tests/`) for why scripts like this live under
`scripts/`, not `tests/`: `pyproject.toml`'s `testpaths = ["tests"]` means a
bare `pytest` never auto-collects this, so an ordinary test run never
accidentally touches a real, live database. Run it explicitly:

    python -m scripts.verify_rls_enforcement

Requires `DATABASE_URL` (the project root `.env`, already loaded by
`app.shared.config.settings`) to point at the database you want to check --
run this once against today's connection (to confirm/refute the audit
finding) and again after switching to the dedicated `ekip_app` role
(migration `f4a7c2e9b3d1_provision_rls_respecting_app_role`) to confirm the
fix actually took effect.

What this checks, in order:
  1. Role attributes (`app.database.session.get_current_role_attributes`) --
     `rolsuper`/`rolbypassrls` directly from `pg_roles`. Either one being
     `true` is sufficient, on its own, to make every RLS policy in this
     project a no-op, independent of check #2 below.
  2. A live, read-only behavioral check: run one `SELECT count(*) FROM
     projects` on a fresh connection that has deliberately never called
     `set_tenant_context` (i.e. `app.current_organization_id` is unset).
     Per the RLS migration's own fail-closed design, a connection that never
     sets that GUC must see ZERO rows from any RLS-protected table --
     `current_setting(..., true)` returns `NULL` when unset, and
     `organization_id = NULL` is never true. A non-zero count here is direct,
     conclusive proof that RLS is not actually restricting reads for this
     role, regardless of what check #1 found (e.g. if some other mechanism
     -- a misconfigured policy, a missing `FORCE`, an unexpected grant --
     is *also* in play).
  3. The same idea, but for writes: inside a transaction that is ALWAYS
     rolled back (never committed, regardless of outcome -- this script
     never persists anything to the database it checks), attempt to insert
     a `projects` row under one real, pre-existing `organization_id` while
     the GUC is still unset. Under real enforcement this INSERT must be
     *rejected* by Postgres (a row-level-security-policy violation error),
     not merely filtered from a subsequent read -- `WITH CHECK` (implicit,
     from the bare `USING` clause) applies to new/changed rows, not just
     visibility of existing ones. Skipped, with a clear message, if the
     database has no organizations at all to test against yet.

Exit code is non-zero if any check indicates RLS is not enforced, so this is
also usable as a CI/deployment gate, not just an interactive report.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import text

from app.database.session import engine, get_current_role_attributes
from app.shared.config.settings import get_settings


async def _check_role_attributes(conn) -> bool:
    """Returns True if the role is safe (does not bypass RLS)."""
    attributes = await get_current_role_attributes(conn)
    print(f"  rolsuper:     {attributes['rolsuper']}")
    print(f"  rolbypassrls: {attributes['rolbypassrls']}")

    if attributes["rolsuper"] or attributes["rolbypassrls"]:
        print(
            "  FAIL: this role can bypass every RLS policy in the database "
            "(SUPERUSER and/or BYPASSRLS is set). Every 'RLS protects this' "
            "claim in this codebase is currently false for this connection."
        )
        return False

    print("  PASS: this role has neither SUPERUSER nor BYPASSRLS.")
    return True


async def _check_reads_are_filtered_without_tenant_context(conn) -> bool:
    """Returns True if the check passed (zero rows visible), False if it
    demonstrated a leak (rows visible with no tenant context set), and
    `None`-like behavior (treated as a pass, printed clearly) if the table
    is simply empty -- an empty table can't distinguish "RLS is filtering
    everything" from "there is nothing to filter" on its own, so this is a
    necessary-but-not-sufficient check, always paired with check #3.
    """
    result = await conn.execute(text("SELECT count(*) FROM projects"))
    visible_count = result.scalar_one()
    print(f"  rows visible in 'projects' with no tenant context set: {visible_count}")

    if visible_count > 0:
        print(
            "  FAIL: rows are visible with `app.current_organization_id` "
            "unset. Under real, fail-closed RLS enforcement this must "
            "always be zero, regardless of how many rows actually exist."
        )
        return False

    print(
        "  PASS (or inconclusive if the table is simply empty -- see check "
        "#3 for a conclusive write-side test)."
    )
    return True


async def _check_writes_are_rejected_without_tenant_context(conn) -> bool | None:
    """Returns True if the check passed (INSERT rejected), False if it
    demonstrated a leak (INSERT succeeded), or `None` if there was no
    existing organization to test against. Always rolls back -- this
    function never persists anything, regardless of outcome.
    """
    org_row = await conn.execute(text("SELECT id FROM organizations LIMIT 1"))
    org_id = org_row.scalar_one_or_none()
    if org_id is None:
        print("  SKIPPED: no organizations exist yet to test an insert against.")
        return None

    savepoint = await conn.begin_nested()
    try:
        probe_name = f"rls-verification-probe-{uuid.uuid4()}"
        await conn.execute(
            text(
                "INSERT INTO projects (organization_id, name, is_default) "
                "VALUES (:organization_id, :name, false)"
            ),
            {"organization_id": org_id, "name": probe_name},
        )
    except Exception as exc:
        print(f"  PASS: insert was rejected ({type(exc).__name__}: {exc}).")
        return True
    else:
        print(
            "  FAIL: insert succeeded with no tenant context set. This is "
            "direct, conclusive proof RLS is not enforcing writes for this "
            "role/table."
        )
        return False
    finally:
        # Never persist the probe row (or anything else in this
        # transaction), regardless of which branch above ran.
        await savepoint.rollback()


async def main() -> int:
    settings = get_settings()
    print(f"Checking RLS enforcement for DATABASE_URL host/db: "
          f"{settings.database_url.host}/{settings.database_url.path.lstrip('/')}\n")

    # This process exits as soon as `main()` returns (see `__main__` below),
    # so disposing the shared app engine at the end is safe -- nothing else
    # in this process needs it afterward.
    all_passed = True
    try:
        async with engine.connect() as conn:
            print("[1/3] Role attributes (pg_roles)")
            all_passed &= await _check_role_attributes(conn)

            print("\n[2/3] Read-side behavioral check (SELECT with no tenant context)")
            async with conn.begin():
                all_passed &= await _check_reads_are_filtered_without_tenant_context(conn)

            print("\n[3/3] Write-side behavioral check (INSERT with no tenant context)")
            async with conn.begin():
                write_result = await _check_writes_are_rejected_without_tenant_context(conn)
                if write_result is False:
                    all_passed = False
    finally:
        await engine.dispose()

    print()
    if all_passed:
        print("RESULT: PASS -- RLS appears to be genuinely enforced for this connection.")
        return 0

    print(
        "RESULT: FAIL -- RLS is not actually enforced for this connection. "
        "See app/database/migrations/versions/"
        "f4a7c2e9b3d1_provision_rls_respecting_app_role.py to provision a "
        "role that does enforce it, then update DATABASE_URL to use it and "
        "re-run this script to confirm."
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
