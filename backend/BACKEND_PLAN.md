# Backend Plan (Industry Readiness Platform)

Status: DRAFT v3. Structure matches your existing code; decisions from your answers (Q1-Q15) are applied. Remaining open items are in section 12. Section 12 lists the questions I need answered. Defaults in this document are proposals, not decisions.

What I already know from the project:
- The backend is FastAPI (the Scraper README says so). It is the only client of the ML services.
- The Scraper is called over gRPC every 24 hours (`ScrapeLatest`) and the backend stores the results itself (`jobs.proto`).
- The AdvisorService (built) is called over gRPC by the backend. It calls the backend back over HTTP for market data (`contracts/backend_tools.openapi.yaml`).
- A backend skeleton now exists (see section 3.1): FastAPI app, `/api/v1` router, `/health`, Redis, uv, Python 3.12. Database, auth, gRPC and everything else are still to build.

---

## 1. Goals and non-goals

Goals
1. Collect and store job postings (scraper), and turn them into analytics: demand per cluster, top skills, trends.
2. Serve the student-facing API for the website: dashboard data, job board, advisor chat, profile.
3. Be the only gateway to the advisor: authenticate the student, then pass a trusted `user_id`.
4. Serve the read-only tool endpoints the advisor needs.
5. Own all user data (profiles, consent, memory, chat history). The advisor keeps only a cache.

Non-goals (for now)
- Resume analysis (planned separate ML service, shares `ml_common`).
- Applying to jobs inside the platform (job cards link to the source site).
- Real-time scraping. Daily is enough.

## 2. Architecture

```
Browser (frontend)
   |  HTTPS: REST + SSE
   v
Backend (FastAPI) ----gRPC (every 24h)----> Scraper
   |   ^                                    
   |   +---- HTTP read-only tools <-------- Advisor (calls back with X-Service-Secret + X-User-Id)
   |-------- gRPC (Ask stream, Upsert, Extract, Delete, admin) ----> Advisor
   v
Database (jobs, analytics tables, users, profiles, chats)
```

Rules that keep it safe and simple:
- The browser never talks to ML services. Only the backend does.
- `user_id` sent to the advisor always comes from the authenticated session, never from the request body.
- ML services are optional at runtime: if one is down, the backend still serves everything else.

## 3. Proposed stack (confirm in section 12)

| Part | Proposal | Why |
|---|---|---|
| Framework | FastAPI, async | already chosen in the Scraper README |
| Python | 3.12 (already set in `.python-version`) | matches the other services |
| Cache / ephemeral store | Redis 7 (already in `compose.yaml`) | caches `/health` and website/system settings (database stays the source of truth), rate limits, refresh-token denylist. Also Celery broker if confirmed. |
| Database | PostgreSQL (decided; added to `compose.yaml`) | analytics queries (group by month, cluster) and arrays/JSON fit well |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic | standard, explicit migrations |
| Validation | Pydantic v2 | comes with FastAPI |
| Scheduler / jobs | Celery worker + Celery beat (decided), broker = Redis (confirm, section 12) | runs `ScrapeLatest`; interval is changeable by the admin at runtime (see 5.1, 5.6) |
| gRPC clients | `grpc.aio`, stubs generated from `jobs.proto` and `advisor.proto` | same as the services |
| Auth | Own accounts: email + password, JWT access token (short) + refresh token; roles `student` and `admin` | decided |
| Tests | pytest + httpx ASGI + fake gRPC servers | same style as the services |
| Packaging | uv (already used) + Docker; `backend/compose.yaml` exists with Redis only, add `postgres` and `backend` services | same as other services |

### 3.1 Folder layout (matches your existing structure)

Your code uses the layered, type-based layout from `src/structure.md` (api / core / models / schemas / services), not the feature-folder layout I proposed first. I adopted yours. The domains from this plan become files inside those layers. Imports stay top-level (`from api...`, `from core...`) because `pythonpath = ["src"]`.

Legend: `[exists]` already in your zip, `[new]` to add.

```
backend/                              # D:\industry-readiness-platform\backend
├── .env / .env.example               # [new] env template (secrets, URLs)
├── .gitignore                        # [new]
├── .python-version, pyproject.toml, uv.lock, README.md   # [exists]
├── compose.yaml                      # [exists] redis only; add postgres + backend
├── Dockerfile                        # [new]
├── alembic.ini, alembic/versions/    # [new] migrations
├── scripts/generate_proto.py         # [new] builds gRPC stubs into src/proto/
├── tests/                            # [new] mirrors src/ (api/, services/, contract/, fakes/)
└── src/
    ├── backend/
    │   ├── main.py                   # [exists] app + CORS + router. Add lifespan (DB, Redis, gRPC channels, scheduler)
    │   └── __main__.py               # [exists] uvicorn dev runner
    ├── api/
    │   ├── deps.py                   # [exists: RedisDep] add DbDep, current_user, require_admin, require_service_secret
    │   └── v1/
    │       ├── router.py             # [exists] include every router below
    │       └── endpoints/
    │           ├── health.py         # [exists]
    │           ├── auth.py           # [new] login, refresh, logout
    │           ├── users.py          # [new] /me, /me/profile, /me/consent, /me/export, DELETE /me
    │           ├── jobs.py           # [new] job board (also used by advisor tools)
    │           ├── analytics.py      # [new] cluster-demand, top-skills, demand-trend (also used by advisor tools)
    │           ├── advisor.py        # [new] ask (SSE relay). History/sessions are handled by the advisor for now
    │           └── admin.py          # [new] system settings, ingest runs/trigger, advisor admin proxy, audit log
    ├── core/
    │   ├── config.py                 # [exists] move to pydantic-settings (already a dependency)
    │   ├── security.py               # [new] JWT, password hashing, constant-time secret compare
    │   ├── exceptions.py             # [new] {code, message} error shape + handlers
    │   └── logging.py                # [new] (or utils/logger.py as in structure.md)
    ├── db/
    │   ├── redis.py                  # [exists]
    │   └── session.py                # [new] async SQLAlchemy engine + session (replaces core/database.py in structure.md)
    ├── models/                       # [exists, empty] SQLAlchemy ORM
    │   ├── base.py, user.py, profile.py
    │   ├── job.py                    # jobs, job_skills
    │   ├── analytics.py              # daily_cluster_counts, daily_skill_counts
    │   ├── ingest.py                 # ingest_runs
    │   ├── settings.py               # app_settings (admin-editable, key/value)
    │   └── audit.py                  # audit_log   (chat/memory models deferred, see 5.4)
    ├── schemas/                      # [new, listed in pyproject but missing on disk] Pydantic DTOs
    │   ├── common.py (error shape), token.py, user.py, profile.py,
    │   └── job.py, analytics.py, advisor.py, ingest.py
    ├── services/                     # [exists, empty] business logic
    │   ├── auth_service.py, user_service.py
    │   ├── job_service.py, analytics_service.py
    │   ├── ingest_service.py         # daily run logic
    │   ├── settings_service.py       # read/write app_settings + Redis cache
    │   ├── advisor_service.py        # SSE relay, profile upsert, advisor admin proxy
    │   └── grpc/
    │       ├── scraper_client.py     # jobs.proto
    │       └── advisor_client.py     # advisor.proto
    ├── workers/                      # [new] Celery
    │   ├── celery_app.py             # app + beat schedule (one `tick` task every minute)
    │   └── tasks/ingest.py           # tick (is a run due?), run_ingest
    ├── proto/                        # [new] jobs.proto, advisor.proto + generated stubs
    └── utils/                        # [exists, empty] logger.py and small helpers
```

Mapping from the first draft: `auth/` -> `api/.../auth.py` + `services/auth_service.py` + `core/security.py`; `ingest/` -> `services/ingest_service.py` + `workers/tasks/ingest.py` + `services/grpc/scraper_client.py`; `tools_api/` -> no separate folder (see note); `advisor/` -> `api/.../advisor.py` + `services/advisor_service.py` + `grpc/advisor_client.py`.

Notes:
- **Tool endpoints and student endpoints share one route.** The advisor calls the same paths the dashboard uses (`/api/v1/analytics/...`, `/api/v1/jobs...`). So there is no separate `tools_api`: each route uses a dependency that accepts either a valid student token or a valid `X-Service-Secret`. The service secret never grants access to user or admin routes.
- **`pyproject.toml` packages list** must match the folders: `src/schemas` is listed but missing; `src/db` and `src/proto` are not listed.
- **Top-level package names** like `api`, `core`, `db` are generic. Fine for running with uv/Docker; just do not publish this as a wheel to share.

### 3.2 Current code: things to fix on the way

| Where | Issue | Planned fix |
|---|---|---|
| `endpoints/health.py` | uses `requests` (blocking, inside an async route, and not in `pyproject.toml`) and HTTP `/health` calls | use the gRPC health check (`grpc.aio`) for scraper and advisor (decided); separate liveness from readiness |
| `endpoints/health.py` | bare `except:`, a `print`, and the 5-minute cache hides outages | catch specific errors, log, cache ~10-15 s |
| `core/config.py` | env var named `SCRAPPER_URL` (typo, differs from `SCRAPER_URL`); plain `os.getenv`, no validation; scraper URL default has a `/scraper` path | `pydantic-settings` class, fix the name, add DB URL, secrets, JWT settings |
| `backend/main.py` | CORS `allow_origins=["*"]` | restrict to the frontend origin(s) from config |
| `backend/__init__.py` | placeholder `main()` | remove or point to uvicorn |
| `compose.yaml` | Redis port 6379 published to the host | bind to `127.0.0.1` or drop the port mapping outside dev |

### 3.3 Docker Compose (local, one file, no hosting yet)

For now one compose file starts everything on your machine. It will be a root `compose.yaml` that `include:`s `backend/compose.yaml` and the ml-services compose files (so each part can still be started alone). Everything shares one network, so containers reach each other by service name. No domain, no HTTPS yet; you open `http://localhost:8000/docs`.

| Service | Image / build | Host port | Notes |
|---|---|---|---|
| `postgres` | postgres:16-alpine | 5432 (dev only) | volume `pg_data`; password in the compose file (test only) |
| `redis` | redis:7-alpine | 6379 (dev only) | cache; DB 0 = cache, DB 1 = Celery broker (if confirmed) |
| `backend` | build `./backend` | 8000 | runs Alembic upgrade, then uvicorn; seeds the admin user |
| `worker` | same image as backend | none | `celery -A workers.celery_app worker` |
| `beat` | same image as backend | none | `celery -A workers.celery_app beat` (exactly one instance) |
| `scraper` | from ml-services | none | gRPC, reached as `scraper:<port>` |
| `advisor` | from ml-services | none | gRPC, reached as `advisor:<port>` |

Test-only secrets written directly in the compose file (replace with `.env` or secret files before any real deployment): Postgres password, `ADMIN_EMAIL` / `ADMIN_PASSWORD` for the seeded admin, `JWT_SECRET`, `ADVISOR_SERVICE_SECRET`, advisor `ADMIN_KEY`.

Rules: `depends_on` with healthchecks (backend waits for postgres and redis); publish only the ports you need; one named volume per stateful service.

## 4. Data model (first version)

| Table | Key columns | Notes |
|---|---|---|
| `jobs` | `key` (PK, "source:id"), source, source_id, title, company, location, link, description, posted_at, **collected_at**, cluster, confidence | `key` comes from the scraper. No open/closed status (we cannot detect it). Known keys are skipped. Oldest rows by `collected_at` are deleted when the count exceeds `max_jobs` (admin setting, default ~1000). |
| `job_skills` | job_key, skill | canonical name from `classifier/skills.py`; deleted with the job |
| `ingest_runs` | id, started_at, finished_at, total, new_jobs, per_source_counts (json), errors (json), status, triggered_by (schedule / admin) | one row per run; also drives `as_of` |
| `daily_cluster_counts` | date, cluster, count | written at ingest time; **survives job deletion**, so trends keep history even after the cap prunes old jobs |
| `daily_skill_counts` | date, cluster, skill, count | same |
| `users` | id, email (unique), password_hash, role (`student`/`admin`), is_active, created_at | |
| `user_profiles` | user_id, current_semester, target_cluster, self_assessed_skills[], goals[], constraints[] | one program for now (fixed `program_id` from config, multi-program later) |
| `app_settings` | key (PK), value (json), updated_by, updated_at | admin-editable system settings (5.6), cached in Redis |
| `audit_log` | id, user_id, action, target, before/after (json), created_at | every admin change |

Deferred (the advisor handles chat history and memory for now; revisit later): `chat_sessions`, `chat_messages`, `user_memory_facts`, `user_memory_summaries`, per-user consent storage.

## 5. Main flows

### 5.1 Scheduled ingest (scraper, Celery)
1. Celery beat runs a cheap `ingest.tick` task every minute. The tick reads `scrape_interval_hours` and `ingest_enabled` from settings (Redis cache, falls back to DB) and compares with the last `ingest_runs.started_at`. If a run is due it starts `ingest.run`. This is how the admin can change the interval without restarting anything (beat's own schedule is static). The admin "Run now" button calls `ingest.run` directly.
2. `ingest.run` takes a Redis lock (no two runs at once), loads known keys, and calls `ScrapeLatest(known_keys, max_age_days)` over gRPC.
3. For each streamed `ClassifiedJob`: if `is_new`, insert job + skills with `collected_at = now`; known jobs are ignored.
4. On the final `ScrapeSummary`: write `ingest_runs` (per-source errors included).
5. Update the daily count tables for the new jobs, then prune: if `count(jobs) > max_jobs`, delete the oldest by `collected_at`. All in one transaction.
6. Scraper down: retry with backoff (3 times), record the failure, keep serving old data (`as_of` stays old, so the advisor marks it stale after 48 hours).
7. Celery runs sync tasks, so the task wraps the async gRPC client with `asyncio.run` and uses a fresh async DB engine per task (NullPool).

### 5.2 Analytics (what the advisor and dashboard read)
- Windows: `24h`, `30d`, `6m`, `1y`.
- `cluster-demand`: postings per cluster in the window, with `share_pct` computed by the backend (the advisor must never calculate).
- `top-skills`: top N canonical skills per cluster.
- `demand-trend`: monthly counts for one cluster or one skill.
- Counts come from the `daily_*` tables, so pruning old jobs does not change history. A job is counted on `COALESCE(posted_at, collected_at)`. Jobs are never "closed" (decided).
- Every analytics response carries `as_of` = finish time of the latest successful ingest run.

### 5.3 Advisor chat (student -> advisor)
1. `POST /api/v1/advisor/ask` (SSE). Backend authenticates, calls gRPC `Ask(question, user_id, session_id, ...)`. History and memory are handled by the advisor for now, so the backend sends no stored turns.
2. Relay events as SSE unchanged: `status, corrected, rerouting, reset, token, source, tool, note, done, error`. The frontend must handle `reset` (discard text so far).
3. If the browser disconnects, cancel the gRPC call (the advisor then stops all work and spends no more quota).
4. Backend never forwards `user_id` from the client; it always comes from the authenticated session. Add timeouts and a per-user rate limit (Redis) in front of the advisor.

### 5.4 Profile sync (memory deferred)
- On profile create/update: `UpsertUserProfile` (semester, cluster, skills, goals, constraints).
- On startup and after an advisor restart: re-push the profile lazily when the user next chats (the advisor treats its copy as a cache).
- Account deletion: delete in the database, then `DeleteUser` on the advisor (retry if it fails).
- Deferred until the advisor is finished: `ExtractMemories`, memory facts/summaries, chat history, consent.

### 5.5 Tool endpoints for the advisor (read-only)
Implement exactly `ml-services/AdvisorService/contracts/backend_tools.openapi.yaml`:
`GET /api/v1/analytics/cluster-demand | top-skills | demand-trend`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{key}`.
- Auth: header `X-Service-Secret` must equal the shared secret (constant-time compare). Different secret from the one students use.
- `X-User-Id` is informational (logging); these endpoints return public market data only.
- Until built, returning 404/501 is fine: the advisor says "not available yet".

### 5.6 Admin-editable system settings
Stored in `app_settings`, cached in Redis, every change written to `audit_log`. Admin only.

| Setting | Default | Used by |
|---|---|---|
| `scrape_interval_hours` | 24 | Celery tick |
| `ingest_enabled` | true | Celery tick |
| `scrape_max_age_days` | (scraper default) | `ScrapeLatest` |
| `max_jobs` | 1000 | pruning after each run |

Advisor settings (LLM/model order, providers on/off, usage, quota, reindex, validate) are **not** copied into our database. The backend proxies to the advisor's admin API (single source of truth) and may cache the read view in Redis for a short time. Admin endpoints: `GET/PUT /admin/settings`, `GET/PUT /admin/advisor/...`, `GET /admin/audit`.

## 6. API for the frontend (proposal)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me` |
| Profile | `GET/PUT /me/profile` (semester, cluster, skills, goals), `DELETE /me` (consent and export come later) |
| Dashboard | `GET /analytics/cluster-demand`, `/top-skills`, `/demand-trend` (same code as the tool endpoints, different auth) |
| Jobs | `GET /jobs` (filters: cluster, skills, location, posted_within_days, query), `GET /jobs/{key}` |
| Advisor | `POST /advisor/ask` (SSE); sessions endpoints later, when the advisor exposes history |
| Admin | `GET /admin/ingest/runs`, `POST /admin/ingest/run`, `GET/PUT /admin/settings` (scrape interval, job cap), advisor admin proxy (model order, provider on/off, usage, quota, reindex, validate), `GET /admin/audit` |

All routes versioned under `/api/v1`. Errors use one JSON shape: `{code, message}`.

## 7. Security and privacy

- Passwords: argon2/bcrypt; or SSO (no passwords stored). Short-lived access tokens; refresh rotation.
- Two secrets with different roles: `ADVISOR_SERVICE_SECRET` (backend -> advisor and advisor -> backend tool calls) and the advisor `ADMIN_KEY` (admin proxy only, never sent from the browser).
- Roles: only `admin` may reach `/admin/*`. Every admin action is audit-logged.
- (Deferred, advisor handles it for now) Privacy by default: chat history and memory are off until the student opts in (consent stored with a timestamp). Without consent, nothing personal goes to the advisor except semester and cluster.
- Export and delete endpoints cover profile, facts, summaries, chat history.
- Free-tier model providers may use prompts for training, so personal data is only sent to providers marked `personal_data_ok`. Use synthetic students for testing on free tiers.
- Rate limiting per user and per IP on login and advisor endpoints.
- No secrets in logs; log `user_id` hashes, not message text.

## 8. Failure behaviour

| Failure | Backend behaviour |
|---|---|
| Advisor down | `/advisor/ask` returns an SSE `error` event (retryable); rest of site works |
| Scraper down | retry, record in `ingest_runs`, serve old data with old `as_of` |
| Database down | `/health` fails, 503s; no partial writes (transactions) |
| Memory extraction fails | skip silently, retry next session end (never block the user) |

## 9. Testing plan

- Unit: analytics queries on a seeded database, window math, share_pct rounding, `as_of` choice.
- Contract: run the advisor's mock-backend fixtures against the real tool endpoints; validate responses against `backend_tools.openapi.yaml`.
- Ingest: fake gRPC scraper server streaming new/known/failed sources; idempotency (running twice adds nothing).
- Advisor relay: fake advisor gRPC server; test event passthrough, reset, cancellation on disconnect, consent on/off.
- Security: wrong/missing secret, student calling admin routes, user A reading user B's sessions (must fail).
- End to end (manual first): real advisor + mock or real data, one question per cluster.

## 10. Milestones

| # | Deliverable | Done when |
|---|---|---|
| 1 | Skeleton + infra: Postgres in compose, settings class, Alembic, Dockerfile, health via gRPC, Celery skeleton, tests (**partly done**: skeleton, `/health`, Redis) | `uv run pytest` passes and the root compose starts postgres, redis, backend, worker, beat |
| 2 | Jobs + ingest (scraper gRPC client, Celery tick/run, run log, job cap pruning) | a run fills the database from the real scraper; running twice adds nothing |
| 3 | Analytics tables + tool endpoints for the advisor | advisor answers market questions from real numbers; contract tests pass |
| 4 | Auth, users, profile, admin seed | students sign in and set semester/cluster; admin exists |
| 5 | Advisor relay (SSE), profile upsert, delete | chat works via Swagger/curl |
| 6 | Admin: settings (scrape interval, job cap), advisor admin proxy, audit log, rate limits | admin changes interval and LLM order, audit rows appear |
| 7 | Hardening: monitoring, load test, docs, move secrets to env/secret files | agreed checklist passes |

## 11. Things I will need from you (later, not blocking the plan)
- The real `classifier/skills.py` canonical names (for validating skill filters).
- Real curriculum file (already needed by the advisor).
- Frontend contract once the UI is designed (event handling for `reset`, `note`, `source`).

## 12. Questions and decisions

Answered
1. Database: PostgreSQL, added to the compose file; test passwords inline for now.
2. Auth: email + password. Roles `student` and `admin`. Admin can change system settings (scrape interval, job cap) and advisor settings (LLM, etc.).
3. One program for now; multi-program later.
4. Frontend: not started; focus on backend only (Swagger for testing).
5. Hosting: none yet. One compose file runs all containers (3.3).
6. Scheduler: inside the backend project, with Celery (worker + beat).
7. Demand: count by `COALESCE(posted_at, collected_at)`; no closed status; keep jobs until the cap (~1000, admin-changeable); field `collected_at`.
8. Job expiry: not needed (answered by 7).
9. Chat history/memory: the advisor handles it; revisit later.
10. English only.
11. Compose: per-service files stay for local testing; root compose includes them.
12. Existing code: read from `backend.zip`.
13. Transport: gRPC only, including health checks.
14. Redis: cache (health, settings, rate limits, refresh tokens).
15. Keep `db/`; no `core/database.py`.

Still open
- A. Celery broker: use Redis (DB 1)? Celery needs a broker; Redis is already there. (default: yes)
- B. When over `max_jobs`, delete the oldest by `collected_at`? Daily counts keep the history. (default: yes)
- C. Files needed from you: `jobs.proto`, `advisor.proto`, `ml-services/compose.yaml` (service names, ports, build paths), and the list of advisor admin functions you want on the admin page.
- D. Start coding milestone 1?
