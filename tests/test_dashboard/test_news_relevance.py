from __future__ import annotations

from options_advisor.dashboard.news_relevance import find_cross_symbol_news

SYMBOLS = ["AAPL", "MSFT", "NVDA", "KO"]


def _item(headline: str, summary: str = "", published_at: str = "2026-07-23T10:00:00+00:00") -> dict:
    return {"headline": headline, "summary": summary, "published_at": published_at, "source": "Yahoo", "url": "https://x"}


def test_news_mentioning_two_symbols_is_flagged():
    items = [_item("Forget the Other Five: These 2 Mag7 Stocks — AAPL and MSFT lead the pack")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert len(result) == 1
    assert result[0]["mentioned_symbols"] == ["AAPL", "MSFT"]


def test_news_mentioning_single_symbol_is_excluded():
    items = [_item("Apple (AAPL) Earnings Expected to Grow")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert result == []


def test_news_mentioning_no_watchlist_symbol_is_excluded():
    items = [_item("Some unrelated headline about TSLA and RIVN")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert result == []


def test_mention_detected_in_summary_not_just_headline():
    items = [_item("Big tech roundup", summary="Analysts weigh in on AAPL and NVDA guidance")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert result[0]["mentioned_symbols"] == ["AAPL", "NVDA"]


def test_word_boundary_avoids_false_positive_substring_match():
    # "KO" (Coca-Cola) no debe matchear dentro de "broke" o "smoke"
    items = [_item("Market broke down as investors smoke-tested new AAPL guidance")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert result == []


def test_sorted_by_symbol_count_desc_then_published_at_desc():
    items = [
        _item("AAPL and MSFT news", published_at="2026-07-20T10:00:00+00:00"),
        _item("AAPL, MSFT and NVDA triple mention", published_at="2026-07-21T10:00:00+00:00"),
        _item("AAPL and NVDA news", published_at="2026-07-23T10:00:00+00:00"),
    ]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert [r["headline"] for r in result] == [
        "AAPL, MSFT and NVDA triple mention",
        "AAPL and NVDA news",
        "AAPL and MSFT news",
    ]


def test_case_insensitive_matching():
    items = [_item("aapl and msft rally together")]
    result = find_cross_symbol_news(items, SYMBOLS)
    assert result[0]["mentioned_symbols"] == ["AAPL", "MSFT"]
