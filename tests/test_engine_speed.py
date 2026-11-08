"""
test_engine_speed.py — the fast dice path in run_simulation must not change any result.

run_simulation draws dice with Random._randbelow (randint(1, s) == 1 + _randbelow(s)) because
randint's argument checking was ~30 % of a simulation's time. These tests run the same lines
through a Random wrapper that hides _randbelow, which forces the plain-randint fallback, and
require byte-identical results.

Run from the repo root:
    python tests/test_engine_speed.py
"""

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_model import APP  # noqa: E402  (loads the app headlessly with a Streamlit stub)


class _RandintOnly:
    """Same stream as random.Random(seed), but only randint/random are visible."""

    def __init__(self, seed):
        r = random.Random(seed)
        self.randint = r.randint
        self.random = r.random


def _canon(res):
    out = {}
    for k, v in res.items():
        out[k] = v.to_json() if hasattr(v, "to_json") else v
    return json.dumps(out, sort_keys=True, default=str)


CASES = [
    dict(caps=[1] * 6 + [0] * 3, sides=[6] * 6 + [0] * 3, start_inv=0, hours=600,
         supply_reliability=1.0),
    dict(caps=[2, 1, 3, 1, 0, 0, 0, 0, 0], sides=[6, 8, 4, 10, 0, 0, 0, 0, 0], start_inv=12,
         hours=600, supply_reliability=0.75, wip_limits=[30, 6, 6, 6, 6, 6, 6, 6, 6],
         order_size=40),
    dict(caps=[1, 2, 1, 1, 1, 1, 0, 0, 0], sides=[6] * 6 + [0] * 3, start_inv=0, hours=600,
         supply_reliability=1.0, scrap=[0, 0, 0.3, 0, 0, 0, 0, 0, 0], demand_dice=2,
         demand_faces=6),
]


def test_fast_dice_match_randint_exactly():
    for i, kw in enumerate(CASES):
        for seed in (1, 7, 2024):
            fast = APP.run_simulation(**dict(kw, rng=random.Random(seed)))
            slow = APP.run_simulation(**dict(kw, rng=_RandintOnly(seed)))
            assert _canon(fast) == _canon(slow), f"case {i}, seed {seed}: results differ"


def test_module_level_stream_matches_randint():
    kw = CASES[2]
    random.seed(99)
    a = APP.run_simulation(**kw)
    b = APP.run_simulation(**dict(kw, rng=_RandintOnly(99)))
    assert _canon(a) == _canon(b)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:      # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
