from __future__ import annotations

from options_advisor.market_context import fear_greed as fg


def test_parse_cnn_valid():
    payload = {"fear_and_greed": {"score": 60.4, "rating": "greed", "timestamp": "2026-08-07T00:00:00"}}
    out = fg._parse_cnn(payload)
    assert out["score"] == 60.4 and out["rating"] == "greed" and out["source"] == "CNN"


def test_parse_cnn_bad():
    assert fg._parse_cnn(None) is None
    assert fg._parse_cnn({}) is None
    assert fg._parse_cnn({"fear_and_greed": {"score": "x"}}) is None


def test_parse_fgc_nested_and_flat():
    assert fg._parse_fgc({"score": {"score": 55}})["score"] == 55.0
    assert fg._parse_fgc({"score": 42})["score"] == 42.0
    assert fg._parse_fgc({"score": {"score": None}}) is None
    assert fg._parse_fgc(None) is None


def test_rating_label_es_ranges():
    assert fg.rating_label_es(10) == "MIEDO EXTREMO"
    assert fg.rating_label_es(30) == "MIEDO"
    assert fg.rating_label_es(50) == "NEUTRAL"
    assert fg.rating_label_es(60) == "CODICIA"
    assert fg.rating_label_es(90) == "CODICIA EXTREMA"
