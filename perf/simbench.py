"""Time + fingerprint run_simulation on representative lab configs. Usage: python perf/simbench.py [REPO_DIR] [prof]
The fingerprints must not change when the engine is optimised (same seed -> same results)."""
import sys, os, time, random, json, hashlib, math, cProfile, pstats, io
ROOT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "prof" else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests")); os.chdir(ROOT)
import importlib; tm = importlib.import_module("test_model"); A = tm.APP
cfgs = [
  ("ops 6x1d6",       dict(caps=[1]*6+[0]*3, sides=[6]*6+[0]*3, start_inv=0, hours=A.HOURS_PER_YEAR, supply_reliability=1.0)),
  ("wip cap",          dict(caps=[1]*6+[0]*3, sides=[6]*6+[0]*3, start_inv=0, hours=A.HOURS_PER_YEAR, supply_reliability=1.0, wip_limits=[4]*9)),
  ("supplier 80%",     dict(caps=[2,1,1,1,1,1,0,0,0], sides=[6]*6+[0]*3, start_inv=20, hours=A.HOURS_PER_YEAR, supply_reliability=0.8, order_size=150)),
  ("scrap+demand",     dict(caps=[1,2,1,1,1,1,0,0,0], sides=[6]*6+[0]*3, start_inv=0, hours=A.HOURS_PER_YEAR, supply_reliability=1.0, scrap=[0,0,.3,0,0,0,0,0,0], demand_dice=1, demand_faces=6)),
]
fp = {}; tot = 0
for name, kw in cfgs:
    ts = []
    for rep in range(5):
        kw2 = dict(kw); kw2["rng"] = random.Random(1234 + rep)
        t = time.perf_counter(); r = A.run_simulation(**kw2); ts.append(time.perf_counter() - t)
        if rep == 0:
            keep = {k: (v.to_json() if hasattr(v, "to_json") else v) for k, v in r.items() if k not in ("frames",)}
            fp[name] = hashlib.md5(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]
            fr = hashlib.md5(json.dumps(r.get("frames"), sort_keys=True, default=str).encode()).hexdigest()[:12]
            fp[name] += "/" + fr
    ts.sort(); tot += ts[2]
    print(f"{name:14s} median {ts[2]*1000:6.1f} ms   fingerprint {fp[name]}")
print(f"TOTAL {tot*1000:.1f} ms")
if "prof" in sys.argv[1:]:
    pr = cProfile.Profile(); pr.enable()
    for _ in range(3): A.run_simulation(**dict(cfgs[0][1], rng=random.Random(1)))
    pr.disable(); s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(12); print(s.getvalue()[-2500:])
