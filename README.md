# DB Governance Agent

A local-first DB governance and catalog agent.

- **Phase 1** (deterministic foundation): three plain, testable tools
  (schema introspection, unused-object detection, storage stats), a
  two-role Postgres connection setup, an audit log, and a JSON-schema tool
  registry.
- **Phase 2** (this update, minimal): `POST /ask` lets a local Ollama model
  read a free-text question and pick one of the same three tools — using
  Ollama's structured-output option so the model's response is constrained
  to `{"tool": ..., "arguments": {...}}` — then validates that choice
  against the registry before running anything. No new tools, no ML
  classifier, no approval/governance logic; see **Future work** below for
  what's intentionally not built yet.

## Why two database roles instead of one

The app connects with two separate Postgres credentials:

- **`db_governance_readonly`** — `SELECT` only, used by all three
  introspection/analysis tools.
- **`db_governance_audit_writer`** — `INSERT` only on `audit_log`, used only
  by `app/audit.py`.

They're kept distinct so that a compromise of either one has a bounded blast
radius: if the read path is compromised, it still can't write anything
anywhere (including tampering with its own audit trail). If the audit
writer is compromised, it still can't read any of the target data it's
logging access to — it can only see `audit_log`'s own schema. Both roles are
verified against `pg_roles` / `information_schema.role_table_grants` on
every connection in `app/db.py`, and the app refuses to start up quietly
with the wrong permissions — it raises `DBPermissionError` instead.

## Project structure

```
app/
  main.py                 FastAPI app: REST endpoints for each tool + /health + /ask
  db.py                   Two-role Postgres connection management + permission checks
  audit.py                @audited decorator + audit_log writer
  tool_registry.py         JSON-schema tool contract (importable standalone)
  llm_client.py            Ollama structured-output client (Phase 2)
  tool_selector.py         Validates the model's {tool, arguments} against the registry (Phase 2)
  schema.sql               audit_log DDL (idempotent, mounted into docker init)
  tools/
    schema_introspect.py   tables/columns/PK/FK/indexes
    unused_objects.py      scan-activity + duplicate-index findings
    storage_stats.py       table/index/total size + oversized-index signal
db/init/                   docker-compose Postgres init scripts (roles, grants)
seed/seed.sql               seeded schema + data + query activity
scripts/eval_tool_selection.py   tool-selection accuracy eval (Phase 2)
tests/                      pytest suite against the real docker-compose DB
tests/golden_set.json       question -> expected_tool pairs for the eval script
```

## Bring up the database

```bash
cp .env.example .env    # or just use the committed .env (local dev creds, not real secrets)
docker compose up -d --wait
```

This runs, in order, against a fresh `postgres:16` container:

1. `db/init/01_create_roles.sh` — creates the two roles from `.env`.
2. `app/schema.sql` (mounted as `02_audit_schema.sql`) — creates `audit_log`.
3. `db/init/03_audit_grants.sh` — grants `INSERT` on `audit_log` to the audit-writer role.
4. `seed/seed.sql` (mounted as `04_seed.sql`) — creates 8 business tables with
   real foreign keys, an intentional duplicate index (`orders_customer_id_idx`
   / `orders_customer_id_idx2`), a `legacy_notes` table that is never queried
   (for unused-object detection), and runs several `SELECT`s against the
   other tables to seed `pg_stat_user_tables` activity.
5. `db/init/05_readonly_grants.sh` — grants `SELECT` on every business table
   (not `audit_log`) to the read-only role.

All scripts are idempotent-safe to re-run against a fresh volume; to reset
from scratch: `docker compose down -v && docker compose up -d --wait`.

## Run the app

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then visit `http://127.0.0.1:8000/docs` for the interactive Swagger UI, or
use curl:

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/tools/schema_introspect \
  -H "Content-Type: application/json" \
  -d '{"schema": "public"}'

curl -X POST http://127.0.0.1:8000/tools/unused_objects \
  -H "Content-Type: application/json" \
  -d '{"schema": "public", "days": 90}'

curl -X POST http://127.0.0.1:8000/tools/storage_stats \
  -H "Content-Type: application/json" \
  -d '{"schema": "public", "ratio_threshold": 1.0}'

curl http://127.0.0.1:8000/tools   # tool registry, same as `python -m app.tool_registry`
```

Every call to a `/tools/*` endpoint writes one row to `audit_log`
(`tool_name`, `parameters`, `row_count_returned`, `caller`, and `error` on
failure) via the `@audited` decorator in `app/audit.py` — no tool has to
remember to log itself. Check it directly:

```bash
docker compose exec postgres psql -U postgres -d governance \
  -c "SELECT tool_name, parameters, row_count_returned, caller FROM audit_log ORDER BY id;"
```

## Phase 2: POST /ask (LLM tool-selection router)

Lets you ask a free-text question instead of calling a `/tools/*` endpoint
directly. The model only ever *chooses* a tool and its arguments — it never
sees or touches the database itself; the actual query still goes through
the same read-only role and the same `@audited` tool functions as the
direct endpoints.

Setup, one time:

```bash
ollama serve                      # if not already running as a background service
ollama pull gemma:7b              # or whatever model you set OLLAMA_MODEL to in .env
```

`.env` controls which model/host are used (`OLLAMA_MODEL`, `OLLAMA_HOST`) —
nothing is hardcoded.

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Are there any tables that havent been queried in the last 90 days?"}'
```

Response shape: `{"tool_chosen": "...", "arguments": {...}, "result": {...}}`.

How it decides, in two steps:

1. **Route** (`app/llm_client.py`): the question + the full tool registry
   (names, descriptions, parameter schemas) are sent to Ollama's `/api/chat`
   with `format` set to a JSON schema built from the registry
   (`{"tool": {"enum": [...]}, "arguments": {}}`), so the model's raw output
   is already constrained to that shape.
2. **Validate** (`app/tool_selector.py`): the chosen tool name must exist in
   the registry, and `arguments` must pass `jsonschema.validate()` against
   that *specific* tool's parameter schema (its real, strict shape —
   `additionalProperties: false` and all). If either check fails, `/ask`
   returns **422 with the specific validation error** — it never guesses a
   substitute tool, drops unexpected fields, or silently retries the model.

In practice, smaller/weaker local models sometimes nest an argument
(`{"schema": {"schema": "public"}}` instead of `{"schema": "public"}`) or
invent a parameter name — step 2 is what catches that, by design.

### Evaluating tool-selection accuracy

```bash
python scripts/eval_tool_selection.py
```

Reads `tests/golden_set.json` (shipped with 3 example entries as a
template — add more questions/tools as you go), asks the model to route
each one, validates the result the same way `/ask` does, and prints
per-question OK/MISS/ERROR plus a final accuracy score and the list of
misses. It only needs `ollama serve` running — not docker-compose — since
it stops at tool *selection* and never executes a tool against the
database.

## Run the tests

Tests run against the real docker-compose Postgres instance — nothing is
mocked. Bring the database up first (see above); `tests/conftest.py` checks
connectivity via `check_health()` and **skips** (not fails) every test with
a clear message if Postgres isn't reachable, since that's an environment
problem rather than a code problem.

```bash
source .venv/bin/activate
pytest tests/ -v
```

Notably: `tests/test_unused_objects.py::test_returns_evidence_not_a_verdict`
and `test_storage_stats.py::test_flagged_entries_carry_a_reason_not_just_a_boolean`
specifically assert that findings carry a `reason` string (and that no bare
`is_unused` boolean field exists) — the "evidence, not verdict" behavior the
tools are built around.

## Notes on the unused-object detection

- `pg_stat_user_tables` counters are cumulative since the last stats reset
  (or database creation, if never reset). A table can look "active" purely
  because of DDL — e.g. building a `PRIMARY KEY`'s backing index requires a
  heap scan, which increments `seq_scan` before a single real query ever
  ran. `legacy_notes` in the seed data deliberately has no
  constraints/indexes so it stays a genuine zero-scan example.
- On Postgres 16+, `last_seq_scan`/`last_idx_scan` timestamps are used to
  check activity specifically within the requested `days` window. On older
  versions, only cumulative counters are available, so a zero-scan table is
  only reported as high-confidence "unused" when the stats-reset time is
  old enough to have covered the whole window — otherwise it's reported as
  `insufficient_history`, never a confident verdict.
- `estimated_row_count` comes from `pg_class.reltuples`, which is a planner
  statistic populated by `ANALYZE` (including autovacuum's autoanalyze). On
  a freshly seeded database it may read `0` until autovacuum runs; this is
  surfaced honestly rather than papered over, consistent with the rest of
  the tool's evidence-first design.

## What's deliberately not here

Phase 2 added LLM *tool selection* only — it still does not add an ML
classifier or any governance/approval logic. `/ask` picks and runs one of
the exact same three deterministic tools; it doesn't reason about whether
running them is a good idea, score its own confidence, or ask anyone for
approval.

## Future work

Explicitly out of scope for this phase — described here so the shape of
later work is clear, not built:

- **Blast-radius analysis** — before/alongside running a tool (especially
  anything with write implications in a later phase), estimate what else in
  the schema would be affected (downstream FKs, dependent views, app code
  references) so a human or policy layer can weigh consequences before
  acting.
- **Calibrated scoring** — the `confidence` strings (`"high"`/`"low"`) in
  `unused_objects` and the `flagged` boolean in `storage_stats` are
  qualitative labels, not calibrated probabilities. A real scoring model
  (e.g. trained against historical "was this actually safe to drop/change"
  outcomes) would replace them with numbers that mean something
  statistically.
- **Approval workflow** — `/ask` currently runs the chosen tool immediately
  once it validates. A governance layer would instead route certain
  tool/argument combinations (or anything above a risk threshold from
  blast-radius analysis) through a human approval step before execution,
  and record that decision in the audit trail alongside the tool call
  itself.


claude --resume 20274e02-0664-4723-b9dc-60b8a2d6193a