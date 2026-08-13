"""Public interface for core/mcp_oauth -- the two operations `app.mcp.oauth.
provider.EkipOAuthProvider` needs from a persistent client registry:
"is this client_id registered, and if so with what metadata/secret" and
"register this client." Exists solely so `mcp/` (which cannot import
`app.database` at all) has a `core`-side function to call, the same
"call the owning layer's service.py, never its repository.py directly"
convention `core.observability` already follows for `mcp/`'s request-logging
path.

No `require_permission` gate on either function: like `record_mcp_request`,
this is MCP-OAuth-protocol bookkeeping triggered by the `mcp` package's own
DCR/token handlers before any EKIP `Identity` exists to check permissions
against -- there is no user session yet at the point a client registers or
presents its client_id at `/token`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mcp_oauth import repository
from app.shared.security import decrypt_secret, encrypt_secret, get_kms

#: `OAuthClientInformationFull` fields carried on `OAuthClient` as their own
#: columns rather than folded into the `client_metadata` JSONB blob.
_COLUMN_BACKED_FIELDS = frozenset(
    {"client_id", "client_secret", "client_id_issued_at", "client_secret_expires_at"}
)


async def get_registered_client(session: AsyncSession, client_id: str) -> dict[str, Any] | None:
    """Look up a previously dynamically-registered OAuth client.

    Returns a plain dict shaped exactly like `OAuthClientInformationFull`
    (the caller -- `EkipOAuthProvider.get_client` -- validates it into that
    model), or `None` if `client_id` was never registered, or was registered
    by a process that has since had its own in-memory state cleared (there
    is no such process anymore now that this is DB-backed, but `None` is
    still the correct "unknown client" answer either way).
    """
    row = await repository.get_oauth_client(session, client_id)
    if row is None:
        return None

    client_secret = None
    if row.client_secret_encrypted is not None:
        client_secret = decrypt_secret(get_kms(), row.client_secret_encrypted)

    return {
        **row.client_metadata,
        "client_id": row.client_id,
        "client_id_issued_at": row.client_id_issued_at,
        "client_secret": client_secret,
        "client_secret_expires_at": row.client_secret_expires_at,
    }


async def register_oauth_client(session: AsyncSession, client_info: dict[str, Any]) -> None:
    """Persist a newly dynamically-registered OAuth client.

    `client_info` is `OAuthClientInformationFull.model_dump(mode="json")` --
    `client_secret`, if present (a public/`token_endpoint_auth_method="none"`
    client has none), is envelope-encrypted before storage; every other
    field is stored verbatim in the `client_metadata` JSONB blob. See
    `app.database.models.mcp_models.OAuthClient`'s docstring for why
    encryption, not hashing, is required here.
    """
    client_secret = client_info.get("client_secret")
    client_secret_encrypted = encrypt_secret(get_kms(), client_secret) if client_secret else None
    client_metadata = {k: v for k, v in client_info.items() if k not in _COLUMN_BACKED_FIELDS}

    await repository.upsert_oauth_client(
        session,
        client_id=client_info["client_id"],
        client_secret_encrypted=client_secret_encrypted,
        client_secret_expires_at=client_info.get("client_secret_expires_at"),
        client_id_issued_at=client_info.get("client_id_issued_at"),
        client_metadata=client_metadata,
    )
