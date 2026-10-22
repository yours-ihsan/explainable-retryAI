"""Catalog of simulated payment scenarios.

IMPORTANT: Everything in this file is FAKE.
No PayPal API is called, no money moves, and no real transaction exists.
Each entry simply describes what a pretend payment gateway would return so the
retry decision engine in `decision_engine.py` has something to reason about.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """One fake payment outcome we can ask the simulator to produce."""

    name: str           # value the API accepts
    label: str          # short human friendly title
    simulated_status: str  # what the pretend gateway returned
    simulated_message: str  # plain language description of the fake error


# The five scenarios required for milestone 1.
SCENARIOS: dict[str, Scenario] = {
    "success": Scenario(
        name="success",
        label="Payment approved",
        simulated_status="HTTP 201 Created",
        simulated_message="The pretend gateway approved and captured the payment.",
    ),
    "temporary_server_error": Scenario(
        name="temporary_server_error",
        label="Temporary server error",
        simulated_status="HTTP 503 Service Unavailable",
        simulated_message="The pretend gateway is overloaded for a few seconds.",
    ),
    "payment_declined": Scenario(
        name="payment_declined",
        label="Card declined by issuer",
        simulated_status="HTTP 422 DECLINED",
        simulated_message="The pretend card issuer refused the payment.",
    ),
    "network_timeout": Scenario(
        name="network_timeout",
        label="Network timeout (no response)",
        simulated_status="No response received",
        simulated_message="The pretend request timed out after 30 seconds with no answer.",
    ),
    "capture_timeout": Scenario(
        name="capture_timeout",
        label="Capture timeout (no confirmation)",
        simulated_status="No capture confirmation",
        simulated_message="The pretend capture call timed out before confirming the result.",
    ),
}


def scenario_names() -> list[str]:
    """Return every scenario name, in a stable order."""
    return list(SCENARIOS)


def get_scenario(name: str) -> Scenario | None:
    """Look up a scenario by name, or return None when it does not exist."""
    return SCENARIOS.get(name)
