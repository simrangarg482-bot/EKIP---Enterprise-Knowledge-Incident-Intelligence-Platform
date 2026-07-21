"""Application configuration, loaded from environment variables.

This is the first implementation file in the project, deliberately: almost
every other module (database connections, Redis, LLM API keys, MCP auth)
depends on settings being loaded correctly, so it has to exist before
anything else can be written meaningfully.

Owned by: shared/ (ARCHITECTURE.md section 3 -- cross-cutting, no business
meaning of its own, importable by every other module).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Every field here corresponds to a concrete need from a document we've
    already written:
      - database_url          -> Neon Postgres connection (DATABASE_DESIGN.md)
      - redis_url              -> ingestion job queue (ENGINEERING_DECISIONS.md #002)
      - default_vector_backend -> per-collection choice exists in
                                   ARCHITECTURE.md section 8, but the
                                   *default* backend for new collections is
                                   a global setting
      - openai_api_key      -> LLM calls in agents/ (AGENT_WORKFLOWS.md)
      - confidence_threshold   -> the routing threshold in the Confidence
                                   Evaluation Node (AGENT_WORKFLOWS.md 2.2)
                                   -- exposed as config, not hardcoded, since
                                   the exact value is still an open item in
                                   ENGINEERING_DECISIONS.md 

    Deliberately NOT included: individual connector credentials (Slack/GitHub/
    Jira tokens). Those belong to ingestion/connectors/ configuration, scoped
    per-source, not global app settings -- mixing them in here would make
    this class a dumping ground and couple core app startup to whichever
    connectors happen to be configured.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment -----------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Database (DATABASE_DESIGN.md) ------------------------------------
    database_url: PostgresDsn = Field(
        description="Neon Postgres connection string, asyncpg driver."
    )

    # --- Job queue (ENGINEERING_DECISIONS.md #002) ------------------------
    redis_url: RedisDsn = Field(
        description="Backs the arq job queue used by ingestion workers."
    )

    # --- Vector retrieval (ARCHITECTURE.md section 8) ----------------------
    default_vector_backend: Literal["pgvector", "qdrant"] = "qdrant"
    qdrant_url: str | None = Field(
        default=None,
        description="Required only if any collection uses the qdrant backend.",
    )

    # --- LLM (AGENT_WORKFLOWS.md) ------------------------------------------
    openai_api_key: str = Field(description="Used by all agent LLM calls.")

    # --- Agent behavior (AGENT_WORKFLOWS.md 2.2) ---------------------------
    confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
    )

    # --- Auth (API_DESIGN.md section 1) -------------------------------------
    jwt_secret_key: str = Field(description="Signs/verifies session tokens.")
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor.

    Using a cached function rather than a module-level singleton keeps this
    override-able in tests (pytest fixtures can call
    `get_settings.cache_clear()` and monkeypatch environment variables
    per-test) without every other module needing to know that trick exists.
    """
    return Settings()