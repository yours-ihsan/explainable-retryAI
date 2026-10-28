"""Real PayPal Sandbox client.

Talks to the PayPal REST API using the keys in your .env file:
    PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_BASE_URL

Design rules
------------
* Every function returns a PayPalResult. It never raises on HTTP or network
  problems, so the caller can always feed the result to the decision engine.
* A timeout is NOT a failure. It means "we do not know what happened"
  (the decision engine treats it as UNKNOWN).
* Amounts are strings like "49.99". Never floats.
* Secrets and tokens are never printed or logged.
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TIMEOUT_SECONDS = 10

# Possible values of PayPalResult.outcome
OUTCOME_SUCCESS = "SUCCESS"              # PayPal answered with a 2xx status
OUTCOME_TIMEOUT = "TIMEOUT"              # no answer in time: result is UNKNOWN
OUTCOME_NETWORK_ERROR = "NETWORK_ERROR"  # connection failed: result is UNKNOWN
OUTCOME_HTTP_ERROR = "HTTP_ERROR"        # PayPal answered with 4xx or 5xx
OUTCOME_AUTH_ERROR = "AUTH_ERROR"        # could not get a token: payment never sent
OUTCOME_CONFIG_ERROR = "CONFIG_ERROR"    # .env values missing

# Issue codes we treat as an explicit decline.
# TODO: verify the exact list in PayPal's capture error docs.
DECLINE_ISSUES = {"INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"}

# Token cache: {"token": str, "expires_at": float}
_token_cache: dict = {}


@dataclass(frozen=True)
class PayPalResult:
    ok: bool
    outcome: str
    http_status: Optional[int] = None
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    paypal_error_name: Optional[str] = None
    paypal_issue: Optional[str] = None
    paypal_error_message: Optional[str] = None
    is_simulation: bool = False  # real PayPal call (sandbox money)


def _config() -> Optional[tuple]:
    base_url = os.environ.get("PAYPAL_BASE_URL", "").strip().rstrip("/")
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
    if not (base_url and client_id and client_secret):
        return None
    return base_url, client_id, client_secret


def _get_token() -> tuple:
    """Return (token, None) on success or (None, PayPalResult) on failure."""
    cached = _token_cache.get("token")
    if cached and time.time() < _token_cache.get("expires_at", 0):
        return cached, None

    config = _config()
    if config is None:
        return None, PayPalResult(
            ok=False,
            outcome=OUTCOME_CONFIG_ERROR,
            paypal_error_message="PAYPAL_BASE_URL, PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is missing in .env",
        )
    base_url, client_id, client_secret = config

    try:
        resp = requests.post(
            f"{base_url}/v1/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException:
        # No payment request was sent yet, so this is NOT an unknown payment state.
        return None, PayPalResult(ok=False, outcome=OUTCOME_AUTH_ERROR)

    if resp.status_code != 200:
        return None, PayPalResult(
            ok=False, outcome=OUTCOME_AUTH_ERROR, http_status=resp.status_code
        )
    try:
        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 300))
    except (ValueError, KeyError, TypeError):
        return None, PayPalResult(
            ok=False, outcome=OUTCOME_AUTH_ERROR, http_status=resp.status_code
        )

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + max(expires_in - 60, 0)
    return token, None


def _to_result(resp) -> PayPalResult:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if resp.status_code < 400:
        return PayPalResult(
            ok=True,
            outcome=OUTCOME_SUCCESS,
            http_status=resp.status_code,
            order_id=data.get("id"),
            order_status=data.get("status"),
        )

    if resp.status_code == 401:
        _token_cache.clear()  # token probably expired: fetch a new one next time

    issue = None
    details = data.get("details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        issue = details[0].get("issue")

    return PayPalResult(
        ok=False,
        outcome=OUTCOME_HTTP_ERROR,
        http_status=resp.status_code,
        paypal_error_name=data.get("name"),
        paypal_issue=issue,
        paypal_error_message=data.get("message"),
    )


def _call(method: str, path: str, json_body=None, request_id: Optional[str] = None) -> PayPalResult:
    token, failure = _get_token()
    if failure is not None:
        return failure

    base_url = _config()[0]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # idempotency key

    try:
        resp = requests.request(
            method,
            f"{base_url}{path}",
            headers=headers,
            json=json_body,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        return PayPalResult(ok=False, outcome=OUTCOME_TIMEOUT)
    except requests.exceptions.RequestException:
        return PayPalResult(ok=False, outcome=OUTCOME_NETWORK_ERROR)
    return _to_result(resp)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------
def create_order(amount: str, currency: str, request_id: str) -> PayPalResult:
    """Create an order. Reusing the same request_id returns the same order."""
    if not isinstance(amount, str):
        raise TypeError('amount must be a string like "49.99", never a float')
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"amount": {"currency_code": currency.upper(), "value": amount}}
        ],
    }
    return _call("POST", "/v2/checkout/orders", json_body=body, request_id=request_id)


def get_order(order_id: str) -> PayPalResult:
    """Read the real, current status of an order (CREATED, APPROVED, COMPLETED...)."""
    return _call("GET", f"/v2/checkout/orders/{order_id}")


def capture_order(order_id: str, request_id: str) -> PayPalResult:
    """Capture an order the buyer has approved. This is where money moves."""
    return _call(
        "POST",
        f"/v2/checkout/orders/{order_id}/capture",
        json_body={},
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# Bridge to the decision engine
# ---------------------------------------------------------------------------
def map_to_scenario(result: PayPalResult, operation: str) -> Optional[str]:
    """Turn a real PayPal result into a scenario name known to decision_engine.

    operation is "create", "get" or "capture".
    Returns None when no retry decision is needed or no rule applies.
    """
    if result.outcome in (OUTCOME_TIMEOUT, OUTCOME_NETWORK_ERROR):
        return "capture_timeout" if operation == "capture" else "network_timeout"

    if result.ok:
        if operation == "capture" and result.order_status == "COMPLETED":
            return "success"
        return None

    if result.outcome == OUTCOME_HTTP_ERROR:
        status = result.http_status or 0
        if status >= 500:
            return "temporary_server_error"
        if result.paypal_issue in DECLINE_ISSUES:
            return "payment_declined"
    return None