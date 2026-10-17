import json

import requests

from trades import jev
from trades.proposal import Proposal


class Response:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def answers(**values):
    return {"model": "jev-1.13", "answers": {q: {"type": "noul", "noul": v} for q, v in values.items()}}


P = Proposal(ticker="NVDA", side="buy", size_pct=4, reasoning="trend pullback", stop=224)


def test_batches_three_questions_in_one_request(settings):
    calls = []

    def post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return Response(200, answers(setup_violation=0.1, revenge_trading=0.2, missing_stop=0.7))

    result = jev.ask(P, settings, post=post)
    assert result.probabilities == {"setup_violation": 0.1, "revenge_trading": 0.2, "missing_stop": 0.7}
    assert len(calls) == 1
    url, body, headers = calls[0]
    assert url == settings.jev_url and headers["Authorization"] == "Bearer k"
    assert body["model"] == "jev-test" and set(body["questions"]) == set(jev.QUESTIONS)
    assert all(isinstance(v, str) for v in body["state"].values())
    assert json.loads(body["state"]["proposal"])["stop"] == 224


def test_malformed_answer_is_an_error_not_a_guess(settings):
    bad = {"answers": {"setup_violation": {"type": "choice", "choice": "x"}}}
    result = jev.ask(P, settings, post=lambda *a, **k: Response(200, bad))
    assert result.probabilities is None and result.error == "malformed response"


def test_out_of_range_probability_rejected(settings):
    payload = answers(setup_violation=1.4, revenge_trading=0.1, missing_stop=0.1)
    assert jev.ask(P, settings, post=lambda *a, **k: Response(200, payload)).probabilities is None


def test_client_error_not_retried(settings):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return Response(401, {})
    result = jev.ask(P, settings, post=post)
    assert result.error == "HTTP 401" and len(calls) == 1


def test_connection_errors_retried_then_reported(settings, monkeypatch):
    monkeypatch.setattr(jev.time, "sleep", lambda s: None)

    def post(*a, **k):
        raise requests.ConnectionError()
    assert jev.ask(P, settings, post=post).error == "ConnectionError"


def test_no_key_is_unavailable(settings):
    from dataclasses import replace
    result = jev.ask(P, replace(settings, jev_key=None))
    assert result.probabilities is None and "no Jev key" in result.error


def test_cache_serves_repeat_requests(settings, tmp_path):
    cache = jev.Cache(tmp_path / "cache.json")
    calls = []

    def post(*a, **k):
        calls.append(1)
        return Response(200, answers(setup_violation=0.1, revenge_trading=0.1, missing_stop=0.1))
    first = jev.ask(P, settings, cache, post=post)
    second = jev.ask(P, settings, jev.Cache(tmp_path / "cache.json"), post=post)
    assert len(calls) == 1 and not first.cached and second.cached
    assert second.probabilities == first.probabilities


def test_network_secrets_send_no_key(settings):
    from dataclasses import replace
    s = replace(settings, jev_key=None, network_secrets=True)
    calls = []

    def post(url, json, headers, timeout):
        calls.append(headers)
        return Response(200, answers(setup_violation=0.1, revenge_trading=0.1, missing_stop=0.1))
    assert jev.ask(P, s, post=post).probabilities is not None
    assert calls == [{}]
