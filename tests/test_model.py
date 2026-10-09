"""
test_model.py — model-invariant and state tests for Juicetification: Capacity Crush.

These tests load the Streamlit app module headlessly (a tiny stub stands in for `streamlit`
so no server or browser is needed) and exercise the simulation engine and the assessment /
progress logic directly. Run from the repo root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_model.py

They cover the invariants called out in the improvement spec:
  - conservation of units (material balance)
  - deterministic reproducibility (same seed -> same run)
  - scrap / yield reconciliation
  - actual supplier-order counting (instrumentation)
  - Little's Law compatibility (no-scrap case)
  - service-level vs. customer fill-rate are distinct measures
  - completion vs. pass are tracked separately (out of tries != passed)
  - Director config -> snapshot expansion
"""

import importlib.util
import os
import sys
import types
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# --------------------------------------------------------------------------------------
# Minimal Streamlit stub so the app module imports without a running server.
# --------------------------------------------------------------------------------------
def _install_stub():
    class _QP(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    class _Stub:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getattr__(self, n):
            return _Stub()

        def __call__(self, *a, **k):
            return self

        def __bool__(self):
            return False

    class _SS(dict):
        def __getattr__(self, n):
            return self[n]

        def __setattr__(self, n, v):
            self[n] = v

    class _ST(types.ModuleType):
        def __init__(self):
            super().__init__("streamlit")
            self.session_state = _SS()
            self.query_params = _QP()
            self.secrets = {}

        def __getattr__(self, n):
            return _Stub()

        def columns(self, spec, **k):
            return [_Stub() for _ in range(spec if isinstance(spec, int) else len(spec))]

        def set_page_config(self, *a, **k):
            return None

        def dialog(self, *a, **k):
            def _d(f):
                return f
            return _d

        def cache_data(self, *a, **k):
            # Passthrough for the app's @st.cache_data on the scan functions. Supports both
            # @st.cache_data and @st.cache_data(...). (Real caching only matters in the app.)
            if a and callable(a[0]) and not k:
                return a[0]

            def _deco(fn):
                return fn
            return _deco

        def file_uploader(self, *a, **k):
            return None          # matches real Streamlit: None until a file is uploaded

        def button(self, *a, **k):
            return False

        def download_button(self, *a, **k):
            return False

    st = _ST()
    st.sidebar = _Stub()
    st.column_config = _Stub()
    comp = types.ModuleType("streamlit.components")
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *a, **k: None
    comp.v1 = v1
    st.components = comp
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = comp
    sys.modules["streamlit.components.v1"] = v1
    return st


def _load_app():
    _install_stub()
    sys.path.insert(0, ROOT)
    for m in ("juice_director", "manifest", "student_store", "jcc_app"):
        sys.modules.pop(m, None)
    spec = importlib.util.spec_from_file_location("jcc_app", os.path.join(ROOT, "juicetification.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APP = _load_app()
H = APP.HOURS_PER_YEAR
N = APP.N_OPS


def _pad(xs, fill=0):
    return list(xs) + [fill] * (N - len(xs))


def _run(dice, sides, seed=7, **kw):
    random.seed(seed)
    return APP.run_simulation(
        _pad(dice), _pad(sides),
        int(kw.get("start_inv", 0)), kw.get("hours", H),
        kw.get("supply_reliability", 1.0), kw.get("wip_limits"),
        track_flow=kw.get("track_flow", True),
        demand_dice=kw.get("demand_dice", 0), demand_faces=kw.get("demand_faces", 0),
        order_size=kw.get("order_size", 1), reorder_point=kw.get("reorder_point"),
        scrap=kw.get("scrap"),
    )


# A representative spread of line configurations the labs actually use.
CASES = {
    "balanced": dict(dice=[1] * 6, sides=[6] * 6),
    "bottleneck": dict(dice=[1] * 6, sides=[6, 6, 6, 2, 6, 6]),
    "unreliable": dict(dice=[1] * 6, sides=[6] * 6, supply_reliability=0.5,
                       reorder_point=40, order_size=10),
    "scrap": dict(dice=[1] * 6, sides=[6] * 6, scrap=_pad([0, 0, 0.6, 0, 0, 0])),
    "demand": dict(dice=[1] * 6, sides=[6] * 6, demand_dice=6, demand_faces=6),
    "wipcap": dict(dice=[1] * 6, sides=[6] * 6, wip_limits=[10] * N),
    "single": dict(dice=[1], sides=[6]),
}


def test_conservation_of_units():
    """initial + received == finished + scrapped + ending, exactly, for every case."""
    for name, cfg in CASES.items():
        r = _run(**cfg)
        lhs = r["initial_material"] + r["raw_received"]
        rhs = r["total_output"] + r["scrap_total"] + r["ending_material"]
        assert r["material_residual"] == 0, f"{name}: residual {r['material_residual']}"
        assert lhs == rhs, f"{name}: {lhs} != {rhs}"


def test_deterministic_reproducibility():
    """Same seed + same config -> identical output; a different seed -> (almost surely) different."""
    for name, cfg in CASES.items():
        a = _run(seed=7, **cfg)
        b = _run(seed=7, **cfg)
        assert a["total_output"] == b["total_output"], f"{name}: not reproducible"
        assert a["material_residual"] == 0
    c = _run(seed=7, **CASES["balanced"])
    d = _run(seed=8, **CASES["balanced"])
    assert c["total_output"] != d["total_output"], "seeds 7 and 8 gave identical output"


def test_scrap_reconciliation():
    """With scrap on, good output + scrap accounts for everything worked; yield_rate is consistent."""
    r = _run(**CASES["scrap"])
    assert r["scrap_total"] > 0, "expected some scrap with a 60% scrap station"
    denom = r["total_output"] + r["scrap_total"]
    assert abs(r["yield_rate"] - r["total_output"] / denom) < 1e-9
    # No-scrap case scraps nothing and yields 100%.
    r0 = _run(**CASES["balanced"])
    assert r0["scrap_total"] == 0 and abs(r0["yield_rate"] - 1.0) < 1e-9


def test_actual_order_counts_are_instrumented():
    """The engine records actual deliveries/orders (not inferred from consumption)."""
    r = _run(**CASES["unreliable"])
    assert r["delivery_events"] >= 1
    assert r["orders_placed"] >= r["delivery_events"]   # each delivery ships >= 1 lot
    # A fully reliable JIT line is replenished every hour.
    rj = _run(**CASES["balanced"])
    assert rj["delivery_events"] == H


def test_littles_law_compatible_when_stationary():
    """Little's Law (W = L/λ) matches the measured average sojourn when the line is roughly
    STATIONARY (bounded WIP) — a WIP-capped line here, within a small finite-run tolerance.
    This pins the compatible-boundary case (same population: post-Op1 WIP, good throughput)."""
    r = _run(dice=[1] * 6, sides=[6] * 6, wip_limits=[10] * N)
    assert r["scrap_total"] == 0
    derived, measured = r["flow_time_derived"], r["flow_time_measured"]
    assert derived > 0 and measured > 0
    assert abs(derived - measured) / derived < 0.05, f"L/λ={derived:.1f} vs measured={measured:.1f}"


def test_littles_law_diverges_when_nonstationary():
    """Documented limitation (spec 3F): on an UNCAPPED bottleneck the line never reaches steady
    state — WIP keeps growing, so units still queued at the end are excluded from the measured
    flow time while they are still counted in average WIP. The two measures legitimately diverge;
    this test asserts that known behavior so it can't regress silently."""
    r = _run(**CASES["bottleneck"])
    assert r["ending_wip_downstream"] > 1000, "bottleneck should pile up WIP"
    derived, measured = r["flow_time_derived"], r["flow_time_measured"]
    assert derived > measured, "derived L/λ should exceed censored measured flow on a growing queue"


def test_service_level_and_fill_rate_are_distinct():
    """service_level is raw-material availability (supplier side); fill_rate is the customer
    side and only meaningful with a fluctuating market."""
    r = _run(**CASES["unreliable"])
    assert 0.0 <= r["service_level"] <= 1.0
    assert r["fill_rate"] == 1.0            # no market -> everything produced is 'sold'
    rd = _run(**CASES["demand"])
    assert 0.0 <= rd["fill_rate"] <= 1.0    # a real customer fill rate exists with demand on


def test_zero_throughput_case():
    """A line whose single station can't be fed still returns a well-formed, conserving result."""
    r = _run(dice=[1], sides=[6], supply_reliability=0.0, reorder_point=5)
    assert r is not None
    assert r["material_residual"] == 0
    assert r["total_output"] >= 0


def test_completion_vs_pass_are_separate():
    """A challenge 'resolved' by running out of tries must be recorded as done-but-NOT-passed.
    Mirrors the assessment logic in _render_challenge / the report."""
    ss = APP.st.session_state
    ss.clear()
    APP.initialize_state()
    prefix = "ops"
    # locate a challenge step
    ci = next(i for i, s in enumerate(APP.LABS[prefix]["steps"]) if s.get("challenge"))
    # Simulate: ran out of tries, never met the goal.
    ss[f"{prefix}_chal_attempts_{ci}"] = 3
    ss[f"{prefix}_chal_passed_{ci}"] = False
    APP.mark_step_done(prefix, ci)           # out-of-tries still marks the STEP done
    prog = APP._get_progress().get(prefix, set())
    assert ci in prog, "out-of-tries should mark the step done (student may continue)"
    assert ss.get(f"{prefix}_chal_passed_{ci}") is False, "must NOT be recorded as passed"


def test_director_snapshot_expansion():
    """Director aggregate params expand into per-station snapshot keys (contract with the Director)."""
    snap = APP._director_to_snapshot({
        "capacities": [1, 1, 1, 1, 1, 1, 0, 0, 0],
        "sides": [6, 6, 6, 6, 6, 6, 0, 0, 0],
    })
    assert snap.get("capacity_0") == 1 and snap.get("sides_0") == 6
    assert snap.get("capacity_6") == 0


def test_part_reports_are_independent():
    """Part 1 and Part 2 reports cover disjoint labs."""
    p1 = set(APP.LAB_PARTS[APP.LAB_PART_ORDER[0]])
    p2 = set(APP.LAB_PARTS[APP.LAB_PART_ORDER[1]])
    assert p1 and p2 and not (p1 & p2)
    assert p1 | p2 == set(APP.LAB_ORDER)


def test_private_rng_is_isolated_and_equivalent():
    """Concurrent Streamlit sessions share one process. A run on a private random.Random must
    (a) equal the legacy global-seeded run, and (b) be unaffected by other sessions touching the
    global `random` stream in between."""
    kw = dict(track_flow=False, demand_dice=1, demand_faces=6, supply_reliability=0.9)
    legacy = _run([1, 1, 1], [6, 6, 6], seed=11, **kw)["total_output"]
    a, b = random.Random(11), random.Random(11)
    args = (_pad([1, 1, 1]), _pad([6, 6, 6]), 0, H, 0.9, None)
    r1 = APP.run_simulation(*args, track_flow=False, demand_dice=1, demand_faces=6, rng=a)
    random.seed(999)                      # another "session" reseeds the global stream
    r2 = APP.run_simulation(*args, track_flow=False, demand_dice=1, demand_faces=6, rng=b)
    assert r1["total_output"] == r2["total_output"] == legacy


def _fin(order_size=150.0, rmc=0.55, order_cost=25.0):
    """A financial settings dict shaped like get_fin()'s output, built from the app's defaults."""
    import pandas as pd
    return {
        "revenue_per_unit": 3.0, "alloc_pct": 33.0, "wip_holding": 0.04, "rmc": rmc,
        "order_cost": order_cost, "order_size": float(order_size), "raw_holding": 0.04,
        "table": pd.DataFrame({
            "Faces": list(APP.FACE_ROWS),
            "Fixed cost per die ($)": [APP.DEFAULT_FIN_LOOKUP[f][0] for f in APP.FACE_ROWS],
            "Production cost per unit ($)": [APP.DEFAULT_FIN_LOOKUP[f][1] for f in APP.FACE_ROWS],
        }),
    }


def test_worked_units_are_recorded_separately_from_good():
    """Audit P2 instrumentation: a station's worked count includes the units it scrapped."""
    clean = _run([1, 1, 1], [6, 6, 6], seed=3)
    for d in clean["op_detail"]:
        assert abs(d["avg_worked"] - d["avg_prod"]) < 1e-9      # no scrap => identical
    scrapped = _run([1, 1, 1], [6, 6, 6], seed=3, scrap=[0.25, 0, 0] + [0] * (N - 3))
    op1 = scrapped["op_detail"][0]
    assert op1["avg_worked"] > op1["avg_prod"]
    worked = op1["avg_worked"] * scrapped["hours"]
    good = op1["avg_prod"] * scrapped["hours"]
    assert abs((worked - good) - scrapped["scrap_by_station"][0]) < 1.0


def test_scrapped_units_are_not_free():
    """Audit P2: raw material and processing are charged on units STARTED/WORKED, so turning
    scrap on must raise cost — previously a scrapped bottle cost nothing."""
    fin = _fin()
    caps, sides = _pad([1, 1, 1]), _pad([6, 6, 6])
    clean = _run([1, 1, 1], [6, 6, 6], seed=3, order_size=150)
    dirty = _run([1, 1, 1], [6, 6, 6], seed=3, order_size=150,
                 scrap=[0.25, 0, 0] + [0] * (N - 3))
    f_clean = APP.compute_financials(clean, caps, sides, 1.0, fin)
    f_dirty = APP.compute_financials(dirty, caps, sides, 1.0, fin)
    assert dirty["total_output"] < clean["total_output"]        # scrap costs output ...
    assert f_dirty["raw_cost"] >= f_clean["raw_cost"] - 1e-6    # ... and is still paid for
    assert f_dirty["profit"] < f_clean["profit"]


def test_ordering_cost_counts_actual_shipments():
    """Audit P1: ordering cost comes from the orders the supplier really shipped, and falls
    back to the idealised estimate when the P&L is priced at a different order size."""
    caps, sides = _pad([1] * 6), _pad([6] * 6)
    r = _run([1] * 6, [6] * 6, seed=5, order_size=600, hours=H)
    f = APP.compute_financials(r, caps, sides, 1.0, _fin(order_size=600))
    assert f["orders"] == r["orders_placed"]
    # Priced at an order size the run never used -> must not report the recorded count.
    mismatched = APP.compute_financials(r, caps, sides, 1.0, _fin(order_size=150))
    assert mismatched["orders"] != r["orders_placed"]


def test_short_horizons_are_shorter_and_noisier():
    """The run-length presets exist so students can see the dice-game swings a full year
    averages away. A one-shift run must be both short and far more variable."""
    assert APP.HORIZONS[APP.HORIZON_YEAR] is None
    shift = APP.HORIZONS["One shift (8 h)"]
    assert shift == APP.HOURS_PER_DAY
    rates = []
    for seed in range(40):
        r = APP.run_simulation(_pad([1] * 6), _pad([6] * 6), 0, shift, 1.0, None,
                               track_flow=False, rng=random.Random(seed))
        assert r["hours"] == shift
        rates.append(r["total_output"] / shift)
    year = [APP.run_simulation(_pad([1] * 6), _pad([6] * 6), 0, H, 1.0, None,
                               track_flow=False, rng=random.Random(seed))["total_output"] / H
            for seed in range(10)]
    spread = lambda v: (max(v) - min(v)) / (sum(v) / len(v))
    assert spread(rates) > 5 * spread(year)


# --------------------------------------------------------------------------------------
# Plain-python runner (so the suite works without pytest installed).
# --------------------------------------------------------------------------------------
def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
