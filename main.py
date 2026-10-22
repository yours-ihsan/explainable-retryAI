"""Explainable Retry AI - Milestone 1

A payment failure SIMULATOR plus a deterministic (rule-based) retry decision
engine, built with FastAPI and SQLite.

What this app does
------------------
* POST  /simulate  - pretend a payment scenario happened and get a
                     classification (SUCCESS / RETRYABLE / NON_RETRYABLE /
                     UNKNOWN), a recommended next action and an explanation.
* GET   /history   - list the simulations stored in SQLite, newest first.
* GET   /health    - quick liveness check.

What this app does NOT do (later milestones)
--------------------------------------------
* It never calls PayPal (no real money moves).
* It never calls an LLM. Every decision comes from the static rule table in
  `decision_engine.py`, so the same input always gives the same output.

Every response is clearly labelled as a simulation.
"""

import sqlite3
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

import database
import decision_engine
import scenarios

APP_VERSION = "0.1.0"
SIMULATION_NOTICE = (
    "SIMULATED TRANSACTION - this is a fake payment created for the hackathon demo. "
    "No real PayPal payment was made."
)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the SQLite table once, when the server starts."""
    try:
        database.init_db()
    except sqlite3.Error as exc:  # pragma: no cover - only fires on bad disk/permissions
        raise RuntimeError(
            f"Could not open the SQLite database at '{database.get_db_path()}': {exc}"
        ) from exc
    yield


app = FastAPI(
    title="Explainable Retry AI (Payment Simulator)",
    version=APP_VERSION,
    description=(
        "Hackathon milestone 1: a simulated payment failure playground with a "
        "deterministic, explainable retry decision engine. Everything is simulated - "
        "no real PayPal integration and no LLM yet."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Friendly error responses
# ---------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Turn pydantic's raw errors into short, readable messages."""
    messages = []
    for err in exc.errors():
        location = " -> ".join(str(part) for part in err["loc"] if part != "body")
        messages.append(f"{location or 'body'}: {err['msg']}")
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": messages},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Same JSON shape for 404s, 405s and the errors we raise ourselves."""
    detail: Any = exc.detail
    if isinstance(detail, str):
        detail = [detail]  # keep `detail` a list of messages, like the 422 handler
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "request_error", "detail": detail},
        headers=getattr(exc, "headers", None),
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class SimulateRequest(BaseModel):
    """Body accepted by POST /simulate."""

    scenario: str = Field(
        ...,
        description="Which simulated payment outcome to create.",
        examples=["network_timeout"],
    )
    amount: Optional[float] = Field(
        default=None,
        gt=0,
        le=1_000_000,
        description="Optional simulated amount (> 0, max 1000000, at most 2 decimals).",
        examples=[49.99],
    )
    currency: str = Field(
        default="USD",
        pattern=r"^[A-Za-z]{3}$",
        description="Three-letter currency code, e.g. USD, EUR, GBP.",
        examples=["USD"],
    )
    client_reference: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Optional order/reference id you want to correlate with this run.",
        examples=["ORDER-1001"],
    )
    attempt: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Which attempt of the retry loop this simulation represents.",
        examples=[1],
    )

    @field_validator("scenario")
    @classmethod
    def normalize_scenario(cls, value: str) -> str:
        """Accept 'Network_Timeout ' as well as 'network_timeout'."""
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("scenario must not be empty")
        return cleaned

    @field_validator("amount")
    @classmethod
    def amount_has_max_two_decimals(cls, value: Optional[float]) -> Optional[float]:
        """Money has cents: reject 10.999 style amounts."""
        if value is None:
            return value
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as exc:  # pragma: no cover - pydantic already checked float
            raise ValueError("amount must be a valid number") from exc
        if decimal_value.as_tuple().exponent < -2:
            raise ValueError("amount may have at most 2 decimal places (e.g. 19.99)")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()


class SimulateResponse(BaseModel):
    """Body returned by POST /simulate."""

    simulation_id: int
    is_simulation: bool = True
    simulation_notice: str = SIMULATION_NOTICE
    scenario: str
    scenario_label: str
    simulated_status: str
    simulated_message: str
    classification: str = Field(description="SUCCESS | RETRYABLE | NON_RETRYABLE | UNKNOWN")
    retry_recommended: bool
    next_action: str
    explanation: str
    rule_id: str
    rule_condition: str
    attempt: int
    amount: Optional[float] = None
    currency: Optional[str] = None
    client_reference: Optional[str] = None
    created_at: str


class HistoryItem(BaseModel):
    """One stored simulation returned by GET /history."""

    simulation_id: int
    is_simulation: bool
    scenario: str
    classification: str
    retry_recommended: bool
    next_action: str
    explanation: str
    rule_id: str
    attempt: int
    amount: Optional[float] = None
    currency: Optional[str] = None
    client_reference: Optional[str] = None
    created_at: str


class HistoryResponse(BaseModel):
    count: int
    results: list[HistoryItem]


class HealthResponse(BaseModel):
    status: str = Field(description="ok when the API and database respond")
    service: str
    version: str
    database: str = Field(description="connected | error")
    stored_simulations: Optional[int]
    mode: str = "simulation"
    simulation_notice: str = SIMULATION_NOTICE


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Verify the backend (and its database) are up."""
    try:
        stored = database.count_simulations()
    except sqlite3.Error:
        return HealthResponse(
            status="degraded",
            service="explainable-retry-ai",
            version=APP_VERSION,
            database="error",
            stored_simulations=None,
        )
    return HealthResponse(
        status="ok",
        service="explainable-retry-ai",
        version=APP_VERSION,
        database="connected",
        stored_simulations=stored,
    )


@app.post(
    "/simulate",
    response_model=SimulateResponse,
    status_code=201,
    tags=["simulation"],
)
def simulate(payload: SimulateRequest) -> SimulateResponse:
    """Create one simulated payment and explain the retry decision for it."""
    # 1. Validate the scenario name with a clear, actionable message.
    if scenarios.get_scenario(payload.scenario) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown scenario '{payload.scenario}'. "
                f"Choose one of: {', '.join(scenarios.scenario_names())}"
            ),
        )

    scenario = scenarios.get_scenario(payload.scenario)
    assert scenario is not None  # guard: validated just above

    # 2. Ask the deterministic rule engine what to do.
    decision = decision_engine.decide(payload.scenario)

    # 3. Persist the result (and the explanation) in SQLite.
    created_at = database.utc_now()
    try:
        simulation_id = database.save_simulation(
            {
                "scenario": decision.scenario,
                "classification": decision.classification,
                "retry_recommended": decision.retry_recommended,
                "next_action": decision.next_action,
                "explanation": decision.explanation,
                "rule_id": decision.rule_id,
                "attempt": payload.attempt,
                "amount": payload.amount,
                "currency": payload.currency,
                "client_reference": payload.client_reference,
                "created_at": created_at,
            }
        )
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not save the simulation result: {exc}",
        ) from exc

    # 4. Answer with the decision and a human readable explanation.
    return SimulateResponse(
        simulation_id=simulation_id,
        scenario=decision.scenario,
        scenario_label=scenario.label,
        simulated_status=scenario.simulated_status,
        simulated_message=scenario.simulated_message,
        classification=decision.classification,
        retry_recommended=decision.retry_recommended,
        next_action=decision.next_action,
        explanation=decision.explanation,
        rule_id=decision.rule_id,
        rule_condition=decision.rule_condition,
        attempt=payload.attempt,
        amount=payload.amount,
        currency=payload.currency,
        client_reference=payload.client_reference,
        created_at=created_at,
    )


@app.get("/history", response_model=HistoryResponse, tags=["simulation"])
def history(
    limit: int = Query(default=20, ge=1, le=100, description="How many results to return (1-100)."),
    scenario: Optional[str] = Query(default=None, description="Optional scenario name filter."),
) -> HistoryResponse:
    """Return previous simulation results from SQLite, newest first."""
    if scenario is not None:
        scenario = scenario.strip().lower()
        if scenario and scenarios.get_scenario(scenario) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown scenario filter '{scenario}'. "
                    f"Choose one of: {', '.join(scenarios.scenario_names())}"
                ),
            )
        if not scenario:
            scenario = None

    try:
        rows = database.get_history(limit=limit, scenario=scenario)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not read the simulation history: {exc}",
        ) from exc

    results = [
        HistoryItem(
            simulation_id=row["id"],
            is_simulation=row["is_simulation"],
            scenario=row["scenario"],
            classification=row["classification"],
            retry_recommended=row["retry_recommended"],
            next_action=row["next_action"],
            explanation=row["explanation"],
            rule_id=row["rule_id"],
            attempt=row["attempt"],
            amount=row["amount"],
            currency=row["currency"],
            client_reference=row["client_reference"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return HistoryResponse(count=len(results), results=results)
