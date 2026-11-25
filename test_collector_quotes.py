"""A chain without two-sided quotes must never count as a closing chain."""

from collector import MIN_QUOTED_SHARE, quoted_share


def test_yahoo_after_close_chain_has_no_quotes():
    rows = [{"bid": 0.0, "ask": 0.0}] * 50
    assert quoted_share(rows) == 0.0 < MIN_QUOTED_SHARE


def test_quoted_chain_passes_and_bad_values_count_as_unquoted():
    rows = [{"bid": 1.2, "ask": 1.4}] * 8 + [{"bid": "", "ask": None}, {"bid": 0, "ask": 0.05}]
    assert quoted_share(rows) == 0.8 >= MIN_QUOTED_SHARE
    assert quoted_share([]) == 0.0
