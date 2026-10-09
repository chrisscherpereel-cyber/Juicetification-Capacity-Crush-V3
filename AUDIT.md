# Model & Assessment Audit — Juicetification: Capacity Crush

This audit documents the simulation's current assumptions and separates **verified defects**
(reproduced in code/tests) from **modeling assumptions** and **proposed changes that would move
tuned numbers** (left for a sign-off step so lab thresholds can be re-tuned deliberately). It
reflects the code as reviewed on 2026-10-07. Tests live in `tests/test_model.py`
(`python tests/test_model.py` → 11/11 passing).

Guiding rule followed here: *do not silently change simulation outputs or the supply model.*
Every change shipped in this pass is either additive instrumentation, a label/wording
clarification, or UI/assessment/reporting — none of them alter the random stream or the
tuned lab numbers (verified: the Operations lab back-compat sequence
`[6965, 6869, 4129, 4197, 4119, 3071, 7062, 7082]` is unchanged).

## 1. Current model (as implemented)

- **Line.** Up to 9 serial stations; a station is *active* with dice>0 and faces>0. Each hour a
  station's potential output is the sum of `dice` draws of `uniform(1..faces)` — mean
  `dice·(faces+1)/2` bottles/hr. A station passes the **min** of (its roll, the inventory in
  front of it, and downstream free space under any WIP cap). Moves resolve **downstream-first**.
- **Time/units.** 1 tick = 1 bottling hour; `HOURS_PER_DAY` per day, `DAYS_PER_YEAR` days/yr,
  `HOURS_PER_YEAR` hours/yr. All inventories are whole bottles.
- **Event order each hour:** (1) roll potentials; (2) **supplier reorder check** on the raw
  buffer; (3) resolve moves downstream→upstream, applying **scrap** to the units each station
  works; (4) finished goods meet **demand** (sold / lost / held as FGI); (5) accumulate stats.
- **Supply model (precise).** A **reorder policy**, *not* a lead-time model: when the raw buffer
  in front of Op 1 falls below the reorder point, the supplier ships `ceil(deficit/order_size)`
  lots of `order_size` **with probability = reliability that hour**; a missed hour simply retries
  next hour. "Supplier reliability" is therefore the per-hour chance a replenishment *opportunity*
  succeeds — there is no explicit transit lead time. Default reorder point ≈ one hour of Op 1
  capacity.
- **Scrap/yield.** Applied deterministically via a fractional carry (draws **no** RNG, so
  `scrap=None` is a byte-for-byte no-op). Scrapped units leave the system at the station.
- **Starvation / "service level".** `service_level = 1 − starved_hours/hours` = share of hours Op 1
  had material = **production availability** (supplier/line side). A separate **customer fill
  rate** `= sold/demand` exists and is only meaningful when a fluctuating market is on.
- **Financials.** Revenue = units sold × price. Production cost = Σ(good units × per-unit cost).
  Fixed (dice) cost allocated per active die. Holding = avg WIP (unit-days) × rate, plus FGI
  holding. Raw cost = Op 1 good output × unit cost. Ordering = `ceil(consumption/order_size) ×
  order_cost`.

## 2. Verified defects

| # | Area | Finding (reproduced) | Status |
|---|------|----------------------|--------|
| D0 | Deploy | The GitHub repo is **missing `.streamlit/config.toml`** (the web "upload files" flow drops hidden folders). Without it a dark-mode viewer sees instruction text blend into the background — the earlier dark-mode report, re-introduced by the upload. | **Fixed** — file restored; also a CSS light-theme safety net is in place. |
| D1 | Service label (spec 3G) | The glossary called `service_level` a "fill rate", and the results panel labeled it "Service level (line fed)". It is **production availability**, not a customer service level; a true customer fill rate also exists but wasn't surfaced. | **Fixed** — relabeled to "Raw-material availability"; customer fill rate now shown separately when a market is on; glossary distinguishes the two. |
| D2 | Assessment (spec 5) | Running **out of tries** marks a challenge step "done" (student may continue) — correct — but the sidebar progress tracker showed the same green ✓ as a pass, so "attempted" and "passed" looked identical on screen. (The PDF report already distinguished them.) | **Fixed** — progress card now shows "Challenges passed: X of Y" and flags attempted-but-not-passed separately. |
| D3 | Save status (spec 6) | The caption always read "progress saved automatically" even if a save failed or no storage was configured. | **Fixed** — status now reports enabled/last-saved/failed honestly, and a storage-independent **backup/restore** (JSON) is provided. |

## 3. Verified modeling inconsistencies — **applied in V3**

These were documented and instrumented in V2 but left unapplied, pending a deliberate re-tuning
pass. V3 applies P1 and P2 and adds the P3 note. The re-tuning check was done and is recorded
under each item; the simulation's random stream and throughput are untouched — these move money
only.

- **P1 — Ordering cost is inferred, not counted (spec 3C).** `orders = ceil(consumption/order_size)`
  ignores the actual replenishment events. The engine now **records the real counts**
  (`delivery_events`, `orders_placed`); e.g. a balanced JIT line consumes ≈6965 units but actually
  ships 7112 lots over 2096 delivery hours — so the proxy understates orders. **Applied:** both the
  P&L and the EOQ cost curve now cost ordering from `orders_placed`. *Re-tuning check:* across
  Q = 10…1500 the real count runs **3–8% above** the inferred one and keeps the same ~1/Q shape, so
  the curve's minimum barely moves — on the default line the textbook EOQ (184) and the measured
  cheapest order size (184) still agree exactly, and the "within 10% of best" challenge still
  passes at the formula's answer. The two EOQ challenges score against the curve's own minimum, so
  they re-tune themselves. A guard falls back to the inferred count when a P&L is priced at a
  different order size than the run used (or when reading a run saved before V3).
- **P2 — Raw & processing cost exclude scrapped units (spec 3B).** Raw cost uses Op 1 **good**
  output and processing cost uses **good** units, so bottles a station worked and then scrapped are
  effectively free. *Proposed:* charge raw on units **started** at Op 1 and processing on units
  **worked** (`mv`) rather than good. **Applied:** the engine now records `avg_worked` per station
  and the P&L charges raw material on units Operation 1 started and processing on units worked.
  *Re-tuning check:* with scrap off this changes nothing at all (worked == good); with 20% scrap at
  Operation 1 raw cost rises from $3,280 to $4,100 (+25%), which is the intended lesson. No graded
  threshold in the Quality lab is money-based — its challenges score on `total_output` and
  `yield_rate` — so nothing needed re-tuning there.
- **P3 — Little's Law boundary on non-stationary lines (spec 3F).** For a **stationary** line
  (balanced or WIP-capped) derived `W=L/λ` matches the measured sojourn within ~1% (test
  `test_littles_law_compatible_when_stationary`). For an **uncapped bottleneck** WIP never settles,
  so units still queued at run end are excluded from measured flow while still counted in average
  WIP — a legitimate finite-run/censoring divergence (~58% in the test case). *Proposed, optional:*
  a warm-up/observation window and an on-screen "line hasn't reached steady state" note. This is a
  limitation to **explain**, not a bug to hide. **Applied (the note):** `steady_state_note()` now
  prints under the dashboard headline in two cases — a run shorter than six weeks (the line spends
  a real share of it just filling, so the average rate understates the steady rate) and a run that
  ended with downstream WIP more than 1.5x its own average (WIP still climbing, so the averages
  describe a line in transition). No warm-up window was introduced: discarding a warm-up period
  would change the tuned numbers, and naming the limitation is what the lab is teaching.

## 4. Assumptions worth stating (not defects)

- Starting inventory is placed in front of **every** station (a deliberate UI choice, labeled as
  such), not only at Op 1.
- The reported financial result is a **simplified operating result**, not accrual accounting or
  cash flow; initial inventory is not costed, unsold FGI is held at the holding rate, capital is
  allocated per die. This should be stated in-app wherever "profit" appears (minor doc task).
- "Stability" is never asserted from two close estimates; the app reports distributions/efficiency
  rather than a binary stable/unstable verdict.

## 5. What this pass changed intentionally (deliverable #6)

- **Simulation outputs: no change.** The RNG stream and all existing return values are identical
  (verified by the back-compat sequence and `test_deterministic_reproducibility`). New return
  fields are **additive** (`raw_received`, `delivery_events`, `orders_placed`, `initial_material`,
  `ending_material`, `ending_wip_downstream`, `material_residual`).
- **Labels/UI/assessment/reporting:** the D0–D3 fixes above, plus the test suite.

## 6. Deferred to later phases (plan)

Phase 2: consolidate the guided-lab workspace (one primary action; task controls in the main
panel) and surface the line-diagram instrumentation (nominal vs processed vs good vs starved vs
blocked). Phase 3: matched exogenous random draws for A/B (thread an explicit `random.Random`
per stream so a configuration change doesn't desync draws), CSV export of comparisons, and the
three targeted scenarios (moving constraint, quality location, capacity investment under
uncertainty). Phase 4: split engine / financials / lab definitions / assessment / reports /
presentation into modules behind the current entry point. Each of P1/P2 above belongs with a
re-tuning of the affected lab thresholds.

## 7. V3 concurrency & performance pass (measured, not estimated)

Method: a real Streamlit server driven by raw websocket clients speaking Streamlit's own
protocol, 30 concurrent sessions, timings as medians. One process serves every student, so the
GIL serialises script execution and latency scales roughly linearly with class size.

| Interaction (30 concurrent sessions) | V2 | V3 |
|---|---|---|
| Answer a lab question (predict / estimate / reflect) | 1.93 s · 43 KB | **0.82 s · 5 KB** |
| Any full rerun (navigation, Run) | 1.93 s · 43 KB | 1.93 s · 43 KB |
| EOQ cost curve, per run | ~250 ms, never shared | cached on config, shared |

- **Lab panel is an `st.fragment`.** Its buttons (navigation, "set up & run", the sidebar-focus
  jump) escalate to `st.rerun(scope="app")` through `_lab_nav_cb`, because they change state the
  main window renders from; only the answer widgets stay fragment-local.
- **`_eoq_sim_point`** splits the EOQ scan's simulations from its arithmetic. The scan was keyed
  on a run-derived `margin` (five distinct values in five runs), so it never hit cache.

### Two things investigated and found *not* to be problems

- **Inline CSS was not actually bloating reruns.** The ~28 KB `<style>` block looked like it was
  re-sent on every interaction. It is not: Streamlit's `ForwardMsgCache` substitutes a `ref_hash`
  for any cacheable message the client already holds, driven by `cached_message_hashes` on the
  rerun request. A raw test client that omits that field sees 71 KB per rerun; a client that sends
  it sees 43 KB. Real browsers send it. Moving the stylesheet to `static/app.css` was tried and
  **reverted**: Streamlit's static handler serves only an allowlist of extensions (images, fonts,
  `.pdf`, `.xml`, `.json`) and sends everything else as `text/plain` with `X-Content-Type-Options:
  nosniff`, so browsers refuse the stylesheet outright. Do not retry this without a different host
  for the file.
- **Grading a challenge on a single stochastic run is sound at the year horizon.** Across 200
  seeds and four plausible designs for the "Fix Line A" challenge, every design passed 100% of the
  time; one-year throughput varies only about ±2%. The thresholds are robust. This is exactly why
  the V3 run-length presets leave challenge grading pinned to a full year.

### Attempted and reverted

Making the **whole sidebar** a fragment (to make typing in it fragment-local) broke mode
switching: with the mode/part/lab-choice radios inside the fragment, selecting "Sandbox" left
`app_mode` on "Guided Lab". Reproduced in a real browser, not just in `AppTest`, and backed out.
A future attempt should keep those three stateful radios *outside* the fragment and scope it to
the configuration controls below them.
