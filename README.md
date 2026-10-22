# Explainable Retry AI — Milestone 1

A **PayPal AI hackathon** project: a payment failure **simulator** plus a
**deterministic, explainable retry decision engine**, built with **FastAPI** and **SQLite**.

> ⚠️ **Everything here is a simulation.** No real PayPal payment is ever created and
> no LLM is called yet. Every API response carries a `SIMULATED TRANSACTION` notice so
> demo data can never be mistaken for real money movement.

---

## What this milestone does

| Endpoint | Purpose |
|---|---|
| `GET /health` | Check that the backend and the SQLite database are up. |
| `POST /simulate` | Create one simulated payment outcome and get back a classification, a recommended next action and a plain-English explanation. |
| `GET /history` | Read back previously stored simulations (newest first), including their explanations. |

### Classifications

| Classification | Meaning | Retry? |
|---|---|---|
| `SUCCESS` | The simulated payment worked. | Never — retrying would double-charge. |
| `RETRYABLE` | Transient error, nothing was charged. | Yes, with exponential backoff. |
| `NON_RETRYABLE` | Permanent failure (e.g. card declined). | Never — the same request will fail again. |
| `UNKNOWN` | The outcome is unclear (any **timeout**). | **Never blindly.** Check the transaction status first. |

### The five scenarios

| Scenario | Simulated gateway response | Classification |
|---|---|---|
| `success` | HTTP 201 Created (approved + captured) | `SUCCESS` |
| `temporary_server_error` | HTTP 503 Service Unavailable | `RETRYABLE` |
| `payment_declined` | HTTP 422 DECLINED (issuer refusal) | `NON_RETRYABLE` |
| `network_timeout` | No response after 30s | `UNKNOWN` |
| `capture_timeout` | No capture confirmation | `UNKNOWN` |

**Key safety rule (requirement 6):** a timeout is treated as `UNKNOWN` until the
transaction status is checked — the engine never recommends a blind retry, because
retrying an uncertain payment can double-charge the customer.

---

## Project structure

```
retry-copilot/
├── main.py              # FastAPI app: /health, /simulate, /history + validation & error handling
├── scenarios.py         # Catalog of the five simulated payment outcomes (all fake)
├── decision_engine.py   # Static rule table: scenario -> classification + action + explanation
├── database.py          # SQLite storage (creates retry_history.db automatically)
├── tests/
│   ├── conftest.py              # Test client wired to a temporary database
│   ├── test_api.py              # Endpoint, validation and history tests
│   └── test_decision_engine.py  # Rule engine unit tests
├── pytest.ini           # Pytest configuration
├── requirements.txt
├── .env.example         # Placeholder PayPal sandbox credentials (used in a LATER milestone)
└── README.md
```

Design notes for beginners:

* **No magic, no randomness.** `decision_engine.RULES` is a plain `dict` of rules, so the
  same scenario always produces the same decision — that is what makes it *explainable*.
* **One source of truth for scenarios.** `scenarios.SCENARIOS` defines what can be simulated;
  the engine must have a rule for every scenario (a test enforces this).
* **Database path is configurable** via the `RETRY_DB_PATH` environment variable
  (default `retry_history.db`), which is how the tests stay isolated.

---

## How to run

```bash
# 1. Create and activate a virtual environment (skip if you already have one)
python -m venv .venv
#   Windows:  .venv\Scripts\activate
#   macOS/Linux:  source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the server
uvicorn main:app --reload
```

Then open:

* API root / Swagger UI: <http://127.0.0.1:8000/docs>
* Health check: <http://127.0.0.1:8000/health>

### Try it with curl

```bash
# Health
curl http://127.0.0.1:8000/health

# A successful payment
curl -X POST http://127.0.0.1:8000/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "success", "amount": 49.99, "currency": "USD"}'

# A timeout -> must come back UNKNOWN, no retry
curl -X POST http://127.0.0.1:8000/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "network_timeout", "client_reference": "ORDER-1001"}'

# Previous results (stored in SQLite)
curl "http://127.0.0.1:8000/history?limit=10"
```

Example response for `network_timeout` (trimmed):

```json
{
  "simulation_id": 2,
  "is_simulation": true,
  "simulation_notice": "SIMULATED TRANSACTION - this is a fake payment created for the hackathon demo. No real PayPal payment was made.",
  "scenario": "network_timeout",
  "classification": "UNKNOWN",
  "retry_recommended": false,
  "next_action": "Do not retry yet. Look up the transaction status by order id / idempotency key first, and only retry if the payment is confirmed as NOT processed.",
  "explanation": "The simulated request timed out before any answer arrived, so we cannot tell whether the payment went through or not. ...",
  "rule_id": "RULE-4"
}
```

### Request validation

`POST /simulate` accepts:

| Field | Rules |
|---|---|
| `scenario` | **Required.** Must be one of the five names (case/space insensitive). Unknown names → `422` with the list of valid options. |
| `amount` | Optional. `> 0`, `≤ 1000000`, at most 2 decimal places. |
| `currency` | Optional, default `USD`. Exactly 3 letters; stored uppercase. |
| `client_reference` | Optional, max 64 characters. |
| `attempt` | Optional, `1–10`. |

Errors always come back as JSON: `{"error": "...", "detail": ["..."]}` with status
`422` (bad input) or `503` (database unavailable).

---

## How to run the tests

```bash
pytest -v
```

44 tests cover: every scenario's classification, the "timeouts are never blindly
retried" rule, request validation, history filtering/limits/persistence across a
server restart, and health.

---

## Roadmap (later milestones — *not* built yet)

1. ✅ **Milestone 1 (this):** simulator + deterministic rule engine + SQLite.
2. ⬜ Real PayPal sandbox integration (create/capture/query transactions) using `.env` credentials.
3. ⬜ LLM-generated explanations layered on top of (never replacing) the rule decision.
4. ⬜ A small demo UI showing the retry dashboard.
