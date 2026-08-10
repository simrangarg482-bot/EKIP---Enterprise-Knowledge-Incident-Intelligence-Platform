"""Tests for `app.database.session.set_tenant_context` and
`get_current_role_attributes` -- Milestone 10's RLS session-variable wiring,
plus the 2026-08 audit "C2" role-verification addition. Exercised against a
fake `AsyncSession`/connection that just records what it was asked to
execute (or returns a canned row), since a real Postgres connection isn't
available to this test suite.
"""

from __future__ import annotations

import uuid

import pytest

from app.database.session import get_current_role_attributes, set_tenant_context


class _FakeResult:
    def __init__(self, row=None) -> None:
        self._row = row

    def one(self):
        return self._row


class _FakeRoleRow:
    def __init__(self, *, rolsuper: bool, rolbypassrls: bool) -> None:
        self.rolsuper = rolsuper
        self.rolbypassrls = rolbypassrls


class _FakeSession:
    def __init__(self, *, role_row: _FakeRoleRow | None = None) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._role_row = role_row

    async def execute(self, statement, params=None):
        # `text(...)` objects stringify back to the SQL they were built
        # from -- comparing that string is enough to confirm this used
        # `set_config(...)`, not a literal `SET LOCAL ...` string, and that
        # it was called with bound parameters rather than interpolated ones.
        self.executed.append((str(statement), params or {}))
        return _FakeResult(self._role_row)


@pytest.mark.asyncio
async def test_set_tenant_context_calls_set_config_with_bound_parameters() -> None:
    session = _FakeSession()
    organization_id = uuid.uuid4()

    await set_tenant_context(session, organization_id)

    assert len(session.executed) == 1
    statement, params = session.executed[0]
    assert "set_config" in statement
    # Not a literal `SET LOCAL app.current_organization_id = '<uuid>'`
    # string built by interpolation -- both values must arrive as bound
    # parameters, matching this function's own docstring.
    assert "app.current_organization_id" not in statement
    assert str(organization_id) not in statement
    assert params == {"guc_name": "app.current_organization_id", "org_id": str(organization_id)}


@pytest.mark.asyncio
async def test_set_tenant_context_stringifies_the_uuid() -> None:
    """`set_config`'s second argument is a Postgres `text` parameter -- the
    UUID must be passed as its string form, not the raw `uuid.UUID` object
    (which asyncpg would reject for a `text`-typed function argument).
    """
    session = _FakeSession()
    organization_id = uuid.uuid4()

    await set_tenant_context(session, organization_id)

    _statement, params = session.executed[0]
    assert isinstance(params["org_id"], str)
    assert params["org_id"] == str(organization_id)


# --- get_current_role_attributes (2026-08 audit "C2") ------------------------


@pytest.mark.asyncio
async def test_get_current_role_attributes_queries_pg_roles_for_current_user() -> None:
    session = _FakeSession(role_row=_FakeRoleRow(rolsuper=False, rolbypassrls=False))

    await get_current_role_attributes(session)

    assert len(session.executed) == 1
    statement, _params = session.executed[0]
    assert "pg_roles" in statement
    assert "current_user" in statement


@pytest.mark.asyncio
async def test_get_current_role_attributes_reports_a_safe_role() -> None:
    session = _FakeSession(role_row=_FakeRoleRow(rolsuper=False, rolbypassrls=False))

    attributes = await get_current_role_attributes(session)

    assert attributes == {"rolsuper": False, "rolbypassrls": False}


@pytest.mark.asyncio
async def test_get_current_role_attributes_reports_a_bypassing_role() -> None:
    """The exact condition that makes every Milestone 10 RLS policy a
    silent no-op: a role with `BYPASSRLS` set, even without `SUPERUSER`.
    """
    session = _FakeSession(role_row=_FakeRoleRow(rolsuper=False, rolbypassrls=True))

    attributes = await get_current_role_attributes(session)

    assert attributes == {"rolsuper": False, "rolbypassrls": True}


@pytest.mark.asyncio
async def test_get_current_role_attributes_reports_a_superuser_role() -> None:
    session = _FakeSession(role_row=_FakeRoleRow(rolsuper=True, rolbypassrls=False))

    attributes = await get_current_role_attributes(session)

    assert attributes == {"rolsuper": True, "rolbypassrls": False}
