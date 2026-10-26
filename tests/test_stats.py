import pytest

from holder_snapshot.stats import gini, summarize, top_share


def test_equal_holdings_have_zero_gini():
    assert gini([5, 5, 5, 5]) == pytest.approx(0)


def test_one_holder_with_everything_hits_the_maximum():
    n = 10
    assert gini([0] * (n - 1) + [1000]) == pytest.approx((n - 1) / n)


def test_known_value():
    # [1, 2, 3, 4]: sum of |xi - xj| over all pairs / (2 n^2 mean) = 0.25
    assert gini([1, 2, 3, 4]) == pytest.approx(0.25)


def test_order_does_not_matter():
    assert gini([4, 1, 3, 2]) == pytest.approx(gini([1, 2, 3, 4]))


def test_empty_and_all_zero_are_zero():
    assert gini([]) == 0.0
    assert gini([0, 0]) == 0.0


def test_handles_values_beyond_float_precision():
    # 1e30-scale token amounts must not lose the ordering
    big = 10**30
    assert gini([big, big]) == pytest.approx(0)


def test_top_share():
    assert top_share([50, 30, 20], 1) == pytest.approx(0.5)
    assert top_share([50, 30, 20], 2) == pytest.approx(0.8)
    assert top_share([50, 30, 20], 10) == pytest.approx(1.0)
    assert top_share([], 3) == 0.0


def test_summary_as_dict_uses_strings_for_big_totals():
    s = summarize({"a": 10**30, "b": 10**30}).as_dict()
    assert s["holders"] == 2
    assert s["total"] == str(2 * 10**30)
    assert s["top1Share"] == 0.5
