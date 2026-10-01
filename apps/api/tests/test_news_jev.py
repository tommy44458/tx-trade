import pytest

from trade_helper.news_jev import API_URL, QUESTIONS, classify_article, validate_response

ARTICLE = {"source": "licensed_test", "title": "A Bitcoin exchange suffers an outage",
           "body": "A large Bitcoin trading venue reported an order matching outage and paused "
                   "new orders while engineers investigated. The venue said it would publish "
                   "a status update after restoring the service.",
           "content_quality": "summary", "model_use_allowed": True}


def fake_response():
    answers = {}
    for name, question in QUESTIONS.items():
        if question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        elif question["type"] == "choice":
            names = list(question["criteria"])
            answers[name] = {"type": "choice", "choice": names[0], "confidence": 0.9,
                             "probabilities": {key: 1.0 if key == names[0] else 0.0
                                               for key in names}}
        else:
            answers[name] = {"type": "score", "score": 2.0, "confidence": 0.8,
                             "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0}}
    return {"model": "jev-1.13.0", "answers": answers,
            "usage": {"input_tokens": 250, "output_tokens": 40}}


def test_no_key_no_rights_or_title_only_never_calls_model(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert classify_article(ARTICLE)["status"] == "disabled"
    assert classify_article(ARTICLE | {"model_use_allowed": False}, key="test")["status"] == "blocked_rights"
    assert classify_article(ARTICLE | {"body": ARTICLE["title"]}, key="test")["status"] == "insufficient_text"


def test_jev_request_and_typed_response_are_validated():
    called = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_response()

    def post(url, **kwargs):
        called.append((url, kwargs))
        return Response()

    result = classify_article(ARTICLE, key="unit-test-key", post=post)
    assert result["status"] == "classified_unreviewed"
    assert result["model"] == "jev-1.13.0"
    assert len(result["input_hash"]) == 64
    assert called[0][0] == API_URL
    assert called[0][1]["headers"] == {"Authorization": "Bearer unit-test-key"}
    assert set(called[0][1]["json"]["questions"]) == set(QUESTIONS)
    assert "quantity" not in called[0][1]["json"]["state"]


def test_invalid_choice_and_probability_are_rejected():
    invalid = fake_response()
    invalid["answers"]["topic"]["choice"] = "buy_btc"
    with pytest.raises(ValueError, match="unsupported"):
        validate_response(invalid)
    invalid = fake_response()
    invalid["answers"]["market_relevance"]["noul"] = 1.4
    with pytest.raises(ValueError, match="0..1"):
        validate_response(invalid)


def test_unclassified_news_text_cannot_enter_strategy_agent_context():
    from trade_helper.agent import agent_context

    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h"}
    quote = {"observed_at": "2026-09-28T00:00:00+00:00", "news_context": {
        "risk": "none", "source_status": {"fed_monetary_rss": "ok"},
        "items": [{"title": "UNCLASSIFIED_SENTINEL"}], "archive": []}}
    context = agent_context(request, [{"close_time": "2026-09-27T23:59:59+00:00"}], quote, None)
    assert context["official_announcement_risk"]["strategy_news_evidence"] == "not_ready"
    assert "UNCLASSIFIED_SENTINEL" not in str(context)
