"""Option implied probabilities and the ladder statistics. Synthetic chains with
known answers check the whole path: forward from put call parity, out of the
money conversion, implied vol, smile fit and the finite difference."""
import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

import match_close as mc
import yes_tilt as yt


def black_put(f, k, sigma, t):
    return mc.black_call(f, k, sigma, t) - (f - k)


def synthetic_chain(f, vol, t, expiry="2026-11-20", half_spread=0.05):
    rows = []
    for k in np.arange(f - 400, f + 401, 10.0):
        sigma = vol(k)
        for cp, price in (("C", mc.black_call(f, k, sigma, t)), ("P", black_put(f, k, sigma, t))):
            rows.append({"root": "SPXW", "expiry": expiry, "cp": cp, "strike": k,
                         "bid": max(price - half_spread, 0.0), "ask": price + half_spread})
    # Another root and expiry that must be ignored.
    rows.append({"root": "SPX", "expiry": expiry, "cp": "C", "strike": f, "bid": 999, "ask": 1000})
    rows.append({"root": "SPXW", "expiry": "2026-11-27", "cp": "C", "strike": f, "bid": 999, "ask": 1000})
    return pd.DataFrame(rows)


def digital(f, k, sigma, t):
    s = sigma * np.sqrt(t)
    return norm.cdf((np.log(f / k) - s * s / 2) / s)


def test_black_call_limits():
    assert mc.black_call(100.0, 90.0, 0.0, 1.0) == 10.0
    assert mc.black_call(100.0, 110.0, 0.0, 1.0) == 0.0
    # At the money: F * (2 N(s/2) - 1)
    s = 0.2
    assert mc.black_call(100.0, 100.0, s, 1.0) == pytest.approx(100 * (2 * norm.cdf(s / 2) - 1))


def test_implied_vol_round_trip_and_bad_prices():
    f, t = 7300.0, 1 / 252
    for k in (7100.0, 7300.0, 7450.0):
        price = mc.black_call(f, k, 0.18, t)
        assert mc.implied_vol(price, f, k, t) == pytest.approx(0.18, abs=1e-6)
    assert np.isnan(mc.implied_vol(50.0, f, 7200.0, t))    # below intrinsic of 100
    assert np.isnan(mc.implied_vol(1e6, f, 7300.0, t))     # above any vol in range


def test_option_probabilities_flat_vol_recovers_digital():
    f, t, vol = 7300.0, 1 / 252, 0.15
    chain = synthetic_chain(f, lambda k: vol, t)
    strikes = [7200.0, 7250.0, 7300.0, 7350.0, 7400.0]
    probs, info = mc.option_probabilities(chain, "2026-11-20", t, strikes)
    assert info["forward"] == pytest.approx(f, abs=0.5)
    assert info["atm_iv"] == pytest.approx(vol, abs=2e-3)
    for k in strikes:
        assert probs[k] == pytest.approx(digital(f, k, vol, t), abs=0.01)
    # Probabilities fall as the strike rises.
    assert all(probs[a] >= probs[b] for a, b in zip(strikes, strikes[1:]))


def test_option_probabilities_skew_moves_probability_the_right_way():
    """P(F >= K) = -dC/dK = N(d2) - vega * dsigma/dK. With put skew dsigma/dK
    is negative, so the probability sits above the flat vol digital."""
    f, t = 7300.0, 5 / 252
    skew = lambda k: 0.16 - 0.4 * np.log(k / f)
    chain = synthetic_chain(f, skew, t)
    probs, _ = mc.option_probabilities(chain, "2026-11-20", t, [f])
    s = skew(f) * np.sqrt(t)
    vega = f * norm.pdf(s / 2) * np.sqrt(t)
    dsigma_dk = -0.4 / f
    expected = digital(f, f, skew(f), t) - vega * dsigma_dk
    assert probs[f] == pytest.approx(expected, abs=0.01)
    assert probs[f] > digital(f, f, skew(f), t)


def test_option_probabilities_outside_fit_range_is_nan():
    f, t = 7300.0, 1 / 252
    chain = synthetic_chain(f, lambda k: 0.15, t)
    probs, info = mc.option_probabilities(chain, "2026-11-20", t, [5000.0, 9000.0])
    assert np.isnan(probs[5000.0]) and np.isnan(probs[9000.0])
    assert info["fit_lo"] > 5000.0 and info["fit_hi"] < 9000.0


def test_pick_reads_plain_dollar_and_fp_fields():
    assert mc.pick({"yes_bid": 0.4}, "yes_bid") == 0.4
    assert mc.pick({"yes_bid_dollars": "0.41"}, "yes_bid") == 0.41
    assert mc.pick({"volume_fp": "12"}, "volume") == 12.0
    assert mc.pick({}, "yes_bid") is None and mc.pick(None, "yes_bid") is None


# ------------------------------------------------------------------ ladder stats

def test_cluster_mean_matches_hand_calculation():
    values = [1.0, 2.0, 3.0, 6.0]
    mean, se = yt.cluster_mean(values, ["a", "a", "b", "b"])
    # residuals -2, -1, 0, 3; cluster sums -3 and 3
    assert mean == 3.0
    assert se == pytest.approx(np.sqrt(9 + 9) / 4)


def test_cluster_mean_singletons_is_heteroskedastic_se():
    rng = np.random.default_rng(0)
    v = rng.normal(size=50)
    _, se = yt.cluster_mean(v, np.arange(50))
    assert se == pytest.approx(np.sqrt(((v - v.mean()) ** 2).sum()) / 50)


def ladder(strikes, mids):
    return pd.DataFrame({"strike": strikes, "mid": mids})


def test_implied_median_interpolates_the_crossing():
    g = ladder([7200, 7250, 7300, 7350], [0.90, 0.70, 0.40, 0.10])
    assert yt.implied_median(g) == pytest.approx(7250 + (0.20 / 0.30) * 50)


def test_implied_median_rejects_unbracketed_or_wide_crossing():
    assert yt.implied_median(ladder([7200, 7250], [0.9, 0.7])) is None
    assert yt.implied_median(ladder([7200, 7250], [0.95, 0.10])) is None   # gap 0.85 > MAX_GAP


def test_implied_mean_of_uniform_distribution_is_its_midpoint():
    k = np.arange(7000, 7401, 50.0)
    p = 1 - (k - 7000) / 400          # P(S >= K) for S uniform on [7000, 7400]
    assert yt.implied_mean(ladder(k, p)) == pytest.approx(7200.0)


def test_implied_mean_needs_tail_coverage_and_monotone_quotes():
    assert yt.implied_mean(ladder([7200, 7250, 7300, 7350], [0.80, 0.6, 0.4, 0.02])) is None
    # A non monotone quote is capped by the running minimum, not allowed to add area.
    k = [7000.0, 7100.0, 7200.0, 7300.0, 7400.0]
    clean = yt.implied_mean(ladder(k, [1.0, 0.75, 0.5, 0.25, 0.0]))
    bumped = yt.implied_mean(ladder(k, [1.0, 0.75, 0.9, 0.25, 0.0]))
    # 0.9 is capped to 0.75, which widens the two trapezoids around 7200 by
    # (0.75 - 0.5) / 2 * 100 each: 25 index points in total.
    assert bumped == pytest.approx(clean + 25.0)
    assert bumped < yt.implied_mean(ladder(k, [1.0, 0.9, 0.9, 0.25, 0.0])) + 1e-9
