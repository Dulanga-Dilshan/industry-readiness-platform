# Backend Plan (Industry Readiness Platform)

Status: DRAFT for your review. Section 12 lists the questions I need answered. Defaults in this document are proposals, not decisions.

What I already know from the project:
- The backend is FastAPI (the Scraper README says so). It is the only client of the ML services.
- The Scraper is called over gRPC every 24 hours (`ScrapeLatest`) and the backend stores the results itself (`jobs.proto`).
- The AdvisorService (built) is called over gRPC by the backend. It calls the backend back over HTTP for market data (`contracts/backend_tools.openapi.yaml`).
- Nothing else of the backend exists in the zip, so everything about its stack is open.

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
| Python | 3.12 | matches the other services; avoid the 3.15 alpha |
| Database | PostgreSQL | analytics queries (group by month, cluster) and arrays/JSON fit well |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic | standard, explicit migrations |
| Validation | Pydantic v2 | comes with FastAPI |
| Scheduler | APScheduler inside the app, or a separate `worker` container | needs to run `ScrapeLatest` every 24h |
| gRPC clients | `grpc.aio`, stubs generated from `jobs.proto` and `advisor.proto` | same as the services |
| Auth | JWT access token (short) + refresh token | confirm: university SSO or own accounts? |
| Tests | pytest + httpx ASGI + fake gRPC servers | same style as the services |
| Packaging | uv + Docker, added to `ml-services/compose.yaml` or a root compose | same as other services |

Proposed folder layout (inside a new `backend/` folder next to `ml-services/`):

```
backend/
  src/app/
    main.py                  # FastAPI app, lifespan, routers
    config.py                # env settings
    db/                      # engine, session, models, alembic/
    auth/                    # login, tokens, dependencies (current_user, require_admin)
    jobs/                    # repository, job board endpoints
    analytics/               # aggregation + endpoints (public to students)
    ingest/                  # scraper client + daily scheduler + run log
    advisor/                 # advisor gRPC client, SSE relay, profile sync, memory extraction
    tools_api/               # read-only endpoints for the advisor (service secret only)
    users/                   # profile, consent, data export/delete
    admin/                   # admin endpoints (proxy to advisor admin, ingest status)
    proto/                   # jobs.proto, advisor.proto + generated stubs
  tests/
  scripts/generate_proto.py
  pyproject.toml, Dockerfile
```

## 4. Data model (first version)

| Table | Key columns | Notes |
|---|---|---|
| `jobs` | `key` (PK, "source:id"), source, source_id, title, company, location, link, description, posted_at, first_seen_at, last_seen_at, cluster, confidence | `key` comes from the scraper. Known jobs only update `last_seen_at`. |
| `job_skills` | job_key, skill | skill = canonical name from `classifier/skills.py` |
| `ingest_runs` | id, started_at, finished_at, total, new_jobs, per_source_counts (json), errors (json), status | one row per daily run; also drives `as_of` |
| `daily_cluster_counts` | date, cluster, count | precomputed after each run (cheap analytics) |
| `daily_skill_counts` | date, cluster, skill, count | precomputed, powers top-skills and trend |
| `users` | id, email, password_hash or sso_subject, role (student/admin), created_at | role is `student` or `admin` |
| `user_profiles` | user_id, program_id, current_semester, target_cluster, self_assessed_skills[], goals[], constraints[], consent (json) | the advisor profile source of truth |
| `user_memory_facts` | id, user_id, type, topic, text, confidence, source_session, created_at | returned by advisor `ExtractMemories` |
| `user_memory_summaries` | id, user_id, session_id, text, created_at | expire after retention days |
| `chat_sessions` / `chat_messages` | ids, user_id, role, content, created_at | only stored if the student consents (see section 8) |

Design choices to confirm: counting method for "demand" (section 5.2) and job expiry (jobs not seen for N days are "closed").

## 5. Main flows

### 5.1 Daily ingest (scraper)
1. Scheduler fires every 24h (and an admin button can trigger it).
2. Load known keys from `jobs` and call `ScrapeLatest(known_keys, max_age_days)`.
3. For each streamed `ClassifiedJob`: if `is_new`, insert job + skills; else update `last_seen_at` only.
4. On the final `ScrapeSummary`: write `ingest_runs` (including per-source errors).
5. Rebuild the daily count tables for the affected dates in one transaction.
6. If the scraper is down, retry with backoff (3 times), record the failure, keep serving old data (analytics `as_of` stays old, so the advisor will mark it stale after 48 hours).

### 5.2 Analytics (what the advisor and dashboard read)
- Windows: `24h`, `30d`, `6m`, `1y`.
- `cluster-demand`: postings per cluster in the window, with `share_pct` computed by the backend (the advisor must never calculate).
- `top-skills`: top N canonical skills per cluster.
- `demand-trend`: monthly counts for one cluster or one skill.
- Every analytics response carries `as_of` = finish time of the latest successful ingest run.
- Open decision: count by `posted_at` or by `first_seen_at` when the source has no date; and whether closed jobs still count in `6m`/`1y` windows. My proposal: count by `COALESCE(posted_at, first_seen_at)`, include closed jobs (they were real demand at the time).

### 5.3 Advisor chat (student -> advisor)
1. `POST /api/v1/advisor/ask` (SSE). Backend authenticates, loads the last N turns (if consented), calls gRPC `Ask(question, user_id, session_id, turns)`.
2. Relay events as SSE unchanged: `status, corrected, rerouting, reset, token, source, tool, note, done, error`. The frontend must handle `reset` (discard text so far).
3. If the browser disconnects, cancel the gRPC call (the advisor then stops all work and spends no more quota).
4. Backend stores the final answer text (after the last `reset`) only if the student consented.
5. Backend never forwards `user_id` from the client. Add timeouts and a per-user rate limit in front of the advisor as well (the advisor has its own).

### 5.4 Profile and memory sync
- On profile create/update (or consent change): `UpsertUserProfile` with facts and summaries from the database.
- Session end (explicit "end chat", or 30 min idle): `ExtractMemories(turns)`. Store returned `merged_facts` (replace the user's facts) and the new `summary`, then push `UpsertUserProfile` again. Skip when consent is off.
- On startup and after an advisor restart: re-push profiles lazily (when the user next chats) because the advisor treats its copy as a cache.
- Account deletion or "forget me": delete in the database, then `DeleteUser` on the advisor. Do both even if one fails (retry queue).

### 5.5 Tool endpoints for the advisor (read-only)
Implement exactly `ml-services/AdvisorService/contracts/backend_tools.openapi.yaml`:
`GET /api/v1/analytics/cluster-demand | top-skills | demand-trend`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{key}`.
- Auth: header `X-Service-Secret` must equal the shared secret (constant-time compare). Different secret from the one students use.
- `X-User-Id` is informational (logging); these endpoints return public market data only.
- Until built, returning 404/501 is fine: the advisor says "not available yet".

## 6. API for the frontend (proposal)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me` |
| Profile | `GET/PUT /me/profile` (semester, cluster, skills, goals), `PUT /me/consent`, `GET /me/export`, `DELETE /me` |
| Dashboard | `GET /analytics/cluster-demand`, `/top-skills`, `/demand-trend` (same code as the tool endpoints, different auth) |
| Jobs | `GET /jobs` (filters: cluster, skills, location, posted_within_days, query), `GET /jobs/{key}` |
| Advisor | `POST /advisor/ask` (SSE), `GET /advisor/sessions`, `GET /advisor/sessions/{id}`, `POST /advisor/sessions/{id}/end` |
| Admin | `GET /admin/ingest/runs`, `POST /admin/ingest/run`, advisor admin proxy (model order, provider on/off, usage, quota, reindex, validate) |

All routes versioned under `/api/v1`. Errors use one JSON shape: `{code, message}`.

## 7. Security and privacy

- Passwords: argon2/bcrypt; or SSO (no passwords stored). Short-lived access tokens; refresh rotation.
- Two secrets with different roles: `ADVISOR_SERVICE_SECRET` (backend -> advisor and advisor -> backend tool calls) and the advisor `ADMIN_KEY` (admin proxy only, never sent from the browser).
- Roles: only `admin` may reach `/admin/*`. Every admin action is audit-logged.
- Privacy by default: chat history and memory are off until the student opts in (consent stored with a timestamp). Without consent, nothing personal goes to the advisor except semester and cluster.
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
| 1 | Project skeleton, config, DB + migrations, health, Docker, CI tests | `uv run pytest` and `docker compose up backend` work |
| 2 | Jobs + ingest (scraper client, scheduler, run log) | a daily run fills the database from the real scraper |
| 3 | Analytics tables + tool endpoints for the advisor | advisor answers market questions from real numbers; contract tests pass |
| 4 | Auth, users, profile, consent | students can sign in and set semester/cluster |
| 5 | Advisor relay (SSE), session end -> memory extraction, profile sync, delete/export | full chat works in the browser (or Swagger/curl) |
| 6 | Admin endpoints + audit log + rate limits | model switching and usage view work |
| 7 | Hardening: backups, monitoring, load test, docs | agreed checklist passes |

## 11. Things I will need from you (later, not blocking the plan)
- The real `classifier/skills.py` canonical names (for validating skill filters).
- Real curriculum file (already needed by the advisor).
- Frontend contract once the UI is designed (event handling for `reset`, `note`, `source`).

## 12. Questions (please answer, short answers are fine)

1. **Database:** PostgreSQL OK? Is one already chosen or running?
2. **Auth:** do students sign in with university SSO, email + password, or something else? Is there an admin role already?
3. **Who are the users:** only students of one program, or several programs/universities? (affects `program_id` and the curriculum file: the advisor supports one program per file today.)
4. **Frontend:** what framework, and who builds it? Is it already started?
5. **Hosting:** Docker Compose on one server, or a cloud service? Any domain/HTTPS setup?
6. **Scheduler:** in-process (simplest) or a separate worker container?
7. **Demand counting:** count by posted date (or first seen), and should closed/expired jobs still count in the 6m and 1y windows? (my proposal in 5.2)
8. **Job expiry:** after how many days without being seen should a job count as closed? (proposal: 14)
9. **Chat history:** store it by default, only with opt-in consent (my proposal), or not at all? Retention period?
10. **Languages:** English only, or Sinhala/Tamil too? (affects advisor prompts and spell-check)
11. **Repo layout:** new `backend/` folder next to `ml-services/`, with one root compose file? Or keep using `ml-services/compose.yaml`?
12. **Existing backend code:** does any exist outside the zip? If yes, tell me the stack (I will not need to read the code, only the folder structure and the DB choice).
