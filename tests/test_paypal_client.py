"""Mocked tests for paypal_client.py. No real network calls are made."""

import pytest
import requests

import paypal_client as pc


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://example.test")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    pc._token_cache.clear()
    yield
    pc._token_cache.clear()


@pytest.fixture
def token_calls(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})

    monkeypatch.setattr(pc.requests, "post", fake_post)
    return calls


def patch_request(monkeypatch, response=None, error=None):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["method"] = method
        seen["url"] = url
        seen["headers"] = kwargs.get("headers", {})
        seen["json"] = kwargs.get("json")
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(pc.requests, "request", fake_request)
    return seen


def test_create_order_sends_idempotency_header(monkeypatch, token_calls):
    seen = patch_request(monkeypatch, FakeResponse(201, {"id": "ORD1", "status": "CREATED"}))
    result = pc.create_order("49.99", "usd", "key-1")
    assert result.ok and result.order_id == "ORD1" and result.order_status == "CREATED"
    assert result.is_simulation is False
    assert seen["headers"]["PayPal-Request-Id"] == "key-1"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["json"]["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "49.99"}


def test_amount_must_be_string(token_calls):
    with pytest.raises(TypeError):
        pc.create_order(49.99, "USD", "key-1")


def test_timeout_is_not_a_failure(monkeypatch, token_calls):
    patch_request(monkeypatch, error=requests.exceptions.ReadTimeout())
    result = pc.create_order("10.00", "USD", "key-2")
    assert result.ok is False and result.outcome == pc.OUTCOME_TIMEOUT


def test_network_error(monkeypatch, token_calls):
    patch_request(monkeypatch, error=requests.exceptions.ConnectionError())
    assert pc.get_order("ORD1").outcome == pc.OUTCOME_NETWORK_ERROR


def test_http_error_keeps_paypal_details(monkeypatch, token_calls):
    payload = {"name": "UNPROCESSABLE_ENTITY", "message": "nope", "details": [{"issue": "INSTRUMENT_DECLINED"}]}
    patch_request(monkeypatch, FakeResponse(422, payload))
    result = pc.capture_order("ORD1", "key-3")
    assert result.outcome == pc.OUTCOME_HTTP_ERROR
    assert result.http_status == 422
    assert result.paypal_issue == "INSTRUMENT_DECLINED"


def test_token_is_cached(monkeypatch, token_calls):
    patch_request(monkeypatch, FakeResponse(200, {"id": "ORD1", "status": "CREATED"}))
    pc.get_order("ORD1")
    pc.get_order("ORD1")
    assert len(token_calls) == 1


def test_auth_failure_means_payment_never_sent(monkeypatch):
    monkeypatch.setattr(pc.requests, "post", lambda url, **kw: FakeResponse(401, {}))
    result = pc.create_order("10.00", "USD", "key-4")
    assert result.outcome == pc.OUTCOME_AUTH_ERROR


def test_missing_config(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    assert pc.get_order("ORD1").outcome == pc.OUTCOME_CONFIG_ERROR


def test_mapper_timeouts():
    timeout = pc.PayPalResult(ok=False, outcome=pc.OUTCOME_TIMEOUT)
    assert pc.map_to_scenario(timeout, "create") == "network_timeout"
    assert pc.map_to_scenario(timeout, "capture") == "capture_timeout"


def test_mapper_http_errors():
    server = pc.PayPalResult(ok=False, outcome=pc.OUTCOME_HTTP_ERROR, http_status=503)
    declined = pc.PayPalResult(ok=False, outcome=pc.OUTCOME_HTTP_ERROR, http_status=422, paypal_issue="INSTRUMENT_DECLINED")
    other = pc.PayPalResult(ok=False, outcome=pc.OUTCOME_HTTP_ERROR, http_status=400)
    assert pc.map_to_scenario(server, "create") == "temporary_server_error"
    assert pc.map_to_scenario(declined, "capture") == "payment_declined"
    assert pc.map_to_scenario(other, "create") is None


def test_mapper_success():
    done = pc.PayPalResult(ok=True, outcome=pc.OUTCOME_SUCCESS, http_status=201, order_status="COMPLETED")
    created = pc.PayPalResult(ok=True, outcome=pc.OUTCOME_SUCCESS, http_status=201, order_status="CREATED")
    assert pc.map_to_scenario(done, "capture") == "success"
    assert pc.map_to_scenario(created, "create") is None