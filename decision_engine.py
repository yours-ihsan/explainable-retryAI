"""Deterministic retry decision engine.

This is a plain rule table: the same scenario always produces the same
decision. There is no randomness, no LLM and no network call.

The four possible classifications are:
    SUCCESS         -> the payment worked, never retry
    RETRYABLE       -> safe to retry the same request with backoff
    NON_RETRYABLE   -> repeating the request can never succeed (or is harmful)
    UNKNOWN         -> we do NOT know the outcome yet: never retry blindly,
                       check the transaction status first
"""

from dataclasses import dataclass

SUCCESS = "SUCCESS"
RETRYABLE = "RETRYABLE"
NON_RETRYABLE = "NON_RETRYABLE"
UNKNOWN = "UNKNOWN"

VALID_CLASSIFICATIONS = {SUCCESS, RETRYABLE, NON_RETRYABLE, UNKNOWN}


class UnknownScenarioError(ValueError):
    """Raised when the decision engine is asked about a scenario it has no rule for."""


@dataclass(frozen=True)
class Rule:
    """One explainable 'if this, then that' rule."""

    rule_id: str
    condition: str          # when this rule applies (plain English)
    classification: str
    retry_recommended: bool
    next_action: str        # what a human/system should do next
    explanation: str        # why the engine reached this decision


@dataclass(frozen=True)
class Decision:
    """The engine output for one scenario."""

    scenario: str
    classification: str
    retry_recommended: bool
    next_action: str
    explanation: str
    rule_id: str
    rule_condition: str


# ---------------------------------------------------------------------------
# The rule table (one rule per simulated scenario)
# ---------------------------------------------------------------------------
RULES: dict[str, Rule] = {
    "success": Rule(
        rule_id="RULE-1",
        condition="Gateway answered with a successful approval/capture",
        classification=SUCCESS,
        retry_recommended=False,
        next_action="Do not retry. Mark the order as paid, fulfil it and send the receipt.",
        explanation=(
            "The simulated payment was approved and captured, so the money is already "
            "collected. Retrying would charge the customer a second time, so the engine "
            "stops here."
        ),
    ),
    "temporary_server_error": Rule(
        rule_id="RULE-2",
        condition="Gateway returned a transient 5xx error (e.g. HTTP 503)",
        classification=RETRYABLE,
        retry_recommended=True,
        next_action=(
            "Retry the same request with exponential backoff (wait 2s, 4s, 8s) for up to "
            "3 attempts; if it still fails, show a 'try again later' message."
        ),
        explanation=(
            "HTTP 503 means the gateway was temporarily unavailable and did not process "
            "the payment. Nothing was charged, so repeating the identical request with "
            "the same idempotency key is safe and usually succeeds on a later attempt."
        ),
    ),
    "payment_declined": Rule(
        rule_id="RULE-3",
        condition="Gateway/issuer returned an explicit decline (HTTP 422 DECLINED)",
        classification=NON_RETRYABLE,
        retry_recommended=False,
        next_action=(
            "Do not retry. Tell the customer the payment was declined and ask for a "
            "different card or payment method."
        ),
        explanation=(
            "The simulated issuer rejected the payment on purpose (for example "
            "insufficient funds or a blocked card). Sending the exact same request again "
            "will be declined again, so the engine blocks the retry instead of wasting "
            "attempts or tripping fraud controls."
        ),
    ),
    "network_timeout": Rule(
        rule_id="RULE-4",
        condition="Request timed out with no response received",
        classification=UNKNOWN,
        retry_recommended=False,
        next_action=(
            "Do not retry yet. Look up the transaction status by order id / idempotency "
            "key first, and only retry if the payment is confirmed as NOT processed."
        ),
        explanation=(
            "The simulated request timed out before any answer arrived, so we cannot tell "
            "whether the payment went through or not. Retrying an uncertain payment can "
            "double-charge the customer, therefore a timeout is treated as UNKNOWN until "
            "the transaction status is checked."
        ),
    ),
    "capture_timeout": Rule(
        rule_id="RULE-5",
        condition="Capture call timed out with no confirmation",
        classification=UNKNOWN,
        retry_recommended=False,
        next_action=(
            "Do not re-capture. Query the capture status first; only resend the capture "
            "if it is not COMPLETE, reusing the same idempotency key."
        ),
        explanation=(
            "The simulated capture call never confirmed its result, so the order may "
            "already be captured. Re-capturing without checking could charge the customer "
            "twice, so a capture timeout is UNKNOWN until the capture status is verified."
        ),
    ),
}


def decide(scenario_name: str) -> Decision:
    """Return the deterministic decision for a scenario name.

    Raises UnknownScenarioError when no rule exists for the given name.
    """
    rule = RULES.get(scenario_name)
    if rule is None:
        raise UnknownScenarioError(
            f"No retry rule for scenario '{scenario_name}'. "
            f"Known scenarios: {', '.join(sorted(RULES))}"
        )
    return Decision(
        scenario=scenario_name,
        classification=rule.classification,
        retry_recommended=rule.retry_recommended,
        next_action=rule.next_action,
        explanation=rule.explanation,
        rule_id=rule.rule_id,
        rule_condition=rule.condition,
    )
