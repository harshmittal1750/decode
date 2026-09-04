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


def test_chart_renders_whole_book_ascending_with_spot_marker():
    row = {"spot": 150.0, "levels": [[100.0, 9.0], [200.0, 1.0], [50.0, 4.0]]}
    out = liq.chart(row, width=10).splitlines()
    prices = [ln.strip() for ln in out if ln.lstrip().startswith("$")]
    assert [p.split()[0] for p in prices] == ["$50", "$100", "$200"]      # ascending
    assert prices[0].split()[1] == "L" and prices[2].split()[1] == "S"    # sides
    marker = [i for i, ln in enumerate(out) if "SPOT" in ln]
    assert len(marker) == 1
    # marker sits between the last level below spot and the first above
    assert out[marker[0] - 1].lstrip().startswith("$100")
    assert out[marker[0] + 1].lstrip().startswith("$200")


def test_chart_marks_spot_above_every_level():
    row = {"spot": 999.0, "levels": [[100.0, 9.0], [200.0, 1.0]]}
    out = liq.chart(row, width=10)
    assert "SPOT" in out and out.rstrip().splitlines()[-4].strip().startswith("---")


def test_chart_handles_empty_book():
    assert liq.chart({"spot": 1.0, "levels": []}) == "empty book"


def test_zones_merge_adjacent_levels_into_one_wall():
    """Eight adjacent buckets are one wall, and must outrank a single fat bucket."""
    levels = [[100.0 + i * 10, 50.0] for i in range(8)]      # wall: 8 x 50 = 400
    levels += [[500.0, 200.0]]                                # lone fat bucket
    zs = liq.zones({"spot": 90.0, "levels": levels})
    assert len(zs) == 2
    assert zs[0]["usd"] == 400.0 and zs[0]["n"] == 8          # wall ranks first
    assert zs[0]["lo"] == 100.0 and zs[0]["hi"] == 170.0
    assert zs[1]["usd"] == 200.0 and zs[1]["n"] == 1


def test_zone_side_and_distance_use_the_near_edge():
    levels = [[100.0, 5.0], [110.0, 5.0], [300.0, 9.0], [310.0, 9.0]]
    zs = {(z["lo"], z["hi"]): z for z in liq.zones({"spot": 200.0, "levels": levels})}
    below, above = zs[(100.0, 110.0)], zs[(300.0, 310.0)]
    assert below["side"] == "long" and above["side"] == "short"
    # near edge: 110 is -45% from 200, not 100 (-50%)
    assert below["dist_pct"] == pytest.approx(-45.0)
    assert above["dist_pct"] == pytest.approx(50.0)


def test_zone_straddling_spot_is_labelled():
    zs = liq.zones({"spot": 105.0, "levels": [[100.0, 1.0], [110.0, 1.0]]})
    assert zs[0]["side"] == "straddles"


def test_zones_needs_two_levels():
    assert liq.zones({"spot": 1.0, "levels": [[100.0, 1.0]]}) == []


def test_ladder_orders_by_reach_and_accumulates():
    """Nearest zone first, cumulative = everything crossed to get there."""
    levels = [[110.0, 5.0], [120.0, 5.0], [200.0, 30.0], [210.0, 30.0]]
    lad = liq.ladder({"spot": 100.0, "levels": levels}, "short")
    assert [z["lo"] for z in lad] == [110.0, 200.0]        # nearest first
    assert lad[0]["cum_usd"] == 10.0
    assert lad[1]["cum_usd"] == 70.0                        # includes the near zone
    assert lad[0]["gap_pct"] is None                        # first zone has no gap
    # gap measured from previous zone's FAR edge (120) to this one's near edge (200)
    assert lad[1]["gap_pct"] == pytest.approx(100.0 - 20.0)


def test_ladder_splits_sides():
    levels = [[90.0, 3.0], [80.0, 3.0], [110.0, 7.0], [120.0, 7.0]]
    row = {"spot": 100.0, "levels": levels}
    assert all(z["hi"] < 100 for z in liq.ladder(row, "long"))
    assert all(z["lo"] > 100 for z in liq.ladder(row, "short"))


def test_walls_report_names_the_dominant_zone_and_its_forcing():
    levels = [[110.0, 1.0], [200.0, 50.0], [210.0, 50.0], [90.0, 2.0]]
    txt = liq.walls_report({"spot": 100.0, "levels": levels})
    assert "MOST shorts sit at $200" in txt   # core = the fat bucket, not the support
    assert "forced BUYING" in txt and "forced SELLING" in txt
    assert "not a forecast" in txt


def test_core_finds_the_narrow_wall_inside_a_wide_plateau():
    """Dense books cannot be gap-split: one peak + long tail merges into one zone.
    The core must report where the mass is, not the whole support."""
    levels = [[1000.0, 500.0], [1010.0, 500.0]]                # the wall
    levels += [[1020.0 + i * 10, 20.0] for i in range(40)]     # long thin tail
    zs = liq.zones({"spot": 900.0, "levels": levels})
    assert len(zs) == 1                                        # gaps are uniform: one zone
    z = zs[0]
    assert z["hi"] - z["lo"] == pytest.approx(410.0)           # wide support
    assert (z["core_lo"], z["core_hi"]) == (1000.0, 1010.0)    # narrow core
    assert z["core_usd"] == pytest.approx(1000.0)
    assert z["core_frac_of_width"] < 0.1


def test_core_equals_zone_when_mass_is_uniform():
    levels = [[100.0 + i * 10, 10.0] for i in range(4)]
    z = liq.zones({"spot": 50.0, "levels": levels})[0]
    assert z["core_frac_of_width"] <= 1.0
    assert z["core_usd"] >= sum(v for _, v in [(p, 10.0) for p in range(4)]) * 0.5


def test_walls_report_quotes_the_core_not_the_support():
    levels = [[1000.0, 500.0], [1010.0, 500.0]]
    levels += [[1020.0 + i * 10, 20.0] for i in range(40)]
    txt = liq.walls_report({"spot": 900.0, "levels": levels})
    assert "MOST shorts sit at $1,000-$1,010" in txt
    assert "tail out to $1,410" in txt
