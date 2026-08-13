"""Persistence for core/mcp_oauth -- `oauth_clients` only. Pure data access,
same discipline as every other repository.py in this codebase: one
statement per function, ORM rows in/out, no business rules, no
ORM->dict mapping (that's `service.py`'s job, since it also owns the
encrypt/decrypt step around `client_secret_encrypted`).
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.mcp_models import OAuthClient


async def get_oauth_client(session: AsyncSession, client_id: str) -> OAuthClient | None:
    return await session.get(OAuthClient, client_id)


async def upsert_oauth_client(
    session: AsyncSession,
    *,
    client_id: str,
    client_secret_encrypted: str | None,
    client_secret_expires_at: int | None,
    client_id_issued_at: int | None,
    client_metadata: dict,
) -> OAuthClient:
    """Insert one registered client, or overwrite it if `client_id` already
    exists.

    An upsert, not a plain insert: RFC 7591 gives every registration a fresh
    `client_id` (a `uuid4`), so a genuine collision is not expected in
    practice -- but a client that retries a registration call which actually
    succeeded server-side (its own response was merely lost) would otherwise
    hit a needless `IntegrityError` for a request that should be a safe
    no-op/overwrite, not a crash.
    """
    stmt = pg_insert(OAuthClient).values(
        client_id=client_id,
        client_secret_encrypted=client_secret_encrypted,
        client_secret_expires_at=client_secret_expires_at,
        client_id_issued_at=client_id_issued_at,
        client_metadata=client_metadata,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[OAuthClient.client_id],
        set_={
            "client_secret_encrypted": stmt.excluded["client_secret_encrypted"],
            "client_secret_expires_at": stmt.excluded["client_secret_expires_at"],
            "client_id_issued_at": stmt.excluded["client_id_issued_at"],
            "metadata": stmt.excluded["metadata"],
        },
    ).returning(OAuthClient)
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()
