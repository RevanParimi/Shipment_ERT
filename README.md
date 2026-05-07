# Agentic Freight Visibility & Disruption Management System

Autonomous AI agent system that ingests multi-source supply chain signals, detects shipment risks, evaluates business impact, and executes mitigation actions — escalating to humans only when confidence is low or cost is high.

---

## Architecture

### Dynamic LangGraph Flow

The pipeline is not a fixed sequence — LangGraph routes between nodes based on live state. Agents that are not needed are skipped entirely, saving LLM calls and latency.

```
                    ┌─────────────────────┐
                    │   Data Ingestion     │  SQLite (filtered) + Emails
                    └──────────┬──────────┘
                               │
              ┌────────────────┴─────────────────────┐
              │ no active shipments                   │ has active shipments
              ▼                                       ▼
             END                          ┌─────────────────────┐
          (0 LLM calls)                   │  Risk Detection      │  Status flag (rule)
                                          │  Agent               │  Milestone delay (LLM batch)
                                          │                      │  GPS freeze (LLM batch)
                                          └──────────┬──────────┘  Email analysis (LLM parallel)
                                                     │
                              ┌──────────────────────┴──────────────────────┐
                              │ nothing at risk                              │ shipments at risk
                              ▼                                              ▼
                             END                                 ┌─────────────────────┐
                          (0 LLM calls)                          │  Impact Analysis     │  buffer_days (Python)
                                                                 │  Agent               │  SO breach (Python)
                                                                 └──────────┬──────────┘  → LLM holistic severity
                                                                            │
                                              ┌─────────────────────────────┴────────────────────────────┐
                                              │ all severity = LOW                                        │ any HIGH or MEDIUM
                                              ▼                                                           ▼
                                  ┌─────────────────────┐                               ┌─────────────────────┐
                                  │  Fast Mitigation     │                               │  Mitigation          │
                                  │  (rules only)        │                               │  Decision Agent      │
                                  │  0 LLM calls         │                               │  Groq llama-3.3-70b  │
                                  └──────────┬──────────┘                               └──────────┬──────────┘
                                             │                                                      │
                                             └───────────────────────┬──────────────────────────────┘
                                                                     ▼
                                                         ┌─────────────────────┐
                                                         │  Autonomous Action   │  conf ≥ 0.75 + cost < $5k
                                                         │  Agent + HITL        │  → [AUTO] execute
                                                         │                      │  → [ESCALATE] human approval
                                                         └──────────┬──────────┘
                                                                    ▼
                                                                   END
                                                                    │
                                                         ┌──────────▼──────────┐
                                                         │   FastAPI REST API   │
                                                         │   (port 8000)        │
                                                         └─────────────────────┘
```

### What each route saves

| Scenario | Nodes skipped | LLM calls saved |
|---|---|---|
| All shipments delivered | detect_risk, analyze_impact, both mitigations, execute_actions | All |
| Active but no risk signals fired | analyze_impact, both mitigations, execute_actions | All |
| At-risk but all LOW severity | plan_mitigation (Groq) | N calls (one per at-risk shipment) |
| Any HIGH or MEDIUM severity | Nothing — full pipeline runs | None (full reasoning needed) |

### Data sources simulated

| Source | Tables / Files | Rows |
|--------|---------------|------|
| ERP — Procurement | `purchase_orders` | 30 |
| ERP — Orders | `sales_orders` | 30 |
| ERP — Inventory | `inventory` | 25 (5 plants × 5 materials) |
| Transportation | `shipments` | 30 (~30% delayed) |
| Transportation | `milestones` | 210 (7 per shipment) |
| Tracking | `gps_feed` | ~180 pings |
| Communications | `data/emails/*.json` | 22 email files |

---

## Agent Descriptions

### 1. Data Ingestion Agent (`agents/ingestion.py`)
Scope-filtered DB reads — only loads rows that require a decision:

| Table | Filter | Reason |
|-------|--------|--------|
| `shipments` | `status != 'delivered'` | Delivered shipments are closed — nothing to act on |
| `milestones` | `shipment_id IN (active IDs)` | History only matters for open shipments |
| `gps_feed` | active IDs + `timestamp >= SIM_NOW − 4h` | Recent movement is what matters; configurable via `GPS_LOOKBACK_HOURS` |
| `purchase_orders` | `po_id IN (active shipment po_ids)` | Only POs with an open shipment are actionable |
| `sales_orders` | `material IN (active PO materials)` | Impact analysis only needs SOs for materials currently at risk |
| `inventory` | `(plant\|\|'\|'\|\|material) IN (active dest+material pairs)` | Multi-column filter using SQLite string concatenation — scales to millions of rows |

### 2. Shipment Risk Detection Agent (`agents/risk_detection.py`)
Four signals per shipment — signals 1-3 are deterministic (LLM cannot do better), signal 4 is semantic (LLM essential):

| Signal | Method | Why |
|--------|--------|-----|
| Explicit status flag | Deterministic rule | `status = delayed` is unambiguous — no reasoning needed |
| Milestone slippage | Deterministic formula | `actual_ts − planned_ts` is arithmetic |
| GPS freeze | Deterministic rule | `speed == 0` for 3+ pings is factual |
| Email analysis | **Groq LLM** (1 call per shipment, all emails combined) | Keywords cannot distinguish "delay resolved" from "delay ongoing" |

LLM email calls run in parallel via `ThreadPoolExecutor(max_workers=4)`. Falls back to keyword matching without an API key.

### 3. Impact Analysis Agent (`agents/impact_analysis.py`)
Numerical facts are calculated in Python (unambiguous arithmetic), then handed to the LLM as grounded inputs for holistic reasoning:

- `buffer_days = (current_stock − safety_stock) / daily_consumption`
- `days_so_breach = (commitment_date − now).days`
- The LLM receives: material type, transport mode, carrier, priority, buffer days, SO breach days, **and the email summary from Agent 2**
- LLM returns: `severity`, `impact_type`, `urgency_score (1-10)`, `key_concern`, `recommended_days_to_act`

Why LLM here: a formula cannot know that ENG-3201 (engine component) with a 3-day buffer is more critical than SUSP-4402 (suspension) at the same buffer, or that Maersk ocean delays at a specific port typically run 7+ days — the LLM reasons across all these dimensions simultaneously.

LLM calls run in parallel via `ThreadPoolExecutor(max_workers=4)`. Formula fallback available.

### 4. Mitigation Decision Agent (`agents/mitigation.py`)
Two paths depending on what the graph router decides after Agent 3:

**LLM path** (`plan_mitigation`) — triggered when any shipment is HIGH or MEDIUM severity. Calls Groq llama-3.3-70b-versatile with the full enriched context — including LLM summaries from Agents 2 and 3 — giving the model a complete picture. All per-shipment calls are parallelised via `ThreadPoolExecutor(max_workers=4)`.

**Rules path** (`fast_mitigation`) — triggered when all shipments are LOW severity. Applies the deterministic rule engine directly with zero LLM calls. Produces the same `mitigation_plan` shape so Agent 5 works identically downstream. The `decided_by` field records which path ran: `"llm"`, `"rules"`, or `"rules_fast_path"`.

Actions and their estimated costs:

| Action | Cost (USD) |
|--------|-----------|
| hold | $0 |
| notify_customer | $50 |
| reroute | $1,200 |
| expedite | $2,500 |
| mode_switch | $4,000 |
| escalate | $0 |

### 5. Autonomous Action Agent + HITL (`agents/action.py`)
Executes autonomously when **all three conditions** are met:
- `confidence ≥ 0.75`
- `cost_delta < $5,000` (configurable via `AUTONOMOUS_COST_THRESHOLD`)
- `action ≠ "escalate"`

Otherwise logs `[ESCALATE]` and sets `escalation_required = True`.

---

## Data Scoping Design

### SIM_NOW — the simulation clock

All dates in the seeded database were generated relative to a fixed anchor:

```python
# seed_data.py
NOW = datetime(2026, 5, 4, 8, 0, 0)

# e.g. a sales order commitment 10 days out = 2026-05-14
"delivery_commitment_date": NOW + timedelta(days=10)
```

The impact analysis agent needs to compute `days_so_breach = commitment_date − now`. If it used `datetime.now()` (the real clock), the math would be wrong because commitment dates are all relative to 2026-05-04, not today.

`SIM_NOW` is a shared frozen clock (`agents/utils.py`) that makes all date arithmetic meaningful within the simulation.

**In production with live ERP data**, replace it with:
```python
# agents/utils.py
SIM_NOW = datetime.utcnow()   # real clock
```
and drop the constant — date arithmetic works the same way.

---

### "Active" — what it means and why it matters

"Active shipment" = `status IN ('in_transit', 'delayed', 'customs_hold')` — i.e. `status != 'delivered'`.

The filter chain flows downstream through every table:

```
shipments  WHERE status != 'delivered'
    │  each row has po_id
    ▼
purchase_orders  WHERE po_id IN (active shipment po_ids)
    │  each PO has a material
    ▼
sales_orders  WHERE material IN (active PO materials)

shipments  (dest_plant, po → material)
    │
    ▼
inventory  WHERE (plant || '|' || material) IN (active dest+material pairs)
```

**Why this matters at scale:**

| Table | Demo rows | 50-plant enterprise | Without filter |
|-------|-----------|---------------------|----------------|
| shipments | 30 | 500 000 | load all → RAM spike |
| purchase_orders | 30 | 800 000 | load all → RAM spike |
| inventory | 25 | 4 000 000 | load all → minutes |
| **With active filter** | **~20** | **~2 000 open** | **milliseconds** |

The inventory filter uses SQLite string concatenation (`plant || '|' || material`) because SQLite has no native multi-column `IN` clause. In PostgreSQL you would write `WHERE (plant, material) IN (VALUES ...)`.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent orchestration | LangGraph 0.1.5 (StateGraph) |
| LLM (Agents 2, 3, 4) | Groq llama-3.3-70b-versatile via groq SDK |
| Parallelism | `concurrent.futures.ThreadPoolExecutor` (max 4 workers per agent) |
| Retry / rate-limit | Exponential backoff on `groq.RateLimitError` (2 s → 4 s) |
| API | FastAPI 0.111.0 + uvicorn |
| Database | SQLite via stdlib `sqlite3` (filtered queries, not SELECT *) |
| Mock data | Faker 25 + custom generators |
| Vector store | ChromaDB 0.5.0 (available, extensible) |

---

## Project Structure

```
supply_chain_ai/
├── main.py                    # uvicorn entry point
├── requirements.txt
├── .env.example               # copy → .env and fill keys
├── Dockerfile                 # container image definition
├── docker-compose.yml         # local deployment (one command)
├── entrypoint.sh              # seeds DB on first boot, then starts server
├── agents/
│   ├── utils.py               # shared Groq client, SIM_NOW, retry logic
│   ├── ingestion.py           # Agent 1 — scope-filtered DB reads
│   ├── risk_detection.py      # Agent 2 — 4-signal risk scoring (LLM + rules)
│   ├── impact_analysis.py     # Agent 3 — LLM holistic impact assessment
│   ├── mitigation.py          # Agent 4 — LLM action recommendation
│   └── action.py              # Agent 5 — autonomous execution / HITL
├── graph/
│   ├── state.py               # PipelineState dataclass (schema reference)
│   └── pipeline.py            # LangGraph StateGraph assembly
├── api/
│   └── routes.py              # FastAPI REST endpoints
├── scripts/
│   └── seed_data.py           # Mock data generator
└── data/
    ├── supply_chain.db        # Seeded SQLite database
    └── emails/                # 22 email JSON files
```

---

## Running the Project

Two options — pick whichever fits your environment.

---

### Option A — Python directly (recommended for development)

**Prerequisites:** Python 3.11 or 3.12 on your machine. Get your free Groq API key at https://console.groq.com (takes 30 seconds, no credit card).

**Step 1 — Navigate to the project folder**
```bash
cd supply_chain_ai
```

**Step 2 — Create and activate a virtual environment**
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac / Linux
python -m venv .venv
source .venv/bin/activate
```

**Step 3 — Install dependencies**
```bash
pip install -r requirements.txt
```

**Step 4 — Set your Groq API key**
```bash
# Windows
copy .env.example .env

# Mac / Linux
cp .env.example .env
```

Open `.env` and set:
```
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```
Everything else in `.env` has sensible defaults — you only need to set the key.

**Step 5 — The database is already seeded. Skip this unless starting fresh.**
```bash
python scripts/seed_data.py
```

**Step 6 — Start the server**
```bash
python main.py
```

**Step 7 — Verify it's running**
```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "supply-chain-ai"}
```

**Step 8 — Run the full pipeline**
```bash
curl -X POST http://localhost:8000/run
```

**Step 9 — Open interactive API docs**

Visit `http://localhost:8000/docs` in your browser. Every endpoint is listed with a "Try it out" button — no curl required.

---

### Option B — Docker (zero Python install needed)

**Prerequisites:** Docker Desktop installed and running. Groq API key from https://console.groq.com.

**Step 1 — Set your API key**
```bash
# Windows
copy .env.example .env

# Mac / Linux
cp .env.example .env
```
Open `.env`, set `GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx`.

**Step 2 — Build and start**
```bash
docker compose up --build
```

This builds the image, seeds the database automatically on first boot, and starts the server. Subsequent `docker compose up` reuses the existing database.

**Step 3 — Verify**
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/run
```

**Step 4 — Stop**
```bash
docker compose down
```

The `data/` folder is volume-mounted — your database survives `down` and `up` cycles.

**Re-seed if needed:**
```bash
docker compose exec api python scripts/seed_data.py
```

---

### Works without a Groq key too

If `GROQ_API_KEY` is empty or missing, every LLM call falls back automatically:

| Agent | LLM path | Fallback |
|-------|----------|----------|
| Risk Detection — email signal | Groq semantic analysis | Keyword matching |
| Risk Detection — milestone/GPS | Groq batch assessment | Confidence formula |
| Impact Analysis | Groq holistic reasoning | Severity formula |
| Mitigation | Groq action selection | Rule engine |

The pipeline runs end-to-end either way. LLM mode produces richer reasoning; fallback mode produces deterministic results.

---

## API Reference

### `GET /health`
```json
{"status": "ok", "service": "supply-chain-ai"}
```

### `POST /run`
Triggers the full 5-agent pipeline. Returns complete analysis.

```bash
curl -X POST http://localhost:8000/run
```

Example response (truncated):
```json
{
  "at_risk_count": 12,
  "autonomous_actions": 7,
  "escalations": 5,
  "escalation_required": true,
  "at_risk_shipments": [
    {"shipment_id": "SHP-PO-0001", "risk_type": "delayed|milestone_delay_18h|email_alert_x2", "confidence": 0.9},
    {"shipment_id": "SHP-PO-0004", "risk_type": "delayed|gps_stuck|email_alert_x1", "confidence": 0.9}
  ],
  "mitigation_plan": {
    "SHP-PO-0001": {
      "action": "expedite",
      "rationale": "High-priority material with 18h milestone delay; expediting prevents SO breach.",
      "confidence": 0.88,
      "cost_delta": 2500,
      "severity": "HIGH",
      "decided_by": "llm"
    }
  },
  "action_log": [
    "[AUTO] SHP-PO-0001 | action=expedite | severity=HIGH | cost_delta=$2,500 | Expedite request sent to carrier...",
    "[ESCALATE] SHP-PO-0004 | action=mode_switch | severity=HIGH | cost exceeds threshold ($4,000 >= $5,000)"
  ]
}
```

### `GET /shipments/at-risk`
Returns the at-risk shipment list from the last run.

```bash
curl http://localhost:8000/shipments/at-risk
```

### `GET /escalations`
Returns shipments pending human approval.

```bash
curl http://localhost:8000/escalations
```

### `POST /escalations/{shipment_id}/approve`
Human operator approves a flagged action.

```bash
curl -X POST http://localhost:8000/escalations/SHP-PO-0004/approve
```

```json
{
  "message": "Action 'mode_switch' for SHP-PO-0004 approved and executed.",
  "log": "[HUMAN_APPROVED] SHP-PO-0004 | action=mode_switch | approved and executed by human operator | ..."
}
```

### `GET /summary`
Dashboard-style summary counts.

```bash
curl http://localhost:8000/summary
```

```json
{
  "at_risk_shipments": 12,
  "autonomous_actions": 7,
  "escalations_pending": 5,
  "human_approved": 0,
  "escalation_required": true,
  "severity_breakdown": {"HIGH": 4, "MEDIUM": 6, "LOW": 2}
}
```

---

## Dynamic Routing & Autonomous Decision Logic

### Graph routing (pipeline level)

```
AFTER ingest:
  if no active shipments → END                         # nothing to monitor

AFTER detect_risk:
  if no at-risk shipments → END                        # all clear

AFTER analyze_impact:
  if ALL severity == LOW  → fast_mitigation (rules)   # minor issues, no LLM needed
  if ANY severity >= MEDIUM → plan_mitigation (Groq)  # consequential, LLM reasons
```

### Action decision (per shipment, inside execute_actions)

```
for each shipment in mitigation_plan:

  if confidence >= 0.75
  AND cost_delta  <  $5,000   (AUTONOMOUS_COST_THRESHOLD)
  AND action     !=  "escalate":
      → [AUTO]  log action, simulate execution

  else:
      → [ESCALATE]  flag for human, set escalation_required = True
                    human calls POST /escalations/{id}/approve
```

### decided_by field in mitigation_plan

Each entry records which reasoning path produced the action:

| Value | Meaning |
|---|---|
| `"llm"` | Groq llama-3.3-70b-versatile reasoned over full context |
| `"rules"` | Rule engine ran because GROQ_API_KEY was absent or LLM failed |
| `"rules_fast_path"` | Graph router chose fast_mitigation (all-LOW scenario) |
| `"error"` | Unexpected exception — action defaulted to `escalate` |

---

## Evaluation Criteria Mapping

| Criterion | Implementation |
|-----------|---------------|
| **System Design** | 5-agent LangGraph graph with conditional routing (3 dynamic branch points); FastAPI REST layer; scope-filtered SQLite queries |
| **Autonomy** | Graph skips agents that aren't needed; confidence + cost thresholds control AUTO vs ESCALATE; HITL approval endpoint |
| **Reasoning** | LLM used in 3 agents: Agent 2 (email semantics + batch milestone/GPS), Agent 3 (holistic impact), Agent 4 (action decision with enriched context from upstream LLMs) |
| **Data Integration** | Structured signals (milestone math, GPS speed) computed in Python; unstructured signals (emails) via LLM; both feed the same state |
| **Code Quality** | Each agent is a pure function; routing functions are separate from node logic; `decided_by` field makes every decision traceable |
| **Creativity** | Dynamic graph routing (not fixed pipeline); LLM batch assessment across shipments; inventory filtered to active (plant, material) pairs; cross-agent LLM context chaining |

---

## Running Without a Groq Key

Set `GROQ_API_KEY=` (empty) or omit it. The mitigation agent will use the built-in rule engine:

- `severity=HIGH` + GPS/customs → `mode_switch`
- `severity=HIGH` + other → `expedite`
- `severity=MEDIUM` → `reroute`
- `severity=LOW` → `notify_customer`

All other agents (ingestion, risk, impact, action) work without any API key.

---

## Docker Deployment

### Quick start

```bash
# 1. Copy env file and add your Groq key
cp .env.example .env
# edit .env → set GROQ_API_KEY=gsk_...

# 2. Build and start
docker compose up --build

# 3. API is live at http://localhost:8000
curl -X POST http://localhost:8000/run
```

### How it works

- The image is built from `python:3.11-slim`
- `entrypoint.sh` seeds the SQLite database on first boot if `data/supply_chain.db` is absent, then starts the server
- `data/` is mounted as a volume so the database and email files persist across container restarts
- Pass secrets via `.env` — never bake `GROQ_API_KEY` into the image

### Environment variables (all configurable in `.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq API key (required for LLM; rule fallback if absent) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model to use |
| `DB_PATH` | `data/supply_chain.db` | SQLite database path |
| `EMAILS_DIR` | `data/emails` | Email JSON files directory |
| `AUTONOMOUS_COST_THRESHOLD` | `5000` | Max USD cost for autonomous action |
| `CONFIDENCE_THRESHOLD` | `0.75` | Min confidence for autonomous action |
| `GPS_LOOKBACK_HOURS` | `4` | GPS time window for freeze detection |

### Re-seed the database inside the container

```bash
docker compose exec api python scripts/seed_data.py
```

---

## Stretch Goals Implemented

- Multi-shipment simulation: all 30 shipments processed in a single pipeline run
- Cross-shipment intelligence: impact agent correlates shared material across PO + SO + inventory
- Human-in-the-Loop: `/escalations` and `/escalations/{id}/approve` endpoints

## Potential Extensions

- ChromaDB semantic search over emails (collection schema ready in `chroma_store/`)
- Real-time streaming via Server-Sent Events on `/run/stream`
- ETA prediction model using GPS speed and milestone history
- Risk dashboard (React + Recharts) consuming `/summary` and `/shipments/at-risk`
