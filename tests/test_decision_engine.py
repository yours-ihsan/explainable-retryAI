"""Unit tests for the deterministic retry decision engine.

These tests do not touch FastAPI or SQLite - only the rule table.
"""

import pytest

import scenarios
from decision_engine import (
    RULES,
    VALID_CLASSIFICATIONS,
    UnknownScenarioError,
    decide,
)

# What milestone 1 promises for each simulated scenario.
EXPECTED = {
    "success": ("SUCCESS", False),
    "temporary_server_error": ("RETRYABLE", True),
    "payment_declined": ("NON_RETRYABLE", False),
    "network_timeout": ("UNKNOWN", False),
    "capture_timeout": ("UNKNOWN", False),
}


@pytest.mark.parametrize(
    "scenario,expected",
    list(EXPECTED.items()),
    ids=list(EXPECTED),
)
def test_every_scenario_classifies_as_promised(scenario, expected):
    decision = decide(scenario)
    classification, retry_recommended = expected
    assert decision.classification == classification
    assert decision.retry_recommended is retry_recommended
    assert decision.classification in VALID_CLASSIFICATIONS


def test_every_scenario_has_exactly_one_rule():
    """The catalog and the rule table must never drift apart."""
    assert set(scenarios.SCENARIOS) == set(RULES)


@pytest.mark.parametrize("scenario", ["network_timeout", "capture_timeout"])
def test_timeouts_are_unknown_and_never_auto_retried(scenario):
    """Rule 6: a timeout is UNKNOWN until the transaction status is checked."""
    decision = decide(scenario)
    assert decision.classification == "UNKNOWN"
    assert decision.retry_recommended is False
    # The next action must tell us to verify the status before any retry.
    assert "status" in decision.next_action.lower()


def test_only_temporary_server_error_is_retried():
    retryable = [name for name in RULES if decide(name).retry_recommended]
    assert retryable == ["temporary_server_error"]


def test_every_decision_explains_itself():
    for name in RULES:
        decision = decide(name)
        assert decision.explanation.strip(), name
        assert decision.next_action.strip(), name
        assert decision.rule_id.startswith("RULE-"), name
        assert decision.rule_condition.strip(), name


def test_same_input_always_gives_the_same_output():
    """Deterministic engine: no randomness, no LLM."""
    assert decide("capture_timeout") == decide("capture_timeout")
    assert decide("success") == decide("success")


def test_unknown_scenario_raises_a_clear_error():
    with pytest.raises(UnknownScenarioError) as excinfo:
        decide("give_me_free_money")
    assert "give_me_free_money" in str(excinfo.value)
