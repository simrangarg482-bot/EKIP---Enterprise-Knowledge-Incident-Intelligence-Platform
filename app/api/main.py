"""FastAPI application factory for EKIP's REST API.

Owned by: app/api. Per API_DESIGN.md section 1 and ARCHITECTURE.md section 6:
REST and MCP are thin, parallel wrappers around the same core/agents
Pydantic-typed internal interfaces -- this module's routers contain no
business logic beyond request/response translation, matching MCP's tool
handlers' own "no logic beyond this translation" rule.

Scope of what's wired up here (see each router's own docstring for what's
excluded and why): auth (the real SSO/PKCE flow core/auth actually
implements, superseding API_DESIGN.md's older `/auth/login`
username+password sketch), incidents (full CRUD + timeline), ask
(`answer_question` + `triage_incident`), postmortems (generate/read/edit/
approve), knowledge (the review queue -- list/publish/reject/gaps),
observability (`/observability/agents`, `/observability/mcp` -- the
"dashboards, latency metrics" requirement, PROJECT_PLAN.md section 10,
Milestone 10), tenancy (`/tenancy/connectors` -- registering and listing
connector configurations, the previously-missing REST surface for
`core.tenancy.service.register_connector`/`list_connectors`, closed as a
follow-up after Milestone 10), tenancy's `admin_router` (organizations,
projects, SSO configuration, access rules, invitations -- the rest of
`core.tenancy.service`'s previously-unreachable surface, closed in the same
integration-gaps pass that added project-scoped RBAC and logout-everywhere),
and users (`/users/{user_id}/logout-all` -- the admin-triggered session
revocation counterpart to `/auth/logout-all`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import ekip_error_handler
from app.api.routers import (
    ask,
    auth,
    incidents,
    knowledge,
    observability,
    postmortems,
    tenancy,
    users,
)
from app.core.exceptions import EKIPError
from app.shared.config.settings import get_settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Owns the one `arq` Redis pool this process uses to *enqueue* jobs
    (`POST /tenancy/connectors/{id}/sync`, `app.api.deps.get_arq_pool`) --
    not to run them. Running jobs is `scripts/ingestion_worker.py`'s /
    `app.ingestion.workers.main.WorkerSettings`'s job, a separate process,
    exactly as `app.ingestion.workers.main`'s own docstring already
    establishes (ENGINEERING_DECISIONS.md #002: API server and worker are
    separate processes sharing one Redis queue, not one process doing both).
    Built from the same `Settings.redis_url` that worker already reads, so
    there is one source of truth for the connection string, not a second one
    hand-maintained here.

    `default_queue_name` must match `app.ingestion.workers.main.WorkerSettings
    .queue_name` ("arq:queue:ingestion"), the only worker that registers
    `run_ingestion_job_task` -- the sole function this pool ever enqueues.
    Without it, `create_pool` falls back to arq's own hardcoded default
    ("arq:queue"), which no worker polls (both workers opted out of that
    default for the queue-collision reason documented on their own
    `queue_name` attributes), so every job enqueued here would sit in Redis
    forever and connector syncs would silently never run.
    """
    app.state.arq_pool = await create_pool(
        RedisSettings.from_dsn(str(get_settings().redis_url)),
        default_queue_name="arq:queue:ingestion",
    )
    try:
        yield
    finally:
        await app.state.arq_pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="EKIP API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(EKIPError, ekip_error_handler)

    app.include_router(auth.router)
    app.include_router(incidents.router)
    app.include_router(ask.router)
    app.include_router(postmortems.router)
    app.include_router(knowledge.router)
    app.include_router(observability.router)
    app.include_router(tenancy.router)
    app.include_router(tenancy.admin_router)
    app.include_router(users.router)

    return app


app = create_app()
