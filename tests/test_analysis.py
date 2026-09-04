"""Analysis guards -- each of these pins a bug that actually happened."""
import pytest

from decode.analysis import liq, pressure
from decode.analysis.pressure import AlignmentError


# --- liq ---------------------------------------------------------------------

def test_between_only_counts_levels_actually_crossed():
    """A move to 150 must not trigger the level at 100 -- it never gets there."""
    levels = {100.0: 9.0, 200.0: 1.0, 400.0: 4.0}
    assert liq.between(250, levels, 150) == {
        "side": "long", "total": 1.0, "levels": [(200.0, 1.0)]}
    assert liq.between(250, levels, 100)["total"] == 10.0     # now 100 is in range
    assert liq.between(250, levels, 400)["side"] == "short"


def test_at_picks_nearest_bucket():
    assert liq.at({100.0: 9.0, 200.0: 1.0}, 210) == (200.0, 1.0)


def test_per_level_skew_divides_out_window_geometry():
    """Raw skew is inflated when there is more price space below spot than above."""
    # 4 levels below at 10 each, 1 above at 10 -> raw 4x, but per-level 1x
    levels = {10.0: 10.0, 20.0: 10.0, 30.0: 10.0, 40.0: 10.0, 60.0: 10.0}
    s = liq.skew(50, levels)
    assert s["raw"] == pytest.approx(4.0)
    assert s["per_level"] == pytest.approx(1.0)


def test_skew_handles_spot_outside_the_book():
    s = liq.skew(1000, {10.0: 5.0, 20.0: 5.0})
    assert s["raw"] is None and s["above_usd"] == 0


def test_fuel_within_band():
    levels = {95.0: 1.0, 100.0: 2.0, 140.0: 8.0}
    assert liq.fuel_within(100, levels, 0.05) == 3.0


# --- pressure ----------------------------------------------------------------

def test_misaligned_basis_is_refused():
    """The +0.998 bug: a 'basis' with price-change magnitude must not be trusted."""
    fake = [0.0, 1.5, -2.0, 1.1, -1.4, 2.2]      # sd ~1.6%, i.e. a return series
    with pytest.raises(AlignmentError, match="not a basis"):
        pressure.sanity_check(fake)


def test_plausible_basis_accepted():
    pressure.sanity_check([0.01, -0.02, 0.03, -0.01, 0.02])


def test_decompose_attributes_moves_to_the_right_quadrant():
    # spot rises while basis expands -> futures-led up
    rows = [{"ts": i, "spot": s, "perp": s, "basis_pct": b}
            for i, (s, b) in enumerate([(100, 0.00), (101, 0.02), (102, 0.04),
                                        (101, 0.02), (100, 0.00)])]
    d = pressure.decompose(rows)
    q = d["quadrants"]
    assert q["FUTURES-led UP  "]["n"] == 2
    assert q["FUTURES-led DOWN"]["n"] == 2
    assert d["net_futures_led"] == pytest.approx(
        sum(x["cum"] for k, x in q.items() if k.startswith("FUTURES")))


def test_decompose_needs_enough_rows():
    with pytest.raises(ValueError, match=">=4"):
        pressure.decompose([{"ts": 0, "spot": 1, "perp": 1, "basis_pct": 0.0}])


def test_corr_is_zero_for_constant_series():
    assert pressure.corr([1, 1, 1], [1, 2, 3]) == 0.0
