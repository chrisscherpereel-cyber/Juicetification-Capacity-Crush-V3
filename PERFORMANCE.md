# Performance audit — Capacity Crush on Streamlit Community Cloud

Branch `perf/streamlit-cloud-optimization`, audited against `main` @ `bd591b0` (7 Nov 2026).
Goal: make the app as responsive as possible for 30–40 students at once, with no change to
hosting, features, simulation results or what students see.

## A. Executive summary

**What was wrong.** On Streamlit Community Cloud the whole class shares one Python process and
a small, variable slice of CPU. Every click a student makes re-runs the app's script on that
process. Under load, clicks queue behind each other. When the server is limited to a quarter of
one CPU core, the local test reproduces the ~10 s waits measured earlier on Cloud with 35
students. The time went to:

1. **A Streamlit default that does a full memory clean-up after every click** (`runner.postScriptGC`).
   Its cost grows with everything in memory, so every connected student makes every other
   student's click slower. Profiled with 20 students connected, it was **~45–50 % of the work done for each click**.
2. **Streamlit's anonymous usage telemetry**, which records the arguments of every element on
   every click: **~14 % of server CPU**.
3. **The dice rolls inside the simulation**: Python's `randint` spends most of its time checking
   its arguments. This was ~30 % of each simulation.

**What changed.** Two settings in `.streamlit/config.toml` and a faster, exactly equivalent way
of rolling the dice. Nothing students see changed. Every simulation result is byte-for-byte
identical: the same seed gives the same run.

**Result (measured).** Server CPU per student click fell from **~140 ms to ~73 ms (−48 %)**.
With the server limited to a quarter core (a Cloud-like setting):
- **30 students:** a typical "Set up & run this step" dropped from **11.2 s to 1.0 s** (median),
  and answering a question from **7.0 s to 0.5 s**.
- **40 students:** the server still saturates at peaks (median 1–4 s, 95th percentile ~8–11 s),
  but it completed **77 % more student actions** in the same time.
- No errors at any level, before or after.

## B. Optimization results

All figures are **measured** locally unless marked otherwise (method in section D).

| Component | Problem identified | Modification | Measured improvement |
|---|---|---|---|
| Application startup | Heavy libraries (Dropbox, fpdf2, cryptography) are already imported only when used; Streamlit + pandas import ≈ 0.45 s once per server start | None needed | — (already good) |
| Page loading (first open) | First page build is the same cost as any click; a whole class opening the link within ~10 s queues | Indirect (cheaper clicks) | 40 students, full core: median 0.15 → 0.09 s, p95 0.64 → 0.23 s. Quarter core, 40 arriving in 10 s: median 14.3 → 10.1 s (still slow — see F) |
| Session management | Session state is small (≈140 keys); simulation frames are already dropped after each run | None needed | ≈4 MB server memory per connected student (measured) |
| CPU utilization | Full garbage collection after every click; usage telemetry on every element; slow dice rolls | `runner.postScriptGC = false`; `browser.gatherUsageStats = false`; `_randbelow` dice | CPU per click 140 → 73 ms; 40 students on one core: 0.49 → 0.25 cores |
| Memory consumption | Trade-off of skipping the per-click clean-up | Python's automatic collection still runs | Peak with 40 students 199 → 234 MB (+35 MB); after three back-to-back classes of 40, 242 → 302 MB, levelling off. Cloud minimum is 690 MB |
| File uploads | Only upload is the ≤1 MB progress-backup JSON; it is read once from memory, guarded by a SHA-256 so it isn't re-applied, no temp files | None needed | Not measured (input is a few KB) |
| File downloads | PDF report is built only on "Prepare my report", stored once per student, and rebuilt only when answers change (fingerprint). The small backup JSON is rebuilt every click (≈2 % of CPU) | None. Deferring the backup needs a newer Streamlit than the `>=1.50` requirement allows | PDF build ≈0.5 s, once per request (measured in test harness) |
| Caching | `st.cache_data` on the die-size scan, EOQ curve and run-spread figures, keyed only on line settings (no student data), with TTL and size caps | None needed — reviewed safe | — |
| Concurrent users | Clicks queue when CPU is short | All of the above | Quarter core, "Set up & run" median: 20 students 3.3 → 0.5 s; 30 students 11.2 → 1.0 s; 40 students 20.1 → 3.9 s |

### Full before/after tables

**Server limited to a quarter of one CPU core** (Cloud-like). Median / 95th percentile seconds
from click to fully drawn page:

| Students | Set up & run this step | Answer a question | Next step | Open the app | Actions completed in 90 s |
|---|---|---|---|---|---|
| 10 | 0.65 / 1.46 → **0.31 / 0.93** | 0.41 / 0.88 → **0.18 / 0.66** | 0.36 / 0.92 → **0.14 / 0.72** | 2.2 / 4.8 → **1.1 / 3.6** | 100 → 100 |
| 20 | 3.27 / 9.41 → **0.51 / 1.48** | 1.58 / 6.57 → **0.25 / 1.04** | 1.85 / 9.88 → **0.20 / 0.95** | 5.6 / 11.9 → **3.4 / 7.2** | 158 → 196 |
| 30 | 11.2 / 15.8 → **0.99 / 6.7** | 6.97 / 12.7 → **0.52 / 3.85** | 10.6 / 13.8 → **0.65 / 5.4** | 10.0 / 18.2 → **5.6 / 12.8** | 186 → 252 |
| 40 | 20.1 / 24.6 → **3.86 / 10.9** | 15.0 / 19.4 → **1.21 / 8.3** | 18.5 / 21.7 → **3.25 / 9.4** | 14.3 / 26.4 → **10.1 / 15.4** | 166 → 294 |

**Server on one full CPU core**:

| Students | CPU used (cores) | CPU per click | Set up & run (median / p95) | Answer (median / p95) | Peak memory |
|---|---|---|---|---|---|
| 1 | 0.019 → 0.014 | — | 0.14 / 0.17 → 0.09 / 0.09 | 0.12 / 0.13 → 0.07 / 0.08 | 161 → 161 MB |
| 10 | 0.126 → 0.074 | 137 → 81 ms | 0.15 / 0.21 → 0.09 / 0.16 | 0.11 / 0.16 → 0.06 / 0.10 | 161 → 184 MB |
| 20 | 0.246 → 0.134 | 138 → 74 ms | 0.17 / 0.29 → 0.09 / 0.18 | 0.12 / 0.22 → 0.06 / 0.11 | 173 → 202 MB |
| 30 | 0.391 → 0.197 | 144 → 73 ms | 0.21 / 0.66 → 0.09 / 0.17 | 0.14 / 0.36 → 0.06 / 0.12 | 188 → 224 MB |
| 40 | 0.490 → 0.250 | 145 → 72 ms | 0.28 / 0.64 → 0.10 / 0.24 | 0.16 / 0.36 → 0.07 / 0.13 | 199 → 234 MB |

Errors: 0 at every level, before and after.

## C. Code modifications

| File | Change | Why |
|---|---|---|
| `.streamlit/config.toml` | `[runner] postScriptGC = false` | Removes the full garbage collection after every click (largest single cost; grows with class size). Supported option, present in Streamlit 1.50 and current releases. |
| `.streamlit/config.toml` | `[browser] gatherUsageStats = false` | Stops per-element telemetry recording (~14 % CPU). Also stops anonymous usage data going to Streamlit. Nothing visible changes. |
| `juicetification.py` (`run_simulation`) | Dice drawn with `Random._randbelow(faces) + 1` instead of `randint(1, faces)`; (dice, faces) pairs precomputed | `randint(1, s)` *is* `1 + _randbelow(s)`, so the random stream is identical. Falls back to `randint` if `_randbelow` is unavailable. Simulation ≈30 % faster. |
| `tests/test_engine_speed.py` (new) | Runs WIP-capped, unreliable-supplier, scrap and market-demand lines with the fast path and with plain `randint`; requires identical results | Guards the equivalence |
| `perf/` (new) | `loadtest.py` (Streamlit-aware load generator), `run_levels.sh` (1–40 students, optional CPU cap), `simbench.py` (engine timing + result fingerprints) | Repeatable before/after measurement |
| `PERFORMANCE.md` (new) | This report | — |

Dependencies: **no change**. Compatibility: both settings exist in Streamlit 1.50 (the minimum
in `requirements.txt`) and in current releases. `_randbelow` has been part of Python's `random`
module throughout Python 3, and a fallback keeps the code working if it were ever removed.

Considered and **not** done:
- **`st.fragment` for the lab panel.** An earlier V3 build tried this, and it caused two blocking
  state bugs; see the `render_lab` docstring.
- **Lazy backup download.** It needs a newer Streamlit minimum.
- **`gc.freeze()`.** Automatic garbage collection after the change cost only ~3 % of CPU with
  40 students, so the extra code isn't worth it.
- **Turning off the file watcher.** Cloud may rely on it to pick up new commits.

## D. Testing

**Method.** Each simulated student is a real websocket session speaking Streamlit's own protocol,
the same messages a browser sends. Ordinary HTTP load tools can't simulate Streamlit users.

- **Workload.** The Operations lab: open the app, then repeat Set up & run → answer → Next, with
  4–12 s of "reading" between clicks. Students arrive within the first 10 s.
- **Levels.** 1, 5, 10, 20, 30 and 40 students, 90 s each, a fresh server every time.
- **Setup.** The server is pinned to one CPU core and the load generator to another. The
  quarter-core runs cap the server with a Linux CPU quota, so 0.25 core stands in for a busy
  Cloud container. This model reproduced the ~10 s waits at ~30–35 students that were seen on
  Cloud.
- **Profiling.** `py-spy` was attached to the live server during load.
- **Environment.** Streamlit 1.66, Python 3.13 (local).
- **Not tested.** Nothing was load-tested against the live class app.

**Correctness checks (all pass).**
- `tests/test_model.py` 17/17, including the fixed-seed back-compat sequence.
- `tests/test_store_async.py` 5/5.
- New `tests/test_engine_speed.py` 2/2.
- Engine fingerprints (summaries and frames) are identical before and after for four
  representative lines, and for the module-level random stream.
- End-to-end walkthroughs:
  - **Self-explanations:** saved, still there after Back and after a refresh, and included in
    the PDF.
  - **Part 1 capstone:** answer choices stay locked until the step is run.
  - **Economics lab:** same profit curve and the same "8-sided die ≈ $3,477/yr" reveal as
    `main`.
  - **Design challenge:** "Run my design" uses the student's own settings and counts one try.
  - **Coverage:** every Part 2 lab, the PDF report, a Sandbox run and 20 replications ran
    without exceptions.
- **Session isolation.** Caches are keyed only on line settings, and every student keeps a
  private random stream (unchanged).

**Maximum concurrency tested:** 40 students (three back-to-back waves of 40 for memory).

## E. GitHub

- Branch: `perf/streamlit-cloud-optimization`.
- Pull request into `main`, not merged; awaiting approval.

## F. Remaining constraints and recommendations

**Platform limits (Community Cloud).**
- CPU is "0.078 cores minimum, 2 cores maximum" and memory "690MB minimum, 2.7GBs maximum".
  Streamlit says these "may change at any time".
- The share an app actually gets at a given moment is **not published**. The quarter-core
  figure above is a model, not a measurement of Cloud.
- Apps with no traffic for 12 hours go to sleep.
- All students share one Python process, so their clicks run one after another.

**Is 30–40 realistic?**
- **30 students:** on a Cloud-like quarter core, yes. Typical clicks now take about a second,
  with occasional peaks of several seconds.
- **40 students:** the app works, but a quarter core is still **slightly less** than 40 active
  students need at peak. Expect 1–4 s typical waits and up to ~10 s at peaks; that was 15–20 s
  before.
- If Cloud grants more CPU at that moment, it will be faster. This can't be controlled from
  the code.

**What helps without code.**
- Open the app a few minutes before class so it is awake.
- Have students open the link in two waves rather than all at once. The class-start burst is
  the slowest moment (first-open median 10 s for 40 at once on a quarter core).

**Further code-level gains (need a UI decision).**
- After this change, the biggest single piece of each click is the **station setup grid** in
  the sidebar:
  - 9 rows, each with 2 number boxes, 4 ▲/▼ buttons and 6 columns.
  - It is **~16 % of server CPU** per click and roughly **45 % of the ~250 page elements**
    every click redraws.
- Showing only the stations in use, or a more compact control, would cut that. It changes the
  layout, so it needs sign-off.
- Beyond that, meaningful gains need a different host with dedicated CPU, which would be an
  infrastructure change.

## How to re-run the measurements

```bash
pip install streamlit websockets            # plus requirements.txt
python perf/simbench.py                     # engine timing + result fingerprints (must not change)
perf/run_levels.sh . out/full               # 1–40 students, server on one full core
sudo perf/run_levels.sh . out/quarter 0.25 "10 20 30 40"   # server capped at 0.25 core (Linux)
python tests/test_model.py && python tests/test_engine_speed.py && python tests/test_store_async.py
```

Run these on a local machine only, never against the live class app.
