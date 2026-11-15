# AdvisorService

The Career Advisor container: Ask (streamed), user profile and memory, knowledge base, curriculum tools,
backend data tools, model routing with fallback, quota tracking, evaluation. gRPC for the backend, REST/SSE for testing.

## Files you must remove from the old stub
The repo still has an empty stub in this folder. Delete these before copying (they conflict):
`.python-version` (says 3.15), `test.py`, `src/advisorservice/`, the old `README.md` and `uv.lock` (replace them with these).

## Layout 
```
src/ml_common/     shared with the future resume service: providers + router, embeddings, vector store, quota, auth, events
src/advisor/       the advisor: orchestrator, agents (triage, evidence, verifier, prompts/*.md), knowledge, curriculum,
                   memory, tools (backend, web), safety, cache, eval, server (gRPC proto + REST)
knowledge_base/    markdown + front matter. Only status: approved is indexed. Drafts are marked, skeletons hold TODO(user).
curriculum/        curriculum.yaml (TEMPLATE with EXAMPLE data: replace it)
data/              skills_glossary.txt (STARTER list: replace via scripts/export_skills_glossary.py)
contracts/         backend_tools.openapi.yaml (what the backend must serve)
mock_backend/      fake backend (all numbers are fake) for testing alone
tests/             158+ tests, all with fake models (no quota used)
```

## Run
1. `ml-services/.env`: copy `.env.example`, set `SERVICE_SECRET`, `ADMIN_KEY`, provider keys, `BACKEND_BASE_URL`.
2. `docker compose up -d --build advisor` (starts Qdrant too). Embeddings need Ollama (`--profile local`, then
   `docker exec irp-ollama ollama pull nomic-embed-text`) or set `EMBEDDING_PROVIDER=gemini`, `EMBEDDING_MODEL=gemini-embedding-2`.
3. Without a backend: `docker compose --profile mock up -d` and `BACKEND_BASE_URL=http://mock-backend:9000`.
4. Tests: `uv sync` then `uv run pytest` (inside AdvisorService).

The service refuses to start without `SERVICE_SECRET` and `ADMIN_KEY` (different values). The knowledge index builds
in the background at first start; `GET /advisor/health` shows `index: ready`.

## Try it (REST, localhost only)
```
curl -N -X POST localhost:8001/advisor/ask -H "X-Service-Secret: $S" -H "Content-Type: application/json" \
     -d '{"question":"What is Docker?","user_id":"u1"}'
```
Events: `status, corrected, rerouting, reset, token, source, tool, note, done, error` (same in gRPC `AskEvent`).
`reset` means: discard the text shown so far, a new answer follows (at most once per question).

Admin (needs `X-Admin-Key` too): `/advisor/admin/` + `config` (GET/PUT), `model` (PUT), `providers`, `probe`, `reindex`,
`usage`, `quota`, `metrics`, `faults` (simulate provider failures), `validate` (knowledge + curriculum errors),
`capacity`, `cache/clear`; `/advisor/eval/questions|run|runs/{id}`. Model order, enabling a provider, budgets, cache and the
tool-failure message change at runtime without a restart and survive restarts (`/data/runtime_config.json`).

## Things to know / decide (nothing here was guessed silently)
* **Env var names**: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY` are the conventional names;
  I could not see your `check_provider_keys.py`. Change them in `advisor/config/settings.py` if yours differ.
* **Tested models only**: `large` tier reuses the three tested small models in another order until the bake-off
  (`scripts/run_bakeoff.py`) picks better ones. Local Ollama entries exist but are disabled.
* **Personal data**: free tiers are `personal_data_ok=false`. Without a private model the advisor drops goals/facts/memory
  from prompts and says so in a note. To test personalisation with *synthetic* students on free tiers:
  `PUT /advisor/admin/model {"provider":"groq-gpt-oss-20b","personal_data_ok":true}`.
* **Skills glossary / curriculum**: validation uses `data/skills_glossary.txt`. The shipped list is a starter. Run
  `scripts/export_skills_glossary.py` (needs one edit to name the variable inside `skills.py`; I did not read that code).
  One program per curriculum file; semester ids look like `3.1` (pattern in `curriculum/validator.py`).
* **Knowledge base**: guides/FAQ are drafts from general knowledge with `(verify)` marks. `framework/` and `recruiters/`
  are empty skeletons for your research content. Approve a file by setting `status: approved`, then `POST /admin/reindex`.
* **Unverified external details**: Ollama `/api/embed` and the Gemini embed behaviour (one text per call) were checked in
  docs; Wikipedia/Tavily request shapes, Groq header names and `stream_options` support are from memory and covered only
  by mock tests: run `POST /admin/probe` and one real question per provider first. Wikipedia text licence/attribution
  and the `USER_AGENT` contact in `tools/wikipedia.py` need your confirmation.
* **Thresholds to tune with real data**: `min_vector_score` (weak knowledge match, default 0.3), cache threshold 0.95,
  cooldowns (30 s/60 s), spell-check confidence. The number verifier allows integers up to 10 and years without evidence.
* **Backend contract**: until your backend serves `contracts/backend_tools.openapi.yaml`, tools say "not available yet".
  Backend must send `as_of` on analytics responses and compute percentages.
* **Not built**: LLM re-ranking, streaming of tool-result tables, multi-program curricula, auth beyond shared secrets,
  Prometheus server (metrics text endpoint only).
