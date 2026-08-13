# EKIP Frontend

Enterprise frontend for **EKIP — Enterprise Knowledge Incident Intelligence Platform**.

This directory is fully self-contained and does not modify or depend on the
existing Python backend (`app/`) beyond calling its REST API over HTTP.

## Stack

React 18 · TypeScript · Vite · Tailwind CSS · TanStack Query · React Router · Recharts · Lucide icons

## Getting started

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

The app runs on `http://localhost:5173`. By default it talks to the real
FastAPI backend at `VITE_API_BASE_URL` — sign up for a new account (creates a
new organization) or log in at `/signup` / `/login`. Set
`VITE_USE_MOCK_DATA=true` to fall back to the in-memory mock data layer
instead — see below.

## Project structure

```text
src/
├── api/          Resource-oriented API modules (incidents.ts, knowledge.ts, ...).
│                 Each function checks VITE_USE_MOCK_DATA and either returns
│                 mock data or calls the real FastAPI backend via client.ts.
├── mocks/        Mock data only. Never imported outside src/api/*.
├── types/        Shared TypeScript types, mirroring backend response shapes.
├── components/
│   ├── ui/       Generic, app-agnostic primitives (Button, Modal, Table shell, ...).
│   ├── data/     Data-display building blocks (DataTable, badges, MetricCard, ...).
│   ├── layout/   App shell pieces (Sidebar, Topbar, PageHeader, ...).
│   └── domain/   EKIP-specific composites (AIAnalysisPanel, ConnectorCard, ...).
├── layouts/      Route-level layouts (AppLayout, AuthLayout).
├── pages/        One folder per feature area, matching the route tree.
├── context/      AuthContext, TenantContext, ToastContext.
├── hooks/        Small reusable hooks (debounce, media query, click-outside).
├── routes/       Router config and navigation metadata.
└── utils/        Formatting/date helpers and the cn() class merger.
```

## Authentication

Email/password, not SSO — `POST /auth/signup` and `POST /auth/login` (a
parallel, additive auth path alongside the backend's real SSO/PKCE flow,
which this frontend does not use). Signing up always creates a brand-new
organization for the new user (there is no join-existing-org flow yet);
logging in resolves the organization created at that user's own signup.

Token handling (`src/context/tokenStore.ts`, `src/context/AuthContext.tsx`):
the short-lived access token is held in memory only; the longer-lived
refresh token is persisted to `localStorage` so a page reload calls
`POST /auth/refresh` instead of forcing a fresh login. `GET /auth/me`
doesn't return `organization_id`, so it's read off the access token's own
(unverified, client-side-only) JWT claims — the backend still re-verifies
the real signature on every request.

## Connecting to the real backend

1. Set `VITE_USE_MOCK_DATA=false` in `.env` (the default) to use the real
   FastAPI backend instead of `src/mocks/*`.
2. Set `VITE_API_BASE_URL` to wherever the backend is reachable
   (defaults to `http://localhost:8000`, matching the backend's default).
3. The backend needs `CORSMiddleware` configured for this origin (see
   `app/shared/config/settings.py`'s `cors_allowed_origins`, which defaults
   to `http://localhost:5173`) or every request is blocked by the browser
   before it reaches the backend at all.
4. `apiRequest()` (`src/api/client.ts`) converts request/response bodies
   between camelCase (every frontend type) and snake_case (every real
   Pydantic schema — there's no alias generator on the backend side) via
   `src/api/caseConversion.ts`. This is automatic and applies to every
   `src/api/*` call — no per-endpoint mapping needed.

**Verified against the real backend** (2026-08-12): signup, login, asking a
real question (`POST /ask`, confidence-routed to a real answer or
investigation depending on what's been ingested), question history
(`GET /ask/history`), registering a GitHub/Slack connector and triggering an
on-demand sync (`POST /tenancy/connectors/{id}/sync`, enqueued onto the real
`arq` worker queue), and browsing published knowledge (`GET /knowledge`) all
work end-to-end over real HTTP. The following gaps are still real and left
as explicit empty/error states rather than faked:

- `GET /observability/activity` — recent system activity feed (dashboard).
- `GET /analytics/summary` — no analytics router exists at all yet (dashboard charts + Analytics page).
- `GET /users` — no "list users in my organization" endpoint; only `/users/{id}/logout-all` exists (Settings → Users page).
- Reading back SSO config — only `POST /organizations/{id}/sso/configure` (write) exists, no `GET` equivalent (Settings → SSO page loads empty). Not applicable to this frontend's own login flow, which doesn't use SSO.
- `POST /mcp/tools/{name}/invoke` — generic MCP tool invocation (MCP Tools page's "Test Tool").

The real tenancy admin routes have no `/tenancy` prefix — they're
`GET /organizations` and `GET /organizations/{id}/projects`, not
`/tenancy/organizations/*`.

## Notes

- Multi-tenancy (organization → project) is modeled by `TenantContext` and
  surfaced via the switcher in the top bar. No organization ID is hardcoded.
- "Ask EKIP" (`/ask`, `src/pages/ask/AskPage.tsx`) is the default landing
  page after login — a chat-style interface over the real `POST /ask`
  retrieval/agent pipeline, with grounded citations and a history tab backed
  by `GET /ask/history`. Like every other page, it only returns mock data
  when `VITE_USE_MOCK_DATA=true`; with the default `false` it always calls
  the real backend — see `src/api/ask.ts`.
