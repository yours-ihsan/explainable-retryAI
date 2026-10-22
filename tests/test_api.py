"""API tests: /health, /simulate and /history.

Every test runs against a temporary SQLite file (see conftest.py).
"""

import pytest
from fastapi.testclient import TestClient

import scenarios
from main import app

EXPECTED = {
    "success": "SUCCESS",
    "temporary_server_error": "RETRYABLE",
    "payment_declined": "NON_RETRYABLE",
    "network_timeout": "UNKNOWN",
    "capture_timeout": "UNKNOWN",
}


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
def test_health_reports_a_running_backend(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["mode"] == "simulation"
    assert body["stored_simulations"] == 0


# ---------------------------------------------------------------------------
# POST /simulate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scenario,expected", list(EXPECTED.items()), ids=list(EXPECTED))
def test_simulate_classifies_every_scenario(client, scenario, expected):
    response = client.post(
        "/simulate",
        json={"scenario": scenario, "amount": 19.99, "currency": "usd"},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["classification"] == expected
    assert body["scenario"] == scenario
    assert body["retry_recommended"] is (expected == "RETRYABLE")

    # Every answer must come with a clear next action and explanation.
    assert body["next_action"].strip()
    assert body["explanation"].strip()
    assert body["rule_id"].startswith("RULE-")

    # Everything must be loudly labelled as a simulation.
    assert body["is_simulation"] is True
    assert "SIMULATED" in body["simulation_notice"]
    assert body["simulated_status"].strip()
    assert body["amount"] == 19.99
    assert body["currency"] == "USD"


@pytest.mark.parametrize("scenario", ["network_timeout", "capture_timeout"])
def test_timeouts_are_unknown_until_status_is_checked(client, scenario):
    body = client.post("/simulate", json={"scenario": scenario}).json()
    assert body["classification"] == "UNKNOWN"
    assert body["retry_recommended"] is False
    assert "status" in body["next_action"].lower()
    # The explanation must also tell the reader to verify the status.
    assert "status" in body["explanation"].lower()


def test_success_is_not_retried(client):
    body = client.post("/simulate", json={"scenario": "success"}).json()
    assert body["classification"] == "SUCCESS"
    assert body["retry_recommended"] is False


def test_declined_card_is_not_retried(client):
    body = client.post("/simulate", json={"scenario": "payment_declined"}).json()
    assert body["classification"] == "NON_RETRYABLE"
    assert body["retry_recommended"] is False


def test_scenario_names_are_case_and_space_insensitive(client):
    response = client.post("/simulate", json={"scenario": "  Network_Timeout "})
    assert response.status_code == 201
    assert response.json()["classification"] == "UNKNOWN"


def test_unknown_scenario_is_rejected_with_helpful_message(client):
    response = client.post("/simulate", json={"scenario": "free_money"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("Unknown scenario" in message for message in detail)
    assert any(scenarios.scenario_names()[0] in message for message in detail)


def test_missing_scenario_is_rejected(client):
    response = client.post("/simulate", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert response.json()["error"] == "validation_error"
    assert any("scenario" in message for message in detail)


def test_empty_scenario_is_rejected(client):
    response = client.post("/simulate", json={"scenario": "   "})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario": "success", "amount": -5},
        {"scenario": "success", "amount": 0},
        {"scenario": "success", "amount": 10.999},
        {"scenario": "success", "amount": 2_000_000},
        {"scenario": "success", "currency": "US"},
        {"scenario": "success", "currency": "DOLLARS"},
        {"scenario": "success", "attempt": 0},
        {"scenario": "success", "client_reference": "x" * 65},
    ],
    ids=[
        "negative-amount",
        "zero-amount",
        "too-many-decimals",
        "amount-too-large",
        "currency-too-short",
        "currency-not-a-code",
        "attempt-zero",
        "reference-too-long",
    ],
)
def test_invalid_fields_are_rejected(client, payload):
    response = client.post("/simulate", json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_amount_may_be_omitted(client):
    response = client.post("/simulate", json={"scenario": "success"})
    assert response.status_code == 201
    assert response.json()["amount"] is None


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------
def test_history_starts_empty(client):
    response = client.get("/history")
    assert response.status_code == 200
    assert response.json() == {"count": 0, "results": []}


def test_history_returns_saved_results_newest_first(client):
    client.post("/simulate", json={"scenario": "success"})
    client.post("/simulate", json={"scenario": "payment_declined"})

    body = client.get("/history").json()
    assert body["count"] == 2
    assert body["results"][0]["scenario"] == "payment_declined"
    assert body["results"][1]["scenario"] == "success"

    stored = body["results"][0]
    assert stored["classification"] == "NON_RETRYABLE"
    assert stored["explanation"].strip()
    assert stored["next_action"].strip()
    assert stored["is_simulation"] is True
    assert stored["simulation_id"] >= 1


def test_history_can_be_limited(client):
    for _ in range(3):
        client.post("/simulate", json={"scenario": "success"})

    body = client.get("/history", params={"limit": 2}).json()
    assert body["count"] == 2


def test_history_can_filter_by_scenario(client):
    client.post("/simulate", json={"scenario": "success"})
    client.post("/simulate", json={"scenario": "network_timeout"})
    client.post("/simulate", json={"scenario": "network_timeout"})

    body = client.get("/history", params={"scenario": "network_timeout"}).json()
    assert body["count"] == 2
    assert all(item["scenario"] == "network_timeout" for item in body["results"])


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"scenario": "nope"}])
def test_history_validates_query_parameters(client, params):
    response = client.get("/history", params=params)
    assert response.status_code == 422


def test_results_survive_a_server_restart(client, tmp_path):
    """The SQLite file really is the source of truth."""
    client.post("/simulate", json={"scenario": "temporary_server_error"})
    db_file = tmp_path / "test_history.db"
    assert db_file.exists()

    # A new TestClient = a fresh server start against the same database file.
    with TestClient(app) as restarted:
        body = restarted.get("/history").json()
        assert body["count"] == 1
        assert body["results"][0]["scenario"] == "temporary_server_error"


def test_unknown_endpoint_returns_json_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"] == "request_error"
