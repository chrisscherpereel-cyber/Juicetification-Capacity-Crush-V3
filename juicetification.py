# juicetification.py  —  Juicetification: Capacity Crush
import streamlit as st  # type: ignore[import]
import pandas as pd  # type: ignore[import]
import math
import os
import random
import re
import time
import json
import base64
import hashlib
import statistics
from collections import deque
import streamlit.components.v1 as components
from juice_director import serve_manifest_if_requested, resolve_config
from manifest import MANIFEST
import student_store as store

# =========================================================
# Page configuration
# =========================================================
st.set_page_config(
    page_title="Juicetification: Capacity Crush",
    page_icon="🎲",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# Juicetification Director integration
# =========================================================
# 1) Serve this app's parameter schema when the Director asks for it (?manifest=1).
# 2) Resolve any instructor configuration / random seed the Director passes in.
# With no Director parameters in the URL, resolve_config returns built-in defaults and
# the app behaves exactly as before. (Instructor config still arrives through the app's
# own ?cfg= snapshot mechanism below; DIRECTOR_CTX is used only for the shared seed.)
serve_manifest_if_requested(MANIFEST)


def _session_rng():
    """This session's private random.Random. The module-level `random` stream is process-wide
    and every Streamlit session shares it, so seeding it per student would let concurrent
    students overwrite each other's streams. Seeding this instance is equivalent but isolated."""
    if "_rng" not in st.session_state:
        st.session_state["_rng"] = random.Random()
    return st.session_state["_rng"]

_DIRECTOR_PARAMS, DIRECTOR_CTX = resolve_config(MANIFEST)

# =========================================================
# Per-student persistence (student_store)
# =========================================================
# Identify the student from the URL before the scenario is created. When shared storage is
# configured, block on a small gate until a student ID is entered — this lets progress be
# saved/resumed and makes each student's scenario stable. When storage is NOT configured,
# store.enabled() is False, every store.* call is a safe no-op, and the app behaves exactly
# as before (no gate, random scenario).
_STORE_GAME = store.game_code()
_STORE_SID = store.get_student_id()

if store.enabled() and not _STORE_SID:
    st.title("🧃 Juicetification: Capacity Crush")
    st.text_input("Enter your student ID to begin", key="_sid_entry",
                  placeholder="e.g., your campus username")
    if st.button("Start", type="primary"):
        _entered = (st.session_state.get("_sid_entry") or "").strip()
        if _entered:
            store.set_student_id(_entered)
            st.rerun()
    st.stop()

# Scenario seed. An instructor-set Director ?seed= pins a fixed exercise for everyone;
# otherwise every student gets their OWN random scenario (a unique result), assigned once and
# kept in the URL as ?rs= so it stays stable across reruns and reloads while differing between
# students. (The per-run counter added at run time then varies each repeat — see the run trigger.)
if DIRECTOR_CTX.get("seed") is not None:
    SCENARIO_SEED = DIRECTOR_CTX["seed"]
else:
    _rs = st.query_params.get("rs")
    try:
        SCENARIO_SEED = int(_rs) if _rs not in (None, "") else None
    except (TypeError, ValueError):
        SCENARIO_SEED = None
    if SCENARIO_SEED is None:
        SCENARIO_SEED = random.SystemRandom().randint(1, 1_000_000_000)
        try:
            st.query_params["rs"] = str(SCENARIO_SEED)
        except Exception:
            pass

# =========================================================
# Defaults
# =========================================================
N_OPS = 9
DEFAULT_CAPACITIES = [1, 1, 1, 1, 1, 1, 0, 0, 0]
DEFAULT_SIDES = [6, 6, 6, 6, 6, 6, 0, 0, 0]
DEFAULT_STARTING_INVENTORY = 0
DEFAULT_SIMULATION_YEARS = 1
DEFAULT_SUPPLY_RELIABILITY_LEGACY = None  # (reliability now lives in DEFAULT_SUPPLY_RELIABILITY)
MAX_YEARS = 5

HOURS_PER_DAY = 8
DAYS_PER_YEAR = 262
HOURS_PER_YEAR = DAYS_PER_YEAR * HOURS_PER_DAY
# Run-length presets. The classic dice game runs ~20 rounds, where the swings are violent; a
# full simulated year averages them almost away. Measured on a balanced 6-station 1d6 line, the
# spread between the best and worst run is ~160% over one shift, 36% over a week, and 4% over a
# year — and an empty line only reaches its steady rate after the pipeline fills (0.62 bottles/hr
# in the first shift vs 3.37 over a year). Students need to see both: the same line looks wildly
# unstable up close and perfectly predictable from far away. HORIZON_YEAR keeps the original
# behaviour (simulation_years x HOURS_PER_YEAR) and stays the default.
HORIZON_YEAR = "Full year"
HORIZONS = {
    "One shift (8 h)": HOURS_PER_DAY,
    "One week (40 h)": HOURS_PER_DAY * 5,
    "Six weeks (240 h)": HOURS_PER_DAY * 30,
    HORIZON_YEAR: None,
}
DEFAULT_HORIZON = HORIZON_YEAR

CAP_MIN, CAP_MAX = 0, 20

# Whether the live "line running" playback is ON by default. Each user can flip it in the
# sidebar, but an instructor expecting many simultaneous users can turn it OFF for everyone
# by setting the environment variable JCC_ANIMATIONS=off (the playback is the main per-run
# server cost: it holds the session for a couple of seconds and streams ~240 frames).
ANIMATIONS_DEFAULT_ON = os.environ.get("JCC_ANIMATIONS", "off").strip().lower() in (
    "1", "on", "true", "yes", "enable", "enabled")
# Charts (the line/bar charts and cost-curve pictures) are the heaviest thing to render and
# re-render on every interaction. They're ON by default, but a student can switch them off to
# keep just the numbers, and an instructor can default the whole class to OFF with
# JCC_CHARTS=off to cut server/bandwidth load on Streamlit Cloud's single CPU.
CHARTS_DEFAULT_ON = os.environ.get("JCC_CHARTS", "off").strip().lower() in (
    "1", "on", "true", "yes", "enable", "enabled")
SIDES_MIN, SIDES_MAX = 0, 100

# ---- Per-station WIP (work-in-process) limit ----
DEFAULT_WIP_LIMIT_ON = False     # off => uncapped push line (original behavior)
DEFAULT_WIP_CAP = 10             # default cap per station when limits are switched on
WIP_CAP_MIN, WIP_CAP_MAX = 0, 99999  # 0 blocks flow; a large value ≈ unlimited

# ---- Financial defaults (calibrated so the default six-6-sided line earns a
#      small, reliable annual profit at $3.00 revenue) ----
DEFAULT_REVENUE_PER_UNIT = 3.00
DEFAULT_ALLOC_PCT = 33          # yearly allocation of fixed (die) cost, %
DEFAULT_WIP_HOLDING = 0.04      # $ per unit of WIP per day
DEFAULT_RMC = 0.55              # $ per unit of raw material
DEFAULT_ORDER_COST = 25.00      # $ per raw-material order
DEFAULT_ORDER_SIZE = 150        # units of raw material per order (drives ordering cost)
RAW_HOLD_PER_DAY = 0.04         # $ to hold one bottle of raw material per day (EOQ holding cost)
DEFAULT_RAW_HOLDING = RAW_HOLD_PER_DAY   # adjustable EOQ holding cost (fin_raw_holding)

# Variability switches (default off → classic textbook line)
DEFAULT_SUPPLY_RELIABILITY = 100    # % of hours the supplier delivers; 100 = never starves Op 1
DEFAULT_REORDER_ON = False          # off → auto reorder point (≈1 hour of Op 1 capacity)
DEFAULT_REORDER_POINT = 40          # safety-stock trigger level when the manual control is on
DEFAULT_SCRAP_ON = False            # off → perfect yield everywhere (classic line)
DEFAULT_SCRAP_PCT = 0               # per-station scrap % when quality is on
DEFAULT_DEMAND_VARIABLE = False     # on → a fluctuating market; unsold units sit in finished goods
DEFAULT_DEMAND_DICE = 1             # demand each hour = roll of this many dice ...
DEFAULT_DEMAND_FACES = 6            # ... of this many faces (1×6 ≈ 3.5 units/hr, like a station)

# Cost of a die, and per-unit production cost, by die type (number of faces).
# Fixed costs rise *convexly* with die size: crude dice are cheap, big precision
# dice are very expensive. Combined with the falling per-unit production cost,
# this creates a genuine sweet spot — profit peaks at the 6-sided die and the
# largest dice actually lose money.
DEFAULT_FIN_TABLE = pd.DataFrame({
    "Faces": [0, 2, 4, 6, 8, 10, 12],
    "Fixed cost per die ($)": [0.0, 800.0, 1800.0, 3000.0, 5000.0, 8000.0, 12000.0],
    "Production cost per unit ($)": [1.00, 0.15, 0.12, 0.10, 0.08, 0.06, 0.05],
})
# Per-die cost table as plain (face -> (fixed, production)) defaults. The Set-Financials
# form renders these as individual number inputs (not a data-editor), so the cells always
# show their seeded values instead of going blank/zero on first open.
FACE_ROWS = [int(f) for f in DEFAULT_FIN_TABLE["Faces"].tolist()]
DEFAULT_FIN_LOOKUP = {
    int(r["Faces"]): (float(r["Fixed cost per die ($)"]), float(r["Production cost per unit ($)"]))
    for _, r in DEFAULT_FIN_TABLE.iterrows()
}


def fin_table_from_state():
    """Build the per-die cost DataFrame from the individual session-state cost inputs,
    falling back to the defaults for any value that hasn't been set yet."""
    fixed, prod = [], []
    for f in FACE_ROWS:
        dfx, dpx = DEFAULT_FIN_LOOKUP[f]
        fixed.append(float(st.session_state.get(f"fin_fixed_{f}", dfx)))
        prod.append(float(st.session_state.get(f"fin_prod_{f}", dpx)))
    return pd.DataFrame({
        "Faces": list(FACE_ROWS),
        "Fixed cost per die ($)": fixed,
        "Production cost per unit ($)": prod,
    })


# =========================================================
# Session state + step helpers
# =========================================================
def _snapshot_keys():
    """The sidebar / run inputs whose last-used values are remembered between runs."""
    keys = [f"capacity_{i}" for i in range(N_OPS)] + [f"sides_{i}" for i in range(N_OPS)]
    keys += [f"wip_cap_{i}" for i in range(N_OPS)]
    keys += ["starting_inventory", "simulation_years", "horizon", "supply_reliability", "demand_variable",
             "demand_dice", "demand_faces", "wip_limit_on", "fin_order_size"]
    keys += ["reorder_point_on", "reorder_point", "scrap_on"] + [f"scrap_pct_{i}" for i in range(N_OPS)]
    # Financials persist with the run too, so they survive reloads and only return to
    # defaults when "Reset financials" is pressed.
    keys += ["fin_revenue_per_unit", "fin_alloc_pct", "fin_wip_holding", "fin_rmc", "fin_order_cost"]
    keys += ["fin_raw_holding"]
    keys += [f"fin_fixed_{f}" for f in FACE_ROWS] + [f"fin_prod_{f}" for f in FACE_ROWS]
    return keys


def save_run_snapshot():
    """Remember the inputs that produced the latest run so the sidebar re-opens on
    them (within the session, and across reloads via a compact URL parameter)."""
    snap = {k: st.session_state[k] for k in _snapshot_keys() if k in st.session_state}
    st.session_state["last_run_config"] = snap
    try:
        enc = base64.urlsafe_b64encode(
            json.dumps(snap, separators=(",", ":")).encode()).decode()
        if st.query_params.get("cfg") != enc:
            st.query_params["cfg"] = enc
    except Exception:
        pass


def _director_to_snapshot(cfg):
    """Normalize a ?cfg= dict to this app's per-station snapshot keys so that links built
    two different ways both restore correctly:
      * the app's own snapshot format (capacity_i, sides_i, wip_cap_i, scrap_pct_i, ...),
        which passes straight through, and
      * the Juicetification Director / manifest format, whose four aggregate keys
        (capacities, sides, wip_cap, scrap_pct) are expanded here into per-station keys.
    Every other manifest key (scalars and fin_*) already matches a snapshot key 1:1."""
    if not isinstance(cfg, dict):
        return {}
    out = dict(cfg)
    caps = out.pop("capacities", None)
    if isinstance(caps, (list, tuple)):
        for i in range(N_OPS):
            if i < len(caps):
                out[f"capacity_{i}"] = caps[i]
    sides = out.pop("sides", None)
    if isinstance(sides, (list, tuple)):
        for i in range(N_OPS):
            if i < len(sides):
                out[f"sides_{i}"] = sides[i]
    wip_cap = out.pop("wip_cap", None)
    if wip_cap is not None:
        for i in range(N_OPS):
            out[f"wip_cap_{i}"] = wip_cap
    scrap_pct = out.pop("scrap_pct", None)
    if scrap_pct is not None:
        for i in range(N_OPS):
            out[f"scrap_pct_{i}"] = scrap_pct
    return out


def _load_run_seed():
    """Values to seed the inputs with on a fresh session: the last run if we have one
    (from this session or restored from the URL), otherwise an empty dict (→ defaults).
    Accepts both the app's own snapshot keys and the Director's manifest keys."""
    if isinstance(st.session_state.get("last_run_config"), dict):
        return st.session_state["last_run_config"]
    raw = st.query_params.get("cfg")
    if raw:
        try:
            seed = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
            if isinstance(seed, dict):
                seed = _director_to_snapshot(seed)
                st.session_state["last_run_config"] = seed
                return seed
        except Exception:
            pass
    return {}


# ---------------------------------------------------------
# Lab progress tracking + submission report
# ---------------------------------------------------------
# The ordered list of labs used by the progress tracker and report. Kept as a
# module-level constant so the code, the tracker card, and the decoder all agree on the
# set of labs and their short keys. (LABS itself is defined much later, so the tracker
# reads step counts from it lazily at call time.)
LAB_ORDER = ["ops", "little", "pull", "var", "qual", "fin", "ta", "eoqd", "eoq", "ss", "diag", "diag2"]
LAB_SHORT = {"ops": "Operations", "little": "Little's Law", "pull": "Pull vs. Push",
             "var": "Variability", "qual": "Quality & Yield", "fin": "Economics",
             "ta": "Throughput Acct.", "eoqd": "EOQ Drivers", "eoq": "EOQ limits",
             "ss": "Safety Stock", "diag": "Capstone", "diag2": "Capstone II"}
# Full labels shown in the sidebar "Choose a lab" picker — one source of truth, in LAB_ORDER
# order, so the picker and the "next lab" jump can never drift apart.
LAB_CHOICE_LABEL = {
    "ops": "Operations · Five Focusing Steps",
    "little": "Little's Law · WIP, throughput & lead time",
    "pull": "Pull vs. Push · Capping WIP for free",
    "var": "Variability · Taming uncertainty",
    "qual": "Quality & Yield · When quality is capacity",
    "fin": "Economics · The P&L behind the line",
    "ta": "Throughput Accounting · T, I & OE",
    "eoqd": "EOQ Drivers · Finding the best order size",
    "eoq": "Inventory · The EOQ model & its limits",
    "ss": "Safety Stock · Reorder point & service level",
    "diag": "Capstone · Diagnose & Fix (no labels)",
    "diag2": "Capstone · Diagnose & Fix (the money side)",
}

# The 11 labs are split into two assignments. Part 1 (default) covers the physical line —
# constraints, flow and quality, capped by the diagnostic capstone. Part 2 is the economics
# and inventory-policy extension. Each part is submitted separately via its own report.
LAB_PART_ORDER = ["Part 1 — Constraints & Flow", "Part 2 — Economics & Inventory"]
LAB_PARTS = {
    "Part 1 — Constraints & Flow": ["ops", "little", "pull", "var", "qual", "diag"],
    "Part 2 — Economics & Inventory": ["fin", "ta", "eoqd", "eoq", "ss", "diag2"],
}

def _part_labs(part):
    return LAB_PARTS.get(part, LAB_PARTS[LAB_PART_ORDER[0]])

def _part_of_lab(prefix):
    for part, labs in LAB_PARTS.items():
        if prefix in labs:
            return part
    return LAB_PART_ORDER[0]
_CODE_SALT = "juice-bottling-lab-v1"


def _get_progress():
    """Progress lives in session as {prefix: set(done step indices)}. On a fresh session it
    is restored from the compact `prog` URL parameter so it survives reloads."""
    if "lab_progress" not in st.session_state:
        prog = {}
        raw = st.query_params.get("prog")
        if raw:
            try:
                data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
                if isinstance(data, dict):
                    prog = {k: set(int(i) for i in v) for k, v in data.items()}
            except Exception:
                prog = {}
        st.session_state["lab_progress"] = prog
    return st.session_state["lab_progress"]


def _save_progress():
    prog = st.session_state.get("lab_progress", {})
    try:
        payload = {k: sorted(v) for k, v in prog.items() if v}
        enc = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()).decode()
        if st.query_params.get("prog") != enc:
            st.query_params["prog"] = enc
    except Exception:
        pass


def _jsonable(v):
    """Make a value safe for json.dumps: numpy arrays/scalars, sets, and DataFrames all
    become plain lists / numbers / dicts. Anything else unknown becomes None."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, set):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if hasattr(v, "to_dict"):            # pandas DataFrame / Series
        try:
            return v.to_dict("list")
        except Exception:
            try:
                return v.to_dict()
            except Exception:
                return None
    if hasattr(v, "tolist"):            # numpy array / scalar
        try:
            return _jsonable(v.tolist())
        except Exception:
            return None
    for _cast in (int, float):
        try:
            return _cast(v)
        except Exception:
            pass
    return None


def _is_store_progress_key(k):
    """The session keys that make up a student's *progress* (persisted to shared storage).
    Deliberately excludes the sidebar scenario config (set by the seed / Director) and the
    two answer widgets that pass an explicit default (_pred_, _est_), which Streamlit would
    warn about if pre-seeded — matching the app's existing reload behavior for those."""
    if not isinstance(k, str):
        return False
    if k in ("lab_choice", "student_name"):
        return True
    if k.endswith("_step") and k[:-5] in LAB_ORDER:
        return True
    if "_reflect_" in k:
        return True
    if "_chal_attempts_" in k or "_chal_passed_" in k or "_chal_seen_" in k:
        return True
    if "_pred_" in k or "_est_" in k:        # the student's MC / numeric answers themselves
        return True
    return False


def _progress_snapshot():
    """A plain-JSON snapshot of just the student's progress (no figures, RNG, or widgets)."""
    ss = st.session_state
    snap = {}
    lp = ss.get("lab_progress")
    if isinstance(lp, dict):
        snap["lab_progress"] = {str(p): sorted(int(x) for x in v) for p, v in lp.items() if v}
    rd = ss.get("reflect_done")
    if isinstance(rd, (set, list, tuple)):
        snap["reflect_done"] = sorted(str(x) for x in rd)
    for k in list(ss.keys()):
        if _is_store_progress_key(k):
            snap[k] = _jsonable(ss[k])
    return snap


def _restore_progress(saved):
    """Copy a saved snapshot back into session_state (progress keys only). Called once, right
    after initialize_state() and before any widgets are created, so pre-seeding is safe."""
    if not isinstance(saved, dict) or not saved:
        return
    ss = st.session_state
    for k, v in saved.items():
        if k == "lab_progress" and isinstance(v, dict):
            ss["lab_progress"] = {str(p): set(int(x) for x in lst) for p, lst in v.items()}
        elif k == "reflect_done" and isinstance(v, (list, tuple)):
            ss["reflect_done"] = set(str(x) for x in v)
        elif _is_store_progress_key(k):
            ss[k] = v


def _autosave():
    """Persist progress to shared storage after a meaningful step. No-op when storage is off
    or no student is identified; skips the write when nothing has changed."""
    if not store.enabled() or not _STORE_SID:
        return
    try:
        snap = _progress_snapshot()
        enc = json.dumps(snap, separators=(",", ":"), sort_keys=True)
        if st.session_state.get("_last_saved") == enc:
            return
        if store.save(_STORE_GAME, _STORE_SID, snap):
            st.session_state["_last_saved"] = enc
            st.session_state["_last_saved_ts"] = time.time()
            st.session_state["_save_failed"] = False
        else:
            st.session_state["_save_failed"] = True
    except Exception:
        # Record the failure so the UI can warn honestly instead of claiming work was saved.
        st.session_state["_save_failed"] = True


def _get_answers():
    """Restore saved MC/estimate selections from the compact `ans` URL parameter (once per
    session), so a browser refresh keeps each step's actual answer — not just which steps are
    done. Runs before any widget is created, so pre-seeding the widget keys is safe."""
    if st.session_state.get("_answers_loaded"):
        return
    st.session_state["_answers_loaded"] = True
    raw = st.query_params.get("ans")
    if not raw:
        return
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and ("_pred_" in k or "_est_" in k) and k not in st.session_state:
                st.session_state[k] = v


def _save_answers():
    """Mirror the current MC/estimate selections into the `ans` URL parameter so they survive a
    refresh even when shared storage is off (progress already rides in `prog`/`refl`)."""
    ss = st.session_state
    data = {k: ss[k] for k in list(ss.keys())
            if isinstance(k, str) and ("_pred_" in k or "_est_" in k) and ss[k] is not None}
    try:
        enc = base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":"), sort_keys=True).encode()).decode()
        if st.query_params.get("ans") != enc:
            st.query_params["ans"] = enc
    except Exception:
        pass


def _on_answer_change():
    """Fired whenever a prediction/estimate changes: persist it immediately (URL + store) so a
    changed answer is saved right away, not only when the student advances to the next step."""
    _save_answers()
    _autosave()


def mark_step_done(prefix, i):
    """Record that the student has predicted and run step i of a lab. Idempotent; only
    touches the URL when something actually changed."""
    prog = _get_progress()
    done = prog.setdefault(prefix, set())
    if i not in done:
        done.add(i)
        _save_progress()
        _autosave()


def _get_reflect():
    """Set of 'prefix:idx' keys for reflections the student has written, restored from the
    compact `refl` URL parameter so it survives reloads."""
    if "reflect_done" not in st.session_state:
        done = set()
        raw = st.query_params.get("refl")
        if raw:
            try:
                data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
                if isinstance(data, list):
                    done = set(str(x) for x in data)
            except Exception:
                done = set()
        st.session_state["reflect_done"] = done
    return st.session_state["reflect_done"]


def _save_reflect():
    done = st.session_state.get("reflect_done", set())
    try:
        enc = base64.urlsafe_b64encode(
            json.dumps(sorted(done), separators=(",", ":")).encode()).decode()
        if st.query_params.get("refl") != enc:
            st.query_params["refl"] = enc
    except Exception:
        pass


def mark_reflect_done(prefix, i):
    done = _get_reflect()
    key = f"{prefix}:{i}"
    if key not in done:
        done.add(key)
        _save_reflect()
        _autosave()


def reflect_totals():
    """(#reflections written, #reflection prompts across all labs)."""
    total = sum(1 for p in LAB_ORDER if p in LABS
                for s in LABS[p]["steps"] if s.get("reflect"))
    valid = {f"{p}:{j}" for p in LAB_ORDER if p in LABS
             for j, s in enumerate(LABS[p]["steps"]) if s.get("reflect")}
    done = len(_get_reflect() & valid)
    return done, total


def progress_summary():
    """Per-lab and overall completion, computed against the live LABS step counts."""
    prog = _get_progress()
    rows = []
    total_done = total_steps = 0
    for pre in LAB_ORDER:
        n = len(LABS[pre]["steps"]) if pre in LABS else 0
        done = len(prog.get(pre, set()) & set(range(n)))
        rows.append((pre, LAB_SHORT.get(pre, pre), done, n))
        total_done += done
        total_steps += n
    pct = round(100 * total_done / total_steps) if total_steps else 0
    return rows, total_done, total_steps, pct


def lab_is_complete(prefix):
    """True when every step of a lab has been predicted-and-run."""
    n = len(LABS[prefix]["steps"]) if prefix in LABS else 0
    if n == 0:
        return False
    prog = _get_progress()
    return len(prog.get(prefix, set()) & set(range(n))) == n


def part_complete(part):
    """True when every lab in the given part has been fully completed."""
    labs = [p for p in _part_labs(part) if p in LABS]
    return bool(labs) and all(lab_is_complete(p) for p in labs)


def all_labs_complete():
    """True when every lab in the course has been fully completed."""
    labs = [p for p in LAB_ORDER if p in LABS]
    return bool(labs) and all(lab_is_complete(p) for p in labs)


def next_incomplete_lab(after_prefix):
    """The next lab within the SAME part (wrapping around) that still has unfinished steps.
    Returns None when the part is done."""
    part = _part_of_lab(after_prefix)
    order = [p for p in _part_labs(part) if p in LABS]
    if after_prefix in order:
        k = order.index(after_prefix)
        seq = order[k + 1:] + order[:k + 1]
    else:
        seq = order
    for p in seq:
        if not lab_is_complete(p):
            return p
    return None


def _report_data(part):
    """Collect what the student did across a part's labs, for the submission report."""
    prog = _get_progress()
    ss = st.session_state
    out = {"labs": [], "steps_done": 0, "steps_total": 0, "labs_done": 0, "labs_total": 0,
           "refl_written": 0, "refl_total": 0}
    for pre in _part_labs(part):
        if pre not in LABS:
            continue
        steps = LABS[pre]["steps"]
        n = len(steps)
        d = len(prog.get(pre, set()) & set(range(n)))
        out["steps_total"] += n
        out["steps_done"] += d
        out["labs_total"] += 1
        complete = (n > 0 and d >= n)
        if complete:
            out["labs_done"] += 1
        refls, chal = [], None
        for i, s in enumerate(steps):
            if s.get("reflect"):
                out["refl_total"] += 1
                txt = (ss.get(f"{pre}_reflect_{i}") or "").strip()
                if txt:
                    out["refl_written"] += 1
                refls.append((s["reflect"], txt))
            if s.get("challenge"):
                chal = {"passed": bool(ss.get(f"{pre}_chal_passed_{i}", False)),
                        "attempts": int(ss.get(f"{pre}_chal_attempts_{i}", 0) or 0),
                        "done": i in prog.get(pre, set())}
        out["labs"].append({"name": LAB_SHORT.get(pre, pre), "label": LAB_CHOICE_LABEL.get(pre, pre),
                            "done": d, "total": n, "complete": complete,
                            "reflections": refls, "challenge": chal})
    return out


def build_part_report_html(part, student_name):
    """A self-contained HTML report of a student's work on one part, for LMS upload."""
    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    d = _report_data(part)
    name = esc((student_name or "").strip() or "(name not entered)")
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    secs = []
    for L in d["labs"]:
        badge = ("done" if L["complete"] else ("part" if L["done"] else "none"))
        badge_txt = ("Complete" if L["complete"] else (f"{L['done']}/{L['total']} steps" if L["done"] else "Not started"))
        ch = ""
        if L["challenge"]:
            c = L["challenge"]
            cs = ("passed ✓" if c["passed"] else (f"attempted ({c['attempts']} tries)" if c["attempts"] else "not attempted"))
            ch = f'<div class="ch">Design challenge: <b>{cs}</b></div>'
        rfl = ""
        if L["reflections"]:
            items = []
            for q, a in L["reflections"]:
                a_html = f'<div class="ans">{esc(a)}</div>' if a else '<div class="ans none">— not written —</div>'
                items.append(f'<div class="refl"><div class="q">{esc(q)}</div>{a_html}</div>')
            rfl = '<div class="refls"><div class="rh">Self-explanations</div>' + "".join(items) + "</div>"
        secs.append(
            f'<div class="lab"><div class="labhead"><span class="labname">{esc(L["label"])}</span>'
            f'<span class="pill {badge}">{badge_txt}</span></div>{ch}{rfl}</div>')
    pct = round(100 * d["steps_done"] / d["steps_total"]) if d["steps_total"] else 0
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Capacity Crush — {esc(part)} — {name}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color:#1f2a44;
         max-width: 820px; margin: 32px auto; padding: 0 20px; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 2px; }}
  .sub {{ color:#5b6472; margin-bottom: 18px; }}
  .meta {{ background:#f4f5f9; border:1px solid #e4e7ec; border-radius:10px; padding:12px 16px; margin-bottom:18px; }}
  .meta b {{ color:#1f2a44; }}
  .summary {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .stat {{ flex:1; min-width:120px; background:#fff; border:1px solid #e4e7ec; border-left:4px solid #ea580c;
          border-radius:9px; padding:10px 14px; }}
  .stat .n {{ font-size:1.5rem; font-weight:800; }}
  .stat .l {{ color:#5b6472; font-size:.82rem; }}
  .lab {{ border:1px solid #e4e7ec; border-radius:10px; padding:12px 16px; margin-bottom:12px; }}
  .labhead {{ display:flex; justify-content:space-between; align-items:center; }}
  .labname {{ font-weight:700; }}
  .pill {{ font-size:.76rem; font-weight:700; padding:3px 10px; border-radius:999px; }}
  .pill.done {{ background:#eafaf0; color:#15803d; }} .pill.part {{ background:#fff8e6; color:#8a5300; }}
  .pill.none {{ background:#f1f3f7; color:#8a93a6; }}
  .ch {{ margin-top:6px; font-size:.9rem; color:#334155; }}
  .refls {{ margin-top:10px; }} .rh {{ font-weight:700; font-size:.85rem; color:#5b6472; margin-bottom:4px; }}
  .refl {{ margin:8px 0; }} .q {{ font-size:.88rem; color:#475569; }}
  .ans {{ background:#eef2ff; border:1px solid #c7d2fe; border-radius:7px; padding:7px 10px; margin-top:3px; }}
  .ans.none {{ background:#fff5f5; border-color:#fecaca; color:#b42318; }}
  footer {{ color:#8a93a6; font-size:.78rem; margin-top:22px; border-top:1px solid #e4e7ec; padding-top:10px; }}
</style></head><body>
<h1>🧃 Juicetification: Capacity Crush</h1>
<div class="sub">{esc(part)} — submission report</div>
<div class="meta"><b>Student:</b> {name} &nbsp;·&nbsp; <b>Generated:</b> {ts}</div>
<div class="summary">
  <div class="stat"><div class="n">{d['labs_done']}/{d['labs_total']}</div><div class="l">labs complete</div></div>
  <div class="stat"><div class="n">{d['steps_done']}/{d['steps_total']}</div><div class="l">steps done ({pct}%)</div></div>
  <div class="stat"><div class="n">{d['refl_written']}/{d['refl_total']}</div><div class="l">self-explanations written</div></div>
</div>
{"".join(secs)}
<footer>Generated by Juicetification: Capacity Crush. This report records the student's work on this
part (labs completed, design-challenge outcomes, and self-explanations) for upload to the course LMS.</footer>
</body></html>"""


def _pdf_text(s):
    """Fold the app's Unicode down to Latin-1 so the report PDF renders with the built-in font
    and needs no bundled TTF on the deployment."""
    if not isinstance(s, str):
        s = str(s)
    repl = {"\u2014": "-", "\u2013": "-", "\u00f7": "/", "\u00d7": "x", "\u2248": "~",
            "\u2264": "<=", "\u2265": ">=", "\u03bb": "lambda", "\u00b7": "-", "\u2192": "->",
            "\u2022": "-", "\u2026": "...", "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
            "\u2713": "[ok]", "\u2717": "[x]", "\u221a": "sqrt", "\u00b2": "2"}
    for a, b in repl.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "ignore").decode("latin-1")


def build_part_report_pdf(part, student_name):
    """A self-contained PDF of a student's work on one part, for LMS upload (built-in font only)."""
    from fpdf import FPDF
    d = _report_data(part)
    name = (student_name or "").strip() or "(name not entered)"
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    pct = round(100 * d["steps_done"] / d["steps_total"]) if d["steps_total"] else 0
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 9, _pdf_text("Juicetification: Capacity Crush"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 100, 114)
    pdf.multi_cell(0, 6, _pdf_text(part + " - submission report"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(31, 42, 68)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, _pdf_text(f"Student: {name}        Generated: {ts}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.multi_cell(0, 6, _pdf_text(
        f"Summary: {d['labs_done']}/{d['labs_total']} labs complete  -  "
        f"{d['steps_done']}/{d['steps_total']} steps ({pct}%)  -  "
        f"{d['refl_written']}/{d['refl_total']} self-explanations written"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for L in d["labs"]:
        status = ("Complete" if L["complete"]
                  else (f"{L['done']}/{L['total']} steps" if L["done"] else "Not started"))
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_draw_color(220, 224, 230)
        pdf.multi_cell(0, 6.5, _pdf_text(f"{L['label']}   -   {status}"), border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        pdf.set_font("Helvetica", "", 10)
        if L["challenge"]:
            c = L["challenge"]
            cs = ("passed" if c["passed"]
                  else (f"attempted ({c['attempts']} tries)" if c["attempts"] else "not attempted"))
            pdf.multi_cell(0, 5, _pdf_text(f"Design challenge: {cs}"), new_x="LMARGIN", new_y="NEXT")
        for q, a in L["reflections"]:
            pdf.set_text_color(90, 100, 114)
            pdf.set_font("Helvetica", "I", 9)
            pdf.multi_cell(0, 4.6, _pdf_text("Q:  " + q), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(31, 42, 68)
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _pdf_text("A:  " + (a if a else "(not written)")), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        pdf.ln(2.5)
    return bytes(pdf.output())


def initialize_state():
    fresh = "capacity_0" not in st.session_state
    seed = _load_run_seed() if fresh else {}

    def sd(key, original):
        # Seed from the last run when starting a fresh session; otherwise keep whatever
        # the widgets already hold (so live edits are never overwritten).
        st.session_state.setdefault(key, seed.get(key, original))

    for i in range(N_OPS):
        sd(f"capacity_{i}", DEFAULT_CAPACITIES[i])
        sd(f"sides_{i}", DEFAULT_SIDES[i])
    sd("starting_inventory", DEFAULT_STARTING_INVENTORY)
    sd("simulation_years", DEFAULT_SIMULATION_YEARS)
    sd("horizon", DEFAULT_HORIZON)
    sd("supply_reliability", DEFAULT_SUPPLY_RELIABILITY)
    sd("fin_order_size", DEFAULT_ORDER_SIZE)
    sd("demand_variable", DEFAULT_DEMAND_VARIABLE)
    sd("demand_dice", DEFAULT_DEMAND_DICE)
    sd("demand_faces", DEFAULT_DEMAND_FACES)
    st.session_state.setdefault("anim_speed", "Normal")
    st.session_state.setdefault("animate", ANIMATIONS_DEFAULT_ON)
    st.session_state.setdefault("sim_results", None)
    # WIP limits
    sd("wip_limit_on", DEFAULT_WIP_LIMIT_ON)
    for i in range(N_OPS):
        sd(f"wip_cap_{i}", DEFAULT_WIP_CAP)
    # Reorder point / safety stock, and per-station quality (scrap)
    sd("reorder_point_on", DEFAULT_REORDER_ON)
    sd("reorder_point", DEFAULT_REORDER_POINT)
    sd("scrap_on", DEFAULT_SCRAP_ON)
    for i in range(N_OPS):
        sd(f"scrap_pct_{i}", DEFAULT_SCRAP_PCT)
    # Replications
    st.session_state.setdefault("n_reps", 20)
    st.session_state.setdefault("rep_results", None)
    # Guided labs (operations + economics + variability; each tracks its own step)
    st.session_state.setdefault("app_mode", "Guided Lab")
    st.session_state.setdefault("lab_choice", "Operations · Five Focusing Steps")
    st.session_state.setdefault("ops_step", 0)
    st.session_state.setdefault("fin_step", 0)
    st.session_state.setdefault("var_step", 0)
    st.session_state.setdefault("eoq_step", 0)
    st.session_state.setdefault("eoqd_step", 0)
    st.session_state.setdefault("little_step", 0)
    st.session_state.setdefault("pull_step", 0)
    st.session_state.setdefault("ta_step", 0)
    st.session_state.setdefault("ss_step", 0)
    st.session_state.setdefault("qual_step", 0)
    st.session_state.setdefault("diag_step", 0)
    st.session_state.setdefault("diag2_step", 0)
    st.session_state.setdefault("lab_part", LAB_PART_ORDER[0])
    st.session_state.setdefault("run_counter", 0)
    # Navigation tokens for the scroll-to-top-on-nav behavior (equal on first load → no scroll
    # until a real navigation event advances the token).
    st.session_state.setdefault("_nav_token", 0)
    st.session_state.setdefault("_nav_token_seen", 0)
    # Financials — seed from the last run when starting a fresh session (so they persist
    # across reloads), otherwise keep whatever the widgets hold. They only return to
    # defaults when the "Reset financials" button is pressed.
    sd("fin_revenue_per_unit", DEFAULT_REVENUE_PER_UNIT)
    sd("fin_alloc_pct", DEFAULT_ALLOC_PCT)
    sd("fin_wip_holding", DEFAULT_WIP_HOLDING)
    sd("fin_rmc", DEFAULT_RMC)
    sd("fin_order_cost", DEFAULT_ORDER_COST)
    sd("fin_raw_holding", DEFAULT_RAW_HOLDING)
    for f in FACE_ROWS:
        dfx, dpx = DEFAULT_FIN_LOOKUP[f]
        sd(f"fin_fixed_{f}", dfx)
        sd(f"fin_prod_{f}", dpx)
    # Repair stale/blank financials: a value left at 0 where its default is non-zero is a
    # corrupted cell (the form would show zeros). Restore the default so the cells always
    # show real numbers. Runs every load, before the form's widgets are created — and it
    # never touches a legitimately non-zero value the user (or the last run) set.
    def _repair(key, default):
        if default:
            try:
                cur = float(st.session_state.get(key))
            except (TypeError, ValueError):
                cur = 0.0
            if cur == 0.0:
                st.session_state[key] = default
    _repair("fin_revenue_per_unit", DEFAULT_REVENUE_PER_UNIT)
    _repair("fin_alloc_pct", DEFAULT_ALLOC_PCT)
    _repair("fin_wip_holding", DEFAULT_WIP_HOLDING)
    _repair("fin_rmc", DEFAULT_RMC)
    _repair("fin_order_cost", DEFAULT_ORDER_COST)
    _repair("fin_raw_holding", DEFAULT_RAW_HOLDING)
    for f in FACE_ROWS:
        dfx, dpx = DEFAULT_FIN_LOOKUP[f]
        _repair(f"fin_fixed_{f}", dfx)
        _repair(f"fin_prod_{f}", dpx)


def step_value(key, delta, lo, hi):
    """Callback for the −/+ buttons. Runs before widgets re-instantiate, so it
    may safely write to the number_input's own key."""
    st.session_state[key] = int(min(hi, max(lo, int(st.session_state[key]) + delta)))


def reset_financials():
    """Restore every financial input — including each per-die cost cell — to its default.
    Used as an on_click callback so the writes land *before* the widgets are rebuilt."""
    st.session_state["fin_revenue_per_unit"] = DEFAULT_REVENUE_PER_UNIT
    st.session_state["fin_alloc_pct"] = DEFAULT_ALLOC_PCT
    st.session_state["fin_wip_holding"] = DEFAULT_WIP_HOLDING
    st.session_state["fin_rmc"] = DEFAULT_RMC
    st.session_state["fin_order_cost"] = DEFAULT_ORDER_COST
    st.session_state["fin_raw_holding"] = DEFAULT_RAW_HOLDING
    for f in FACE_ROWS:
        dfx, dpx = DEFAULT_FIN_LOOKUP[f]
        st.session_state[f"fin_fixed_{f}"] = dfx
        st.session_state[f"fin_prod_{f}"] = dpx


def reset_line_to_defaults():
    """Reset the production-line configuration (not the lab progress or financials)
    back to defaults and clear the last run. Used when the lab advances to a new step
    so the user starts each step from a clean slate."""
    for i in range(N_OPS):
        st.session_state[f"capacity_{i}"] = DEFAULT_CAPACITIES[i]
        st.session_state[f"sides_{i}"] = DEFAULT_SIDES[i]
        st.session_state[f"wip_cap_{i}"] = DEFAULT_WIP_CAP
        st.session_state[f"scrap_pct_{i}"] = DEFAULT_SCRAP_PCT
    st.session_state["starting_inventory"] = DEFAULT_STARTING_INVENTORY
    st.session_state["simulation_years"] = DEFAULT_SIMULATION_YEARS
    # Always back to a full year: every lab step (and every challenge) is written and tuned
    # against the year-long horizon, so a short horizon picked for one experiment can't leak
    # into the next step.
    st.session_state["horizon"] = DEFAULT_HORIZON
    st.session_state["supply_reliability"] = DEFAULT_SUPPLY_RELIABILITY
    st.session_state["demand_variable"] = DEFAULT_DEMAND_VARIABLE
    st.session_state["demand_dice"] = DEFAULT_DEMAND_DICE
    st.session_state["demand_faces"] = DEFAULT_DEMAND_FACES
    st.session_state["wip_limit_on"] = DEFAULT_WIP_LIMIT_ON
    st.session_state["reorder_point_on"] = DEFAULT_REORDER_ON
    st.session_state["reorder_point"] = DEFAULT_REORDER_POINT
    st.session_state["scrap_on"] = DEFAULT_SCRAP_ON
    st.session_state["fin_order_size"] = DEFAULT_ORDER_SIZE
    st.session_state["sim_results"] = None


def reset_defaults():
    reset_line_to_defaults()
    st.session_state["rep_results"] = None
    st.session_state.pop("scenario_A", None)
    st.session_state.pop("scenario_B", None)
    reset_financials()
    # Forget the remembered last-run inputs so the sidebar returns to original defaults.
    st.session_state.pop("last_run_config", None)
    try:
        st.query_params.pop("cfg", None)
    except Exception:
        pass


def validation_errors(caps, sides):
    errs = []
    for i in range(N_OPS):
        if caps[i] > 0 and sides[i] == 0:
            errs.append(f"Operation #{i + 1}: a number of dice is set, but each die has 0 faces. "
                        f"Give it at least 1 face, or set the dice to 0 to switch it off.")
        if caps[i] == 0 and sides[i] > 0:
            errs.append(f"Operation #{i + 1}: the die has faces, but 0 dice are rolled. "
                        f"Give it at least 1 die, or set the faces to 0 to switch it off.")
    if not any(c > 0 and s > 0 for c, s in zip(caps, sides)):
        errs.append("At least one operation needs both dice and faces greater than 0.")
    return errs


# =========================================================
# Simulation engine
# =========================================================
def _pop_fifo(q, k):
    """Pop k units (oldest first) from a deque of [entry_hour, qty] batches.
    Returns the popped sub-batches as (entry_hour, qty) tuples."""
    out = []
    while k > 0 and q:
        eh, qty = q[0]
        take = qty if qty <= k else k
        out.append((eh, take))
        if take == qty:
            q.popleft()
        else:
            q[0][1] = qty - take
        k -= take
    return out


def run_simulation(caps, sides, start_inv, hours, supply_reliability=1.0, wip_limits=None,
                   track_flow=True, demand_dice=0, demand_faces=0, order_size=1,
                   reorder_point=None, scrap=None, rng=None):
    """Juice-bottling line: a serial production line of bottling stations.

    Active stations (dice>0 and faces>0) form the line. Each hour a station's
    *potential* output (bottles/hour) is the sum of `capacity` dice with `sides` faces:

        min potential  = capacity            (every die shows 1)
        avg potential  = capacity*(sides+1)/2
        max potential  = capacity*sides       (every die shows its max face)

    A station can only pass on the SMALLER of (its roll this hour) and (the bottles
    waiting in front of it). Operation 1 is fed raw material (empty bottles / juice)
    by a supplier:

        supply_reliability is a probability in [0, 1]. Each hour the supplier tops the
        raw-material buffer in front of Operation 1 up to one hour of Op 1's capacity,
        but only with probability = reliability. At 100% it always delivers, so Op 1 is
        never starved. Below 100%, deliveries are missed at random, the raw buffer can
        run dry, and Operation 1 is STARVED. The raw-material inventory IS the inventory
        shown in front of Operation 1.

    WIP limits (wip_limits): an optional per-station cap (full length N_OPS, use
    math.inf for unlimited) on how many bottles may wait in front of a station. When
    a downstream buffer is full, the upstream station is BLOCKED from pushing into
    it — a pull / Kanban control that holds WIP down but can throttle throughput.
    Moves are resolved downstream-first so a station that drains its buffer this
    hour frees space for the one feeding it. With all caps infinite this is exactly
    the classic uncapped push line.

    Fluctuation plus dependency drags throughput below average capacity and piles
    work-in-process (WIP) in front of the slowest station — the constraint.

    Flow time (track_flow): when on, each bottle released into the line is tagged with
    its entry hour (via FIFO token queues) and its time-in-system is recorded when it
    finishes. This gives a *measured* average flow time and a flow-time distribution,
    which should match the *derived* Little's-Law value W = L / λ (avg WIP ÷ throughput).
    """
    active_idx = [i for i in range(N_OPS) if caps[i] > 0 and sides[i] > 0]
    n = len(active_idx)
    if n == 0:
        return None

    a_caps = [caps[i] for i in active_idx]
    a_sides = [sides[i] for i in active_idx]
    labels = [f"#{i + 1}" for i in active_idx]
    if wip_limits is None:
        a_limits = [math.inf] * n
    else:
        a_limits = [(math.inf if (wip_limits[i] is None or wip_limits[i] < 0)
                     else wip_limits[i]) for i in active_idx]

    # Start no fuller than each station's cap. buffers[0] is the raw-material inventory
    # in front of Operation 1 — the supplier fills it and Operation 1 draws from it, so
    # "raw-material inventory" and "inventory at Operation 1" are the same thing.
    buffers = [min(start_inv, a_limits[p]) for p in range(n)]
    supply_reliability = max(0.0, min(1.0, supply_reliability))
    if reorder_point is None:
        reorder_point = a_caps[0] * a_sides[0]          # reorder when down to ~1 hour of Op 1 capacity
        reorder_point = min(reorder_point, a_limits[0])  # never above Op 1's WIP cap
    else:
        # A user-set reorder point: the safety-stock level that triggers a replenishment
        # order. Higher ⇒ the raw buffer is refilled earlier and sits fuller, insuring the
        # line against a shaky supplier at the cost of more raw-material holding.
        reorder_point = min(max(1, int(reorder_point)), a_limits[0])
    order_q = max(1, int(order_size))                   # bottles per supplier delivery (batch / EOQ lot)

    # Per-station scrap probability (yield loss). Applied deterministically via a fractional
    # carry so it draws NO random numbers — the supply/demand/roll RNG stream is byte-for-byte
    # identical whether or not scrap is active, and scrap=None is an exact no-op. A station that
    # scraps a share of its output has its *good* capacity cut by that share, so a fast station
    # with poor yield can quietly become the real constraint.
    if scrap is None:
        a_scrap = [0.0] * n
    else:
        a_scrap = [max(0.0, min(1.0, float(scrap[i]))) for i in active_idx]
    scrap_carry = [0.0] * n
    scrap_total = 0
    scrap_by_station = [0] * n
    any_scrap = any(s > 0 for s in a_scrap)

    hourly_out, daily_out, cum_out, wip_total = [], [], [], []
    raw_series, fgi_series = [], []   # daily raw-material inbound & finished-goods inventory
    produced = [0] * n
    # Units each station actually worked on, scrap included. `produced` counts only the good
    # ones, so costing from it makes a scrapped bottle free — see compute_financials (audit P2).
    worked = [0] * n
    inv_sum = [0.0] * n
    finished = 0
    # Material-balance + replenishment instrumentation (additive; draws no RNG, changes no
    # existing output). Lets the test-suite reconcile units and lets the app report the ACTUAL
    # number of supplier orders instead of inferring it from consumption ÷ order size.
    initial_material = sum(buffers)   # bottles present across all buffers at hour 0
    raw_received = 0                  # bottles actually added to the raw buffer over the run
    delivery_events = 0               # hours a supplier shipment actually arrived
    orders_placed = 0                 # purchase orders (lots of order_q) actually shipped
    starved_hours = 0    # hours Operation 1 was held back by a lack of raw material
    frames = []          # per-day snapshot for the live dashboard
    inv_scale = 1        # largest inventory seen (for a stable bar axis)

    # Demand side: when a (fluctuating) market is on, finished units wait in
    # finished-goods inventory (FGI) until sold; demand the line can't meet is lost.
    demand_on = demand_dice > 0 and demand_faces > 0
    fgi = 0              # finished-goods inventory on hand
    fgi_sum = 0.0        # unit-hours of FGI (for holding cost)
    sold = 0
    lost_sales = 0
    demand_total = 0

    # Flow-time tracking: tq[p] is a FIFO of [entry_hour, qty] batches for the WIP
    # waiting in front of station p (p>=1). flow_counts maps flow-time(hours)->units.
    tq = [deque() for _ in range(n)]
    flow_counts = {}
    # Per-call RNG: Streamlit serves every session from one process, so the module-level `random`
    # stream is shared by all students. Callers pass their own random.Random; None falls back to
    # the module stream (single-user / test use).
    _rnd = rng if rng is not None else random
    flow_sum = 0
    flow_n = 0
    if track_flow:
        for p in range(1, n):
            if buffers[p] > 0:
                tq[p].append([0, buffers[p]])   # pre-existing WIP, stamped hour 0

    static = [
        {"label": labels[p], "op_num": active_idx[p] + 1,
         "max_cap": a_caps[p] * a_sides[p], "min_cap": a_caps[p],
         "wip_cap": a_limits[p]}
        for p in range(n)
    ]

    daily_accum = 0
    for hour in range(1, hours + 1):
        potentials = [
            sum(_rnd.randint(1, a_sides[p]) for _ in range(a_caps[p]))
            for p in range(n)
        ]

        # --- supplier delivery (reorder policy). When the raw buffer in front of Op 1
        #     falls to the reorder point, the supplier ships whole BATCHES of `order_q`
        #     bottles — but only if this hour's delivery succeeds (probability =
        #     reliability). Batch ordering (order_q > 1) leaves cycle stock sitting in
        #     raw-material inventory; order_q = 1 is just-in-time. At 100% reliability the
        #     buffer is always refilled before Op 1 works, so Op 1 is never starved. ---
        if buffers[0] < reorder_point:
            if supply_reliability >= 1.0 or _rnd.random() < supply_reliability:
                deficit = reorder_point - buffers[0]
                batches = math.ceil(deficit / order_q)
                _before = buffers[0]
                buffers[0] += batches * order_q
                if buffers[0] > a_limits[0]:
                    buffers[0] = a_limits[0]
                raw_received += buffers[0] - _before   # actual bottles added (after any cap clip)
                delivery_events += 1                   # one shipment arrived this hour
                orders_placed += batches               # lots of order_q on that shipment

        # Resolve moves downstream-first: a station that empties its buffer this
        # hour frees space for the one feeding it (a pull / Kanban step). A station
        # can move only the smallest of its roll, its inbound inventory, and the
        # space remaining in the next station's buffer. With unlimited caps the
        # space term never binds and this equals the classic push line.
        moved = [0] * n
        for p in range(n - 1, -1, -1):
            in_avail = buffers[p]
            out_space = math.inf if p == n - 1 else (a_limits[p + 1] - buffers[p + 1])
            mv = min(potentials[p], in_avail, max(out_space, 0))
            # Operation 1 is "starved" when raw material (not downstream space) was
            # the binding constraint on its output this hour.
            if p == 0 and in_avail < potentials[0] and in_avail <= max(out_space, 0):
                starved_hours += 1
            # Yield: of the mv units worked this hour, only `good` pass inspection; the
            # rest are scrapped (deterministic, via a fractional carry — no RNG drawn).
            if any_scrap and a_scrap[p] > 0 and mv > 0:
                exact = mv * (1.0 - a_scrap[p]) + scrap_carry[p]
                good = int(exact)
                scrap_carry[p] = exact - good
                scrapped = mv - good
                scrap_total += scrapped
                scrap_by_station[p] += scrapped
            else:
                good = mv
            moved[p] = good
            buffers[p] -= mv
            if p < n - 1:
                buffers[p + 1] += good
            else:
                finished += good
            produced[p] += good
            worked[p] += mv

            # --- flow-time tokens: mirror this move on the FIFO queues. mv units leave
            #     buffers[p]; only the `good` ones advance, so scrapped tokens are dropped. ---
            if track_flow and mv > 0:
                if p == 0 and n == 1:
                    if good > 0:
                        flow_counts[0] = flow_counts.get(0, 0) + good  # enter & exit same hour
                        flow_n += good
                elif p == 0:
                    if good > 0:
                        tq[1].append([hour, good])                     # good units released
                elif p == n - 1:
                    remain = good
                    for eh, qty in _pop_fifo(tq[p], mv):               # consume mv from queue
                        adv = min(qty, remain)                          # only good finish
                        if adv > 0:
                            ft = hour - eh
                            flow_counts[ft] = flow_counts.get(ft, 0) + adv
                            flow_sum += ft * adv
                            flow_n += adv
                            remain -= adv
                else:
                    remain = good
                    for eh, qty in _pop_fifo(tq[p], mv):               # consume mv from queue
                        adv = min(qty, remain)                          # only good advance
                        if adv > 0:
                            tq[p + 1].append([eh, adv])
                            remain -= adv

        # --- finished goods meet the market ---
        fgi += moved[n - 1]
        if demand_on:
            d = sum(_rnd.randint(1, demand_faces) for _ in range(demand_dice))
            sold_now = fgi if d >= fgi else d
            fgi -= sold_now
            sold += sold_now
            lost_sales += d - sold_now
            demand_total += d
        else:
            sold += fgi        # unlimited market: everything finished is sold
            fgi = 0
        fgi_sum += fgi

        for p in range(n):
            inv_sum[p] += buffers[p]
        hourly_out.append(moved[n - 1])
        inv_scale = max(inv_scale, max(buffers))

        daily_accum += moved[n - 1]
        if hour % HOURS_PER_DAY == 0:
            day = hour // HOURS_PER_DAY
            hrs_so_far = day * HOURS_PER_DAY
            daily_out.append(daily_accum)
            cum_out.append(finished)
            wip_total.append(sum(buffers[1:]))
            raw_now = buffers[0]            # raw-material inventory = inventory at Op 1
            raw_series.append(raw_now)
            fgi_series.append(fgi)
            daily_accum = 0
            frames.append({
                "day": day,
                "total_output": finished,
                "avg_out_hr": finished / hrs_so_far,
                "raw_inv": raw_now,
                "fgi": fgi,
                "op_detail": [
                    {**static[p],
                     "avg_prod": produced[p] / hrs_so_far,
                     "avg_inv": inv_sum[p] / hrs_so_far,
                     "end_inv": buffers[p]}
                    for p in range(n)
                ],
            })

    op_detail = [
        {
            "label": labels[p],
            "op_num": active_idx[p] + 1,
            "max_cap": a_caps[p] * a_sides[p],
            "min_cap": a_caps[p],
            "wip_cap": a_limits[p],
            "avg_prod": produced[p] / hours,
            "avg_worked": worked[p] / hours,
            "avg_inv": inv_sum[p] / hours,
            "end_inv": buffers[p],
        }
        for p in range(n)
    ]

    days = hours // HOURS_PER_DAY
    avg_out_hr = finished / hours
    theoretical_hr = min(c * (s + 1) / 2 for c, s in zip(a_caps, a_sides))
    bottleneck_pos = min(range(n), key=lambda p: a_caps[p] * (a_sides[p] + 1) / 2)
    # Effective (yield-adjusted) capacity. A scrap station only ever works on what the line
    # feeds it, so its *good* output is (feed rate × yield), which can be far below its nominal
    # rate. Propagate the good flow down the line to find the true good-throughput ceiling and
    # the station that sets it — often a "fast" station whose poor yield makes it the constraint.
    _g = math.inf
    _gseq = []
    for p in range(n):
        processed = min(_g, a_caps[p] * (a_sides[p] + 1) / 2)
        _g = processed * (1.0 - a_scrap[p])
        _gseq.append(_g)
    eff_bottleneck_rate = _gseq[-1] if _gseq else 0.0
    eff_bottleneck_pos = next((p for p in range(n)
                               if abs(_gseq[p] - eff_bottleneck_rate) < 1e-9), bottleneck_pos)

    # --- demand side summary ---
    if demand_on:
        total_sold = sold
        fill_rate = (sold / demand_total) if demand_total else 1.0
    else:
        total_sold = finished
        fill_rate = 1.0
    avg_fgi = fgi_sum / hours

    # --- Little's Law: L (avg WIP in system) = downstream inventory; λ = throughput ---
    wip_L = sum(inv_sum[p] for p in range(1, n)) / hours
    lam = avg_out_hr
    flow_derived = (wip_L / lam) if lam > 0 else 0.0          # hours, W = L / λ
    if track_flow and flow_n > 0:
        flow_measured = flow_sum / flow_n                     # hours, average sojourn
        ks = sorted(flow_counts)

        def _pctl(q):
            target = q * flow_n
            cum = 0
            for k in ks:
                cum += flow_counts[k]
                if cum >= target:
                    return k
            return ks[-1] if ks else 0

        flow_min, flow_max = (ks[0], ks[-1]) if ks else (0, 0)
        flow_median, flow_p90 = _pctl(0.50), _pctl(0.90)
    else:
        flow_measured = flow_derived
        flow_counts = {}
        flow_min = flow_max = flow_median = flow_p90 = 0

    idx = pd.RangeIndex(1, days + 1, name="Day")
    return {
        "labels": labels,
        "total_output": finished,
        "avg_out_hr": avg_out_hr,
        "avg_out_day": finished / days,
        "theoretical_hr": theoretical_hr,
        "efficiency": (avg_out_hr / theoretical_hr) if theoretical_hr else 0.0,
        "bottleneck_label": labels[bottleneck_pos],
        "eff_bottleneck_label": labels[eff_bottleneck_pos],
        "eff_bottleneck_rate": eff_bottleneck_rate,
        "supply_reliability": supply_reliability,
        "unlimited_supply": supply_reliability >= 1.0,
        "demand_on": demand_on,
        "total_sold": total_sold,
        "lost_sales": lost_sales,
        "demand_total": demand_total if demand_on else total_sold,
        "avg_fgi": avg_fgi,
        "fill_rate": fill_rate,
        "end_fgi": fgi,
        "end_raw": buffers[0],
        "order_size": order_q,
        "wip_capped": any(math.isfinite(l) for l in a_limits),
        "starved_hours": starved_hours,
        "service_level": (1.0 - starved_hours / hours) if hours else 1.0,
        # Material balance (additive): initial + received = finished + scrapped + ending.
        "initial_material": initial_material,
        "raw_received": raw_received,
        "ending_material": sum(buffers),
        "ending_wip_downstream": sum(buffers[1:]),
        "material_residual": initial_material + raw_received - finished - scrap_total - sum(buffers),
        # Actual replenishment events (vs. the EOQ proxy orders=ceil(consumption/order_size)).
        "delivery_events": delivery_events,
        "orders_placed": orders_placed,
        "reorder_point": reorder_point,
        "scrap_total": scrap_total,
        "scrap_by_station": [scrap_by_station[p] for p in range(n)],
        "yield_rate": (finished / (finished + scrap_total)) if (finished + scrap_total) else 1.0,
        "hours": hours,
        "wip_L": wip_L,
        "throughput_rate": lam,
        "flow_time_derived": flow_derived,
        "flow_time_measured": flow_measured,
        "flow_counts": flow_counts,
        "flow_min": flow_min,
        "flow_max": flow_max,
        "flow_median": flow_median,
        "flow_p90": flow_p90,
        "op_detail": op_detail,
        "frames": frames,
        "inv_scale": inv_scale,
        "days": days,
        "cum_series": cum_out,
        "wip_series": wip_total,
        "daily_series": daily_out,
        "raw_series": raw_series,
        "fgi_series": fgi_series,
        "demand_on": demand_on,
        # Raw-material inventory in front of Op 1 always exists (cycle stock from the
        # order size, plus any supply swings), so we always show it. Finished goods only
        # accumulate when the market fluctuates (otherwise everything sells instantly).
        "show_raw": True,
        "show_fgi": demand_on,
        "show_inventory": True,
        "df_cum": pd.DataFrame({"Cumulative bottles finished": cum_out}, index=idx),
        "df_daily": pd.DataFrame({"Bottles finished that day": daily_out}, index=idx),
        "df_wip": pd.DataFrame({"Total WIP (bottles) waiting in the line": wip_total}, index=idx),
        "df_raw": pd.DataFrame({"Raw material in front of Op 1": raw_series}, index=idx),
        "df_fgi": pd.DataFrame({"Finished goods after last Op": fgi_series}, index=idx),
        "df_inventory": pd.DataFrame(
            {"Raw material (inbound to Op 1)": raw_series, "Finished goods (after last Op)": fgi_series},
            index=idx),
        "df_end": (pd.Series(buffers[1:], index=labels[1:], name="Ending WIP")
                   if n > 1 else pd.Series(dtype=float, name="Ending WIP")),
    }


# =========================================================
# Small HTML building blocks for the LIVE dashboard
# =========================================================
def sparkline(values, color="#ea580c", fill="rgba(234,88,12,0.12)", width=260, height=46):
    """A tiny inline-SVG line chart that grows as `values` lengthens."""
    if not values:
        return f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:{height}px"></svg>'
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    n = len(values)
    if n == 1:
        pts = [(0, height / 2), (width, height / 2)]
    else:
        pts = [(i / (n - 1) * width, height - 3 - (v - lo) / rng * (height - 6))
               for i, v in enumerate(values)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{height} " + poly + f" {width},{height}"
    return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
            f'style="width:100%;height:{height}px;display:block">'
            f'<polygon points="{area}" fill="{fill}"/>'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def dual_sparkline(a, b, color_a="#0ea5e9", color_b="#9333ea", width=260, height=70):
    """Two inline-SVG lines on a shared scale — raw-material vs finished-goods inventory."""
    series = [s for s in (a, b) if s]
    if not series:
        return f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:{height}px"></svg>'
    hi = max((max(s) for s in series), default=1) or 1
    lo = 0

    def poly(vals):
        if not vals:
            return ""
        n = len(vals)
        if n == 1:
            pts = [(0, height / 2), (width, height / 2)]
        else:
            pts = [(i / (n - 1) * width, height - 3 - (v - lo) / (hi - lo or 1) * (height - 6))
                   for i, v in enumerate(vals)]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    pa, pb = poly(a), poly(b)
    line_a = (f'<polyline points="{pa}" fill="none" stroke="{color_a}" stroke-width="2" '
              f'stroke-linejoin="round" stroke-linecap="round"/>') if pa else ""
    line_b = (f'<polyline points="{pb}" fill="none" stroke="{color_b}" stroke-width="2" '
              f'stroke-linejoin="round" stroke-linecap="round"/>') if pb else ""
    return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
            f'style="width:100%;height:{height}px;display:block">{line_a}{line_b}</svg>')


def mini_bars(op_detail, bottleneck_label, scale):
    """Small vertical bars for ending WIP in front of each downstream station."""
    items = op_detail[1:]
    if not items:
        return '<div class="mini-empty">Single-station line — no inter-station WIP.</div>'
    scale = max(scale, 1)
    cells = []
    for d in items:
        h = max(3.0, min(100.0, d["end_inv"] / scale * 100))
        cls = "mini-bar bn" if d["label"] == bottleneck_label else "mini-bar"
        cells.append(
            f'<div class="mini-col"><div class="mini-track">'
            f'<div class="{cls}" style="height:{h:.1f}%"></div></div>'
            f'<div class="mini-lab">{d["label"]}</div>'
            f'<div class="mini-num">{d["end_inv"]:,}</div></div>'
        )
    return '<div class="mini-wrap">' + "".join(cells) + '</div>'


def render_op_panel(op_detail, bottleneck_label, scale=None,
                    raw_inv=None, fgi=None):
    """Build the Excel-style per-station results panel (HTML). The bottles waiting in
    front of Operation 1 ARE the raw-material inventory, so Op 1's row is tagged as raw
    material. Finished goods (made but not yet sold) show as a bookend after the last
    station when a fluctuating market is on."""
    # Scale the bars by the production WIP (downstream of Op 1) plus finished goods, so a
    # large raw-material cycle stock (from big purchase orders) doesn't crush the WIP bars.
    # Operation 1's raw bar is then clamped, with its true count shown.
    downstream = [d["end_inv"] for d in op_detail[1:]]
    basis = downstream + ([int(fgi)] if fgi else [])
    if basis:
        inv_scale = max(max(basis), 1)
    else:
        inv_scale = max(scale or 0, max((d["end_inv"] for d in op_detail), default=0), 1)

    def bookend(kind, title, sub, value):
        pct = max(2.0, min(100.0, value / inv_scale * 100))
        val_left = f"calc({pct:.1f}% + 6px)" if pct < 85 else "auto"
        val_right = "8px" if pct >= 85 else "auto"
        return f"""
        <div class="opd-row opd-{kind}">
            <div class="opd-left">
                <div class="opd-h">{title}</div>
                <div class="opd-cap"><span>{sub}</span><b></b></div>
            </div>
            <div class="opd-track">
                <div class="opd-bar opd-bar-{kind}" style="width:{pct:.1f}%"></div>
                <div class="opd-val" style="left:{val_left}; right:{val_right}">{value:,}</div>
            </div>
            <div class="opd-stats"></div>
        </div>"""

    rows = []
    for d in op_detail:
        pct = max(2.0, min(100.0, d["end_inv"] / inv_scale * 100))
        val_left = f"calc({pct:.1f}% + 6px)" if pct < 85 else "auto"
        val_right = "8px" if pct >= 85 else "auto"
        flag = ('<div class="opd-bottleneck">◀ constraint (slowest station)</div>'
                if d["label"] == bottleneck_label else "")
        raw_tag = ('<div class="opd-rawtag">📦 raw material from supplier</div>'
                   if (raw_inv is not None and d["op_num"] == op_detail[0]["op_num"]) else "")
        _cap = d.get("wip_cap", math.inf)
        cap_html = ('<div class="opd-cap"><span>WIP cap</span><b>{}</b></div>'.format(int(_cap))
                    if _cap != math.inf else "")
        rows.append(f"""
        <div class="opd-row">
            <div class="opd-left">
                <div class="opd-h">Operation {d['op_num']}</div>
                <div class="opd-cap"><span>Max output / hr</span><b>{d['max_cap']}</b></div>
                <div class="opd-cap"><span>Min output / hr</span><b>{d['min_cap']}</b></div>
                {cap_html}
                {raw_tag}
                {flag}
            </div>
            <div class="opd-track">
                <div class="opd-bar" style="width:{pct:.1f}%"></div>
                <div class="opd-val" style="left:{val_left}; right:{val_right}">{d['end_inv']:,}</div>
            </div>
            <div class="opd-stats">
                <div class="opd-stat">
                    <div class="opd-stat-h">Avg bottles produced / hr</div>
                    <div class="opd-stat-v">{d['avg_prod']:.2f}</div>
                </div>
                <div class="opd-stat">
                    <div class="opd-stat-h">Avg WIP waiting / hr</div>
                    <div class="opd-stat-v">{d['avg_inv']:.2f}</div>
                </div>
            </div>
        </div>""")
    if fgi is not None:
        rows.append(bookend("fgi", "🏷️ Finished goods", "made but not yet sold", int(fgi)))
    axis = (f'<div class="opd-axis"><span>0</span>'
            f'<span>Bottles waiting in front of each station</span>'
            f'<span>{int(inv_scale):,}</span></div>')
    html = axis + "".join(rows)
    return "".join(line.strip() for line in html.splitlines())


def build_live_dashboard(frame, full):
    """One animation frame: a complete live dashboard (header metrics + progress,
    per-operation panel, and growing cumulative / WIP / ending-WIP charts)."""
    day = frame["day"]
    days = full["days"]
    pct = day / days * 100
    theo = full["theoretical_hr"]
    avg = frame["avg_out_hr"]
    eff = (avg / theo * 100) if theo else 0.0

    header = f"""
    <div class="anim-head">
        <div class="anim-top">
            <div class="anim-day">Day {day} <span>/ {days}</span></div>
            <div class="anim-metrics">
                <div class="anim-m"><span>Bottles finished</span><b>{frame['total_output']:,}</b></div>
                <div class="anim-m"><span>Bottles / hr</span><b>{avg:.2f}</b></div>
                <div class="anim-m"><span>Constraint / hr</span><b>{theo:.2f}</b></div>
                <div class="anim-m"><span>Efficiency</span><b>{eff:.1f}%</b></div>
            </div>
        </div>
        <div class="anim-prog"><div class="anim-prog-fill" style="width:{pct:.1f}%"></div></div>
    </div>"""

    show_raw = full.get("show_raw", True)
    show_fgi = full.get("show_fgi", full.get("demand_on", False))
    panel = render_op_panel(
        frame["op_detail"], full["bottleneck_label"], full["inv_scale"],
        raw_inv=(frame.get("raw_inv", 0) if show_raw else None),
        fgi=(frame.get("fgi", 0) if show_fgi else None),
    )

    cum = full["cum_series"][:day]
    wip = full["wip_series"][:day]
    cum_now = cum[-1] if cum else 0
    wip_now = wip[-1] if wip else 0

    inv_cards = ""
    if show_raw:
        raw = full.get("raw_series", [])[:day]
        raw_now = raw[-1] if raw else 0
        inv_cards += f"""
        <div class="live-card">
            <h4>Raw material (in front of Op 1)</h4>
            <div class="lc-val" style="color:#0ea5e9">{raw_now:,}</div>
            {sparkline(raw, color="#0ea5e9", fill="rgba(14,165,233,0.12)")}
        </div>"""
    if show_fgi:
        fgi_s = full.get("fgi_series", [])[:day]
        fgi_now = fgi_s[-1] if fgi_s else 0
        inv_cards += f"""
        <div class="live-card">
            <h4>Finished goods (after last Op)</h4>
            <div class="lc-val" style="color:#9333ea">{fgi_now:,}</div>
            {sparkline(fgi_s, color="#9333ea", fill="rgba(147,51,234,0.12)")}
        </div>"""

    charts = f"""
    <div class="live-charts">
        <div class="live-card">
            <h4>Cumulative bottles finished</h4>
            <div class="lc-val">{cum_now:,}</div>
            {sparkline(cum, color="#ea580c", fill="rgba(234,88,12,0.12)")}
        </div>
        <div class="live-card">
            <h4>Total WIP in the line</h4>
            <div class="lc-val">{wip_now:,}</div>
            {sparkline(wip, color="#f79009", fill="rgba(247,144,9,0.12)")}
        </div>
        <div class="live-card">
            <h4>Ending WIP by station</h4>
            {mini_bars(frame['op_detail'], full['bottleneck_label'], full['inv_scale'])}
        </div>
        {inv_cards}
    </div>"""

    body = header + panel + charts
    body = "".join(line.strip() for line in body.splitlines())
    return f'<div class="anim-card">{body}</div>'


# =========================================================
# Financials — interpolation, P&L computation, rendering, popup
# =========================================================
def _interp(x, xs, ys):
    """Linear interpolation of a per-die cost against the die-cost table,
    clamped at both ends (so any number of faces gets a sensible cost)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for k in range(1, len(xs)):
        if x <= xs[k]:
            x0, x1, y0, y1 = xs[k - 1], xs[k], ys[k - 1], ys[k]
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return ys[-1]


def get_fin():
    """Collect the current financial settings from session state."""
    return {
        "revenue_per_unit": float(st.session_state["fin_revenue_per_unit"]),
        "alloc_pct": float(st.session_state["fin_alloc_pct"]),
        "wip_holding": float(st.session_state["fin_wip_holding"]),
        "rmc": float(st.session_state["fin_rmc"]),
        "order_cost": float(st.session_state["fin_order_cost"]),
        "order_size": float(st.session_state["fin_order_size"]),
        "table": fin_table_from_state(),
    }


def run_hours():
    """Hours in the next run. Short horizons let students watch the dice-game swings that a
    full year averages out; HORIZON_YEAR is the original behaviour and the default."""
    h = HORIZONS.get(st.session_state.get("horizon", DEFAULT_HORIZON))
    if h is None:
        return int(st.session_state["simulation_years"]) * HOURS_PER_YEAR
    return int(h)


def run_years():
    """Run length in years (fractional for sub-year horizons) — the P&L allocates fixed cost
    annually, so a one-week run must not be charged a year of dice."""
    return run_hours() / HOURS_PER_YEAR


def is_full_year_run():
    return HORIZONS.get(st.session_state.get("horizon", DEFAULT_HORIZON)) is None


def steady_state_note(results):
    """Audit P3: say out loud when the averages on screen describe a line that never settled.

    Two distinct causes, both worth naming rather than hiding. A short run spends a real share
    of itself just filling the pipeline, so the average rate understates the steady rate. An
    uncapped bottleneck never settles at all: WIP climbs for the whole run, so units still in
    the line at the end are counted in average WIP but never in measured flow time."""
    if not results:
        return None
    hours = results.get("hours", 0)
    avg_wip = results.get("wip_L", 0.0)
    end_wip = results.get("ending_wip_downstream", 0.0)
    if hours and hours < HOURS_PER_DAY * 30:
        return ("⏱️ **Short run.** The line starts empty, so part of this run is spent just "
                "filling it — the average rate here is below the rate the same line settles at "
                "over a year. Run it again: the swing between runs is the point.")
    if avg_wip > 0 and end_wip > 1.5 * avg_wip:
        return ("📈 **The line never reached steady state.** WIP was still climbing when the run "
                "ended, so averages here describe a line in transition, not a settled one. "
                "Units still queued at the end count in average WIP but never finished, so "
                "measured flow time understates the true wait.")
    return None


def compute_financials(results, caps, sides, years, fin):
    """Turn a completed run into a profit-and-loss statement.

    Revenue      = bottles finished × price.
    Production   = Σ (units a station produced × its per-unit production cost).
    Fixed (dies) = Σ (fixed cost per die × dice) × allocation% × years.
    WIP holding  = average WIP (unit-days) × $/unit/day.
    Raw material = units fed into the line × $/unit.
    Ordering     = number of raw orders × $/order.
    """
    df = fin["table"].sort_values("Faces")
    xs = [float(v) for v in df["Faces"].tolist()]
    fx = [float(v) for v in df["Fixed cost per die ($)"].tolist()]
    px = [float(v) for v in df["Production cost per unit ($)"].tolist()]
    hours = results["hours"]

    prod_cost = 0.0
    fixed_alloc = 0.0
    for d in results["op_detail"]:
        op = d["op_num"]
        c, s = caps[op - 1], sides[op - 1]
        # Audit P2: charge processing on every unit the station WORKED, not just the good ones.
        # A bottle that is filled and then fails inspection consumed the same capacity and
        # materials as one that passed; costing only good units made scrap free, which is the
        # opposite of the lesson the Quality lab is teaching. `.get` keeps older saved runs
        # (which predate avg_worked) readable.
        worked_units = d.get("avg_worked", d["avg_prod"]) * hours
        prod_cost += worked_units * _interp(s, xs, px)
        fixed_alloc += _interp(s, xs, fx) * c
    fixed_alloc *= (fin["alloc_pct"] / 100.0) * years

    # WIP holding: inventory waiting in front of stations 2..n, in unit-days.
    wip_unit_hours = sum(d["avg_inv"] * hours for d in results["op_detail"][1:])
    wip_cost = (wip_unit_hours / HOURS_PER_DAY) * fin["wip_holding"]

    # Finished-goods holding: unsold output waiting to be bought (only with a market).
    avg_fgi = results.get("avg_fgi", 0.0)
    fgi_cost = (avg_fgi * hours / HOURS_PER_DAY) * fin["wip_holding"]

    # Audit P2: raw material is consumed by every unit Operation 1 STARTS, including the ones
    # it then scraps — not only the ones that make it through.
    _op1 = results["op_detail"][0]
    raw_units = _op1.get("avg_worked", _op1["avg_prod"]) * hours
    raw_cost = raw_units * fin["rmc"]

    # Audit P1: count the purchase orders the supplier actually shipped rather than inferring
    # them as ceil(consumption / order size). The reorder policy ships whenever the raw buffer
    # dips below the reorder point, so the real count runs a few percent above the idealised
    # one; inferring it quietly understated ordering cost. Falls back to the old estimate for
    # runs saved before the engine recorded it.
    osz = fin["order_size"]
    orders = results.get("orders_placed")
    if orders is None or results.get("order_size") != max(1, int(osz or 1)):
        # Either an older saved run with no recorded count, or a P&L being priced at a different
        # order size than the run used — in both cases the recorded count doesn't describe this
        # P&L, so fall back to the idealised estimate rather than reporting a mismatched number.
        orders = math.ceil(raw_units / osz) if osz and osz > 0 else (1 if raw_units > 0 else 0)
    order_cost = orders * fin["order_cost"]

    total_sold = results.get("total_sold", results["total_output"])
    lost_sales = results.get("lost_sales", 0)
    revenue = total_sold * fin["revenue_per_unit"]
    total_cost = prod_cost + fixed_alloc + wip_cost + fgi_cost + raw_cost + order_cost
    profit = revenue - total_cost
    units = total_sold
    return {
        "revenue": revenue, "prod_cost": prod_cost, "fixed_alloc": fixed_alloc,
        "wip_cost": wip_cost, "fgi_cost": fgi_cost, "raw_cost": raw_cost,
        "order_cost": order_cost, "orders": int(orders), "raw_units": raw_units,
        "total_cost": total_cost, "profit": profit,
        "units_sold": total_sold, "lost_sales": lost_sales,
        "lost_revenue": lost_sales * fin["revenue_per_unit"],
        "profit_per_unit": (profit / units) if units else 0.0,
        "margin": (profit / revenue * 100) if revenue else 0.0,
    }


def compute_breakeven(results):
    """Cost-Volume-Profit view of a run: contribution margin per unit, the break-even
    sales volume, and the margin of safety. Fixed cost is the line's allocated capital;
    variable cost is materials + production (both scale with every unit)."""
    cfg = results.get("config", {})
    dice = cfg.get("dice", []) or []
    sides = cfg.get("faces", []) or []
    years = cfg.get("years", 1)
    fin = get_fin()
    f = compute_financials(results, dice, sides, years, fin)
    price = fin["revenue_per_unit"]
    made = max(1.0, f["raw_units"])
    unit_var = (f["raw_cost"] + f["prod_cost"]) / made
    cm = price - unit_var
    fixed = f["fixed_alloc"]
    be_units = (fixed / cm) if cm > 0 else float("inf")
    sold = f["units_sold"]
    mos = ((sold - be_units) / sold * 100.0) if sold else 0.0
    return {"price": price, "unit_var": unit_var, "cm": cm, "fixed": fixed,
            "be_units": be_units, "sold": sold, "mos": mos, "profit": f["profit"]}


def compute_throughput_accounting(results):
    """Reframe a run's P&L in Goldratt's Throughput-Accounting terms:
        T  (Throughput)        = Revenue − truly-variable (raw-material) cost
        OE (Operating Expense) = everything else spent to run the system
                                 (conversion, allocated fixed, holding, ordering)
        I  (Investment)        = capital in the machines (dice) + cash tied up in inventory
        Net Profit = T − OE   (identical to the P&L profit — same money, different lens)
        ROI        = (T − OE) / I
    All figures come straight from the existing P&L and the run's inventory levels;
    no engine change is involved."""
    cfg = results.get("config", {})
    dice = cfg.get("dice", []) or []
    sides = cfg.get("faces", []) or []
    years = cfg.get("years", 1)
    fin = get_fin()
    f = compute_financials(results, dice, sides, years, fin)
    T = f["revenue"] - f["raw_cost"]
    OE = f["prod_cost"] + f["fixed_alloc"] + f["wip_cost"] + f["fgi_cost"] + f["order_cost"]
    avg_raw = statistics.mean(results["raw_series"]) if results.get("raw_series") else 0.0
    inv_val = (avg_raw + results.get("wip_L", 0.0) + results.get("avg_fgi", 0.0)) * fin["rmc"]
    df = fin["table"].sort_values("Faces")
    xs = [float(v) for v in df["Faces"].tolist()]
    fx = [float(v) for v in df["Fixed cost per die ($)"].tolist()]
    equip = sum(_interp(sides[i], xs, fx) * dice[i] for i in range(len(dice)))
    I = equip + inv_val
    NP = T - OE
    ROI = (NP / I * 100.0) if I > 0 else 0.0
    return {"T": T, "OE": OE, "I": I, "equip": equip, "inv": inv_val, "NP": NP,
            "ROI": ROI, "thru": results.get("total_output", 0), "profit": f["profit"]}


def render_pnl(f):
    """Build the styled profit-and-loss panel (HTML)."""
    def row(lbl, amt, sign, sub="", strong=False):
        cls = "pnl-row strong" if strong else "pnl-row"
        if sign == "-":
            disp, amt_cls = f"−${abs(amt):,.2f}", "pnl-amt neg"
        elif sign == "+":
            disp, amt_cls = f"${amt:,.2f}", "pnl-amt pos"
        else:
            disp = f"${amt:,.2f}" if amt >= 0 else f"−${abs(amt):,.2f}"
            amt_cls = "pnl-amt " + ("profit-pos" if amt >= 0 else "profit-neg")
        sub_html = f'<span class="pnl-sub">{sub}</span>' if sub else ""
        return (f'<div class="{cls}"><div class="pnl-lbl">{lbl}{sub_html}</div>'
                f'<div class="{amt_cls}">{disp}</div></div>')

    rev_sub = "bottles finished × price"
    if f.get("lost_sales"):
        rev_sub = f"{f['units_sold']:,.0f} sold × price · {f['lost_sales']:,.0f} demand lost"
    rows = [
        row("Revenue", f["revenue"], "+", rev_sub),
        row("Production cost", f["prod_cost"], "-", "per-unit cost at every station"),
        row("Fixed-cost allocation", f["fixed_alloc"], "-", "die cost × dice × allocation × years"),
        row("WIP holding cost", f["wip_cost"], "-", "average WIP × $/unit/day"),
    ]
    if f.get("fgi_cost", 0) > 0:
        rows.append(row("Finished-goods holding", f["fgi_cost"], "-", "unsold output × $/unit/day"))
    rows += [
        row("Raw material cost", f["raw_cost"], "-", f"{f['raw_units']:,.0f} bottles fed in"),
        row("Ordering cost", f["order_cost"], "-", f"{f['orders']:,} orders"),
        row("Net profit", f["profit"], "=", strong=True),
    ]
    html = "".join(rows)
    return "".join(line.strip() for line in html.splitlines())


# Standard die sizes used for the "profit vs. die size" sweet-spot scan.
SCAN_DICE = [2, 4, 6, 8, 10, 12]


@st.cache_data(ttl=3600, max_entries=512, show_spinner=False)
def compute_die_scan(caps, base_sides, start_inv, hours, supply_reliability, wip_limits=None,
                     demand_dice=0, demand_faces=0, order_size=1):
    """For each standard die size, re-run the line with *every active station*
    set to that die (keeping each station's number of dice), and keep just the
    operational aggregates the P&L needs. Run once per simulation; the profit
    itself is recomputed cheaply from these aggregates whenever financials change."""
    active = [(c > 0 and s > 0) for c, s in zip(caps, base_sides)]
    if not any(active):
        return []
    out = []
    for d in SCAN_DICE:
        sides_d = [d if a else 0 for a in active]
        res = run_simulation(caps, sides_d, start_inv, hours, supply_reliability, wip_limits,
                             track_flow=False, demand_dice=demand_dice, demand_faces=demand_faces,
                             order_size=order_size, rng=random.Random(20260629))
        if res is None:
            continue
        out.append({
            "faces": d,
            "sides": sides_d,
            "res": {"op_detail": res["op_detail"],
                    "total_output": res["total_output"],
                    "total_sold": res["total_sold"],
                    "avg_fgi": res["avg_fgi"],
                    "lost_sales": res["lost_sales"],
                    "hours": res["hours"]},
        })
    return out


def build_profit_curve_html(curve, current_faces=None):
    """Bar chart (plain HTML/CSS) of annual profit vs. uniform die size — green
    for profit, red for loss, a ★ on the peak, an outline on the line's current
    die size. Built with divs (not SVG) so it renders reliably through st.html."""
    n = len(curve)
    if n == 0:
        return ""
    PH = 168                       # plot height in px (profit area + loss area)
    LABEL = 16                     # px reserved above/below each bar for its value
    profits = [c["profit"] for c in curve]
    pmax, pmin = max(profits + [0]), min(profits + [0])
    rng = (pmax - pmin) or 1
    top_h = round(PH * (pmax / rng))
    bot_h = PH - top_h
    top_space = max(top_h - LABEL, 2)
    bot_space = max(bot_h - LABEL, 2)
    peak = max(curve, key=lambda c: c["profit"])

    def money(v):
        if abs(v) >= 10000:
            return ("−" if v < 0 else "") + f"${abs(v) / 1000:.1f}k"
        return ("−" if v < 0 else "") + f"${abs(v):,.0f}"

    cols = []
    for c in curve:
        v = c["profit"]
        is_peak = (c is peak and v > 0)
        cur = " cur" if (current_faces is not None and c["faces"] == current_faces) else ""
        if v >= 0:
            bh = max(2, round(v / pmax * top_space)) if pmax > 0 else 2
            pos_inner = (f'<span class="pc-v {"peak" if is_peak else "pos"}">'
                         f'{"★ " if is_peak else ""}{money(v)}</span>'
                         f'<div class="pc-bar pos{cur}" style="height:{bh}px"></div>')
            neg_inner = ""
        else:
            bh = max(2, round(abs(v) / abs(pmin) * bot_space)) if pmin < 0 else 2
            pos_inner = ""
            neg_inner = (f'<div class="pc-bar neg{cur}" style="height:{bh}px"></div>'
                         f'<span class="pc-v neg">{money(v)}</span>')
        cols.append(
            f'<div class="pc-col">'
            f'<div class="pc-pos" style="height:{top_h}px">{pos_inner}</div>'
            f'<div class="pc-neg" style="height:{max(bot_h, 0)}px">{neg_inner}</div>'
            f'<div class="pc-x">{c["faces"]}-sided</div>'
            f'</div>'
        )
    html = f'<div class="pc-wrap">{"".join(cols)}</div>'
    return "".join(line.strip() for line in html.splitlines())


# =========================================================
# EOQ (Economic Order Quantity) — cost model, scan, and curve
# =========================================================
@st.cache_data(ttl=3600, max_entries=512, show_spinner=False)
def eoq_annual_demand(caps, sides, hours):
    """Annual raw-material consumption with a reliable, well-stocked supplier — the
    'D' in the EOQ formula. Order size doesn't change throughput, so this is stable."""
    r = run_simulation(caps, sides, 0, hours, 1.0, order_size=4000,
                       rng=random.Random(20260629))
    return r["total_output"]


def eoq_unit_margin(results, caps, sides, years, fin):
    """Contribution margin per bottle (price − production − raw material), used to price
    the stockouts that supply variability causes."""
    f = compute_financials(results, caps, sides, years, fin)
    sold = max(1.0, f["units_sold"])
    return max(0.0, (f["revenue"] - f["prod_cost"] - f["raw_cost"]) / sold)


@st.cache_data(ttl=3600, max_entries=4096, show_spinner=False)
def _eoq_sim_point(caps, sides, hours, reliability, Q):
    """One order-size point on the EOQ curve — the *simulation* aggregates only.

    Keyed on the line configuration alone. The money layered on top (ordering, holding,
    stockout) is arithmetic that depends on order cost, holding rate and unit margin, none
    of which change how the line behaves. Keeping those out of the cache key is what lets a
    whole class share one computation per order size: the scan used to be keyed on the
    run-derived `margin`, so it re-ran ~11 simulations on every run for every student."""
    r = run_simulation(caps, sides, 0, hours, reliability, order_size=Q,
                       rng=random.Random(20260629))
    avg_raw = sum(r["raw_series"]) / len(r["raw_series"]) if r["raw_series"] else 0.0
    return {"avg_raw": avg_raw, "thru": r["total_output"], "starved": r["starved_hours"],
            "orders": r["orders_placed"]}


def compute_eoq_scan(caps, sides, hours, reliability, order_cost, margin, qs=None,
                     include_q=None, hold_per_day=None):
    """Run the line at a spread of order sizes and split the inventory-related cost into
    ordering, raw-holding, and (under unreliable supply) stockout cost. Same seed for every
    order size so the curve is a clean function of Q. `include_q` forces the line's current
    order size onto the curve so the marked point matches the actual setting exactly.
    `hold_per_day` (the raw-material holding cost, $/bottle/day) lets the caller vary H."""
    if hold_per_day is None:
        hold_per_day = RAW_HOLD_PER_DAY
    D = eoq_annual_demand(caps, sides, hours)
    H = hold_per_day * DAYS_PER_YEAR * (hours / HOURS_PER_YEAR)      # holding $/bottle over the run
    Dr = D * (hours / HOURS_PER_YEAR)                                # raw consumed over the run
    eoq = math.sqrt(2 * Dr * order_cost / H) if H > 0 else 0.0
    if qs is None:
        base = [10, 25, 50, 100, 150, 250, 400, 600, 1000, 1500]
        qs = base + [int(round(eoq))]
    if include_q is not None:
        qs = qs + [max(1, int(include_q))]
    qs = sorted(set(q for q in qs if q >= 1))
    rows = []
    for Q in qs:
        sim = _eoq_sim_point(caps, sides, hours, reliability, Q)
        avg_raw, thru = sim["avg_raw"], sim["thru"]
        # Audit P1: the orders the supplier actually shipped, not ceil(demand / Q). Measured a
        # few percent above the idealised count and still ~1/Q in shape, so the textbook EOQ
        # stays a good predictor of the curve's minimum — which is the point of the lab.
        orders = sim.get("orders", math.ceil(Dr / Q) if Q > 0 else 0)
        ordering = orders * order_cost
        holding = avg_raw * H
        lost = max(0, int(round(Dr)) - thru)
        stockout = lost * margin
        rows.append({"Q": Q, "orders": orders, "ordering": ordering, "holding": holding,
                     "stockout": stockout, "total": ordering + holding + stockout,
                     "avg_raw": avg_raw, "thru": thru, "starved": sim["starved"]})
    best = min(rows, key=lambda x: x["total"])
    return {"D": Dr, "H": H, "S": order_cost, "margin": margin, "eoq": eoq,
            "reliability": reliability, "rows": rows, "best_q": best["Q"], "best_total": best["total"]}


def eoq_row_for(scan, Q):
    """The scan row closest to order size Q (the line's current point on the curve)."""
    if not scan or not scan.get("rows"):
        return None
    return min(scan["rows"], key=lambda r: abs(r["Q"] - Q))


def build_eoq_curve_html(scan, current_q=None):
    """Stacked bars of total inventory cost (ordering + holding + stockout) vs order size,
    with the cheapest order size starred and the line's current order size outlined.
    Plain HTML so it renders reliably through st.html. Very small order sizes have a huge
    ordering cost that would flatten the rest of the curve, so the y-axis is capped at the
    cost of the largest order size and taller bars are clipped and flagged with ▲."""
    rows = scan.get("rows", [])
    if not rows:
        return ""
    PH = 170
    has_stockout = any(r["stockout"] > 0.5 for r in rows)
    best_q = scan["best_q"]
    # Scale to the right shoulder of the U (costs at the cheapest order size and above),
    # which rises gently — so the readable region is preserved and only the steep
    # ordering-cost wall at tiny order sizes gets clipped.
    shoulder = [r["total"] for r in rows if r["Q"] >= best_q]
    cap = max(shoulder) if shoulder else max(r["total"] for r in rows)
    cap = max(cap, 1)

    def money(v):
        if abs(v) >= 10000:
            return f"${v / 1000:.1f}k"
        return f"${v:,.0f}"

    cols = []
    for r in rows:
        oh = max(1, round(r["ordering"] / cap * PH))
        hh = max(1, round(r["holding"] / cap * PH))
        sh = max(0, round(r["stockout"] / cap * PH)) if has_stockout else 0
        is_best = (r["Q"] == best_q)
        clipped = r["total"] > cap * 1.02
        cur = (current_q is not None and r["Q"] == current_q)
        outline = "outline:2px solid #1f2a44;outline-offset:1px;" if cur else ""
        star = "★ " if is_best else ("▲ " if clipped else "")
        # A clipped bar fills the full height; the faded top edge signals it runs off-chart.
        topfade = ('<div style="height:5px;background:repeating-linear-gradient('
                   '45deg,#fed7aa,#fed7aa 3px,#fff 3px,#fff 6px)"></div>') if clipped else ""
        seg = (
            f'<div style="display:flex;flex-direction:column-reverse;height:{PH}px;width:100%;{outline}border-radius:4px;overflow:hidden">'
            f'<div style="height:{oh}px;background:#ea580c" title="Ordering {money(r["ordering"])}"></div>'
            f'<div style="height:{hh}px;background:#0ea5e9" title="Holding {money(r["holding"])}"></div>'
            + (f'<div style="height:{sh}px;background:#dc2626" title="Stockout {money(r["stockout"])}"></div>' if has_stockout else "")
            + topfade
            + '</div>')
        lbl_color = "#16a34a" if is_best else ("#c2410c" if clipped else "#475569")
        cols.append(
            f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0">'
            f'<div style="font-size:0.6rem;font-weight:800;color:{lbl_color};white-space:nowrap">{star}{money(r["total"])}</div>'
            f'{seg}'
            f'<div style="font-size:0.62rem;color:{"#1f2a44" if cur else "#64748b"};font-weight:{"800" if cur else "600"}">{r["Q"]:,}</div>'
            f'</div>')
    legend = (
        '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:6px;font-size:0.7rem;font-weight:600;color:#475569">'
        '<span><i style="display:inline-block;width:10px;height:10px;background:#ea580c;border-radius:2px;margin-right:4px"></i>Ordering cost</span>'
        '<span><i style="display:inline-block;width:10px;height:10px;background:#0ea5e9;border-radius:2px;margin-right:4px"></i>Holding cost</span>'
        + ('<span><i style="display:inline-block;width:10px;height:10px;background:#dc2626;border-radius:2px;margin-right:4px"></i>Stockout cost</span>' if has_stockout else "")
        + ('<span style="color:#c2410c">▲ taller than the chart (cost shown)</span>' if any(r["total"] > cap * 1.02 for r in rows) else "")
        + '</div>')
    body = (f'{legend}<div style="display:flex;gap:6px;align-items:flex-end;'
            f'border-bottom:1px solid #e2e8f0;padding-bottom:4px">{"".join(cols)}</div>'
            f'<div style="text-align:center;font-size:0.62rem;color:#94a3b8;margin-top:4px">order size (bottles per purchase order)</div>')
    return "".join(line.strip() for line in body.splitlines())


def flow_histogram_series(flow_counts, nbins=16):
    """Bin the flow-time counter into a Series (index = bin-start hours) for a chart."""
    if not flow_counts:
        return None
    lo, hi = min(flow_counts), max(flow_counts)
    if hi == lo:
        hi = lo + 1
    width = max(1, math.ceil((hi - lo + 1) / nbins))
    bins = {}
    for ft, c in flow_counts.items():
        b = lo + ((ft - lo) // width) * width
        bins[b] = bins.get(b, 0) + c
    s = pd.Series(bins).sort_index()
    s.index.name = "Flow time (hours)"
    s.name = "Units finished"
    return s


def _five_number(vals):
    """min, Q1, median, Q3, max for a list of numbers."""
    xs = sorted(vals)
    n = len(xs)
    if n == 0:
        return (0, 0, 0, 0, 0)
    if n == 1:
        return (xs[0],) * 5

    def q(p):
        idx = p * (n - 1)
        lo = int(math.floor(idx))
        frac = idx - lo
        if lo + 1 < n:
            return xs[lo] + frac * (xs[lo + 1] - xs[lo])
        return xs[lo]
    return (xs[0], q(0.25), q(0.50), q(0.75), xs[-1])


def build_boxplots_html(rows):
    """Horizontal box-and-whisker plots in pure HTML/CSS (no SVG). `rows` = list of
    dicts: {label, values (list), fmt (callable v->str), color}."""
    out = []
    for r in rows:
        vals = r.get("values") or []
        if not vals:
            continue
        lo, q1, med, q3, hi = _five_number(vals)
        span = (hi - lo) or 1
        fmt = r.get("fmt", lambda v: f"{v:,.1f}")
        color = r.get("color", "#ea580c")

        def pos(v):                      # inset 4%..96% so edge labels stay inside
            return 4 + (v - lo) / span * 92

        p_lo, p_q1, p_med, p_q3, p_hi = pos(lo), pos(q1), pos(med), pos(q3), pos(hi)
        out.append(
            f'<div class="bx-row">'
            f'<div class="bx-label">{r["label"]}</div>'
            f'<div class="bx-track">'
            f'<div class="bx-whisker" style="left:{p_lo:.1f}%;width:{max(p_hi-p_lo,0):.1f}%"></div>'
            f'<div class="bx-cap" style="left:{p_lo:.1f}%"></div>'
            f'<div class="bx-cap" style="left:{p_hi:.1f}%"></div>'
            f'<div class="bx-box" style="left:{p_q1:.1f}%;width:{max(p_q3-p_q1,0.5):.1f}%;'
            f'border-color:{color};background:{color}22"></div>'
            f'<div class="bx-med" style="left:{p_med:.1f}%;background:{color}"></div>'
            f'<div class="bx-vmin" style="left:{p_lo:.1f}%">{fmt(lo)}</div>'
            f'<div class="bx-vmed" style="left:{p_med:.1f}%;color:{color}">{fmt(med)}</div>'
            f'<div class="bx-vmax" style="left:{p_hi:.1f}%">{fmt(hi)}</div>'
            f'</div></div>'
        )
    return "".join(out)


def numeric_histogram_series(values, nbins=18, name="Count"):
    """Histogram Series for a list of numbers (handles negatives), index = bin start."""
    if not values:
        return None
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    width = (hi - lo) / nbins
    bins = {}
    for v in values:
        k = int((v - lo) / width)
        if k >= nbins:
            k = nbins - 1
        b = round(lo + k * width)
        bins[b] = bins.get(b, 0) + 1
    s = pd.Series(bins).sort_index()
    s.name = name
    return s


def run_replications(caps, sides, start_inv, hours, supply, wip_limits, years, fin,
                     n_reps, progress=None, demand_dice=0, demand_faces=0, order_size=1, rng=None):
    """Run the line n_reps times (fresh randomness each time) and collect the annual
    metrics whose spread is the lesson: throughput, WIP, flow time, efficiency, profit."""
    out = {"throughput": [], "wip": [], "flow": [], "efficiency": [], "profit": []}
    for k in range(n_reps):
        r = run_simulation(caps, sides, start_inv, hours, supply, wip_limits, track_flow=False,
                           demand_dice=demand_dice, demand_faces=demand_faces, order_size=order_size,
                           rng=rng)
        if r is not None:
            f = compute_financials(r, caps, sides, years, fin)
            out["throughput"].append(r["total_output"])
            out["wip"].append(r["wip_L"])
            out["flow"].append(r["flow_time_derived"] / HOURS_PER_DAY)
            out["efficiency"].append(r["efficiency"] * 100)
            out["profit"].append(f["profit"])
        if progress:
            progress(k + 1, n_reps)
    return out


def _stat_block(vals):
    if not vals:
        return {}
    return {
        "mean": statistics.mean(vals),
        "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
        "min": min(vals), "max": max(vals),
        "median": statistics.median(vals),
    }


def config_summary(cfg):
    """One-line description of the line that produced a run."""
    dice, faces = cfg["dice"], cfg["faces"]
    act = [(dice[i], faces[i]) for i in range(N_OPS) if dice[i] > 0 and faces[i] > 0]
    if not act:
        return "—"
    if len(set(act)) == 1:
        d, f = act[0]
        line = f"{len(act)} stations · {d} die × {f} faces"
    else:
        line = " · ".join(f"{d}×{f}f" for d, f in act)
    sup = "unreliable supply" if not cfg.get("supply_unlimited", True) else "reliable supply"
    dem = "variable demand" if cfg.get("demand_variable") else "sell-all"
    wip = "WIP capped" if cfg.get("wip_on") else "WIP uncapped"
    return f"{line} · {sup} · {dem} · {wip} · {cfg.get('years', 1)} yr"


def snapshot_scenario(results, caps, sides, years, fin):
    """Freeze a run's headline metrics + profit (at the current financials) for A/B."""
    f = compute_financials(results, caps, sides, years, fin)
    return {
        "throughput": results["total_output"],
        "thr_hr": results["avg_out_hr"],
        "wip_L": results["wip_L"],
        "flow_days": results["flow_time_measured"] / HOURS_PER_DAY,
        "efficiency": results["efficiency"] * 100,
        "profit": f["profit"],
        "margin": f["margin"],
        "config": results.get("config", {}),
    }


def build_comparison_html(A, B):
    """Side-by-side A vs B table with a colored Δ (green = better)."""
    metrics = [
        ("Net profit", "profit", lambda v: f"${v:,.0f}", True),
        ("Margin", "margin", lambda v: f"{v:.1f}%", True),
        ("Throughput / yr", "throughput", lambda v: f"{v:,.0f}", True),
        ("Throughput / hr", "thr_hr", lambda v: f"{v:.2f}", True),
        ("Avg WIP (L)", "wip_L", lambda v: f"{v:,.1f}", False),
        ("Flow time", "flow_days", lambda v: f"{v:.2f} d", False),
        ("Efficiency", "efficiency", lambda v: f"{v:.1f}%", True),
    ]
    head = (f'<tr><th>Metric</th><th>A</th><th>B</th><th>Δ (B−A)</th></tr>')
    rows = []
    for label, key, fmt, higher_better in metrics:
        a = A.get(key) if A else None
        b = B.get(key) if B else None
        a_txt = fmt(a) if a is not None else "—"
        b_txt = fmt(b) if b is not None else "—"
        if a is not None and b is not None:
            d = b - a
            better = (d > 0) if higher_better else (d < 0)
            cls = "cmp-up" if (d != 0 and better) else ("cmp-dn" if d != 0 else "cmp-flat")
            sign = "+" if d > 0 else ("−" if d < 0 else "")
            d_txt = f'{sign}{fmt(abs(d))}'
        else:
            cls, d_txt = "cmp-flat", "—"
        rows.append(f'<tr><td class="cmp-m">{label}</td><td>{a_txt}</td><td>{b_txt}</td>'
                    f'<td class="{cls}">{d_txt}</td></tr>')
    cfgA = f'<div class="cmp-cfg"><b>A:</b> {config_summary(A["config"])}</div>' if A else ''
    cfgB = f'<div class="cmp-cfg"><b>B:</b> {config_summary(B["config"])}</div>' if B else ''
    return (f'<table class="cmp">{head}{"".join(rows)}</table>{cfgA}{cfgB}')


def pin_scenario(slot):
    """Freeze the current run into A/B slot (callback so it runs before re-render)."""
    res = st.session_state.get("sim_results")
    if not res or "config" not in res:
        return
    cfg = res["config"]
    st.session_state[f"scenario_{slot}"] = snapshot_scenario(
        res, cfg["dice"], cfg["faces"], cfg.get("years", 1), get_fin())


def clear_scenarios():
    st.session_state.pop("scenario_A", None)
    st.session_state.pop("scenario_B", None)


# =========================================================
# Guided Lab — the Five Focusing Steps as a directed exercise
# =========================================================
def _line(dice6, faces6):
    """Build full-length dice/faces session config from the 6 active stations."""
    cfg = {}
    for i in range(N_OPS):
        cfg[f"capacity_{i}"] = dice6[i] if i < len(dice6) else 0
        cfg[f"sides_{i}"] = faces6[i] if i < len(faces6) else 0
    return cfg


_S2_FACES = [6, 6, 6, 3, 6, 6]   # Operation 4 is the constraint (1 die × 3 faces ≈ 2.0/hr)


def _rev_baseline(r):
    thr = r["total_output"]
    ceil = int(round(3.5 * HOURS_PER_YEAR))
    return ("warn", f"The balanced line finished **{thr:,} bottles** — about **{ceil - thr:,} short** of "
            f"its own {ceil:,} average. With zero imbalance, that gap is pure **variability + "
            f"dependency**: a station can never bank ahead of its roll, but it *can* fall behind, and "
            f"the next station can't recover ground it never received. WIP quietly drifts to "
            f"**{r['wip_L']:.0f}** units. This is the cost the rest of the lab attacks.")


def _rev_length(r):
    return ("warn", f"The nine-station line held only **{r['efficiency'] * 100:.1f}%** of its average and "
            f"drifted to **{r['wip_L']:.0f}** units of WIP — worse than the six-station line "
            f"(≈95%, ≈126 bottles). Every extra station is one more **dependency** link: a low roll "
            f"anywhere starves everyone downstream, and the shortfalls accumulate. Longer lines need "
            f"*more* protection, not less — which is exactly what the focusing steps build.")


def _rev_identify(r):
    op4 = r["op_detail"][3]["end_inv"] if len(r["op_detail"]) > 3 else 0
    return ("good", f"Throughput collapsed to **{r['total_output']:,}/yr** (≈{r['avg_out_hr']:.1f}/hr) — "
            f"the whole line now runs at Operation 4's pace. Inventory stacked **{op4:,.0f} bottles** in "
            f"front of Operation 4 while the stations after it sat starved. That mountain of WIP *is* "
            f"the constraint, and it points to itself: **{r['bottleneck_label']}**.")


def _rev_nonconstraint(r):
    ceil = int(round(2.0 * HOURS_PER_YEAR))
    return ("warn", f"You doubled Operation 2 — a **non-constraint** — and throughput is **"
            f"{r['total_output']:,}/yr**, still pinned at Operation 4's ≈2.0/hr ceiling (≈{ceil:,}), "
            f"exactly like the step before. The extra capacity didn't add a single finished unit; it "
            f"just pumped **more** WIP downstream (now {r['wip_L']:.0f} units). An hour saved at a "
            f"non-constraint is a mirage. **Only the constraint sets throughput.**")


def _rev_subordinate(r):
    return ("good", f"Capping WIP at 6 per station releases work only at the constraint's drumbeat. "
            f"Throughput **held at {r['total_output']:,}/yr** (the constraint is untouched), but WIP "
            f"fell from a ≈1,500-unit mountain to **{r['wip_L']:.0f}** and flow time from ≈56 days to "
            f"**{r['flow_time_measured'] / HOURS_PER_DAY:.1f} days**. Same output, a fraction of the "
            f"inventory and lead time. *That* is subordination — stop overproducing ahead of the "
            f"constraint.")


def _rev_buffer(r):
    return ("warn", f"A cap of **2** is too short a rope: it strangled the constraint, dropping "
            f"throughput to **{r['total_output']:,}/yr** — well below the ≈4,100 the line can sustain. "
            f"A pull system still needs a **buffer** in front of the constraint so a momentary upstream "
            f"dip never idles it. That is the *Buffer* in **Drum-Buffer-Rope**: small enough to keep "
            f"WIP low, large enough to keep the drum beating. A cap of 6 was the sweet spot; 2 is "
            f"starvation.")


def _rev_elevate(r):
    return ("good", f"A second die at Operation 4 lifts it to ≈4.0/hr, above the others' 3.5. "
            f"Throughput jumped to **{r['total_output']:,}/yr** (≈+70%). But notice the constraint "
            f"**moved to {r['bottleneck_label']}** — once you elevate, the bottleneck relocates, so you "
            f"loop back to *Identify*. And any policy you tuned for the old constraint (those WIP caps) "
            f"may now be wrong: that inertia is the fifth trap.")


def _rev_repeat(r):
    return ("warn", f"You added a die to Operation 1 — but the constraint already moved off Operation 4 "
            f"to the cluster still averaging 3.5/hr. Throughput barely moved from the ≈7,060 of the "
            f"previous step (now **{r['total_output']:,}/yr**), and the bottleneck is now "
            f"**{r['bottleneck_label']}**. After every *Elevate* you must loop back to *Identify* — and "
            f"resist running yesterday's playbook on today's constraint. That inertia is the fifth "
            f"trap, and it closes the loop.")


_OFF3 = [0, 0, 0]
_S2_FULL = _S2_FACES + _OFF3
_F6_FULL = [6] * 6 + _OFF3


def _caps0(r):
    c = r["config"].get("wip_caps")
    return c[0] if c else None


LAB_OPS = [
    {
        "icon": "🎬", "phase": "Set the stage",
        "title": "A balanced line that still falls short",
        "intro": "Every one of the six stations rolls **1 die × 6 faces**, averaging **3.5 bottles/hr**, "
                 "so the line's average capacity is **7,336 bottles/year**. The line is perfectly "
                 "balanced — no station is slower than another. Surely it hits its average?",
        "setup": "6 stations · 1 die × 6 faces · unlimited supply · no WIP cap · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100, "demand_variable": False,
                  "starting_inventory": 0, "simulation_years": 1},
        "estimate": {
            "prompt": "How many bottles will this balanced line actually finish in a year?",
            "unit": "", "actual": lambda r: r["total_output"], "tol": 0.06,
            "min": 0.0, "max": 9000.0, "step": 100.0,
            "hint": "Each of the six stations averages 3.5 bottles/hr and the line runs 2,096 hours a "
                    "year — but the stations aren't independent.",
        },
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and not r["config"]["wip_on"],
        "reveal": _rev_baseline,
    },
    {
        "icon": "🔗", "phase": "Why it happens — dependency",
        "title": "A longer line drags even more",
        "intro": "Switch on **all nine stations**, still perfectly balanced at 1 die × 6 faces. More "
                 "hands on deck. Does a longer balanced line keep a *higher* or *lower* share of its "
                 "average than the six-station line did?",
        "setup": "9 stations · 1 die × 6 faces · unlimited · no WIP cap · 1 year",
        "apply": {**_line([1] * 9, [6] * 9), "wip_limit_on": False, "supply_reliability": 100, "demand_variable": False,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "A longer balanced line — what share of its average does it keep?",
        "opts": ["More than the 6-station line — more capacity", "About the same",
                 "Less — longer lines drag more", "It can't finish anything"],
        "answer": 2,
        "check": lambda r: r["config"]["dice"] == [1] * 9 and r["config"]["faces"] == [6] * 9
        and not r["config"]["wip_on"],
        "reveal": _rev_length,
    },
    {
        "icon": "🔎", "phase": "Step 1 — Identify",
        "title": "Find the constraint",
        "intro": "Now Operation 4 is slowed to **1 die × 3 faces** (≈ 2.0/hr); the other five still "
                 "average 3.5/hr. One station is clearly the slowest. Before you run it — where will "
                 "the work pile up, and what happens to total output?",
        "setup": "Operation 4 = 1 die × 3 faces (the others stay 1 × 6) · unlimited · no cap · 1 year",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": False, "supply_reliability": 100, "demand_variable": False,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "Where will inventory pile up?",
        "opts": ["Operation 2", "In front of Operation 4 (the slow one)",
                 "Operation 6 (the end)", "Evenly across the line"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _S2_FULL and not r["config"]["wip_on"],
        "reveal": _rev_identify,
    },
    {
        "icon": "🎯", "phase": "Step 2 — Exploit",
        "title": "Improve the wrong station",
        "intro": "Tempting fix: give a busy upstream station more muscle. We **double Operation 2** to "
                 "2 dice (≈ 7.0/hr) while Operation 4 stays the 2.0/hr constraint. Operation 2 is now a "
                 "powerhouse. What does that do to the line's annual output?",
        "setup": "Operation 2 = 2 dice × 6 faces · Operation 4 still 1 × 3 · unlimited · no cap · 1 year",
        "apply": {**_line([1, 2, 1, 1, 1, 1], _S2_FACES), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "You doubled Operation 2's capacity. What happens to annual throughput?",
        "opts": ["Rises a lot", "Rises a little", "Essentially unchanged", "Falls"],
        "answer": 2,
        "check": lambda r: r["config"]["dice"] == [1, 2, 1, 1, 1, 1] + _OFF3
        and r["config"]["faces"] == _S2_FULL,
        "reveal": _rev_nonconstraint,
        "reflect": "You doubled Operation 2's speed and total output barely moved. In one sentence, "
                   "why didn't the faster station help?",
    },
    {
        "icon": "🪢", "phase": "Step 3 — Subordinate",
        "title": "Pace the line to the drum",
        "intro": "Back to the plain constraint line (Operation 4 slow, everything else 1 × 6). This "
                 "time we **cap WIP at 6 per station** — releasing new work only as fast as the "
                 "constraint uses it up (a pull system). Predict what happens to throughput, and to WIP "
                 "and flow time.",
        "setup": "Operation 4 = 1 × 3 · every station WIP-capped at 6 · unlimited · 1 year",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": True,
                  **{f"wip_cap_{i}": 6 for i in range(N_OPS)},
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Capping WIP and releasing at the drumbeat — what happens?",
        "opts": ["Throughput holds; WIP and flow time collapse",
                 "Throughput and WIP both collapse",
                 "Throughput rises; WIP rises", "Nothing really changes"],
        "answer": 0,
        "check": lambda r: r["config"]["faces"] == _S2_FULL and r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["wip_on"] and _caps0(r) == 6,
        "reveal": _rev_subordinate,
    },
    {
        "icon": "📏", "phase": "Step 3 — Subordinate, sized right",
        "title": "Size the buffer (the rope)",
        "intro": "Same constraint line, but now we **cap WIP at just 2 per station** — a very short "
                 "rope. Tighter must be better for inventory, surely. But predict what it does to "
                 "**throughput**.",
        "setup": "Operation 4 = 1 × 3 · every station WIP-capped at 2 · unlimited · 1 year",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": True,
                  **{f"wip_cap_{i}": 2 for i in range(N_OPS)},
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Cap WIP at only 2 per station. What happens to throughput?",
        "opts": ["Unchanged — the constraint still sets it", "It falls — the constraint gets starved",
                 "It rises", "WIP rises"],
        "answer": 1,
        "check": lambda r: r["config"]["faces"] == _S2_FULL and r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["wip_on"] and _caps0(r) == 2,
        "reveal": _rev_buffer,
    },
    {
        "icon": "🚀", "phase": "Step 4 — Elevate",
        "title": "Add capacity at the constraint",
        "intro": "Now we **elevate**: give Operation 4 a **second die** (now ≈ 4.0/hr, above the others' "
                 "3.5) and drop the WIP caps to see the raw effect. What's the new throughput — and "
                 "where is the constraint now?",
        "setup": "Operation 4 = 2 dice × 3 faces · others 1 × 6 · no cap · unlimited · 1 year",
        "apply": {**_line([1, 1, 1, 2, 1, 1], _S2_FACES), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Add a 2nd die at the old constraint. What happens?",
        "opts": ["Throughput jumps and the constraint moves to another station",
                 "Throughput jumps; Operation 4 stays the constraint",
                 "Throughput barely changes", "Throughput falls"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1, 1, 1, 2, 1, 1] + _OFF3
        and r["config"]["faces"] == _S2_FULL and not r["config"]["wip_on"],
        "reveal": _rev_elevate,
    },
    {
        "icon": "🔁", "phase": "Step 5 — Repeat",
        "title": "The constraint moved — don't run the old playbook",
        "intro": "Operation 4 is fixed, so the bottleneck has relocated to the stations still averaging "
                 "3.5/hr. Out of habit, you reach for the same move as before and **add a die to "
                 "Operation 1**. Will that lift throughput this time?",
        "setup": "Operation 1 = 2 dice × 6 · Operation 4 = 2 × 3 · others 1 × 6 · no cap · 1 year",
        "apply": {**_line([2, 1, 1, 2, 1, 1], _S2_FACES), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Add a die to Operation 1 now. What happens to throughput?",
        "opts": ["It jumps again", "It barely moves — Operation 1 isn't the constraint anymore",
                 "It falls", "The constraint returns to Operation 4"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [2, 1, 1, 2, 1, 1] + _OFF3
        and r["config"]["faces"] == _S2_FULL and not r["config"]["wip_on"],
        "reveal": _rev_repeat,
    },
]


def _nav_bump(target="lab"):
    """Queue a one-time scroll on the next rerun. target: 'lab' (bring the task panel to the
    top, after Prev/Next/lab change), 'run' (after a run — bring the setup+result into view and
    flash the panel so a change is obvious), or 'sidebar' (jump the sidebar to the setup)."""
    st.session_state["_nav_token"] = st.session_state.get("_nav_token", 0) + 1
    st.session_state["_nav_target"] = target


def _current_step_focus():
    """The sidebar card a step wants the student to change (its challenge's `focus`), or None.
    Used to scroll the sidebar to the right control when a configure-the-run step opens."""
    if st.session_state.get("app_mode") != "Guided Lab":
        return None
    prefix = lab_prefix_from_choice(st.session_state.get("lab_choice", ""))
    if prefix not in LABS:
        return None
    steps = LABS[prefix]["steps"]
    i = st.session_state.get(f"{prefix}_step", 0)
    if not (0 <= i < len(steps)):
        return None
    step = steps[i]
    ch = step.get("challenge")
    if isinstance(ch, dict) and ch.get("focus"):
        return ch["focus"]
    return step.get("focus")


def _scroll_to_top_on_nav():
    """Fire a one-time scroll after a nav/run/sidebar event (once per nav-token advance — never
    on ordinary reruns). The nav token is embedded in the markup on purpose: Streamlit reuses an
    iframe whose HTML is byte-identical across reruns and won't re-run its <script>, so a unique
    value per event forces the remount that actually performs the scroll."""
    tok = st.session_state.get("_nav_token", 0)
    if st.session_state.get("_nav_token_seen") == tok:
        return
    st.session_state["_nav_token_seen"] = tok
    target = st.session_state.get("_nav_target", "lab")
    focus = _current_step_focus()
    if target == "sidebar":
        focus_sel = json.dumps(f".st-key-{focus or 'ops_card'}")   # jump to the line editor by default
        main_anchor = json.dumps("")
    elif target == "run":
        focus_sel = json.dumps("")                                 # don't move the sidebar on a run
        main_anchor = json.dumps("jcc-runtop")
    else:  # "lab"
        focus_sel = json.dumps(f".st-key-{focus}" if focus else "")
        main_anchor = json.dumps("jcc-labtop")
    components.html(
        f"""
        <script>
          (function () {{
            var navToken = {tok};  /* unique per event → forces re-execution */
            var doc = window.parent.document;
            var focusSel = {focus_sel};
            var mainAnchor = {main_anchor};
            var isRun = {json.dumps(target == "run")};

            /* Sidebar: bring the setup control into view and flash it. */
            if (focusSel) {{
              var card = doc.querySelector(focusSel);
              if (card) {{
                var scroller = card.parentElement;
                while (scroller && scroller !== doc.body) {{
                  var oy = window.parent.getComputedStyle(scroller).overflowY;
                  if ((oy === 'auto' || oy === 'scroll') &&
                      scroller.scrollHeight > scroller.clientHeight + 4) break;
                  scroller = scroller.parentElement;
                }}
                if (scroller && scroller !== doc.body) {{
                  var top = card.getBoundingClientRect().top
                            - scroller.getBoundingClientRect().top + scroller.scrollTop - 12;
                  try {{ scroller.scrollTo({{top: Math.max(0, top), behavior: 'smooth'}}); }}
                  catch (e) {{ scroller.scrollTop = Math.max(0, top); }}
                }} else {{
                  try {{ card.scrollIntoView({{block: 'start', behavior: 'smooth'}}); }} catch (e) {{}}
                }}
                try {{
                  card.style.transition = 'box-shadow .25s ease';
                  card.style.boxShadow = '0 0 0 3px rgba(234,88,12,0.6)';
                  setTimeout(function () {{ card.style.boxShadow = ''; }}, 1600);
                }} catch (e) {{}}
              }}
            }}

            /* Main: bring the requested anchor to the top (fallback: absolute top). */
            if (mainAnchor) {{
              var a = doc.getElementById(mainAnchor);
              if (a && a.scrollIntoView) {{
                try {{ a.scrollIntoView({{block: 'start', behavior: 'auto'}}); }} catch (e) {{}}
              }} else {{
                var sels = ['section.main', '[data-testid="stMain"]',
                            '[data-testid="stAppViewContainer"]', '.stMainBlockContainer'];
                for (var i = 0; i < sels.length; i++) {{
                  var el = doc.querySelector(sels[i]);
                  if (el) {{ try {{ el.scrollTo({{top: 0, left: 0, behavior: 'auto'}}); }}
                            catch (e) {{ el.scrollTop = 0; }} }}
                }}
                try {{ (doc.scrollingElement || doc.documentElement).scrollTop = 0; }} catch (e) {{}}
                try {{ window.parent.scrollTo(0, 0); }} catch (e) {{}}
              }}
            }}

            /* After a run, flash the task panel so it's obvious a fresh result arrived — even
               with the animation turned off and even if the numbers look similar. */
            if (isRun) {{
              var panel = doc.querySelector('.st-key-lab_card');
              if (panel) {{ try {{
                panel.style.transition = 'box-shadow .18s ease';
                panel.style.boxShadow = '0 0 0 3px rgba(234,88,12,0.5)';
                setTimeout(function () {{ panel.style.boxShadow = ''; }}, 950);
              }} catch (e) {{}} }}
            }}
          }})();
        </script>
        """,
        height=0,
    )


def lab_apply_setup(prefix, idx=None, force_reset=False):
    """Configure the line to match a lab step's scenario (without running). So whenever
    a step is shown, the sidebar already reflects exactly what the step is asking about."""
    steps = LABS[prefix]["steps"]
    if idx is None:
        idx = st.session_state.get(f"{prefix}_step", 0)
    idx = max(0, min(idx, len(steps) - 1))
    reset_line_to_defaults()                       # clean slate (also clears the last run)
    for k, v in steps[idx]["apply"].items():
        st.session_state[k] = v
        # The financial inputs (order cost, holding cost, …) are drawn through fin_number_input,
        # which mirrors each value under a widget-prefixed key and writes it back. If we only set
        # the canonical key, that stale mirror overwrites it on the next render — so a step that
        # changes the order/holding cost silently has no effect. Drop the mirrors so the widgets
        # re-initialize from the value we just applied.
        st.session_state.pop(f"sb_{k}", None)
        st.session_state.pop(f"w_{k}", None)
    st.session_state["sim_results"] = None
    if steps[idx].get("challenge"):
        # A challenge that's already been resolved (recorded in progress) keeps its pass/try
        # state when revisited, so returning to it doesn't wipe the result and disable Next.
        # A fresh challenge — or an explicit "Reset & try again" — starts clean.
        already_done = idx in _get_progress().get(prefix, set())
        if force_reset or not already_done:
            _chal_reset(prefix, idx)


def _chal_reset(prefix, idx):
    """Reset a challenge's attempt counter and pass flag (per step, so a lab can hold more
    than one challenge). Used on entry and on retry."""
    st.session_state[f"{prefix}_chal_attempts_{idx}"] = 0
    st.session_state[f"{prefix}_chal_passed_{idx}"] = False
    st.session_state[f"{prefix}_chal_seen_{idx}"] = st.session_state.get("run_counter", 0)


def lab_goto(prefix, idx):
    steps = LABS[prefix]["steps"]
    idx = max(0, min(idx, len(steps) - 1))
    st.session_state[f"{prefix}_step"] = idx
    # Each new step opens already set up to match what it's asking about; the student
    # predicts first, then presses Run.
    lab_apply_setup(prefix, idx)
    _nav_bump()
    _autosave()          # persist the new step position so a refresh resumes here, not one back


def lab_on_choice_change():
    """When the user picks a different guided lab, set up its current step."""
    prefix = lab_prefix_from_choice(st.session_state.get("lab_choice", ""))
    lab_apply_setup(prefix)
    _nav_bump()
    _autosave()


def lab_on_part_change():
    """Switching Part 1 ↔ Part 2 jumps to the first lab of the chosen part."""
    part = st.session_state.get("lab_part", LAB_PART_ORDER[0])
    first = _part_labs(part)[0]
    st.session_state["lab_choice"] = LAB_CHOICE_LABEL[first]
    lab_apply_setup(first)
    _nav_bump()
    _autosave()


def lab_go_to_lab(prefix):
    """Jump straight to another lab (used by the 'next lab' button on the completion
    screen). Sets the sidebar picker to match and opens that lab at its first step."""
    if prefix in LAB_CHOICE_LABEL:
        st.session_state["lab_choice"] = LAB_CHOICE_LABEL[prefix]
    st.session_state[f"{prefix}_step"] = 0
    lab_apply_setup(prefix, 0)
    _nav_bump()
    _autosave()


def lab_on_mode_change():
    """Entering Guided Lab sets the line up to match the current step."""
    if st.session_state.get("app_mode") == "Guided Lab":
        prefix = lab_prefix_from_choice(st.session_state.get("lab_choice", ""))
        lab_apply_setup(prefix)
    _nav_bump()


def lab_setup_and_run(prefix):
    steps = LABS[prefix]["steps"]
    step = steps[st.session_state[f"{prefix}_step"]]
    for k, v in step["apply"].items():
        st.session_state[k] = v
        st.session_state.pop(f"sb_{k}", None)   # see lab_apply_setup: let fin widgets re-sync
        st.session_state.pop(f"w_{k}", None)
    st.session_state["anim_speed"] = "Fast"
    st.session_state["lab_autorun"] = True
    # After the run, scroll the fresh result into view and flash the panel — so it's obvious
    # something happened even with the animation off.
    _nav_bump("run")


def _request_main_run():
    """The main-window Run button (not just the sidebar): request a run on the next rerun."""
    st.session_state["_main_run"] = True


def reset_simulation():
    """Start over: clear ALL lab progress, saved answers, reflections and challenge state, and
    return to the very first step of Part 1. The line config is reset to defaults too. This is a
    deliberate, confirmed wipe — distinct from 'Reset to defaults' (line only) and a challenge's
    'Reset & try again' (that one step)."""
    ss = st.session_state
    ss["lab_progress"] = {}
    ss["reflect_done"] = set()
    for k in list(ss.keys()):
        if not isinstance(k, str):
            continue
        if ("_pred_" in k or "_est_" in k or "_reflect_" in k
                or "_chal_attempts_" in k or "_chal_passed_" in k or "_chal_seen_" in k
                or (k.endswith("_step") and k[:-5] in LAB_ORDER)):
            del ss[k]
    first = _part_labs(LAB_PART_ORDER[0])[0]
    ss["lab_part"] = LAB_PART_ORDER[0]
    ss["lab_choice"] = LAB_CHOICE_LABEL[first]
    ss[f"{first}_step"] = 0
    ss["sim_results"] = None
    ss["_confirm_reset"] = False
    for _p in ("prog", "refl", "ans"):
        try:
            del st.query_params[_p]
        except Exception:
            pass
    reset_line_to_defaults()
    lab_apply_setup(first, 0, force_reset=True)
    _nav_bump()
    _autosave()


def _focus_sidebar_cb():
    """The 'go to the setup' button: jump the sidebar to the step's control (or the line editor)."""
    _nav_bump("sidebar")


# ---- Economics lab: the P&L behind the line ----
def profit_curve(results, years, fin):
    """Annual profit for each uniform die size, from the cached die-scan."""
    scan = results.get("die_scan") or []
    caps = results.get("config", {}).get("dice", [])
    return [{"faces": e["faces"],
             "profit": compute_financials(e["res"], caps, e["sides"], years, fin)["profit"]}
            for e in scan]


def _fin_of(results):
    cfg = results.get("config", {})
    return compute_financials(results, cfg.get("dice", []), cfg.get("faces", []),
                              cfg.get("years", 1), get_fin())


def _rev_costs(r):
    f = _fin_of(r)
    nstations = sum(1 for c, s in zip(r["config"]["dice"], r["config"]["faces"]) if c > 0 and s > 0)
    parts = sorted([("production", f["prod_cost"]), ("fixed-die", f["fixed_alloc"]),
                    ("WIP holding", f["wip_cost"]), ("raw material", f["raw_cost"]),
                    ("ordering", f["order_cost"])], key=lambda x: -x[1])
    big = parts[0]
    per_unit = f["prod_cost"] / f["units_sold"] if f["units_sold"] else 0
    return ("good", f"Every finished unit is charged production cost at **all {nstations} stations**, so "
            f"a $0.10/unit die rate becomes ≈${per_unit:.2f} of production cost per finished unit — "
            f"**${f['prod_cost']:,.0f}** in total. (The single biggest line item is actually the "
            f"**{big[0]} cost** at **${big[1]:,.0f}** — the capital tied up in six precision dice.) "
            f"Revenue **${f['revenue']:,.0f}** − costs **${f['total_cost']:,.0f}** = "
            f"**${f['profit']:,.0f}** profit, a thin **{f['margin']:.1f}%** margin — so small "
            f"operational changes swing the bottom line hard. (The two costs named above are just the "
            f"headline items — see every line of the **Financial Results** card below for the full "
            f"breakdown that adds up to the total.)")


def _rev_sweetspot(r):
    fin = get_fin()
    curve = profit_curve(r, r.get("config", {}).get("years", 1), fin)
    if not curve:
        return ("warn", "Run the step to build the die-size profit curve.")
    best = max(curve, key=lambda c: c["profit"])
    worst = min(curve, key=lambda c: c["profit"])
    return ("good", f"Profit peaks at the **{best['faces']}-sided die** (≈${best['profit']:,.0f}/yr) and "
            f"the **{worst['faces']}-sided** die is worst (≈${worst['profit']:,.0f}/yr). Bigger dice "
            f"lift throughput, but their fixed cost climbs *convexly* — a 12-sided die costs far more "
            f"than twice a 6-sided one — so past the middle the capital outpaces the extra bottles. The "
            f"cheapest or biggest die is rarely the most profitable: there's a **sweet spot**.")


def _rev_wip_money(r):
    f = _fin_of(r)
    return ("good", f"Capping WIP held production steady but cut holding cost to just "
            f"**${f['wip_cost']:,.0f}** and flow time to **{r['flow_time_measured'] / HOURS_PER_DAY:.1f} "
            f"days**, for a profit of **${f['profit']:,.0f}**. The same line *uncapped* lets ≈1,500 "
            f"units of WIP pile in front of the constraint — that mountain alone would bleed thousands "
            f"in holding cost. Inventory isn't free: every unit sitting in the line is cash on the "
            f"floor. Subordinating (pull) converts WIP into working capital and trims the cost line.")


def _rev_supply_money(r):
    f = _fin_of(r)
    raw_series = r.get("raw_series") or [0]
    avg_raw = sum(raw_series) / len(raw_series)
    return ("good", f"Even at **50% reliability**, the line still finished **{r['total_output']:,}** "
            f"bottles (Operation 1 starved only **{r['starved_hours']:,} hours**) and sold "
            f"**{f['units_sold']:,.0f}** for **${f['profit']:,.0f}**. Because you order in big batches, "
            f"a cushion of raw material (**≈{avg_raw:,.0f} bottles** on average) sat in front of "
            f"Operation 1 and absorbed the missed deliveries. That inventory is **insurance**: it costs "
            f"money to hold, but it buys protection against an unreliable supplier. The flip side — what "
            f"happens with *no* buffer — is the just-in-time line in the Variability Lab, where the same "
            f"supplier slashes output.")


def _rev_demand_money(r):
    f = _fin_of(r)
    unsold = r["total_output"] - f["units_sold"]
    return ("warn", f"You can build all you like, but you only get paid for what sells. The fast line "
            f"produced **{r['total_output']:,}** bottles and sold only **{f['units_sold']:,.0f}**; the "
            f"unsold **{unsold:,.0f}** had nowhere to go and piled into finished-goods inventory "
            f"(averaging **{r['avg_fgi']:,.0f}** bottles), adding **${f['fgi_cost']:,.0f}** of holding "
            f"cost. Net profit cratered to **${f['profit']:,.0f}**. Overbuilding a finite market "
            f"doesn't make money — it manufactures expensive inventory.")


def _rev_match(r):
    f = _fin_of(r)
    return ("good", f"Right-sizing the line to the market beats overbuilding it. With capacity matched "
            f"to demand, finished-goods holding fell and the line sold **{f['units_sold']:,.0f}** bottles "
            f"for **${f['profit']:,.0f}** profit (**{f['margin']:.1f}%** margin). When the market is "
            f"the constraint, the cheapest line that keeps up with demand wins — extra capacity just "
            f"buys idle machines and inventory. *Match the line to the market.*")


def _rev_fin_breakeven(r):
    d = compute_breakeven(r)
    share = (d["be_units"] / d["sold"] * 100.0) if d["sold"] else 0.0
    return ("good", f"Each bottle sells for **${d['price']:.2f}** and carries **${d['unit_var']:.2f}** of "
            f"variable cost (materials + production), leaving a **contribution margin of ${d['cm']:.2f}** "
            f"per bottle to chip away at the **${d['fixed']:,.0f}** of fixed cost (the line's allocated "
            f"capital). Break-even = fixed ÷ margin = **{d['be_units']:,.0f} bottles** — about "
            f"**{share:.0f}%** of the **{d['sold']:,.0f}** it actually sold, leaving a **margin of "
            f"safety of {d['mos']:.0f}%** (how far sales could fall before the line stops making money). "
            f"Below break-even every bottle loses money; above it, each one drops its full "
            f"**${d['cm']:.2f}** straight to profit.")


LAB_FIN = [
    {
        "icon": "🧾", "phase": "Read the P&L",
        "title": "Production cost is charged at every station",
        "intro": "Run the standard balanced line (6 stations, 1 die × 6 faces, sold at $3.00/unit). A "
                 "unit is charged production cost **each time a station works on it**. Across the "
                 "six-station line, how many times does a single finished unit accrue production cost?",
        "setup": "6 × (1 die × 6 faces) · sell-everything market · $3.00/unit · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "How many times is production cost applied to each finished unit?",
        "opts": ["Once — only at the final station", "Twice — start and end",
                 "About six times — once per station", "It depends on WIP"],
        "answer": 2,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and not r["config"].get("demand_variable")
        and r["config"]["supply_unlimited"],
        "reveal": _rev_costs,
    },
    {
        "icon": "📈", "phase": "Break-even",
        "title": "How many must you sell to cover the fixed costs?",
        "intro": "Costs come in two flavors: **fixed** — the capital tied up in the machines, owed "
                 "whether you make one bottle or a million — and **variable** — materials and production "
                 "that scale with every unit. Cost out a solid line whose six stations each roll **8-faced dice**, selling at **$3.00** and "
                 "find the sales volume where the two exactly cancel: the **break-even point**.",
        "setup": "6 × (1 die × 8 faces) · sell-everything · $3.00/unit · 1 year",
        "apply": {**_line([1] * 6, [8] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "This line sells about 9,100 bottles a year. Roughly what share of that must it sell just "
             "to break even?",
        "opts": ["About one-quarter", "About half", "About three-quarters", "Nearly all of it"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == [8] * 6 + _OFF3 and not r["config"].get("demand_variable")
        and r["config"]["supply_unlimited"],
        "reveal": _rev_fin_breakeven,
    },
    {
        "icon": "🎯", "phase": "The capital trade-off",
        "title": "The die-size sweet spot",
        "intro": "Bigger dice produce more per hour but cost steeply more to buy (fixed cost rises "
                 "*convexly*). Starting from a cheap 4-sided line, the run scans **every** die size 2→12 "
                 "and prices its profit — the curve appears below. Which uniform die earns the most?",
        "setup": "Base line 1 die × 4 faces · the run scans every die size 2→12 for profit",
        "apply": {**_line([1] * 6, [4] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Which uniform die size is most profitable?",
        "opts": ["The smallest (2-sided) — cheapest dice", "A middle size (6–8 sided)",
                 "The largest (12-sided) — most output", "They're all about equal"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == [4] * 6 + _OFF3 and not r["config"].get("demand_variable")
        and bool(r.get("die_scan")),
        "reveal": _rev_sweetspot,
    },
    {
        "icon": "💵", "phase": "Inventory is cash",
        "title": "WIP costs money",
        "intro": "Take an unbalanced line (Operation 4 slow) and **cap WIP at 6** to pull instead of "
                 "push. You already know pulling barely changes throughput — but what does it do to "
                 "**holding cost and profit** versus letting WIP pile up?",
        "setup": "Operation 4 = 1 × 3 · WIP capped at 6 · sell-everything · 1 year",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": True,
                  **{f"wip_cap_{i}": 6 for i in range(N_OPS)},
                  "supply_reliability": 100, "demand_variable": False,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "Pulling (WIP cap) vs pushing — what happens to holding cost and profit?",
        "opts": ["Holding cost drops; profit improves", "Holding cost rises; profit falls",
                 "No change to either", "Profit falls sharply"],
        "answer": 0,
        "check": lambda r: r["config"]["faces"] == _S2_FULL and r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["wip_on"] and _caps0(r) == 6,
        "reveal": _rev_wip_money,
    },
    {
        "icon": "🚚", "phase": "Inventory as insurance",
        "title": "Raw-material inventory buffers supply risk",
        "intro": "You buy raw material in big batches (a purchase order of **150 bottles**), so a "
                 "healthy cushion of raw material sits in front of Operation 1. Now make the supplier "
                 "only **50% reliable** — it misses half its deliveries. Does that wreck the line's "
                 "output?",
        "setup": "Balanced 1 × 6 line · 🚚 supplier 50% reliable · big batch orders (150) · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 50,
                  "demand_variable": False, "fin_order_size": 150,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "With a big raw-material buffer, a 50%-reliable supplier does what to output?",
        "opts": ["Barely dents it — the buffer absorbs the misses", "Halves the output",
                 "Stops the line completely", "Only changes finished goods"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and not r["config"]["supply_unlimited"]
        and not r["config"].get("demand_variable") and r["config"].get("order_size", 1) > 50,
        "reveal": _rev_supply_money,
    },
    {
        "icon": "📉", "phase": "Demand risk → money",
        "title": "Building more than the market buys",
        "intro": "Now the market is finite and **fluctuates** (variable demand ≈ 3.5/hr) while we run a "
                 "**fast 2-dice line** that can produce roughly double that. Surely making more is good "
                 "for profit?",
        "setup": "Fast line (2 dice × 6 ≈ 7/hr) · 📉 variable demand (1 × 6 ≈ 3.5/hr) · 1 year",
        "apply": {**_line([2] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": True, "demand_dice": 1, "demand_faces": 6,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "Outproducing a finite, variable market does what to profit?",
        "opts": ["Raises it — more bottles made", "Wrecks it — unsold output becomes costly inventory",
                 "No effect", "Only raises revenue"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [2] * 6 + _OFF3
        and r["config"].get("demand_variable"),
        "reveal": _rev_demand_money,
    },
    {
        "icon": "⚖️", "phase": "Match supply to demand",
        "title": "Right-size the line to the market",
        "intro": "Same fluctuating market (≈ 3.5/hr), but now run a **modest 1-die line** roughly "
                 "matched to demand instead of the oversized one. Predict profit versus the overbuilt "
                 "line in the previous step.",
        "setup": "Right-sized line (1 die × 6 ≈ 3.3/hr) · same variable demand (1 × 6) · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": True, "demand_dice": 1, "demand_faces": 6,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "Matching the line to the market (vs overbuilding) does what to profit?",
        "opts": ["Improves it — less dead inventory, similar sales", "Hurts it — less capacity",
                 "No change", "Eliminates all lost sales"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and r["config"].get("demand_variable"),
        "reveal": _rev_match,
    },
]


def _var_year(dice, sides, rel=1.0, si=0, dd=0, df=0, seed=20260629):
    """Run a one-year reference line for in-reveal comparisons on a private, fixed-seed RNG so
    the comparison number is stable and never touches any other session's random stream."""
    return run_simulation(dice, sides, si, HOURS_PER_YEAR, rel, None, track_flow=False,
                          demand_dice=dd, demand_faces=df, rng=random.Random(seed))


def _rev_var_length(r):
    seed = 20260629
    long9 = _var_year([1] * 9, [6] * 9, seed=seed)
    short2 = _var_year([1] * 2 + [0] * 7, [6] * 2 + [0] * 7, seed=seed)
    return ("warn", f"Run head-to-head on the same luck: the **9-station** line finished only "
            f"**{long9['efficiency'] * 100:.1f}%** of its average capacity, while a **2-station** line "
            f"of identical stations hit **{short2['efficiency'] * 100:.1f}%**. Every extra station is "
            f"another **dependency** link — a low roll anywhere starves everyone downstream, so the "
            f"longer the line, the more of its average it gives up. More stages = more compounded "
            f"variability.")


def _rev_var_steady(r):
    seed = 20260629
    jumpy = _var_year([1] * 6 + _OFF3, [11] * 6 + _OFF3, seed=seed)
    steady = _var_year([3] * 6 + _OFF3, [3] * 6 + _OFF3, seed=seed)
    diff = steady["total_output"] - jumpy["total_output"]
    return ("good", f"Same average (**6 bottles/hr** per station), same luck: the jumpy line (1 die × "
            f"11) finished **{jumpy['total_output']:,}** while the steady line (3 dice × 3) finished "
            f"**{steady['total_output']:,}** — about **{diff:,} more bottles** for the *same* average "
            f"capacity and the same cost. Splitting the work across more, smaller dice cuts the "
            f"hour-to-hour swing: reducing variability is like getting extra capacity for free.")


def _rev_var_buffer(r):
    seed = 20260629
    buf = _var_year([1] * 6 + _OFF3, [6] * 6 + _OFF3, si=40, seed=seed)
    nobuf = _var_year([1] * 6 + _OFF3, [6] * 6 + _OFF3, si=0, seed=seed)
    return ("good", f"Same line, same luck: with a **40-bottle** cushion in front of every station the "
            f"line ran at **{buf['efficiency'] * 100:.1f}%** efficiency versus "
            f"**{nobuf['efficiency'] * 100:.1f}%** with none. The cushion absorbs the swings, so a low "
            f"roll doesn't instantly starve the next station. The catch: that buffer *is* WIP — bottles "
            f"sitting on the floor — so buffering trades inventory cost for smoother flow. (A "
            f"constraint buffer is exactly how Drum-Buffer-Rope protects the bottleneck.)")


def _rev_var_supply(r):
    relpct = r["config"].get("supply_reliability", 100)
    return ("warn", f"Running **just-in-time** (order size 1), there's almost no raw-material buffer — "
            f"so at **{relpct}%** reliability every missed delivery immediately starves Operation 1. It "
            f"sat idle for **{r['starved_hours']:,} hours** and the line finished only "
            f"**{r['total_output']:,}** bottles, well short of the ≈6,965 it makes with a dependable "
            f"supplier. Watch the raw-material line (which *is* Operation 1's inventory) hug zero and "
            f"stall. JIT is lean and cheap, but it leaves you **exposed** to supply variability — the "
            f"Economics Lab shows how a big raw buffer insures against exactly this.")


def _rev_var_demand(r):
    return ("warn", f"The line was perfectly capable, but the market's orders fluctuated. It sold "
            f"**{r['total_sold']:,}** bottles at a **{r['fill_rate'] * 100:.0f}%** fill rate; "
            f"**{r['lost_sales']:,}** bottles of demand went unfilled while finished goods averaged "
            f"**{r['avg_fgi']:,.0f}** bottles waiting to sell. Demand variability shows up at *both* "
            f"ends — lost sales when orders spike, dead inventory when they sag — even with a line "
            f"that never breaks down.")


def _rev_var_combined(r):
    relpct = r["config"].get("supply_reliability", 100)
    return ("warn", f"Production swings, a **{relpct}%**-reliable supplier, and a fluctuating market "
            f"all at once: Operation 1 starved for **{r['starved_hours']:,} hrs**, the line finished "
            f"**{r['total_output']:,}** bottles and sold **{r['total_sold']:,}** "
            f"(**{r['lost_sales']:,}** lost), with inventory swinging at both ends. Variability "
            f"compounds at every stage — and because each source is independent, taming any one of "
            f"them (steadier stations, a more reliable supplier, smoother demand, or a protective "
            f"buffer) buys back throughput that buying raw capacity never could.")


LAB_VAR = [
    {
        "icon": "🔗", "phase": "Variability compounds",
        "title": "A longer line loses more of its average",
        "intro": "Variability isn't just one station's problem — it stacks up along the line. Here is "
                 "a **9-station** bottling line where every station averages the same 3.5 bottles/hr. "
                 "Compared with a short **2-station** line of identical stations, will the 9-station "
                 "line finish a **higher or lower %** of its average capacity?",
        "setup": "9 stations · each 1 die × 6 faces · reliable supply · sell-everything · 1 year",
        "apply": {**_line([1] * 9, [6] * 9), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Versus a 2-station line of the same stations, the 9-station line finishes…",
        "opts": ["A higher % of its average", "A lower % of its average",
                 "Exactly the same %", "100% — length doesn't matter"],
        "answer": 1,
        "check": lambda r: r["config"]["dice"] == [1] * 9 and r["config"]["faces"] == [6] * 9,
        "reveal": _rev_var_length,
    },
    {
        "icon": "🎯", "phase": "Reduce variability",
        "title": "Same average, steadier dice — free capacity",
        "intro": "Two lines bottle the **same average** (6 bottles/hr per station). Line A's stations "
                 "roll **1 die × 11 faces** (big swings); Line B's roll **3 dice × 3 faces** (small "
                 "swings). We'll run the jumpy Line A now. Which line do you think finishes **more "
                 "bottles** over a year?",
        "setup": "6 stations · 1 die × 11 faces (jumpy) · reliable supply · 1 year",
        "apply": {**_line([1] * 6, [11] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "q": "Same average capacity, different spread — which finishes more bottles?",
        "opts": ["Line A — the jumpy one (1×11)", "Line B — the steady one (3×3)",
                 "Identical — same average", "Whichever has more stations"],
        "answer": 1,
        "check": lambda r: r["config"]["faces"] == [11] * 6 + _OFF3
        and r["config"]["dice"] == [1] * 6 + _OFF3,
        "reveal": _rev_var_steady,
    },
    {
        "icon": "🧰", "phase": "Buffers absorb it",
        "title": "A buffer cushions the swings",
        "intro": "Take the standard variable line and start it with a **cushion of 40 bottles** in "
                 "front of every station. Predict what that buffer does to throughput versus starting "
                 "empty.",
        "setup": "6 stations · 1 die × 6 · 40 bottles starting buffer · reliable supply · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 40, "simulation_years": 1},
        "q": "Starting every station with a buffer of bottles does what to throughput?",
        "opts": ["Raises it — buffers absorb the swings", "Lowers it — extra WIP slows the line",
                 "No change at all", "Makes it perfectly efficient"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and r["config"].get("start_inv", 0) > 0
        and not r["config"].get("demand_variable") and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_var_buffer,
    },
    {
        "icon": "🚚", "phase": "Supply variability",
        "title": "JIT + an unreliable supplier starves the line",
        "intro": "Now move the variability to the **front** of the line. We'll run **just-in-time** "
                 "(order size 1 — almost no raw-material buffer) and drop the supplier to **60% "
                 "reliable** (it misses roughly 4 of every 10 deliveries). The bottling line itself is "
                 "unchanged. With no raw buffer to fall back on, what happens?",
        "setup": "6 stations · 1 die × 6 · 🚚 supplier 60% reliable · JIT (order size 1) · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 60,
                  "demand_variable": False, "fin_order_size": 1,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "Running JIT with a 60%-reliable supplier does what?",
        "opts": ["Throughput falls hard; Operation 1 starves", "Throughput rises",
                 "Only raw inventory changes, not output", "No effect — the line catches up"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and r["config"].get("supply_reliability", 100) < 100
        and not r["config"].get("demand_variable") and r["config"].get("order_size", 1) <= 1,
        "reveal": _rev_var_supply,
    },
    {
        "icon": "📉", "phase": "Demand variability",
        "title": "A fluctuating market, a capable line",
        "intro": "Move the variability to the **market**: the line is fully reliable, but daily orders "
                 "fluctuate (variable demand ≈ 3.5 bottles/hr). With a capable line and a jumpy "
                 "market, what shows up?",
        "setup": "6 stations · 1 die × 6 · reliable supply · 📉 variable demand (1 × 6) · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": True, "demand_dice": 1, "demand_faces": 6,
                  "starting_inventory": 0, "simulation_years": 1},
        "q": "A capable line facing a fluctuating market produces…",
        "opts": ["Finished goods pile up AND some orders are lost", "Everything sells instantly",
                 "Higher throughput", "Nothing changes"],
        "answer": 0,
        "check": lambda r: r["config"]["dice"] == [1] * 6 + _OFF3
        and r["config"]["faces"] == _F6_FULL and r["config"].get("demand_variable")
        and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_var_demand,
    },
    {
        "icon": "🌪️", "phase": "It all compounds",
        "title": "Variability everywhere stacks up",
        "intro": "Finally, turn on variability at **every** stage at once: a jumpy line, **just-in-"
                 "time** ordering (order size 1, no raw buffer), a **70%** reliable supplier, and a "
                 "fluctuating market. Predict how the combination compares with any single source of "
                 "variability acting alone.",
        "setup": "6 stations · 1 die × 6 · JIT (order size 1) · 🚚 supplier 70% · 📉 variable demand · 1 year",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 70,
                  "demand_variable": True, "demand_dice": 1, "demand_faces": 6,
                  "fin_order_size": 1, "starting_inventory": 0, "simulation_years": 1},
        "q": "Variability at the line, the supplier, AND the market together…",
        "opts": ["Compounds — each source eats more throughput and sales",
                 "Cancels out", "Is no worse than one source alone", "Improves the bottom line"],
        "answer": 0,
        "check": lambda r: r["config"].get("demand_variable")
        and r["config"].get("supply_reliability", 100) < 100,
        "reveal": _rev_var_combined,
    },
]



def _rev_eoq_tradeoff(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    row = eoq_row_for(scan, 100)
    big = eoq_row_for(scan, 600)
    return ("good", f"At an order size of **100**, the supplier delivers **{row['orders']} times** a year "
            f"(**${row['ordering']:,.0f}** in ordering cost) and the line sits on about "
            f"**{row['avg_raw']:,.0f} bottles** of raw cycle stock (**${row['holding']:,.0f}** in holding "
            f"cost). The two costs pull in **opposite directions**: bump the order up to 600 and ordering "
            f"cost collapses to **${big['ordering']:,.0f}** while holding climbs to **${big['holding']:,.0f}**. "
            f"Order bigger → fewer, larger deliveries (ordering ↓, holding ↑); order smaller → the reverse. "
            f"Total inventory cost is just those two added together — so the cheapest order size has to sit "
            f"**somewhere in the middle**. That's what the next step pins down.")


def _rev_eoq_sweetspot(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    tiny = eoq_row_for(scan, 10)
    return ("good", f"Vary the order size across the range and the total cost traces a **U**. The textbook formula "
            f"**EOQ = √(2 · D · S ÷ H)** — with annual raw demand D ≈ **{scan['D']:,.0f}** bottles, ordering "
            f"cost S = **${scan['S']:,.0f}**/order and holding cost H ≈ **${scan['H']:.2f}**/bottle — predicts "
            f"the bottom at ≈ **{scan['eoq']:.0f} bottles**. The simulated cheapest order size is "
            f"**{scan['best_q']:,}** (**${scan['best_total']:,.0f}** total). Tiny orders drown in ordering "
            f"cost (order 10 → **${tiny['total']:,.0f}**); giant orders drown in holding cost. The formula "
            f"and the running line land on the **same sweet spot**.")


def _rev_eoq_balance(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    e = eoq_row_for(scan, scan["best_q"])
    lo = eoq_row_for(scan, 150)
    hi = eoq_row_for(scan, 250)
    base = scan["best_total"] or 1
    lo_pct = (lo["total"] / base - 1) * 100
    hi_pct = (hi["total"] / base - 1) * 100
    return ("good", f"Right at the EOQ the two costs are **almost equal** — ordering **${e['ordering']:,.0f}** "
            f"versus holding **${e['holding']:,.0f}**. That balance *is* the formula's logic: the U bottoms "
            f"out exactly where **ordering cost = holding cost**. And the bottom is **flat** — ordering "
            f"**150** instead of {scan['best_q']:,} costs only **{lo_pct:.0f}%** more, and **250** only "
            f"**{hi_pct:.0f}%** more. So EOQ is **forgiving**: you don't need the order size exactly right, "
            f"just in the right neighborhood. Good news, because in the real world D, S and H are all "
            f"estimates.")


def _rev_eoq_stockout(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    relpct = r["config"].get("supply_reliability", 100)
    e = eoq_row_for(scan, 184)
    lost = max(0, int(round(scan["D"])) - e["thru"])
    return ("warn", f"Now the supplier delivers only **{relpct}%** of the time. The textbook EOQ quietly "
            f"assumes replenishment is **instant and certain** — but at the EOQ-sized order ({e['Q']}), "
            f"Operation 1 **starved for {e['starved']:,} hours**, losing **{lost:,} bottles** of throughput "
            f"worth **${e['stockout']:,.0f}** in margin. That red **stockout column** is a cost the EOQ "
            f"formula **never sees** — and it bites hardest at exactly the lean order size the formula "
            f"recommended. Total cost at the 'optimal' EOQ has jumped to **${e['total']:,.0f}**, well above "
            f"its reliable-world value. The formula didn't get *wrong* — its **assumptions** did.")


def _rev_eoq_shift(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    cur = eoq_row_for(scan, r["config"].get("order_size", 250))
    e = eoq_row_for(scan, 184)
    return ("good", f"With an unreliable supplier the cheapest order size is **no longer** the textbook EOQ. "
            f"The simulated optimum **shifts up from {scan['eoq']:.0f} to {scan['best_q']:,}**: ordering "
            f"**{cur['Q']:,}** at a time carries more cycle stock (**{cur['avg_raw']:,.0f}** bottles) that "
            f"rides out missed deliveries — cutting starvation to **{cur['starved']:,} hours** and stockout "
            f"cost to **${cur['stockout']:,.0f}**. Its total **${cur['total']:,.0f}** now beats the EOQ's "
            f"**${e['total']:,.0f}**. The lesson: **when supply is variable, order *more* than the EOQ.** "
            f"That deliberate over-ordering is **safety stock**, and the throughput it protects more than "
            f"pays for the extra holding.")


def _rev_eoq_synthesis(r):
    scan = r.get("eoq_scan")
    if not scan:
        return ("info", "Run this step to build the inventory-cost curve, then come back for the takeaway.")
    jit_orders = int(round(scan["D"]))
    jit_ordering = jit_orders * scan["S"]
    return ("info", f"Order size **1** is pure **just-in-time**: raw inventory stays near zero, but the "
            f"supplier would have to deliver ≈ **{jit_orders:,} times** a year — about "
            f"**${jit_ordering:,.0f}** in ordering cost alone. JIT only pays off when each order is **cheap "
            f"to place** *and* the supplier is **rock-solid reliable**. That's the whole EOQ story in one "
            f"lab: the formula **nails the order quantity** in a steady world, and its **flat bottom** "
            f"makes it forgiving of rough estimates. But it stands on three assumptions — **constant "
            f"demand, instant and reliable replenishment, and no stockouts**. Break any of them and the "
            f"formula needs help: an unreliable supplier calls for **safety stock** (order above EOQ), and "
            f"a fluctuating market calls for a **reorder buffer**. EOQ is the right **starting point** — "
            f"not the last word.")


def _eoq_apply(order, rel):
    return {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": rel,
            "fin_order_size": order, "demand_variable": False, "starting_inventory": 0,
            "simulation_years": 1}


LAB_EOQ = [
    {
        "icon": "🔀", "phase": "The trade-off",
        "title": "Two costs that fight each other",
        "intro": "The supplier delivers raw bottles to Operation 1 in **batches**. The **order size** sets "
                 "how big each batch is — and it drives two costs at once. Big batches mean **few orders** "
                 "(low ordering cost) but **lots of cycle stock** sitting in front of Op 1 (high holding "
                 "cost). We'll order **100 bottles** at a time with a fully reliable supplier. Which way do "
                 "these two costs move as the order size **grows**?",
        "setup": "6 stations · 1 die × 6 · reliable supply · order size 100 · 1 year",
        "apply": _eoq_apply(100, 100),
        "q": "As the order size grows, ordering cost and holding cost…",
        "opts": ["Both fall together", "Both rise together",
                 "Move in opposite directions (one up, one down)", "Stay flat — order size doesn't matter"],
        "answer": 2,
        "check": lambda r: r["config"].get("order_size") == 100
        and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_eoq_tradeoff,
    },
    {
        "icon": "🎯", "phase": "The sweet spot",
        "title": "EOQ finds the bottom of the U",
        "intro": "If ordering cost falls and holding cost rises as the batch grows, their **sum** is a "
                 "**U-shaped** curve — and the bottom is the cheapest order size. The classic **Economic "
                 "Order Quantity** formula claims to find it: **EOQ = √(2·D·S ÷ H)**. Work it out from "
                 "the numbers in the hint, then run the line and see how close the simulated cheapest "
                 "order comes.",
        "setup": "6 stations · 1 die × 6 · reliable supply · order size 150 · 1 year",
        "apply": _eoq_apply(150, 100),
        "estimate": {
            "prompt": "Using EOQ = √(2·D·S ÷ H), estimate the cheapest order size (bottles per order):",
            "unit": "", "actual": lambda r: r["eoq_scan"]["eoq"], "tol": 0.15,
            "min": 0.0, "max": 800.0, "step": 5.0,
            "hint": "Yearly demand D ≈ 7,000 bottles, ordering cost S = $25, holding cost H ≈ $10.50 "
                    "per bottle per year. So EOQ = √(2 × 7,000 × 25 ÷ 10.5).",
        },
        "check": lambda r: r["config"].get("order_size") == 150
        and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_eoq_sweetspot,
    },
    {
        "icon": "⚖️", "phase": "Why it works",
        "title": "At the EOQ, the two costs balance",
        "intro": "Let's sit the line **exactly at the EOQ** (≈ 184 bottles). The formula's whole trick is "
                 "that the bottom of the U lands where the two costs are **equal**. Predict how ordering "
                 "cost compares with holding cost at this order size — and how badly it hurts to be a bit "
                 "off.",
        "setup": "6 stations · 1 die × 6 · reliable supply · order size 184 (the EOQ) · 1 year",
        "apply": _eoq_apply(184, 100),
        "q": "At the EOQ order size, ordering cost vs. holding cost will be…",
        "opts": ["Ordering cost much larger", "Holding cost much larger",
                 "About equal — and the curve is flat nearby", "Both zero"],
        "answer": 2,
        "check": lambda r: r["config"].get("order_size") == 184
        and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_eoq_balance,
    },
    {
        "icon": "🚚", "phase": "Assumption broken",
        "title": "An unreliable supplier blows a hole in the formula",
        "intro": "Everything so far assumed the supplier **always** delivers. Keep the order size at the "
                 "**EOQ (184)** but drop the supplier to **25% reliable** — three of every four deliveries "
                 "are missed. The EOQ formula has **no idea** this is happening. What does it do to total "
                 "cost?",
        "setup": "6 stations · 1 die × 6 · supplier 25% reliable · order size 184 (the EOQ) · 1 year",
        "apply": _eoq_apply(184, 25),
        "q": "Holding the EOQ order size but making the supplier 25% reliable will…",
        "opts": ["Change nothing — EOQ already handles it",
                 "Starve Operation 1 and add a stockout cost the formula ignores",
                 "Lower total cost (fewer bottles to hold)", "Make the line run faster"],
        "answer": 1,
        "check": lambda r: r["config"].get("order_size") == 184
        and r["config"].get("supply_reliability", 100) < 100,
        "reveal": _rev_eoq_stockout,
    },
    {
        "icon": "🛡️", "phase": "The fix",
        "title": "When supply is shaky, order above the EOQ",
        "intro": "Same shaky **25%-reliable** supplier — but now order **250** bottles at a time, "
                 "comfortably **above** the textbook EOQ. The extra cycle stock is a deliberate buffer. "
                 "Will ordering *more* than the EOQ end up **cheaper** than the EOQ itself?",
        "setup": "6 stations · 1 die × 6 · supplier 25% reliable · order size 250 (above EOQ) · 1 year",
        "apply": _eoq_apply(250, 25),
        "q": "Under the unreliable supplier, ordering 250 (above the EOQ) versus 184 (the EOQ) is…",
        "opts": ["More expensive — you're holding extra stock for nothing",
                 "Cheaper — the buffer prevents costly stockouts", "Exactly the same",
                 "Impossible to compare"],
        "answer": 1,
        "check": lambda r: r["config"].get("order_size") == 250
        and r["config"].get("supply_reliability", 100) < 100,
        "reveal": _rev_eoq_shift,
    },
    {
        "icon": "🧭", "phase": "Putting it together",
        "title": "EOQ is a starting point, not the last word",
        "intro": "Last one — the **opposite** extreme. Order size **1** is pure **just-in-time**: the "
                 "supplier feeds Op 1 one bottle at a time, so almost no raw inventory is ever held. The "
                 "supplier is reliable again. JIT looks beautifully lean — but what's the catch hiding in "
                 "the cost structure?",
        "setup": "6 stations · 1 die × 6 · reliable supply · order size 1 (just-in-time) · 1 year",
        "apply": _eoq_apply(1, 100),
        "q": "Running pure just-in-time (order size 1) mainly trades away…",
        "opts": ["Nothing — it's strictly best", "Low holding cost for a huge ordering cost (and fragility)",
                 "Throughput for inventory", "Quality for speed"],
        "answer": 1,
        "check": lambda r: r["config"].get("order_size") == 1
        and r["config"].get("supply_reliability", 100) >= 100,
        "reveal": _rev_eoq_synthesis,
    },
]



def _rev_eoqd_baseline(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("good", f"Your **reference point**. This line consumes about **{s['D']:,.0f} bottles/yr** "
            f"(that's **D**), each order costs **${s['S']:,.0f}** to place (**S**), and holding one "
            f"bottle for the year costs **${s['H']:,.2f}** (**H**). The formula "
            f"**EOQ = √(2·D·S ÷ H)** lands at ≈ **{s['eoq']:.0f}** bottles, and the simulated cheapest "
            f"order (the ★) is **{s['best_q']:,}**. Hold this ≈ **{s['eoq']:.0f}** in mind — each of the "
            f"next steps changes just **one** lever and you'll watch the best order size move.")


def _rev_eoqd_demand(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("good", f"You doubled the line's speed, so annual demand climbed to **D ≈ {s['D']:,.0f}** "
            f"bottles. The EOQ rose to ≈ **{s['eoq']:.0f}** (cheapest simulated: **{s['best_q']:,}**) — "
            f"but notice it did **not** double. EOQ grows with the **square root** of demand, so 2× the "
            f"demand lifts the batch only ≈ 1.4×. Busier lines do want bigger orders, but the increase "
            f"is gentle.")


def _rev_eoqd_ordercost(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("good", f"Each purchase order now costs **${s['S']:,.0f}** to place (up from $25). The EOQ "
            f"jumped to ≈ **{s['eoq']:.0f}** (cheapest: **{s['best_q']:,}**) — roughly **double** the "
            f"baseline. EOQ rises with the **square root of ordering cost**, so 4× the paperwork ≈ 2× "
            f"the batch: when orders are **expensive to place**, you place **fewer, bigger** ones to "
            f"spread that cost.")


def _rev_eoqd_holding(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("warn", f"Holding a bottle for the year now costs **${s['H']:,.2f}** (up about 4×). The EOQ "
            f"**fell** to ≈ **{s['eoq']:.0f}** (cheapest: **{s['best_q']:,}**) — roughly **half** the "
            f"baseline. EOQ moves with **1 ÷ √H**, so 4× the holding cost halves the batch: when stock "
            f"is **expensive to carry**, you order **little and often** to keep inventory low.")


def _rev_eoqd_combine(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("good", f"Now demand **and** holding cost were **both** doubled. They push the EOQ in "
            f"**opposite** directions — more demand pulls it up, pricier holding pulls it down — and "
            f"because both live under the same square root as a ratio (**√(D ÷ H)**), they nearly "
            f"**cancel**: the EOQ is ≈ **{s['eoq']:.0f}**, right back near the ≈ 184 baseline. The best "
            f"order size depends on the **balance** of the drivers, not any single one.")


def _rev_eoqd_synth(r):
    s = r.get("eoq_scan")
    if not s:
        return ("info", "Run this step to build the curve and read off the EOQ.")
    return ("info", f"Everything in one line: **EOQ = √(2·D·S ÷ H)**. With **D ≈ {s['D']:,.0f}**, "
            f"**S = ${s['S']:,.0f}**, **H = ${s['H']:,.2f}** it predicts ≈ **{s['eoq']:.0f}**, matching "
            f"the simulated cheapest order **{s['best_q']:,}**. To find the best order quantity for "
            f"**any** line: (1) measure annual demand **D**, (2) the cost to place one order **S**, "
            f"(3) the cost to hold one unit for the period **H** — then plug in. The batch grows with "
            f"**D** and **S**, shrinks with **H**, and every effect is softened by the square root, so "
            f"a rough estimate lands close. Change the line, the ordering cost, or the holding cost in "
            f"the sidebar and watch the ★ move.")


def _eoqd_apply(dice6, S, hpd):
    return {**_line(dice6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
            "fin_order_cost": S, "fin_raw_holding": hpd, "fin_order_size": 150,
            "demand_variable": False, "starting_inventory": 0, "simulation_years": 1}


def _rev_eoqd_robust(r):
    s = r.get("eoq_scan")
    if not s or not s.get("rows"):
        return ("info", "Run this step to build the cost curve.")
    rows = s["rows"]
    eoq = s["eoq"]
    best_total = s["best_total"]

    def nearest(q):
        return min(rows, key=lambda x: abs(x["Q"] - q))

    half = nearest(eoq * 0.5)
    dbl = nearest(eoq * 2)
    hrel = half["ordering"] + half["holding"]
    drel = dbl["ordering"] + dbl["holding"]
    hp = 100 * (hrel - best_total) / best_total if best_total else 0
    dp = 100 * (drel - best_total) / best_total if best_total else 0
    return ("good", f"The bottom of the cost curve is **flat**, which is what makes the EOQ so "
            f"forgiving. The optimum here is ≈ **{eoq:.0f}** bottles at **${best_total:,.0f}**. Order "
            f"**half** of that (≈ {half['Q']}) and the ordering-plus-holding cost rises only "
            f"**{hp:.0f}%**; order **double** (≈ {dbl['Q']}) and it rises **{dp:.0f}%**. Being off by a "
            f"factor of two barely moves the total — so a rough estimate of D, S and H is good enough to "
            f"act on. Note the **asymmetry**: over-ordering costs more than under-ordering (holding "
            f"climbs in a straight line), so when you're unsure, err a little **low**.")


LAB_EOQD = [
    {
        "icon": "📐", "phase": "Reference point",
        "title": "The three drivers of the best order size",
        "intro": "The best order quantity is set by exactly **three** things: how much you use "
                 "(**demand D**), what it costs to **place an order** (**S**), and what it costs to "
                 "**hold a unit** (**H**). The **EOQ formula** ties them together: **EOQ = √(2·D·S ÷ "
                 "H)**. We'll start from a balanced six-station, 1-die line at the default costs and read off the "
                 "baseline EOQ — then change one driver at a time.",
        "setup": "6 stations · 1 die × 6 · S = $25/order · H = $0.04/bottle/day · reliable",
        "apply": _eoqd_apply([1] * 6, 25.0, 0.04),
        "q": "Before we change anything — the cheapest order size sits at the bottom of a curve that is…",
        "opts": ["A straight line", "U-shaped (a trade-off with a clear minimum)",
                 "Always falling", "Flat everywhere"],
        "answer": 1,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_baseline,
    },
    {
        "icon": "📈", "phase": "Driver 1 — demand",
        "title": "More demand → bigger orders (but gently)",
        "intro": "Let's **double the line's speed** — every station now rolls **2 dice**, so it "
                 "produces about twice as much and consumes raw material about twice as fast. Only "
                 "**demand (D)** has changed; ordering and holding costs are the same. Which way does "
                 "the best order size move — and by how much?",
        "setup": "6 stations · 2 dice × 6 (double demand) · S = $25 · H = $0.04/day · reliable",
        "apply": _eoqd_apply([2] * 6, 25.0, 0.04),
        "q": "Doubling demand will move the EOQ…",
        "opts": ["Down", "Up, and roughly double", "Up, but by much less than double (≈ 1.4×)",
                 "Not at all"],
        "answer": 2,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_demand,
    },
    {
        "icon": "🧾", "phase": "Driver 2 — ordering cost",
        "title": "Costly orders → order more each time",
        "intro": "Back to the 1-die line, but now each purchase order is **expensive to place**: "
                 "**S = $100** instead of $25 (think setup, paperwork, delivery fees). Only **ordering "
                 "cost** changed. If placing an order costs more, should each order be bigger or "
                 "smaller?",
        "setup": "6 stations · 1 die × 6 · S = $100/order · H = $0.04/day · reliable",
        "apply": _eoqd_apply([1] * 6, 100.0, 0.04),
        "q": "Quadrupling the ordering cost will move the EOQ…",
        "opts": ["Down", "Up (order more each time, fewer orders)", "Not at all", "To zero"],
        "answer": 1,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_ordercost,
    },
    {
        "icon": "🧊", "phase": "Driver 3 — holding cost",
        "title": "Costly to hold → order little and often",
        "intro": "Still the 1-die line at $25/order, but now raw material is **expensive to hold**: "
                 "**H = $0.16/bottle/day**, four times the default (think refrigeration, spoilage, tied-"
                 "up cash). Only **holding cost** changed. If carrying stock costs more, should each "
                 "order be bigger or smaller?",
        "setup": "6 stations · 1 die × 6 · S = $25 · H = $0.16/bottle/day (4×) · reliable",
        "apply": _eoqd_apply([1] * 6, 25.0, 0.16),
        "q": "Quadrupling the holding cost will move the EOQ…",
        "opts": ["Up", "Down (order less each time, more often)", "Not at all", "It doesn't affect EOQ"],
        "answer": 1,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_holding,
    },
    {
        "icon": "⚖️", "phase": "Two drivers at once",
        "title": "When drivers fight, watch the balance",
        "intro": "Now change **two** at once: **double the demand** (2 dice per station) **and** double "
                 "the **holding cost** (H = $0.08/day). One pushes the EOQ up, the other pushes it "
                 "down. What happens when they act together?",
        "setup": "6 stations · 2 dice × 6 (2× demand) · S = $25 · H = $0.08/day (2×) · reliable",
        "apply": _eoqd_apply([2] * 6, 25.0, 0.08),
        "q": "Doubling demand and doubling holding cost together will leave the EOQ…",
        "opts": ["Much higher", "Much lower", "About the same as the baseline",
                 "Impossible to say"],
        "answer": 2,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_combine,
    },
    {
        "icon": "🛡️", "phase": "How forgiving?",
        "title": "The flat bottom — you don't have to be exact",
        "intro": "The EOQ needs three numbers you can rarely pin down precisely — demand, ordering "
                 "cost, holding cost. Here's the reassuring part: the total-cost curve is **flat near "
                 "its minimum**, so a rough answer costs almost nothing. Run the baseline and see the "
                 "penalty for ordering at **half** or **double** the EOQ.",
        "setup": "6 stations · 1 die × 6 · S = $25 · H = $0.04/bottle/day (baseline)",
        "apply": _eoqd_apply([1] * 6, 25.0, 0.04),
        "q": "If you order at half or double the EOQ instead of exactly at it, the ordering-plus-holding "
             "cost will:",
        "opts": ["Roughly double", "Rise only modestly — a small % penalty", "Stay exactly the same",
                 "Fall"],
        "answer": 1,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_robust,
    },
    {
        "icon": "🧮", "phase": "Putting it together",
        "title": "Reading the best order size off the formula",
        "intro": "One last combination — **pricier orders and cheaper holding**: **S = $50** and "
                 "**H = $0.02/day**. Both changes push the EOQ the **same** way. Predict the direction, "
                 "then run it to see the whole picture: how demand, ordering cost, and holding "
                 "cost combine into a single best order quantity.",
        "setup": "6 stations · 1 die × 6 · S = $50/order · H = $0.02/bottle/day · reliable",
        "apply": _eoqd_apply([1] * 6, 50.0, 0.02),
        "q": "Doubling ordering cost while halving holding cost will move the EOQ…",
        "opts": ["Down", "Up (both push the same way)", "Nowhere", "Down then up"],
        "answer": 1,
        "check": lambda r: bool(r.get("eoq_scan")),
        "reveal": _rev_eoqd_synth,
    },
]



# =========================================================
# Little's Law lab (no engine change — reads L, λ, W straight from results)
# =========================================================
def _little_apply(dice6, si, cap=None):
    a = {**_line(dice6, [6] * 6), "supply_reliability": 100, "demand_variable": False,
         "starting_inventory": si, "simulation_years": 1}
    if cap is None:
        a["wip_limit_on"] = False
    else:
        a["wip_limit_on"] = True
        a.update({f"wip_cap_{i}": cap for i in range(N_OPS)})
    return a


def _ll(r):
    return (r.get("wip_L", 0.0), r.get("throughput_rate", 0.0),
            r.get("flow_time_derived", 0.0), r.get("flow_time_measured", 0.0),
            r.get("total_output", 0))


def _rev_little_establish(r):
    L, lam, Wd, Wm = _ll(r)[:4]
    return ("good", f"Little's Law locks the three big flow numbers together: **L = λ · W**. Here the "
            f"line holds **L ≈ {L:,.0f} bottles** in process on average and finishes **λ ≈ {lam:.2f} "
            f"bottles/hr**, so the law predicts a lead time of **W = L ÷ λ ≈ {Wd:,.0f} h**. The "
            f"simulator *independently measured* the average bottle's time in the line at **≈ {Wm:,.0f} "
            f"h** — essentially the same number. That's the law: you don't get to pick all three freely.")


def _rev_little_cutwip(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("good", f"You capped WIP, so average inventory fell to **L ≈ {L:,.0f}** (from ≈ 173). By "
            f"Little's Law the lead time had to fall with it: **W ≈ {Wm:,.0f} h** (≈ {Wm/HOURS_PER_DAY:.1f} "
            f"work-days), down from ≈ 50 h — about the same proportion as the WIP drop. Meanwhile "
            f"throughput barely moved (**{thru:,}** bottles finished). Cutting WIP bought a big lead-time "
            f"reduction almost for free.")


def _rev_little_toofar(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("warn", f"Squeezing the cap to 5 drove WIP down to **L ≈ {L:,.0f}** and lead time to just "
            f"**W ≈ {Wm:,.0f} h** — Little's Law still holds exactly. But look at throughput: it *dropped* "
            f"to **{thru:,}** (from ≈ 7,100). Starve the line of WIP and stations sit idle waiting for "
            f"work. There's a floor: below it, less WIP costs you output — which is exactly the trade-off "
            f"the **Pull vs. Push** lab is about.")


def _rev_little_throughput(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("good", f"The other lever. You doubled the line's speed, so **λ ≈ {lam:.2f}/hr** (about "
            f"double). With WIP still capped, **L ≈ {L:,.0f}** stayed modest, so **W = L ÷ λ ≈ {Wm:,.0f} "
            f"h** — the lead time fell versus the same cap at single speed (≈ 17 h). Two ways to shrink "
            f"lead time: carry less WIP (**L↓**) or run faster (**λ↑**).")


def _rev_little_synth(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("info", f"Everything in one identity: **W = L ÷ λ**. Lead time is average work-in-process "
            f"divided by throughput — always, in any stable process. So to make the line *feel* faster "
            f"you have exactly two moves: **reduce WIP** or **raise throughput**. You can't wish lead "
            f"time down while both stay put. Here the measured **≈ {Wm:,.0f} h** matches the predicted "
            f"**≈ {Wd:,.0f} h** — the law never breaks.")


LAB_LITTLE = [
    {
        "icon": "📏", "phase": "The law",
        "title": "L = λ · W — the three numbers are linked",
        "intro": "Every stable process obeys **Little's Law**: the average **work-in-process (L)** "
                 "equals the **throughput rate (λ)** times the average **lead time (W)** — so "
                 "**W = L ÷ λ**. We'll start the balanced line with a little inventory in it, measure L "
                 "and λ, and check the law's prediction of W against the lead time the simulator "
                 "actually clocks.",
        "setup": "6 stations · 1 die × 6 · 40 starting units · no WIP cap · reliable supply",
        "apply": _little_apply([1] * 6, 40),
        "q": "Little's Law predicts W = L ÷ λ. Compared with the lead time the simulator actually "
             "measures, that prediction will:",
        "opts": ["Be far too high", "Be far too low", "Match it closely", "Be unrelated"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_little_establish,
    },
    {
        "icon": "✂️", "phase": "Cut WIP",
        "title": "Less inventory → shorter lead time",
        "intro": "Now put a **WIP cap of 20** on every station. That directly limits how much "
                 "inventory (L) the line can hold. Little's Law says lead time W must move with it. "
                 "Throughput is set by the stations' dice — will trimming WIP hurt it?",
        "setup": "Same line · WIP capped at 20 per station",
        "apply": _little_apply([1] * 6, 40, 20),
        "q": "Capping WIP at 20 (down from ≈ 173 average) will make the average lead time:",
        "opts": ["Rise", "Stay the same", "Fall sharply, roughly in proportion to WIP",
                 "Fall, but throughput will crash"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_little_cutwip,
        "reflect": "Cutting WIP shortened the lead time but left throughput unchanged. In one sentence, "
                   "why does W depend on L that way?",
    },
    {
        "icon": "⚠️", "phase": "Too far",
        "title": "The floor: starving the line",
        "intro": "If a little less WIP is good, is a lot less better? Squeeze the cap to **5** per "
                 "station and watch both lead time **and** throughput. Little's Law will still hold — "
                 "but that doesn't mean every number moves in your favor.",
        "setup": "Same line · WIP capped at 5 per station (very tight)",
        "apply": _little_apply([1] * 6, 40, 5),
        "q": "Squeezing the WIP cap all the way to 5 will:",
        "opts": ["Cut lead time further with no downside", "Cut lead time further, but throughput drops too",
                 "Raise lead time", "Break Little's Law"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_little_toofar,
    },
    {
        "icon": "🚀", "phase": "Throughput",
        "title": "The other lever — raise λ",
        "intro": "Lead time is L ÷ λ, so there are **two** ways to cut it. We just worked on L (WIP). "
                 "Now leave a moderate WIP cap on and **double the line's speed** (2 dice per station), "
                 "raising throughput λ. Watch what happens to W.",
        "setup": "2 dice × 6 (double speed) · WIP capped at 20 · reliable",
        "apply": _little_apply([2] * 6, 40, 20),
        "q": "Doubling throughput (λ) while keeping WIP capped will make the average lead time W:",
        "opts": ["Rise", "Fall", "Stay exactly the same", "Go to zero"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_little_throughput,
    },
    {
        "icon": "🎯", "phase": "Synthesis",
        "title": "Reading lead time off the law",
        "intro": "Back to the plain line, no WIP cap. Predict the relationship one last time, then use "
                 "the reveal to pull the whole lesson together: what W = L ÷ λ actually gives you as a "
                 "manager.",
        "setup": "6 stations · 1 die × 6 · no WIP cap (back to the baseline)",
        "apply": _little_apply([1] * 6, 40),
        "q": "To cut a line's lead time, Little's Law (W = L ÷ λ) says your only levers are:",
        "opts": ["Add more WIP", "Reduce WIP or raise throughput", "Lower throughput", "Nothing works"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_little_synth,
    },
]


# =========================================================
# Pull vs. Push lab (uses the WIP-limit feature; unbalanced line so push floods)
# =========================================================
def _pull_apply(cap=None):
    a = {**_line([2, 2, 2, 1, 2, 2], [6, 6, 6, 3, 6, 6]), "supply_reliability": 100,
         "demand_variable": False, "starting_inventory": 0, "simulation_years": 1}
    if cap is None:
        a["wip_limit_on"] = False
    else:
        a["wip_limit_on"] = True
        a.update({f"wip_cap_{i}": cap for i in range(N_OPS)})
    return a


def _rev_pull_push(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("warn", f"With a fast front end pouring work into a slow bottleneck and **no limit on WIP**, "
            f"inventory just piles up: the line averaged **L ≈ {L:,.0f} bottles** in process, and a "
            f"typical bottle spent **W ≈ {Wm:,.0f} h** crawling through — about "
            f"**{Wm/HOURS_PER_DAY:,.0f} work-days**. The bottleneck still set the pace: **{thru:,}** "
            f"bottles finished. This is a **push** line — every station makes all it can and shoves it "
            f"downstream, whether or not the next station can take it.")


def _rev_pull_cap(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("good", f"You put a **WIP cap** on every station — a **pull** line: a station may only make "
            f"more when there's room for it downstream. Inventory collapsed to **L ≈ "
            f"{L:,.0f} bottles** and lead time to **W ≈ {Wm:,.0f} h** — from hundreds of hours to a "
            f"handful. And throughput? **{thru:,}** bottles — *identical* to push. The bottleneck sets "
            f"throughput; all that extra WIP was buying nothing but delay and tied-up cash.")


def _rev_pull_tune(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("good", f"Loosening the cap let the line hold more WIP, so inventory rose to **L ≈ {L:,.0f}** "
            f"and lead time to **W ≈ {Wm:,.0f} h** — but throughput stayed at **{thru:,}**, exactly as "
            f"before. The cap is a **dial for inventory and lead time**, not for output: output is "
            f"pinned by the bottleneck. Set the cap as low as you can without starving the constraint.")


def _rev_pull_synth(r):
    L, lam, Wd, Wm, thru = _ll(r)
    return ("info", f"Push vs. pull, same line and same bottleneck: **{thru:,}** bottles out either way "
            f"— but push carried thousands of units and hundreds of hours of lead time, while pull "
            f"carries dozens and a few hours (**L ≈ {L:,.0f}**, **W ≈ {Wm:,.0f} h** here). Because "
            f"throughput is set by the **constraint**, not by how much WIP you flood in, capping WIP is "
            f"nearly free lead-time and cash. It's Little's Law with L slashed: same λ, tiny L → tiny W. "
            f"That's the idea behind lean manufacturing and kanban systems.")


LAB_PULL = [
    {
        "icon": "🌊", "phase": "Push",
        "title": "A push line floods the bottleneck",
        "intro": "Here the first three stations are **fast** (2 dice × 6) and station 4 is a **slow "
                 "bottleneck** (1 die × 3). There's **no limit on WIP**, so every station makes all it "
                 "can and pushes it downstream. Think about where the work goes when the fast stations "
                 "out-run the slow one.",
        "setup": "Fast stations 1–3 (2 dice × 6) → bottleneck st.4 (1 die × 3) → fast 5–6 · NO WIP cap",
        "apply": _pull_apply(None),
        "q": "With a fast front end, a slow bottleneck, and no WIP limit, the inventory piling up inside "
             "the line over the year will:",
        "opts": ["Stay near zero", "Settle at a small steady level", "Grow into the thousands",
                 "Track the bottleneck rate"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_pull_push,
    },
    {
        "icon": "🛑", "phase": "Pull (cap WIP)",
        "title": "Cap WIP — and lose nothing",
        "intro": "Same line, but now cap WIP at **8** on every station. A station can only run when "
                 "there's space downstream — the definition of a **pull** system. The big question for "
                 "any manager: does capping WIP cut the line's output?",
        "setup": "Same line · WIP capped at 8 per station (a pull system)",
        "apply": _pull_apply(8),
        "q": "Compared with the push line, capping WIP everywhere will make throughput:",
        "opts": ["Collapse", "Stay essentially the same", "Roughly double", "Become erratic"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_pull_cap,
    },
    {
        "icon": "🎛️", "phase": "Tune the cap",
        "title": "The cap sets inventory, not output",
        "intro": "Raise the cap from 8 to **20**. More WIP is now allowed to accumulate. Predict what "
                 "that does — and, importantly, what it does *not* do — to throughput.",
        "setup": "Same line · WIP cap raised to 20 per station",
        "apply": _pull_apply(20),
        "q": "Raising the WIP cap from 8 to 20 will make throughput:",
        "opts": ["Rise with the cap", "Fall", "Stay the same — only WIP and lead time rise",
                 "Go to zero"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_pull_tune,
    },
    {
        "icon": "🎯", "phase": "Synthesis",
        "title": "Why capping WIP is nearly free",
        "intro": "Back to the lean pull setting. Predict the principle, then run it to connect "
                 "push-vs-pull to Little's Law and to the bottleneck that's been setting the pace all "
                 "along.",
        "setup": "Same line · WIP capped at 8 (the lean pull setting)",
        "apply": _pull_apply(8),
        "q": "Capping WIP is nearly free because a line's throughput is set by:",
        "opts": ["The total WIP allowed", "The fastest station", "The bottleneck (the constraint)",
                 "The order size"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "wip_L" in r,
        "reveal": _rev_pull_synth,
    },
]



# =========================================================
# Throughput Accounting lab (no engine change — reframes the P&L as T / I / OE)
# =========================================================
def _ta_apply(dice6, faces6, si=40, cap=None):
    a = {**_line(dice6, faces6), "supply_reliability": 100, "demand_variable": False,
         "starting_inventory": si, "simulation_years": 1}
    if cap is None:
        a["wip_limit_on"] = False
    else:
        a["wip_limit_on"] = True
        a.update({f"wip_cap_{i}": cap for i in range(N_OPS)})
    return a


def _rev_ta_reframe(r):
    d = compute_throughput_accounting(r)
    return ("good", f"Same line, same money — a new lens. **Throughput T = revenue − truly-variable "
            f"(material) cost = ${d['T']:,.0f}**. **Operating Expense OE** (conversion, allocated fixed "
            f"cost, holding, ordering) **= ${d['OE']:,.0f}**. **Investment I** (machines + inventory) "
            f"**= ${d['I']:,.0f}**. Net profit is just **T − OE = ${d['NP']:,.0f}** — *exactly* the P&L "
            f"profit — and **ROI = (T − OE) ÷ I = {d['ROI']:.1f}%**. Three levers now: grow **T**, shrink "
            f"**OE**, shrink **I**.")


def _rev_ta_localeff(r):
    d = compute_throughput_accounting(r)
    return ("warn", f"You sped up a station that **isn't the constraint** — it now looks wonderfully "
            f"busy, and raw throughput even ticked up. But watch the money: OE ballooned to "
            f"**${d['OE']:,.0f}** and investment to **${d['I']:,.0f}** as material poured in and piled "
            f"up as WIP, while **T** barely moved (**${d['T']:,.0f}**). Net profit collapsed to "
            f"**${d['NP']:,.0f}** and ROI to **{d['ROI']:.1f}%**. *Local efficiency is a trap* — output "
            f"at a non-constraint is cost and inventory, not throughput.")


def _rev_ta_reducei(r):
    d = compute_throughput_accounting(r)
    return ("good", f"Capping WIP drained the cash tied up in inventory: **I ≈ ${d['I']:,.0f}** and OE "
            f"eased to **${d['OE']:,.0f}**, while throughput held (**T = ${d['T']:,.0f}**). Net profit "
            f"**${d['NP']:,.0f}**, and **ROI rose to {d['ROI']:.1f}%**. Less money parked in the line, "
            f"the same money coming out — the return on every dollar invested goes up. (That's the "
            f"Pull-vs-Push lesson, seen from the balance sheet.)")


def _rev_ta_elevate(r):
    d = compute_throughput_accounting(r)
    return ("good", f"Now you added capacity **to lift throughput itself**. **T jumped to ${d['T']:,.0f}** "
            f"and net profit to **${d['NP']:,.0f}** (**ROI {d['ROI']:.1f}%**). Same move as the local-"
            f"efficiency step — 'add a die' — but aimed at raising real output instead of making a non-"
            f"constraint look busy. One doubled profit; the other destroyed it. *Where* you add capacity "
            f"is everything.")


def _rev_ta_synth(r):
    d = compute_throughput_accounting(r)
    return ("info", f"The scoreboard that matters: **Net profit = T − OE** and **ROI = (T − OE) ÷ I**. "
            f"To make more money you have exactly three moves — raise **Throughput**, cut **Operating "
            f"Expense**, cut **Investment/Inventory** — and **T comes first** because it has no ceiling "
            f"while OE and I have floors. Traditional cost accounting rewards keeping every station "
            f"busy; Throughput Accounting shows why that piles up inventory and expense without adding a "
            f"cent of throughput. Here: T ${d['T']:,.0f} − OE ${d['OE']:,.0f} = **${d['NP']:,.0f}** on "
            f"**${d['I']:,.0f}** invested.")


LAB_TA = [
    {
        "icon": "📊", "phase": "Reframe",
        "title": "The same P&L as T, I and OE",
        "intro": "Goldratt's **Throughput Accounting** scores a business with three numbers: "
                 "**Throughput (T)** = revenue minus the truly-variable material cost; **Operating "
                 "Expense (OE)** = everything else you spend to run the place; and **Investment (I)** = "
                 "money tied up in machines and inventory. Net profit is **T − OE**; return on "
                 "investment is **(T − OE) ÷ I**. Run the baseline line and meet the three numbers.",
        "setup": "6 stations · 1 die × 6 · 40 starting units · reliable · default financials",
        "apply": _ta_apply([1] * 6, [6] * 6),
        "q": "Reframing the P&L as T − OE, the net profit Throughput Accounting reports will:",
        "opts": ["Be higher than the P&L", "Be lower than the P&L",
                 "Match the P&L profit exactly", "Be unrelated to it"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_ta_reframe,
    },
    {
        "icon": "🏃", "phase": "Local efficiency",
        "title": "Making a non-constraint 'look busy'",
        "intro": "A classic instinct: keep every station fully utilized. Let's **double the speed of "
                 "the first station** (a feeder — not the constraint) so it runs flat-out and its "
                 "efficiency number looks great. Predict what that does to **profit**, not to how busy "
                 "the station looks.",
        "setup": "Station 1 sped to 2 dice × 6 (a non-constraint) · rest 1 die × 6 · no WIP cap",
        "apply": _ta_apply([2, 1, 1, 1, 1, 1], [6] * 6),
        "q": "Running a non-constraint station flat-out so it 'looks busy' will make profit:",
        "opts": ["Rise — more output means more money", "Stay about the same",
                 "Fall sharply — inventory and expense climb while throughput stays flat",
                 "Double"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_ta_localeff,
    },
    {
        "icon": "💧", "phase": "Cut Investment",
        "title": "Drain the inventory, lift the ROI",
        "intro": "Back to the balanced line, but **cap WIP at 20** so far less inventory can accumulate. "
                 "Throughput will be about the same as the baseline. Think about **I** (the cash tied up "
                 "in the line) and therefore **ROI**.",
        "setup": "6 stations · 1 die × 6 · WIP capped at 20 per station",
        "apply": _ta_apply([1] * 6, [6] * 6, cap=20),
        "q": "Capping WIP to cut inventory, with throughput roughly unchanged, makes ROI:",
        "opts": ["Rise — less money tied up for the same output", "Fall", "Stay identical",
                 "Turn negative"],
        "answer": 0,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_ta_reducei,
    },
    {
        "icon": "⛰️", "phase": "Grow Throughput",
        "title": "Add capacity where it counts",
        "intro": "The other kind of 'add a die.' This time raise capacity **across the line so real "
                 "throughput goes up** (every station to 2 dice). It's the same change as the "
                 "local-efficiency step — more dice — but this time aimed at lifting output instead of "
                 "keeping a feeder busy.",
        "setup": "All 6 stations · 2 dice × 6 (throughput doubles) · no WIP cap",
        "apply": _ta_apply([2] * 6, [6] * 6),
        "q": "Adding capacity so that real throughput T rises will make profit:",
        "opts": ["Fall", "Rise — real throughput turns into real profit", "Stay flat", "Turn negative"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_ta_elevate,
    },
    {
        "icon": "🎯", "phase": "Synthesis",
        "title": "The scoreboard that matters",
        "intro": "Back to the baseline. Predict the principle, then run it to lock in how T, I "
                 "and OE decide profit and ROI — and why 'keep everyone busy' is the wrong goal.",
        "setup": "6 stations · 1 die × 6 · 40 starting units (the baseline, revisited)",
        "apply": _ta_apply([1] * 6, [6] * 6),
        "q": "According to Throughput Accounting, the way to make more money is to:",
        "opts": ["Maximize every station's output", "Raise throughput while cutting inventory and expense",
                 "Minimize throughput", "Always buy more machines"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_ta_synth,
    },
]


# =========================================================
# Safety Stock / Reorder Point / Service Level lab
# =========================================================
def _ss_apply(rop, rel=50, osz=10):
    a = {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": rel,
         "demand_variable": False, "starting_inventory": 0, "simulation_years": 1,
         "fin_order_size": osz, "scrap_on": False}
    if rop is None:
        a["reorder_point_on"] = False
    else:
        a["reorder_point_on"] = True
        a["reorder_point"] = rop
    return a


def _ss(r):
    raw = r.get("raw_series") or [0]
    return (r.get("service_level", 1.0) * 100.0, r.get("starved_hours", 0),
            r.get("total_output", 0), r.get("reorder_point", 0),
            statistics.mean(raw))


def _rev_ss_jit(r):
    svc, starved, thru, rop, avg_raw = _ss(r)
    return ("warn", f"With a **50%-reliable** supplier and the reorder point at just **{rop}**, the raw "
            f"buffer runs dry constantly: Operation 1 sat **starved for {starved:,} hours** and the line "
            f"managed only **{thru:,}** bottles at a **service level of {svc:.0f}%** (the share of hours "
            f"it had material to work). Running this lean leaves you exposed — every missed delivery "
            f"stops the line.")


def _rev_ss_add(r):
    svc, starved, thru, rop, avg_raw = _ss(r)
    return ("good", f"Lifting the reorder point to **{rop}** builds a **safety-stock cushion**: the buffer "
            f"now rarely empties, so service climbed to **{svc:.0f}%**, starvation fell to **{starved:,} "
            f"hours**, and throughput rose to **{thru:,}**. Safety stock is inventory held precisely to "
            f"absorb the supplier's misses.")


def _rev_ss_target(r):
    svc, starved, thru, rop, avg_raw = _ss(r)
    return ("good", f"At a reorder point of **{rop}** the cushion fully covers this shaky supplier's "
            f"swings: **service ≈ {svc:.0f}%**, almost no starvation (**{starved:,} hrs**), throughput "
            f"**{thru:,}**. This is the safety-stock level that buys near-perfect service — the line "
            f"barely notices that half the deliveries are missed.")


def _rev_ss_dim(r):
    svc, starved, thru, rop, avg_raw = _ss(r)
    return ("warn", f"Pushing the reorder point all the way to **{rop}** does **nothing** for service — "
            f"it's already ≈ {svc:.0f}% — but the average raw inventory now sits around **{avg_raw:.0f} "
            f"bottles**, all of it holding cost. Service can't exceed 100%, so every unit of safety stock "
            f"beyond what covers the variability is pure tied-up cash. More is not better.")


def _rev_ss_synth(r):
    svc, starved, thru, rop, avg_raw = _ss(r)
    return ("info", f"**Reorder point = expected demand during the lead time + safety stock.** The safety "
            f"stock is your insurance against variability — here, an unreliable supplier. Raise it until "
            f"service hits your target (around **40** covered this 50% supplier), then **stop**: past that "
            f"point service is flat at ≈100% and every extra unit is inventory you pay to hold for no "
            f"gain. It's the same holding-versus-service trade-off as EOQ, now applied to *when* you "
            f"order, not how much.")


LAB_SS = [
    {
        "icon": "🪫", "phase": "Running lean",
        "title": "A thin buffer against a shaky supplier",
        "intro": "The EOQ labs set how *much* to order. This one sets *when* — the **reorder point**, the "
                 "inventory level that triggers a new order. Start almost just-in-time: a supplier that "
                 "delivers only **50% of the time** and a reorder point of just **5** bottles. Will such "
                 "a thin cushion keep Operation 1 fed?",
        "setup": "6 × (1 die × 6) · 🚚 supplier 50% reliable · small orders (10) · reorder point 5",
        "apply": _ss_apply(5),
        "estimate": {
            "prompt": "Estimate the service level — here, the % of hours Operation 1 has raw material "
                      "to work with (its raw-material availability, not a customer fill rate):",
            "unit": "%", "actual": lambda r: r["service_level"] * 100.0, "tol": 0.12,
            "min": 0.0, "max": 100.0, "step": 1.0,
            "hint": "The supplier delivers only half the time, and there's almost no cushion (reorder "
                    "at just 5 bottles).",
        },
        "check": lambda r: isinstance(r, dict) and "service_level" in r,
        "reveal": _rev_ss_jit,
    },
    {
        "icon": "🔋", "phase": "Add safety stock",
        "title": "Order earlier, hold a cushion",
        "intro": "Raise the **reorder point to 15** — order sooner, so a bigger safety cushion sits in "
                 "front of Operation 1 when deliveries miss. Same unreliable supplier. What does the extra "
                 "safety stock do to the service level?",
        "setup": "Same line & 50% supplier · reorder point raised to 15",
        "apply": _ss_apply(15),
        "q": "Raising the reorder point to 15 (more safety stock) will make the service level:",
        "opts": ["Fall", "Rise sharply", "Not change", "Drop to zero"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "service_level" in r,
        "reveal": _rev_ss_add,
        "reflect": "A bigger raw-material cushion pushed the service level up. In one sentence, why does "
                   "holding more safety stock protect the line?",
    },
    {
        "icon": "🎯", "phase": "Hit the target",
        "title": "Enough cushion for near-perfect service",
        "intro": "Push the **reorder point to 40**. Predict whether that's enough safety stock to keep "
                 "the line essentially always fed despite the supplier missing half its deliveries.",
        "setup": "Same line & 50% supplier · reorder point 40",
        "apply": _ss_apply(40),
        "q": "At a reorder point of 40, the service level will be:",
        "opts": ["Around 100% — the cushion covers the misses", "Still low", "Unchanged from 15",
                 "Lower than before"],
        "answer": 0,
        "check": lambda r: isinstance(r, dict) and "service_level" in r,
        "reveal": _rev_ss_target,
    },
    {
        "icon": "💸", "phase": "Diminishing returns",
        "title": "When more safety stock is just cost",
        "intro": "If a reorder point of 40 already buys ≈100% service, what does cranking it to **150** "
                 "buy you? Watch the service level **and** the raw inventory sitting in front of "
                 "Operation 1.",
        "setup": "Same line & 50% supplier · reorder point 150 (well past the target)",
        "apply": _ss_apply(150),
        "q": "Pushing the reorder point to 150 will:",
        "opts": ["Raise service well above 100%", "Barely touch service but pile up raw inventory",
                 "Lower the service level", "Cut holding cost"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "service_level" in r,
        "reveal": _rev_ss_dim,
    },
    {
        "icon": "🧭", "phase": "Synthesis",
        "title": "Sizing the safety stock",
        "intro": "Back to the reorder point that hit the target. Predict the principle, then use the "
                 "reveal to tie reorder point, safety stock, and service level together.",
        "setup": "Same line & 50% supplier · reorder point 40 (the right-sized cushion)",
        "apply": _ss_apply(40),
        "q": "The right safety stock is the level that:",
        "opts": ["Is as high as possible", "Covers the variability, then stop — more is just holding cost",
                 "Is as low as possible", "Always equals the order size"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "service_level" in r,
        "reveal": _rev_ss_synth,
    },
]


# =========================================================
# Quality / Yield lab (per-station scrap; a fast low-yield station is the hidden constraint)
# =========================================================
def _qual_apply(dice6, scrap_map=None, si=40):
    a = {**_line(dice6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
         "demand_variable": False, "starting_inventory": si, "simulation_years": 1,
         "fin_order_size": 150, "reorder_point_on": False}
    if scrap_map:
        a["scrap_on"] = True
        for i in range(N_OPS):
            a[f"scrap_pct_{i}"] = scrap_map.get(i, 0)
    else:
        a["scrap_on"] = False
        for i in range(N_OPS):
            a[f"scrap_pct_{i}"] = 0
    return a


def _q(r):
    return (r.get("total_output", 0), r.get("scrap_total", 0),
            r.get("yield_rate", 1.0) * 100.0, r.get("eff_bottleneck_label", "?"))


def _rev_qual_baseline(r):
    thru, scrap, yld, effbn = _q(r)
    return ("good", f"With perfect quality, Station 3's **two dice** give it plenty of slack — it "
            f"processes everything the 1-die stations feed it and passes it all along. The line runs at "
            f"**{thru:,}** bottles, paced by the ordinary 1-die stations, **not** by fast Station 3. It "
            f"looks like the last place you'd hunt for a bottleneck.")


def _rev_qual_scrap(r):
    thru, scrap, yld, effbn = _q(r)
    return ("warn", f"Now Station 3 scraps most of what it touches. Its dice are still fast — but every "
            f"good bottle takes several attempts, and it can only work on what the line feeds it, so its "
            f"**good** output collapses. Throughput fell to **{thru:,}** (from ≈ 7,200), with "
            f"**{scrap:,}** bottles scrapped and overall yield **{yld:.0f}%**. The real constraint is now "
            f"**Station {effbn.strip('#')}** — the fast station nobody suspected. Quality quietly stole "
            f"the line's capacity.")


def _rev_qual_wrong(r):
    thru, scrap, yld, effbn = _q(r)
    return ("warn", f"You added capacity at a **different** station — and throughput barely moved, still "
            f"about **{thru:,}**. Of course: Station 3's yield is the constraint, so speeding up a station "
            f"that isn't the bottleneck does nothing (the Five Focusing Steps, again). And this "
            f"constraint is a **quality** problem — no amount of extra speed elsewhere fixes it.")


def _rev_qual_fix(r):
    thru, scrap, yld, effbn = _q(r)
    return ("good", f"Improve Station 3's yield and the line springs back: throughput jumped to "
            f"**{thru:,}**, overall yield **{yld:.0f}%**, scrap down to **{scrap:,}**. Nothing about the "
            f"line's *speed* changed — you simply stopped throwing away good work. Every point of yield "
            f"won at the constraint is capacity handed straight back to the line.")


def _rev_qual_synth(r):
    thru, scrap, yld, effbn = _q(r)
    return ("info", f"A station's real capacity is its **speed × its yield**. A fast station with poor "
            f"yield is a **hidden bottleneck**: its nominal rate looks healthy, so it escapes attention "
            f"while its scrap silently caps the whole line. Hunt for the constraint by *effective* (good) "
            f"output, not nominal speed — and remember that lifting quality at the constraint adds "
            f"throughput just as surely as bolting on another machine, usually far more cheaply. **Quality "
            f"is capacity.**")


LAB_QUAL = [
    {
        "icon": "✨", "phase": "The fast station",
        "title": "A station with speed to spare",
        "intro": "Meet a line where **Station 3 runs two dice** — roughly twice as fast as the other five "
                 "(one die each). With perfect quality, run it and confirm where the constraint is. Fast "
                 "Station 3 surely isn't the problem… is it?",
        "setup": "Stations 1,2,4,5,6 = 1 die × 6 · Station 3 = 2 dice × 6 · perfect quality",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1]),
        "q": "Running two dice, Station 3 is twice as fast as the rest. With perfect quality, is it the "
             "line's constraint?",
        "opts": ["Yes — it's the bottleneck", "No — it has spare capacity", "It depends on WIP",
                 "Only when demand is high"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_qual_baseline,
    },
    {
        "icon": "🩹", "phase": "Yield loss appears",
        "title": "Fast — but scrapping most of it",
        "intro": "Now Station 3 develops a **quality problem: it scraps 60%** of every batch it works on "
                 "(bad seals, off-fills). Its dice are as fast as ever. Predict what a 60%-scrap rate at "
                 "this fast station does to the whole line's throughput.",
        "setup": "Same line · Station 3 now scraps 60% of its output",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 60}),
        "q": "Despite its speed, Station 3 scrapping 60% will make the line's throughput:",
        "opts": ["Barely change — it's fast", "Rise", "Collapse — it becomes the real constraint",
                 "Double"],
        "answer": 2,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_qual_scrap,
        "reflect": "Station 3 kept its full speed, yet the line's good output collapsed. In one "
                   "sentence, why is a fast, low-yield station a constraint?",
    },
    {
        "icon": "🔧", "phase": "Fix the wrong thing",
        "title": "Speeding up the wrong station",
        "intro": "The line is starved of good bottles, so the instinct is to add capacity. Let's **speed "
                 "up Station 1 to two dice** — while Station 3 still scraps 60%. Predict whether more "
                 "speed at Station 1 rescues the line.",
        "setup": "Station 1 → 2 dice · Station 3 still scraps 60%",
        "apply": _qual_apply([2, 1, 2, 1, 1, 1], {2: 60}),
        "q": "Speeding up Station 1 while Station 3 still scraps 60% will make throughput:",
        "opts": ["Recover fully", "Barely change — you fixed the wrong station", "Drop further",
                 "Double"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_qual_wrong,
    },
    {
        "icon": "🛠️", "phase": "Fix the constraint",
        "title": "Improve the yield instead",
        "intro": "Leave the speeds alone and attack the real problem: **improve Station 3's yield** so it "
                 "scraps only **10%** instead of 60%. Predict what fixing the constraint's *quality* does "
                 "to throughput.",
        "setup": "Station 1 back to 1 die · Station 3 scrap improved 60% → 10%",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 10}),
        "q": "Improving Station 3's yield (scrap 60% → 10%) will make throughput:",
        "opts": ["Barely change", "Jump back up", "Fall", "Stay at the low level"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_qual_fix,
    },
    {
        "icon": "🧭", "phase": "Synthesis",
        "title": "Quality is capacity",
        "intro": "Back to the fast-but-scrapping line. Predict the principle, then run it to lock "
                 "in why yield belongs in every capacity calculation.",
        "setup": "Station 3 = 2 dice, scrapping 60% (the hidden constraint)",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 60}),
        "q": "A station's real capacity is:",
        "opts": ["Its nominal speed", "Its speed × its yield — quality is capacity",
                 "Whatever WIP allows", "The fastest it can possibly run"],
        "answer": 1,
        "check": lambda r: isinstance(r, dict) and "total_output" in r,
        "reveal": _rev_qual_synth,
    },
]




# =========================================================
# CAPSTONE — "Diagnose & Fix": interleaved cases where the cause is NOT announced.
# The student runs a broken line, reads the dashboard, picks the root cause from the
# same four options every time (forcing discrimination), then fixes it. This is the
# transfer skill the siloed labs can't teach on their own.
# =========================================================
_DIAG_OPTS = [
    "A single slow station — a capacity bottleneck",
    "A station scrapping too many units — low yield",
    "An unreliable raw-material supplier",
    "Work-in-process running away — no WIP cap",
]


def _rev_diag_bottleneck(r):
    out = r["total_output"]
    return ("warn", f"**Capacity bottleneck.** Output is only **{out:,}** and inventory is stacked in "
            f"front of Operation 4 while the stations after it sit idle. Service is **100%** (raw "
            f"material is flowing) and yield is **100%** (nothing is scrapped) — so it isn't the "
            f"supplier or quality. One station is simply too slow. The fix is more capacity there.")


def _rev_diag_supplier(r):
    svc = r["service_level"] * 100
    out = r["total_output"]
    return ("warn", f"**Unreliable supplier.** The tell is the **service level: {svc:.0f}%** — the line "
            f"is starved about **{100 - svc:.0f}%** of the time waiting on raw material, which is why "
            f"output sits at {out:,}. The stations themselves are fine: yield is 100% and no single one "
            f"is the slow point. The fix is a bigger raw buffer — a higher reorder point.")


def _rev_diag_yield(r):
    out = r["total_output"]
    yld = r["yield_rate"] * 100
    scrap = r.get("scrap_total", 0)
    return ("warn", f"**Low yield.** Station 3 runs fast (2 dice), yet good output is only **{out:,}**. "
            f"Look at the scrap: **{scrap:,} bottles** thrown away and a yield of just **{yld:.0f}%**. "
            f"Speed doesn't help if the units don't survive — this station's *effective* capacity is "
            f"its speed × its yield. The fix is better quality there, not more speed.")


def _rev_diag_wip(r):
    out = r["total_output"]
    wip = r["wip_L"]
    return ("warn", f"**Runaway WIP.** Here's the trap: output is actually **fine ({out:,})** — more "
            f"capacity wouldn't help. But average work-in-process is **{wip:,.0f} bottles** and the lead "
            f"time is enormous. A fast front end is pushing far more into the line than the back can pull "
            f"through, with no cap to stop it. The fix is a WIP cap — it cuts inventory without touching "
            f"output.")


_DIAG_CASE_INTRO = ("This is the capstone. Each case hands you a **broken line and no label** — it "
                    "could be a capacity bottleneck, a low-yield station, an unreliable supplier, or "
                    "runaway WIP. **Run it, read the dashboard, and diagnose it** from the four causes "
                    "below; then you'll fix it. Watch **output, WIP, service level, and yield** — each "
                    "cause leaves a different fingerprint.")

LAB_DIAG = [
    # ---- Case 1: capacity bottleneck (answer 0) ----
    {
        "icon": "🔬", "phase": "Case 1 — Diagnose",
        "title": "Line A is underperforming",
        "intro": _DIAG_CASE_INTRO + "\n\n**Line A:** it's finishing far fewer bottles than it should.",
        "setup": "Run it as given, study the dashboard, then pick the cause.",
        "apply": {**_line([1, 1, 1, 1, 1, 1], [6, 6, 6, 2, 6, 6]), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0,
                  "simulation_years": 1},
        "diagnose": True,
        "q": "What's throttling Line A?",
        "opts": _DIAG_OPTS, "answer": 0,
        "reflect": "In one sentence: which dashboard numbers ruled out the supplier and quality, "
                   "leaving a capacity bottleneck?",
        "check": lambda r: r["config"]["faces"][3] == 2 and not r["config"].get("scrap_on")
        and not r["config"].get("reorder_on"),
        "reveal": _rev_diag_bottleneck,
    },
    {
        "icon": "🔧", "phase": "Case 1 — Fix",
        "title": "Fix Line A",
        "intro": "**Your task:** get Line A to finish **≥ 4,000 bottles/year.** You've diagnosed a "
                 "capacity bottleneck — now relieve it.",
        "setup": "Start: the same line, Operation 4 at 1 die × 2.",
        "apply": {**_line([1, 1, 1, 1, 1, 1], [6, 6, 6, 2, 6, 6]), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0,
                  "simulation_years": 1},
        "challenge": {
            "tries": 3,
            "hint": "Focus on the constraint — raise ONLY Operation 4 (more faces, or one more die) "
                    "until it's no longer the slowest station. Padding every station wastes capacity "
                    "and blows the dice budget.",
            "targets": [
                {"label": "Bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
                 "goal": "≥ 4,000", "ok": lambda r: r["total_output"] >= 4000},
                {"label": "Total dice used", "get": lambda r: sum(r["config"]["dice"]), "fmt": "{:,.0f}",
                 "goal": "≤ 7 — fix the constraint, don't pad the whole line",
                 "ok": lambda r: sum(r["config"]["dice"]) <= 7},
            ],
        },
        "check": lambda r: True,
    },
    # ---- Case 2: unreliable supplier (answer 2) ----
    {
        "icon": "🔬", "phase": "Case 2 — Diagnose",
        "title": "Line B keeps stalling",
        "intro": "**Line B:** the stations look balanced and quality is clean, but output is still down. "
                 "Run it and find the cause.",
        "setup": "Run it as given, study the dashboard, then pick the cause.",
        "apply": _ss_apply(5, rel=50, osz=10),
        "diagnose": True,
        "q": "What's throttling Line B?",
        "opts": _DIAG_OPTS, "answer": 2,
        "reflect": "In one sentence: which single metric gave away that the problem was upstream of the "
                   "line — at the supplier?",
        "check": lambda r: r["config"].get("reorder_on") and r["config"].get("supply_reliability", 100) <= 60,
        "reveal": _rev_diag_supplier,
    },
    {
        "icon": "🔧", "phase": "Case 2 — Fix",
        "title": "Fix Line B",
        "intro": "**Your task:** get the **service level ≥ 95%** and output back **≥ 6,000 bottles/year.** "
                 "The supplier only delivers half the time — protect the line.",
        "setup": "Start: a 50%-reliable supplier and a reorder point of just 5.",
        "apply": _ss_apply(5, rel=50, osz=10),
        "challenge": {
            "tries": 3,
            "hint": "Build a raw-material cushion: raise the reorder point (🚚 card) so a delivery miss "
                    "doesn't starve Operation 1.",
            "targets": [
                {"label": "Service level", "get": lambda r: r["service_level"] * 100, "fmt": "{:,.0f}%",
                 "goal": "≥ 95%", "ok": lambda r: round(r["service_level"] * 100) >= 95},
                {"label": "Bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
                 "goal": "≥ 6,000", "ok": lambda r: r["total_output"] >= 6000},
            ],
        },
        "check": lambda r: True,
    },
    # ---- Case 3: low yield (answer 1) ----
    {
        "icon": "🔬", "phase": "Case 3 — Diagnose",
        "title": "Line C runs fast but delivers little",
        "intro": "**Line C:** every station is fed and running — one of them is even double-speed — yet "
                 "good output is low. Run it and find the cause.",
        "setup": "Run it as given, study the dashboard, then pick the cause.",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 60}),
        "diagnose": True,
        "q": "What's throttling Line C?",
        "opts": _DIAG_OPTS, "answer": 1,
        "reflect": "In one sentence: how did a fast station end up being the constraint — what number "
                   "exposed it?",
        "check": lambda r: bool(r["config"].get("scrap_on")),
        "reveal": _rev_diag_yield,
    },
    {
        "icon": "🔧", "phase": "Case 3 — Fix",
        "title": "Fix Line C",
        "intro": "**Your task:** get **good output ≥ 6,000 bottles/year** with overall **yield ≥ 90%.** "
                 "The fast station is scrapping most of what it makes.",
        "setup": "Start: Station 3 runs 2 dice but scraps 60%.",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 60}),
        "challenge": {
            "tries": 3,
            "hint": "Speed isn't the lever here — cut Station 3's scrap rate in the Quality card so its "
                    "good output stops throttling the line.",
            "targets": [
                {"label": "Good bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
                 "goal": "≥ 6,000", "ok": lambda r: r["total_output"] >= 6000},
                {"label": "Overall yield", "get": lambda r: r["yield_rate"] * 100, "fmt": "{:,.0f}%",
                 "goal": "≥ 90%", "ok": lambda r: round(r["yield_rate"] * 100) >= 90},
            ],
        },
        "check": lambda r: True,
    },
    # ---- Case 4: runaway WIP (answer 3) ----
    {
        "icon": "🔬", "phase": "Case 4 — Diagnose",
        "title": "Line D is buried in inventory",
        "intro": "**Line D:** the warehouse is overflowing and lead times are terrible. But look closely "
                 "at the output before you judge. Run it and find the cause.",
        "setup": "Run it as given, study the dashboard, then pick the cause.",
        "apply": {**_line([3, 3, 3, 2, 3, 3], [6] * 6), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0,
                  "simulation_years": 1},
        "diagnose": True,
        "q": "What's the real problem with Line D?",
        "opts": _DIAG_OPTS, "answer": 3,
        "reflect": "In one sentence: why is more capacity the wrong fix here, even though inventory is "
                   "piling up?",
        "check": lambda r: r["config"]["dice"][0] >= 3 and not r["config"].get("wip_on"),
        "reveal": _rev_diag_wip,
    },
    {
        "icon": "🔧", "phase": "Case 4 — Fix",
        "title": "Fix Line D",
        "intro": "**Your task:** keep output **≥ 10,000 bottles/year** while getting **average WIP under "
                 "50.** The output is already fine — tame the inventory without losing it.",
        "setup": "Start: a fast front end (3 dice) flooding the line, no WIP cap.",
        "apply": {**_line([3, 3, 3, 2, 3, 3], [6] * 6), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0,
                  "simulation_years": 1},
        "challenge": {
            "tries": 3,
            "hint": "Don't add or remove capacity — turn on the WIP cap (🚧 card) and set it low. Output "
                    "holds at the bottleneck rate while inventory collapses.",
            "targets": [
                {"label": "Bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
                 "goal": "≥ 10,000", "ok": lambda r: r["total_output"] >= 10000},
                {"label": "Average WIP", "get": lambda r: r["wip_L"], "fmt": "{:,.1f}",
                 "goal": "< 50", "ok": lambda r: r["wip_L"] < 50},
            ],
        },
        "check": lambda r: True,
    },
]




# =========================================================
# PART 2 CAPSTONE — "Diagnose & Fix: the money side". Three broken lines whose numbers look
# fine until you read the money: one is unprofitable, one is ordering stock all wrong, one is
# starving for lack of a buffer. Same three options every time → the student must discriminate.
# =========================================================
_DIAG2_OPTS = [
    "The line is losing money — the price doesn't cover its costs",
    "The order size is wrong — inventory cost is far above the minimum",
    "Stockouts — an unreliable supplier with too thin a buffer",
]


def _pl(r):
    cfg = r.get("config", {})
    dice = cfg.get("dice", [0] * N_OPS)
    sides = cfg.get("faces", [0] * N_OPS)
    return compute_financials(r, dice, sides, float(cfg.get("years", 1) or 1), get_fin())


def _rev_diag2_money(r):
    f = _pl(r)
    return ("warn", f"**Losing money.** The line runs fine, but the **profit is "
            f"${f['profit']:,.0f}** — a loss. Revenue of **${f['revenue']:,.0f}** doesn't cover "
            f"**${f['total_cost']:,.0f}** of cost: every bottle sells for less than it costs to make. "
            f"Service is 100% and the order size is sensible, so it isn't the supplier or the EOQ — "
            f"it's the **price**. Fix it by charging enough to clear the cost per bottle.")


def _rev_diag2_order(r):
    cfg = r.get("config", {})
    osz = int(cfg.get("order_size", 0))
    eoq = r.get("eoq_scan", {}).get("eoq")
    eoqtxt = f"**≈ {eoq:,.0f}**" if eoq else "much smaller"
    return ("warn", f"**Order size is wrong.** The line is profitable and fully served, but the "
            f"inventory bill is far above where it should be: you're ordering **{osz:,} bottles at a "
            f"time**, while the economic order quantity is {eoqtxt}. Too large an order piles up "
            f"holding cost; too small runs up ordering cost. The cost-vs-order-size curve below is a "
            f"**U** — slide the order size to the bottom of it.")


def _rev_diag2_stockout(r):
    svc = r["service_level"] * 100
    return ("warn", f"**Stockouts.** The tell is the **service level: {svc:.0f}%** — the line is "
            f"starved about **{100 - svc:.0f}%** of the time, so sales walk out the door. Profit and the "
            f"EOQ aren't the problem; an unreliable supplier plus a thin raw-material buffer is. Raise "
            f"the **reorder point** so a missed delivery doesn't empty the line.")


_DIAG2_INTRO = ("This is the Part 2 capstone. Each line below **looks like it's running** — bottles "
                "come out — but the *money* tells a different story. Read the **profit, the inventory "
                "cost curve, and the service level**, then name the problem from the three options and "
                "fix it. Nobody labels which lever is broken.")

LAB_DIAG2 = [
    # ---- Case 1: losing money (answer 0) ----
    {
        "icon": "🔬", "phase": "Case 1 — Diagnose",
        "title": "Line P sells plenty but bleeds cash",
        "intro": _DIAG2_INTRO + "\n\n**Line P:** output is healthy, yet the owner is going broke.",
        "setup": "Run it, read the Financial Results, then pick the cause.",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1,
                  "scrap_on": False, "reorder_point_on": False, "fin_order_size": 150,
                  "fin_revenue_per_unit": 0.50},
        "diagnose": True,
        "q": "What's wrong with Line P?",
        "opts": _DIAG2_OPTS, "answer": 0,
        "reflect": "In one sentence: which two numbers in the P&L told you the price was the problem?",
        "check": lambda r: abs(float(r.get("config", {}).get("revenue_per_unit", 3.0)) - 0.50) < 0.01,
        "reveal": _rev_diag2_money,
    },
    {
        "icon": "🔧", "phase": "Case 1 — Fix",
        "title": "Fix Line P",
        "intro": "**Your task:** get the line **back into profit (net profit above $0).** Open the "
                 "💰 Economics card and raise the price until each bottle more than pays for itself.",
        "setup": "Start: the same line, selling at just $0.50 a bottle.",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1,
                  "scrap_on": False, "reorder_point_on": False, "fin_order_size": 150,
                  "fin_revenue_per_unit": 0.50},
        "challenge": {
            "tries": 3, "focus": "fin_card",
            "hint": "Open the 💰 Economics card and raise the selling price. Watch the P&L flip from red "
                    "to black once the price clears the cost per bottle.",
            "targets": [
                {"label": "Net profit", "get": lambda r: _pl(r)["profit"], "fmt": "${:,.0f}",
                 "goal": "> $0", "ok": lambda r: _pl(r)["profit"] > 0},
            ],
        },
        "check": lambda r: True,
    },
    # ---- Case 2: stockouts (answer 2) ----
    {
        "icon": "🔬", "phase": "Case 2 — Diagnose",
        "title": "Line Q keeps running dry",
        "intro": "**Line Q:** priced right and ordering sensibly, but output and sales keep coming up "
                 "short. Run it and find the cause.",
        "setup": "Run it, read the service level, then pick the cause.",
        "apply": _ss_apply(5, rel=50, osz=10),
        "diagnose": True,
        "q": "What's wrong with Line Q?",
        "opts": _DIAG2_OPTS, "answer": 2,
        "reflect": "In one sentence: which single metric revealed that the problem was the supplier, "
                   "not the price or the order size?",
        "check": lambda r: r["config"].get("reorder_on") and r["config"].get("supply_reliability", 100) <= 60,
        "reveal": _rev_diag2_stockout,
    },
    {
        "icon": "🔧", "phase": "Case 2 — Fix",
        "title": "Fix Line Q",
        "intro": "**Your task:** get the **service level to 95% or better.** The supplier only delivers "
                 "half the time — build a buffer so a miss doesn't starve the line.",
        "setup": "Start: a 50%-reliable supplier and a reorder point of just 5.",
        "apply": _ss_apply(5, rel=50, osz=10),
        "challenge": {
            "tries": 3, "focus": "safety_card",
            "hint": "Raise the reorder point (🚚 card) so a cushion of raw material is always on hand "
                    "when a delivery is missed.",
            "targets": [
                {"label": "Service level", "get": lambda r: r["service_level"] * 100, "fmt": "{:,.0f}%",
                 "goal": "≥ 95%", "ok": lambda r: round(r["service_level"] * 100) >= 95},
            ],
        },
        "check": lambda r: True,
    },
    # ---- Case 3: order size wrong (answer 1) ----
    {
        "icon": "🔬", "phase": "Case 3 — Diagnose",
        "title": "Line R's inventory bill is enormous",
        "intro": "**Line R:** profitable and never starved, but the inventory cost is through the roof. "
                 "Run it, read the order-size cost curve, and find the cause.",
        "setup": "Run it, read the EOQ cost curve, then pick the cause.",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1,
                  "scrap_on": False, "reorder_point_on": False, "fin_order_size": 2000,
                  "fin_revenue_per_unit": 3.00},
        "diagnose": True,
        "q": "What's wrong with Line R?",
        "opts": _DIAG2_OPTS, "answer": 1,
        "reflect": "In one sentence: how did the cost-vs-order-size curve show the order size was far "
                   "from its sweet spot?",
        "check": lambda r: int(r["config"].get("order_size", 0)) >= 1000,
        "reveal": _rev_diag2_order,
    },
    {
        "icon": "🔧", "phase": "Case 3 — Fix",
        "title": "Fix Line R",
        "intro": "**Your task:** bring the **order size down to its economic sweet spot** — within about "
                 "100 bottles of the EOQ the curve points to.",
        "setup": "Start: ordering a wasteful 2,000 bottles at a time.",
        "apply": {**_line([1] * 6, [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1,
                  "scrap_on": False, "reorder_point_on": False, "fin_order_size": 2000,
                  "fin_revenue_per_unit": 3.00},
        "challenge": {
            "tries": 3, "focus": "eoqcost_card",
            "hint": "Lower the raw-material order size toward the bottom of the U-shaped cost curve — "
                    "the EOQ for this line is roughly 180.",
            "targets": [
                {"label": "Order size (bottles)", "get": lambda r: int(r["config"].get("order_size", 0)),
                 "fmt": "{:,} bottles", "goal": "within ≈ 100 of the EOQ",
                 "ok": lambda r: abs(int(r["config"].get("order_size", 0))
                                     - (r.get("eoq_scan", {}).get("eoq") or 184)) <= 100},
            ],
        },
        "check": lambda r: True,
    },
]


LABS = {
    "ops": {"title": "🧭 Operations Lab — the Five Focusing Steps", "steps": LAB_OPS,
            "closer": "That's the full cycle — Identify ▸ Exploit ▸ Subordinate ▸ Elevate ▸ Repeat."},
    "fin": {"title": "💰 Economics Lab — the P&L behind the line", "steps": LAB_FIN,
            "closer": "You've connected the line to the bottom line: cost structure, the die-size "
                      "sweet spot, inventory as cash, and supply & demand risk."},
    "ta": {"title": "📊 Throughput Accounting — T, I & OE", "steps": LAB_TA,
           "closer": "Net profit = T − OE, and ROI = (T − OE) ÷ I. Grow throughput, shrink inventory "
                     "and operating expense — and never mistake a busy non-constraint for money in the "
                     "bank."},
    "var": {"title": "🎰 Variability Lab — taming uncertainty", "steps": LAB_VAR,
            "closer": "Variability compounds at every stage — along the line, at the supplier, and "
                      "in the market. Reducing it anywhere buys throughput that raw capacity can't."},
    "eoq": {"title": "📦 Inventory Lab — the EOQ model & where it breaks", "steps": LAB_EOQ,
            "closer": "EOQ pins the order quantity in a steady world and forgives rough estimates — "
                      "but the moment supply turns unreliable, the cheapest order size climbs above "
                      "the formula's answer. The gap between them is your safety stock."},
    "eoqd": {"title": "🧮 EOQ Drivers — what sets the best order size", "steps": LAB_EOQD,
             "closer": "The best order quantity is EOQ = √(2·D·S ÷ H): it grows with demand and "
                       "ordering cost, shrinks with holding cost, and every effect is softened by the "
                       "square root — so measure the three drivers, plug in, and you're close."},
    "little": {"title": "⏱️ Little's Law — WIP, throughput & lead time", "steps": LAB_LITTLE,
               "closer": "Lead time is never free-floating: W = L ÷ λ. Shrink work-in-process or speed "
                         "up the line — those are the only two levers, and the law guarantees the rest."},
    "pull": {"title": "🔄 Pull vs. Push — capping WIP for free", "steps": LAB_PULL,
             "closer": "Push floods the line with inventory that buys nothing; pull caps WIP and lets "
                       "the bottleneck set the pace. Same throughput, a fraction of the inventory and "
                       "lead time — the core idea behind lean manufacturing and kanban."},
    "ss": {"title": "🚚 Safety Stock — reorder point & service level", "steps": LAB_SS,
           "closer": "EOQ says how much to order; the reorder point says when. Safety stock is the "
                     "cushion above lead-time demand that buys service against variability — raise it "
                     "to your target service level, then stop, because beyond that it's pure holding "
                     "cost."},
    "qual": {"title": "✅ Quality & Yield — when quality is capacity", "steps": LAB_QUAL,
             "closer": "A station's real capacity is its speed × its yield. A fast, low-yield station is "
                       "a hidden constraint — so measure the line by good output, and treat quality "
                       "improvement at the constraint as exactly what it is: more capacity."},
    "diag": {"title": "🔬 Capstone — Diagnose & Fix", "steps": LAB_DIAG,
             "closer": "That's the whole toolkit under one roof: read the symptoms, name the "
                       "constraint — bottleneck, yield, supply, or WIP — and reach for the matching "
                       "lever. Nobody labels the problem for you on a real line; now you can label it "
                       "yourself."},
    "diag2": {"title": "🔬 Part 2 Capstone — Diagnose & Fix: the money side", "steps": LAB_DIAG2,
              "closer": "Same discipline, now on the money: a line can run perfectly and still lose "
                        "cash, over-order, or starve. Read the profit, the inventory cost curve, and the "
                        "service level — and fix the one that's actually broken."},
}


# =========================================================
# End-of-lab CHALLENGES: an open design task with an auto-checked, pass/fail goal
# and a limited number of tries. Turns passive prediction into active design.
# =========================================================
def _chal_profit(r):
    cfg = r.get("config", {})
    return compute_financials(r, cfg.get("dice", []), cfg.get("faces", []),
                              cfg.get("years", 1), get_fin())["profit"]


def _chal_avg_raw(r):
    return statistics.mean(r.get("raw_series") or [0])


def _chal_eoq_cost(r):
    """(current ordering+holding cost at the chosen order size, cheapest possible)."""
    s = r.get("eoq_scan")
    if not s or not s.get("rows"):
        return (float("inf"), 1.0)
    q = r.get("config", {}).get("order_size")
    row = [x for x in s["rows"] if x["Q"] == q]
    cur = (row[0]["ordering"] + row[0]["holding"]) if row else float("inf")
    return (cur, s["best_total"])


def _nine(dice, faces=6):
    d = {}
    for i in range(N_OPS):
        d[f"capacity_{i}"] = dice[i] if i < len(dice) else 0
        d[f"sides_{i}"] = faces if (i < len(dice) and dice[i] > 0) else 0
    return d


LAB_CHALLENGES = {
    "ops": {
        "title": "Tame the constraint line",
        "goal": "**Your task:** redesign this line so it finishes **≥ 4,000 bottles/year** while keeping "
                "**average work-in-process (WIP) under 50 bottles**. Right now it makes the bottles but "
                "drowns in inventory.",
        "setup": "Start: Operation 4 slow (1 die × 3), everything else 1 die × 6, no WIP cap.",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "tries": 3,
        "hint": "Throughput is already set by the bottleneck, so adding capacity buys nothing — the fix "
                "is a WIP cap. Turn it on (🚧 in the sidebar) and set it low: output holds while WIP "
                "collapses, all without extra dice.",
        "targets": [
            {"label": "Bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
             "goal": "≥ 4,000", "ok": lambda r: r["total_output"] >= 4000},
            {"label": "Average WIP", "get": lambda r: r["wip_L"], "fmt": "{:,.1f}",
             "goal": "< 50", "ok": lambda r: r["wip_L"] < 50},
            {"label": "Total dice used", "get": lambda r: sum(r["config"]["dice"]), "fmt": "{:,.0f}",
             "goal": "≤ 7 — cap WIP, don't pad the line", "ok": lambda r: sum(r["config"]["dice"]) <= 7},
        ],
    },
    "little": {
        "title": "Cut the lead time",
        "goal": "**Your task:** get the average **lead time (W) under 24 hours** while keeping "
                "throughput **≥ 4,000 bottles/year**. Little's Law is your guide.",
        "setup": "Start: the same constraint line with no WIP cap — bottles crawl through in weeks.",
        "apply": {**_line([1] * 6, _S2_FACES), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "tries": 3,
        "hint": "W = L ÷ λ. Throughput (λ) is pinned by the bottleneck, so the lever is L — cap WIP low "
                "(around 10) and the lead time drops in proportion.",
        "targets": [
            {"label": "Lead time W", "get": lambda r: r["flow_time_measured"], "fmt": "{:,.0f} h",
             "goal": "< 24 h", "ok": lambda r: r["flow_time_measured"] < 24},
            {"label": "Throughput", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
             "goal": "≥ 4,000", "ok": lambda r: r["total_output"] >= 4000},
        ],
    },
    "pull": {
        "title": "Switch the push line to pull",
        "goal": "**Your task:** keep throughput **≥ 3,800 bottles/year** while getting **average WIP "
                "under 30**. The fast front end is flooding the line.",
        "setup": "Start: a fast front end (2 dice) feeding a slow bottleneck (Operation 4), no WIP cap.",
        "apply": {**_line([2, 2, 2, 1, 2, 2], _S2_FACES), "wip_limit_on": False,
                  "supply_reliability": 100, "demand_variable": False, "starting_inventory": 0,
                  "simulation_years": 1},
        "tries": 3,
        "hint": "Pushing more into the line can't beat the bottleneck. Cap WIP (around 8) so a station "
                "makes more only when there's room — throughput holds, inventory collapses.",
        "targets": [
            {"label": "Throughput", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
             "goal": "≥ 3,800", "ok": lambda r: r["total_output"] >= 3800},
            {"label": "Average WIP", "get": lambda r: r["wip_L"], "fmt": "{:,.1f}",
             "goal": "< 30", "ok": lambda r: r["wip_L"] < 30},
        ],
    },
    "var": {
        "title": "Beat the compounding of a long line",
        "goal": "**Your task:** get this **nine-station** line to finish **≥ 7,200 bottles/year**. "
                "Variability compounds down the chain, so it falls short as it stands.",
        "setup": "Start: nine balanced stations (1 die × 6 each), no starting buffers.",
        "apply": {**_nine([1] * 9), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "tries": 3,
        "hint": "Buffers absorb the swings that starve the next station. Add starting inventory in the "
                "Run settings card (a few hundred bottles) so no station sits idle waiting.",
        "targets": [
            {"label": "Bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
             "goal": "≥ 7,200", "ok": lambda r: r["total_output"] >= 7200},
        ],
    },
    "qual": {
        "title": "Fix the quality constraint",
        "goal": "**Your task:** get **good output ≥ 6,000 bottles/year** with overall **yield ≥ 90%**. "
                "A fast station is scrapping most of what it makes.",
        "setup": "Start: Station 3 runs 2 dice but scraps 60% — the hidden constraint.",
        "apply": _qual_apply([1, 1, 2, 1, 1, 1], {2: 60}),
        "tries": 3,
        "hint": "Speed can't help if the units don't survive. Lower Station 3's scrap rate in the "
                "Quality card until its yield stops throttling the whole line.",
        "targets": [
            {"label": "Good bottles finished", "get": lambda r: r["total_output"], "fmt": "{:,.0f}",
             "goal": "≥ 6,000", "ok": lambda r: r["total_output"] >= 6000},
            {"label": "Overall yield", "get": lambda r: r["yield_rate"] * 100, "fmt": "{:,.0f}%",
             "goal": "≥ 90%", "ok": lambda r: round(r["yield_rate"] * 100) >= 90},
        ],
    },
    "fin": {
        "title": "Turn a profit",
        "goal": "**Your task:** design a line that earns **annual profit ≥ \\$4,000**. The current tiny "
                "line barely covers its costs.",
        "setup": "Start: six stations of 1 die × 2 — too little output to pay for the line.",
        "apply": {**_line([1] * 6, [2] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "tries": 3,
        "hint": "Profit peaks at a middle die size: big enough to sell real volume, small enough that the "
                "capital doesn't eat the margin. Try dice around 6–8 faces.",
        "targets": [
            {"label": "Annual profit", "get": _chal_profit, "fmt": "${:,.0f}",
             "goal": "≥ $4,000", "ok": lambda r: _chal_profit(r) >= 4000},
        ],
    },
    "ta": {
        "title": "Stop the local-efficiency bleed",
        "goal": "**Your task:** get **annual profit ≥ \\$4,000**. A feeder station is running flat-out and "
                "burying the line in costly inventory.",
        "setup": "Start: Operation 1 runs 3 dice (way faster than the rest) — busy, but losing money.",
        "apply": {**_line([3, 1, 1, 1, 1, 1], [6] * 6), "wip_limit_on": False, "supply_reliability": 100,
                  "demand_variable": False, "starting_inventory": 0, "simulation_years": 1},
        "tries": 3,
        "hint": "A non-constraint running hard only makes inventory (Investment and Operating Expense), "
                "not throughput. Subordinate it — slow Operation 1 back to match the line.",
        "targets": [
            {"label": "Annual profit", "get": _chal_profit, "fmt": "${:,.0f}",
             "goal": "≥ $4,000", "ok": lambda r: _chal_profit(r) >= 4000},
        ],
    },
    "eoqd": {
        "title": "Order at the sweet spot",
        "goal": "**Your task:** choose an order size whose **ordering + holding cost is within 10% of the "
                "cheapest possible**. You're currently ordering in tiny, costly batches.",
        "setup": "Start: order size 10 — hardly any holding, but a fortune in ordering cost.",
        "apply": {**_eoqd_apply([1] * 6, 25.0, 0.04), "fin_order_size": 10},
        "tries": 3,
        "hint": "Use EOQ = √(2·D·S ÷ H) — around 180 here. Set the order size near it — the "
                "raw-material order size (📦, in the Variability card); the cost curve is flat, so you don't have to be exact.",
        "targets": [
            {"label": "Ordering + holding cost", "get": lambda r: _chal_eoq_cost(r)[0], "fmt": "${:,.0f}",
             "goal": "within 10% of best", "ok": lambda r: _chal_eoq_cost(r)[0] <= _chal_eoq_cost(r)[1] * 1.10},
        ],
    },
    "eoq": {
        "title": "Order at the sweet spot",
        "goal": "**Your task:** choose an order size whose **ordering + holding cost is within 10% of the "
                "cheapest possible**. You're currently ordering in tiny, costly batches.",
        "setup": "Start: order size 10 — hardly any holding, but a fortune in ordering cost.",
        "apply": _eoq_apply(10, 100),
        "tries": 3,
        "hint": "Use EOQ = √(2·D·S ÷ H) — around 180 here. Set the order size near it — the "
                "raw-material order size (📦, in the Variability card); the cost curve is flat, so you don't have to be exact.",
        "targets": [
            {"label": "Ordering + holding cost", "get": lambda r: _chal_eoq_cost(r)[0], "fmt": "${:,.0f}",
             "goal": "within 10% of best", "ok": lambda r: _chal_eoq_cost(r)[0] <= _chal_eoq_cost(r)[1] * 1.10},
        ],
    },
    "ss": {
        "title": "Right-size the safety stock",
        "goal": "**Your task:** reach a **service level ≥ 95%** while keeping **average raw inventory "
                "under 80 bottles**. Too little starves the line; too much wastes cash.",
        "setup": "Start: a 50%-reliable supplier and a reorder point of just 5 — the line starves.",
        "apply": _ss_apply(5),
        "tries": 3,
        "hint": "Raise the reorder point (🚚 card) to build a safety cushion, but stop once service hits "
                "the target — pushing it much past the sweet spot just piles up idle raw inventory.",
        "targets": [
            {"label": "Service level", "get": lambda r: r["service_level"] * 100, "fmt": "{:,.0f}%",
             "goal": "≥ 95%", "ok": lambda r: round(r["service_level"] * 100) >= 95},
            {"label": "Average raw inventory", "get": _chal_avg_raw, "fmt": "{:,.0f}",
             "goal": "< 80", "ok": lambda r: _chal_avg_raw(r) < 80},
        ],
    },
}

# The sidebar card each challenge asks the student to change — used to scroll the sidebar to
# that control (and briefly flash it) when the challenge opens, so the student knows where to
# make the change.
_LAB_CHAL_FOCUS = {
    "ops": "wip_card", "little": "wip_card", "pull": "wip_card", "var": "settings_card",
    "qual": "quality_card", "fin": "ops_card", "ta": "ops_card", "eoqd": "var_card",
    "eoq": "var_card", "ss": "safety_card",
}
for _p, _f in _LAB_CHAL_FOCUS.items():
    if _p in LAB_CHALLENGES:
        LAB_CHALLENGES[_p]["focus"] = _f

# Capstone fix steps (the diagnose→fix pairs) each point at the lever that fixes that case.
_DIAG_FIX_FOCUS = {1: "ops_card", 3: "safety_card", 5: "quality_card", 7: "wip_card"}
for _i, _f in _DIAG_FIX_FOCUS.items():
    if _i < len(LAB_DIAG) and isinstance(LAB_DIAG[_i].get("challenge"), dict):
        LAB_DIAG[_i]["challenge"]["focus"] = _f

# Append each challenge as the final step of its lab (so it joins the road map, the
# navigation, and the progress tracker automatically).
for _pre, _c in LAB_CHALLENGES.items():
    if _pre in LABS:
        LABS[_pre]["steps"].append({
            "icon": "🏁", "phase": "Challenge",
            "title": _c["title"], "intro": _c["goal"], "setup": _c["setup"],
            "apply": _c["apply"], "challenge": _c, "check": lambda r: True,
        })


# =========================================================
# Distractor-specific feedback: for each wrong multiple-choice option, a one-line
# note on why it's tempting and why it's wrong. Keyed by lab prefix → step index →
# option index (only wrong options appear). Correct options are handled by the reveal.
# =========================================================
LAB_DISTRACTORS = {
    "ops": {
        1: {0: "More capacity? No — a longer chain gives fluctuation more places to compound, so it "
               "keeps a *smaller* share of its average.",
            1: "Length matters: more dependent stations means more chances to starve, so it keeps less.",
            3: "It still finishes plenty of bottles — just a smaller fraction of the average."},
        2: {0: "Operation 2 is fast, so inventory doesn't wait there — it banks up in front of the "
               "*slow* station.",
            2: "The end station is fed only a trickle; nothing piles up there. Look in front of the "
               "slowest station.",
            3: "Inventory doesn't spread evenly — it collects in front of the constraint."},
        3: {0: "Operation 2 isn't the constraint, so making it faster just feeds the bottleneck more — "
               "output barely moves.",
            1: "Speeding up a non-constraint adds almost nothing; the slow station still caps the line.",
            3: "It won't fall — you've only added WIP in front of the bottleneck, not removed capacity."},
        4: {1: "If throughput held, WIP wouldn't also collapse — capping WIP cuts inventory *without* "
               "costing output.",
            2: "WIP goes *down*, not up — the cap is what limits it, while throughput stays put.",
            3: "Plenty changes: same output, but far less inventory and much shorter lead time."},
        5: {0: "A cap of 2 is *too* tight — it starves the constraint, so throughput does drop.",
            2: "Squeezing WIP can't raise output above the bottleneck; too tight actually lowers it.",
            3: "WIP falls with the cap — the real effect is the constraint being starved."},
        6: {1: "Once you speed up Operation 4 it's no longer slowest, so the constraint *moves* to "
               "whatever is now slowest.",
            2: "It's a real change: you added capacity at the *constraint*, so throughput jumps.",
            3: "Adding capacity at the bottleneck raises output — it doesn't lower it."},
        7: {0: "Operation 1 isn't the constraint any more, so adding a die there barely helps.",
            2: "It won't fall — you've just added unused capacity away from the constraint.",
            3: "The constraint doesn't jump back to Operation 4 — it already has spare capacity."},
    },
    "little": {
        0: {0: "The law isn't an overestimate — W = L ÷ λ lands right on the measured lead time.",
            1: "It's not too low either; the prediction matches closely.",
            3: "They're tightly linked — that's the whole point of Little's Law."},
        1: {0: "Less WIP can't *raise* lead time — with fewer bottles waiting, each one clears faster.",
            1: "It changes a lot: cut L and W falls with it (W = L ÷ λ).",
            3: "Throughput holds here — the cap of 20 is still above what the line needs."},
        2: {0: "There *is* a downside at a cap of 5 — it's now below what the line needs, so throughput "
               "drops.",
            2: "Tighter WIP lowers lead time; it never raises it.",
            3: "Little's Law still holds — it always does; throughput (λ) simply fell too."},
        3: {0: "Faster flow can't lengthen lead time — with λ up and L capped, W = L ÷ λ falls.",
            2: "W depends on λ: raise λ with L fixed and W must drop.",
            3: "It falls, but not to zero — bottles still take some time to clear."},
        4: {0: "More WIP *raises* lead time — the opposite of what you want.",
            2: "Lowering throughput would *lengthen* lead time (W = L ÷ λ).",
            3: "Two levers do work: cut WIP or raise throughput — the law guarantees it."},
    },
    "pull": {
        0: {0: "With nothing capping it, the fast front end keeps pushing — inventory can't stay near "
               "zero.",
            1: "It won't settle small; with no cap, WIP grows without bound in front of the bottleneck.",
            3: "WIP doesn't track the bottleneck rate — it just keeps climbing."},
        1: {0: "Throughput won't collapse — the bottleneck still runs full; you've only capped the "
               "*waiting* inventory.",
            2: "Capping WIP can't raise output above the bottleneck — it stays the same.",
            3: "It stays steady, not erratic; the constraint still paces the line."},
        2: {0: "A looser cap doesn't raise output — the bottleneck still sets it; only WIP and lead "
               "time grow.",
            1: "It won't fall; you've added slack, not removed capacity.",
            3: "Throughput holds; loosening the cap just lets more inventory sit."},
        3: {0: "Total WIP allowed sets your *inventory*, not your throughput.",
            1: "The fastest station has spare capacity — it never limits the line.",
            3: "Order size is an inventory lever, not what caps output."},
    },
    "var": {
        0: {0: "More stations means more compounding, so the longer line keeps a *lower* share.",
            2: "It's not the same — each added dependent station drags the percentage down.",
            3: "Length very much matters; the 9-station line loses a bigger slice."},
        1: {0: "The jumpy line (1×11) loses more to fluctuation — the steady one finishes more.",
            2: "Same average, but *not* the same output — lower spread means higher throughput.",
            3: "It's the spread, not the station count, that decides it here."},
        2: {1: "Buffers don't slow the line — they absorb swings so downstream stations stay fed.",
            2: "There is a change: starting buffers lift throughput.",
            3: "Buffers help, but they don't make it perfectly efficient — fluctuation remains."},
        3: {1: "An unreliable supplier can't *raise* output — it starves Operation 1.",
            2: "It's not only inventory — with no buffer, missed deliveries stop the line and cut "
               "output.",
            3: "The line can't catch up from a hard stop; throughput falls."},
        4: {1: "A fluctuating market means demand dips — so not everything sells, and goods pile up.",
            2: "Facing variable demand doesn't raise throughput; it strands output as inventory.",
            3: "Plenty changes: unsold goods accumulate *and* some orders are missed."},
        5: {1: "Independent variability sources don't cancel — they stack up.",
            2: "Three sources are worse than one — the losses compound.",
            3: "Compounding variability hurts the bottom line; it doesn't help it."},
    },
    "qual": {
        0: {0: "Fast doesn't mean bottleneck — with perfect quality, Station 3 has spare capacity.",
            2: "WIP isn't the issue here; with good yield the fast station simply isn't the constraint.",
            3: "Demand doesn't decide it; at full quality Station 3 has room to spare."},
        1: {0: "Speed only helps if the units survive — you're counting attempts, not good bottles.",
            1: "Scrapping can't raise output — good bottles per hour fall.",
            3: "It won't double — losing 60% to scrap collapses the line's good output."},
        2: {0: "Station 1 isn't the constraint — speeding it up can't rescue a line choked at "
               "Station 3.",
            2: "It won't drop further; you just added unused speed away from the constraint.",
            3: "Fixing a non-constraint can't double output — the yield problem still caps it."},
        3: {0: "Yield *is* the constraint here — fixing it is exactly what lifts throughput.",
            2: "Better yield raises good output; it can't lower it.",
            3: "It doesn't stay low — cutting scrap frees the good bottles the line was losing."},
        4: {0: "Nominal speed alone overstates capacity — scrap steals part of it.",
            2: "WIP isn't the point; effective capacity is speed × yield.",
            3: "Top speed doesn't matter if units fail quality — good output is what counts."},
    },
    "fin": {
        0: {0: "Every station adds cost, not just the last — the unit is worked about six times.",
            1: "It's not twice — production cost lands once at *each* of the six stations.",
            3: "WIP doesn't change the per-unit production cost; it's charged once per station."},
        1: {0: "A quarter is too low — fixed costs need roughly half the year's sales to cover.",
            2: "Three-quarters is too high — break-even sits near half of what the line sells.",
            3: "It clears break-even well before selling everything — around the halfway mark."},
        2: {0: "The cheapest dice are too slow — too little output to cover the fixed costs well.",
            2: "The biggest dice cost the most capital, and the extra output doesn't pay for it.",
            3: "They're not equal — profit peaks at a middle size that balances output and capital."},
        3: {1: "Capping WIP *lowers* holding cost — less inventory sitting around — so profit rises.",
            2: "There is a change: less tied-up inventory means higher profit.",
            3: "Profit improves, not falls — you shed holding cost without losing output."},
        4: {1: "A big buffer absorbs the misses — output barely dips, it isn't halved.",
            2: "The line doesn't stop — the raw buffer keeps Operation 1 fed through missed "
               "deliveries.",
            3: "It's not only finished goods — the buffer protects *output* from the shaky supplier."},
        5: {0: "More bottles made isn't more profit if the market can't absorb them — they become "
               "costly inventory.",
            2: "There's a big effect: unsold output piles up as holding cost.",
            3: "Revenue is capped by what sells; the extra just adds cost."},
        6: {1: "Matching the market doesn't cost you sales — you sell about the same with far less "
               "dead stock.",
            2: "There is a change: trimming the overbuild cuts inventory cost and lifts profit.",
            3: "It cuts dead inventory, but it doesn't erase every lost sale."},
    },
    "ta": {
        0: {0: "It's not higher — T − OE is just the P&L profit rearranged, so it matches exactly.",
            1: "Nor lower — same profit, different bookkeeping.",
            3: "It's tightly related — identical, in fact."},
        1: {0: "'Looks busy' isn't money — a non-constraint running flat-out just makes inventory and "
               "expense.",
            1: "It doesn't stay flat — profit *falls* as inventory and expense climb.",
            3: "Output at a non-constraint can't double profit; it erodes it."},
        2: {1: "ROI rises, not falls — less money tied up for the same throughput.",
            2: "It changes: cutting Investment with steady throughput lifts ROI.",
            3: "ROI improves; it doesn't turn negative."},
        3: {0: "Real throughput gains turn into profit — it rises, not falls.",
            2: "It doesn't stay flat when *true* throughput T grows.",
            3: "Growing throughput lifts profit; it won't turn negative."},
        4: {0: "Maxing out every station just piles up inventory — that's the local-efficiency trap.",
            2: "Minimizing throughput is the opposite of making money.",
            3: "More machines only help if they lift the *constraint*."},
    },
    "eoqd": {
        0: {0: "It's not a straight line — total cost dips to a minimum, then climbs (a U).",
            2: "It doesn't just fall — order too much and holding cost takes over.",
            3: "It's not flat — there's a clear cheapest point in the middle."},
        1: {0: "More demand raises the EOQ, not lowers it.",
            1: "It rises, but the square root softens it — about 1.4×, not double.",
            3: "Demand definitely moves the EOQ — upward."},
        2: {0: "Pricier orders push you to order *more* each time (fewer orders), so the EOQ rises.",
            2: "It does change — a higher ordering cost lifts the EOQ.",
            3: "It won't go to zero; you'd order larger, not stop ordering."},
        3: {0: "Costlier holding pushes the EOQ *down* — keep less on hand and order more often.",
            2: "It changes: a higher holding cost shrinks the EOQ.",
            3: "Holding cost is the H in the formula — it very much affects the EOQ."},
        4: {0: "The two effects offset — doubling demand pulls up, doubling holding pulls down — so "
               "the EOQ barely moves.",
            1: "Not much lower either — the opposing effects roughly cancel.",
            3: "It's predictable: the two square-root effects cancel to about the same EOQ."},
        5: {0: "It won't double — the cost curve is flat near the bottom, so being off costs little.",
            2: "There is a penalty, just a small one — the curve isn't perfectly flat.",
            3: "Ordering off the optimum can't be *cheaper* than the optimum."},
        6: {0: "Both changes push the EOQ *up* — more ordering cost and less holding cost each raise "
               "it.",
            2: "It moves — both drivers lift the EOQ.",
            3: "No down-then-up wobble; both effects point the same way (up)."},
    },
    "eoq": {
        0: {0: "They don't both fall — order more and holding cost climbs even as ordering cost drops.",
            1: "Not both rise — they trade off, one up while the other goes down.",
            3: "Order size matters a great deal — it's the whole trade-off."},
        2: {0: "At the EOQ they're balanced, not ordering-heavy — that balance is why it's cheapest.",
            1: "Nor holding-heavy — the two costs are about equal at the optimum.",
            3: "Neither is zero — the EOQ *balances* the two costs, it doesn't erase them."},
        3: {0: "EOQ assumes reliable supply — a shaky supplier adds stockout costs it never modeled.",
            2: "It raises cost, not lowers it — stockouts are expensive.",
            3: "An unreliable supplier can't speed the line up — it starves it."},
        4: {0: "Here the extra stock *pays* — it prevents stockouts that cost more than the holding.",
            2: "Not the same — with unreliable supply the bigger order wins.",
            3: "They're comparable — and the buffer (250) comes out cheaper."},
        5: {0: "JIT isn't strictly best — tiny orders mean a huge ordering cost and fragility.",
            2: "It doesn't trade throughput for inventory — it trades holding cost for ordering cost.",
            3: "Quality and speed aren't the trade here; it's holding versus ordering cost."},
    },
    "ss": {
        1: {0: "More safety stock can't *lower* service — a fatter cushion means fewer stockouts.",
            2: "It changes a lot — service climbs sharply with the bigger buffer.",
            3: "It rises toward 100%, nowhere near zero."},
        2: {1: "40 is enough — the cushion now covers the misses, so service is near 100%.",
            2: "It's higher than at 15 — more safety stock buys more service.",
            3: "It rises with the reorder point; it doesn't fall."},
        3: {0: "Service can't exceed 100% — the extra stock buys nothing but holding cost.",
            2: "It won't lower service — it just parks more raw inventory.",
            3: "It *raises* holding cost (more inventory sitting around); it doesn't cut it."},
        4: {0: "As high as possible wastes cash — stop once the variability is covered.",
            2: "As low as possible starves the line — you need enough to cover the swings.",
            3: "It's tied to the variability you're covering, not to the order size."},
    },
    "diag": {
        0: {1: "Check the scrap count and yield — this line's yield is 100%, nothing is being thrown "
               "away. It's not a quality problem.",
            2: "Service is 100% here — raw material is flowing fine. The supplier isn't the issue.",
            3: "Output is *low*, not fine — this isn't just excess inventory. A station is genuinely "
               "too slow to keep up."},
        2: {0: "No single station is the slow point here, and output recovers the moment supply is "
               "steady — the bottleneck is upstream of the line, at the supplier.",
            1: "Yield is 100% — nothing is being scrapped. Look at the service level instead.",
            3: "WIP is actually low here — the line is *starved*, not flooded. The problem is too "
               "little material, not too much."},
        4: {0: "The station in question runs *fast* (2 dice) — raw speed isn't the limit. Look at how "
               "many units survive it.",
            2: "Service is 100% — material is flowing. The loss is happening *inside* the line, not at "
               "the supplier.",
            3: "WIP is modest here — inventory isn't the problem. Check the scrap and yield numbers."},
        6: {0: "Look again at the output — it's high, not low. More capacity wouldn't help; this is an "
               "inventory problem, not a throughput one.",
            1: "Yield is 100% — nothing is being scrapped. The mountain of stock comes from over-"
               "pushing, not quality.",
            2: "Service is 100% — supply is fine. The inventory is piling up *inside* the line, not "
               "waiting on the supplier."},
    },
    "diag2": {
        0: {1: "The order size (150) is sensible and the inventory cost is normal — look at the price "
               "against the cost per bottle instead.",
            2: "Service is 100% — the supplier is fine. The loss is in the price, not the buffer."},
        2: {0: "At full service this line is profitable — the trouble is it *isn't* at full service. "
               "Read the service level.",
            1: "The order size isn't the problem here; the line is simply starved. Look at the service "
               "level."},
        4: {0: "The line is profitable — more so once the ordering is fixed. It isn't the price.",
            2: "Service is 100% — the supplier is fine. The waste is in how much you buy per order."},
    },
}


def render_estimate_line(guess, actual, tol, unit=""):
    """A small horizontal number line showing the student's estimate against the actual
    value and the ±tolerance 'good' band. Solid fills only (st.html strips gradients)."""
    W, H = 560, 96
    lo = min(guess, actual * (1 - tol))
    hi = max(guess, actual * (1 + tol))
    span = (hi - lo) or (abs(actual) or 1.0)
    pad = span * 0.18
    lo -= pad
    hi += pad
    span = (hi - lo) or 1.0
    ml, mr = 46, 46
    plot = W - ml - mr

    def xp(v):
        return ml + (v - lo) / span * plot

    axis_y = 58
    band_x1, band_x2 = xp(actual * (1 - tol)), xp(actual * (1 + tol))
    ax = xp(actual)
    gx = xp(guess)
    hit = abs(guess - actual) <= abs(actual) * tol
    gcol = "#16a34a" if hit else "#ea580c"

    def fmt(v):
        return f"{v:,.0f}"

    svg = f'''<svg viewBox="0 0 {W} {H}" width="100%" height="{H}"
      xmlns="http://www.w3.org/2000/svg" style="max-width:{W}px">
      <rect x="{band_x1:.1f}" y="{axis_y - 12}" width="{max(2, band_x2 - band_x1):.1f}" height="24"
            fill="#dcfce7" stroke="#16a34a" stroke-width="1"/>
      <line x1="{ml}" y1="{axis_y}" x2="{W - mr}" y2="{axis_y}" stroke="#94a3b8" stroke-width="2"/>
      <line x1="{ax:.1f}" y1="{axis_y - 18}" x2="{ax:.1f}" y2="{axis_y + 18}"
            stroke="#0f172a" stroke-width="2.5"/>
      <text x="{ax:.1f}" y="{axis_y + 34}" text-anchor="middle" font-size="12"
            font-weight="800" fill="#0f172a">actual {fmt(actual)}{unit}</text>
      <circle cx="{gx:.1f}" cy="{axis_y}" r="7" fill="{gcol}" stroke="#fff" stroke-width="2"/>
      <text x="{gx:.1f}" y="{axis_y - 24}" text-anchor="middle" font-size="12"
            font-weight="800" fill="{gcol}">you {fmt(guess)}{unit}</text>
    </svg>'''
    return svg


def _md_escape(s):
    """Streamlit's markdown renders text between two '$' signs as LaTeX math, which throws
    away the spaces and jams the words together. Escaping every '$' as '\\$' makes dollar
    amounts render as ordinary text with their spacing intact."""
    return s.replace("$", "\\$") if isinstance(s, str) else s


def _lab_html(s):
    """Lab text (intro/setup/title/prompts) is rendered inside styled HTML <div>s, where
    Streamlit does NOT process markdown — so **bold** showed literal asterisks and \\$ showed a
    literal backslash. This escapes HTML, turns **bold** into <b>, and leaves $ as a plain
    dollar sign (no LaTeX risk inside raw HTML)."""
    if not isinstance(s, str):
        return s
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w\\])\*(?!\s)(.+?)(?<!\s)\*(?![\w])", r"<i>\1</i>", s)
    return s


def _render_reflect(prefix, i, prompt):
    """Free-text self-explanation after a key reveal. The act of generating the sentence is
    what does the learning; what the student writes is saved into their submission report."""
    st.markdown('<div class="reflect-q">✍️ <b>Explain it:</b> ' + _lab_html(prompt) + "</div>",
                unsafe_allow_html=True)
    st.text_area("Your explanation", key=f"{prefix}_reflect_{i}",
                 label_visibility="collapsed",
                 placeholder="In your own words — one or two sentences…")
    # An explicit button to accept the text (clicking it commits what's typed) so students don't
    # have to discover Ctrl+Enter.
    st.button("Save my answer", key=f"{prefix}_reflect_save_{i}")
    txt = (st.session_state.get(f"{prefix}_reflect_{i}") or "").strip()
    if txt:
        mark_reflect_done(prefix, i)
        st.caption("✓ Saved — it will appear in your report. Putting it in your own words is what "
                   "makes the idea stick.")
    else:
        st.caption("Type a sentence and press **Save my answer** (or Ctrl+Enter). It's included in "
                   "your report, so it's worth doing.")


def _render_challenge(prefix, i, challenge, results):
    """Open design task with an auto-checked, pass/fail goal and limited tries. Returns True
    once the challenge is resolved (passed, or out of tries)."""
    tries = challenge.get("tries", 3)
    attempts = st.session_state.get(f"{prefix}_chal_attempts_{i}", 0)
    passed = st.session_state.get(f"{prefix}_chal_passed_{i}", False)
    seen = st.session_state.get(f"{prefix}_chal_seen_{i}", st.session_state.get("run_counter", 0))
    cur = st.session_state.get("run_counter", 0)
    have = bool(results) and "config" in results
    resolved = passed or attempts >= tries
    # Challenges are graded on the full-year horizon they were written and tuned for. A short
    # run (one shift, one week) swings far too much for a pass/fail threshold to mean anything
    # — over one shift a balanced line's throughput varies by ~160% run to run — so a short run
    # is shown on the scoreboard but never consumes a try or scores a pass.
    run_horizon = (results or {}).get("config", {}).get("horizon", HORIZON_YEAR) if have else HORIZON_YEAR
    graded_run = HORIZONS.get(run_horizon) is None

    # A fresh run (while still unresolved) counts as one attempt and is scored.
    if have and graded_run and cur != seen and not resolved:
        st.session_state[f"{prefix}_chal_seen_{i}"] = cur
        attempts += 1
        st.session_state[f"{prefix}_chal_attempts_{i}"] = attempts
        if all(t["ok"](results) for t in challenge["targets"]):
            st.session_state[f"{prefix}_chal_passed_{i}"] = True
            passed = True
        if passed or attempts >= tries:
            mark_step_done(prefix, i)
        resolved = passed or attempts >= tries

    st.markdown('<div class="chal-inst">Redesign the line with the controls in the sidebar, then press '
                '<b>▶ Run this line</b> in the 🚀 Run card. Each run is one try.</div>',
                unsafe_allow_html=True)
    if have and not graded_run:
        st.info(f"That run was **{run_horizon}**, so it didn't use a try. Short runs are great "
                "for *seeing* variability, but they're too noisy to judge a design on — set "
                f"**Run length** back to **{HORIZON_YEAR}** in the sidebar to take a scored try.")

    rows = []
    for t in challenge["targets"]:
        if have:
            ok = t["ok"](results)
            val = t["fmt"].format(t["get"](results))
            icon, cls = ("✓", "chal-ok") if ok else ("✗", "chal-no")
        else:
            val, icon, cls = "—", "•", "chal-idle"
        rows.append(f'<div class="chal-row {cls}"><span class="chal-ic">{icon}</span>'
                    f'<span class="chal-lab">{_md_escape(t["label"])}</span>'
                    f'<span class="chal-goal">goal: {_md_escape(t["goal"])}</span>'
                    f'<span class="chal-val">{_md_escape(val)}</span></div>')
    st.markdown('<div class="chal-board">' + "".join(rows) + "</div>", unsafe_allow_html=True)

    used = min(attempts, tries)
    st.markdown(f'<div class="chal-tries">Tries used: <b>{used} of {tries}</b></div>',
                unsafe_allow_html=True)

    if passed:
        st.success(f"🏆 **Challenge passed** in {attempts} "
                   f"{'try' if attempts == 1 else 'tries'}! You designed a line that meets the goal.")
    elif resolved:
        st.warning("**Out of tries on this one.** Here's the idea: " + _md_escape(challenge.get("hint", ""))
                   + " Press **Reset & try again** to have another go.")
    elif have and attempts > 0:
        st.info("**Not there yet** — check the ✗ rows above. Hint: " + _md_escape(challenge.get("hint", "")))
    else:
        st.caption("Adjust the line in the sidebar and run it to see how you score against the goal.")

    st.button("↺  Reset & try again", use_container_width=True,
              on_click=lab_apply_setup, args=(prefix, i), kwargs={"force_reset": True},
              key=f"{prefix}_chalreset_{i}")
    return resolved


def _lab_nav_cb(fn, *args):
    """Button callback inside the lab fragment.

    Do the work, then ask for a full-app rerun. The panel itself is a fragment so that
    answering a question (radio, estimate, reflection) redraws only the panel instead of
    re-executing the whole script — but navigation, "set up & run" and the sidebar-focus
    jump all change things that live *outside* the fragment (the sidebar's configuration,
    the dashboard, the scroll anchor), so those have to escalate to an app-wide rerun."""
    fn(*args)
    st.session_state["_lab_frag_app_rerun"] = True


@st.fragment
def render_lab(results, prefix):
    """Directed exercise panel: predict → run → reveal, gated on answering."""
    # Set only by _lab_nav_cb, so an ordinary app rerun can never trip it (which would loop).
    if st.session_state.pop("_lab_frag_app_rerun", False):
        st.rerun(scope="app")
    lab = LABS[prefix]
    steps = lab["steps"]
    i = st.session_state[f"{prefix}_step"]
    step = steps[i]
    with st.container(border=True, key="lab_card"):
        st.markdown(f'<div class="card-title">{lab["title"]}</div>', unsafe_allow_html=True)
        chips = []
        for j, s in enumerate(steps):
            cls = "lab-chip lab-chip-on" if j == i else (
                "lab-chip lab-chip-done" if j < i else "lab-chip")
            chips.append(f'<div class="{cls}">{s["icon"]} {s["phase"]}</div>')
        st.markdown(f'<div class="lab-road">{"".join(chips)}</div>', unsafe_allow_html=True)

        st.markdown(f'<div class="lab-phase">{step["icon"]} {step["phase"]}</div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="lab-h">{_lab_html(step["title"])}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="lab-intro">{_lab_html(step["intro"])}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="lab-setup"><b>The setup:</b> {_lab_html(step["setup"])}</div>',
                    unsafe_allow_html=True)
        # Anchor + button: the button jumps the sidebar to the setup controls; the anchor is
        # where the view lands after a run, so the fresh result sits just below it.
        st.markdown('<div id="jcc-runtop" style="scroll-margin-top: 5rem;"></div>',
                    unsafe_allow_html=True)
        st.button("🎛️  Go to the setup controls in the sidebar  ↙",
                  key=f"{prefix}_focus_{i}", on_click=_lab_nav_cb, args=(_focus_sidebar_cb,),
                  use_container_width=True)

        # Play the "line running" animation here, inside the lab panel, after any run in this
        # lab (predict/estimate steps run from the button below; challenge steps run from the
        # sidebar). Consuming it before the branch guarantees it always fires exactly once and
        # is seen — the main-window playback would be above the panel and off-screen.
        _lab_anim = st.session_state.pop("_lab_anim", None)
        if _lab_anim and results:
            _frames, _delay = _lab_anim
            _anim_ph = st.empty()
            for _fr in _frames:
                _anim_ph.html(build_live_dashboard(_fr, results))
                time.sleep(_delay)
            _anim_ph.empty()

        challenge = step.get("challenge")
        if challenge:
            answered = _render_challenge(prefix, i, challenge, results)
            _lock_msg = ("Meet the goal above to unlock the next step — each run counts as one of "
                         "your three tries, so you can move on once you pass or run out of tries.")
        else:
            est_cfg = step.get("estimate")
            if est_cfg:
                # Pre-seed via session_state (set on restore / prior entry); only pass an explicit
                # default the FIRST time the key is absent, so Streamlit doesn't warn and a saved
                # answer is shown on return / after a refresh.
                _ek = f"{prefix}_est_{i}"
                _ekw = {} if _ek in st.session_state else {"value": None}
                st.number_input(f"**Estimate:** {_md_escape(est_cfg['prompt'])}",
                                min_value=float(est_cfg.get("min", 0.0)),
                                max_value=float(est_cfg["max"]) if est_cfg.get("max") is not None else None,
                                step=float(est_cfg.get("step", 1.0)),
                                key=_ek, on_change=_on_answer_change,
                                placeholder="type your best estimate", **_ekw)
                if est_cfg.get("hint"):
                    st.caption("💡 " + _md_escape(est_cfg["hint"]))
            else:
                _pk = f"{prefix}_pred_{i}"
                _opts = [_md_escape(o) for o in step["opts"]]
                # Drop a stale saved answer that isn't one of this step's options (e.g. from an
                # older version) so the radio can't crash on an out-of-range value.
                if _pk in st.session_state and st.session_state[_pk] not in _opts:
                    del st.session_state[_pk]
                _rkw = {} if _pk in st.session_state else {"index": None}
                st.radio(f"**Predict:** {_md_escape(step['q'])}", _opts,
                         key=_pk, on_change=_on_answer_change, **_rkw)
            st.button("▶  Set up & run this step", type="primary", use_container_width=True,
                      on_click=_lab_nav_cb, args=(lab_setup_and_run, prefix),
                      key=f"{prefix}_run_{i}")

            matched = bool(results) and "config" in results
            if matched:
                try:
                    matched = step["check"](results)
                except Exception:
                    matched = False
            if matched:
                if est_cfg:
                    val = st.session_state.get(f"{prefix}_est_{i}")
                    if val is not None:
                        mark_step_done(prefix, i)
                        actual = float(est_cfg["actual"](results))
                        tol = float(est_cfg.get("tol", 0.12))
                        unit = est_cfg.get("unit", "")
                        off = (abs(val - actual) / abs(actual) * 100.0) if actual else 0.0
                        if actual and abs(val - actual) <= abs(actual) * tol:
                            st.markdown(f'<div class="lab-fb lab-fb-ok">✓ Within {tol * 100:.0f}% of the '
                                        f'actual — nice estimate.</div>', unsafe_allow_html=True)
                        else:
                            hilo = "high" if val > actual else "low"
                            st.markdown(f'<div class="lab-fb lab-fb-no">Your estimate was about '
                                        f'{off:.0f}% {hilo}. Here is where it landed against the actual '
                                        f'value:</div>', unsafe_allow_html=True)
                        st.html(render_estimate_line(val, actual, tol, unit))
                else:
                    pred = st.session_state.get(f"{prefix}_pred_{i}")
                    if pred is not None:
                        mark_step_done(prefix, i)
                        # options are displayed dollar-escaped, so the returned value is escaped too
                        chosen = [_md_escape(o) for o in step["opts"]].index(pred)
                        if chosen == step["answer"]:
                            st.markdown('<div class="lab-fb lab-fb-ok">✓ Your prediction matched.</div>',
                                        unsafe_allow_html=True)
                        else:
                            why = LAB_DISTRACTORS.get(prefix, {}).get(i, {}).get(chosen)
                            why_html = (f'<div class="lab-fb-why">{_lab_html(why)}</div>') if why else ""
                            st.markdown(f'<div class="lab-fb lab-fb-no">You picked “{pred}”. {why_html}'
                                        f'<div class="lab-fb-then">Here is what actually happened:</div>'
                                        f'</div>', unsafe_allow_html=True)
                _diag = step.get("diagnose")
                if est_cfg:
                    _engaged = st.session_state.get(f"{prefix}_est_{i}") is not None
                    _show_reveal = True
                else:
                    _engaged = st.session_state.get(f"{prefix}_pred_{i}") is not None
                    _show_reveal = _engaged or not _diag
                if _show_reveal:
                    status, text = step["reveal"](results)
                    {"good": st.success, "warn": st.warning, "info": st.info}[status](_md_escape(text))
                    if step.get("reflect") and _engaged:
                        _render_reflect(prefix, i, step["reflect"])
                elif _diag:
                    st.info("👀 Study the dashboard below — **output, WIP, service level, and yield** — "
                            "then pick your diagnosis above.")

            # To advance you must both run the step and commit an answer. Build a plain-language
            # list of whatever's still missing so it's obvious what "Next" is waiting for.
            if est_cfg:
                _engaged = st.session_state.get(f"{prefix}_est_{i}") is not None
                _eng_todo = "<b>enter your estimate above</b>"
            else:
                _engaged = st.session_state.get(f"{prefix}_pred_{i}") is not None
                _eng_todo = ("<b>pick your diagnosis above</b>" if step.get("diagnose")
                             else "<b>make your prediction above</b>")
            answered = _engaged and matched
            _run_todo = "press <b>▶ Set up &amp; run this step</b>"
            _pairs = ([(_run_todo, not matched), (_eng_todo, not _engaged)] if step.get("diagnose")
                      else [(_eng_todo, not _engaged), (_run_todo, not matched)])
            _todo = [t for t, need in _pairs if need]
            _lock_msg = ("To continue, " + " then ".join(_todo) + ".") if _todo else None
        last = len(steps) - 1
        nav1, nav2, nav3 = st.columns([1, 1, 1])
        nav1.button("‹ Previous", use_container_width=True, disabled=(i == 0),
                    on_click=_lab_nav_cb, args=(lab_goto, prefix, i - 1),
                    key=f"{prefix}_prev_{i}")
        nav2.markdown(f'<div class="lab-count">Step {i + 1} of {len(steps)}</div>',
                      unsafe_allow_html=True)
        nav3.button("Next ›", use_container_width=True,
                    type=("primary" if (answered and i != last) else "secondary"),
                    disabled=(i == last or not answered),
                    on_click=_lab_nav_cb, args=(lab_goto, prefix, i + 1),
                    key=f"{prefix}_next_{i}")

        if i < last:
            if not answered and _lock_msg:
                st.markdown(f'<div class="lab-lock">🔒 <b>The next step is locked.</b> {_lock_msg}</div>',
                            unsafe_allow_html=True)
        else:
            # Final step. Once it's resolved, mark_step_done (above) has recorded it, so the
            # whole lab now counts as complete — confirm that and steer to what's next.
            if lab_is_complete(prefix):
                _part = _part_of_lab(prefix)
                if part_complete(_part):
                    st.success("🏆 **" + _part + " complete — outstanding work!** "
                               + _md_escape(lab["closer"]))
                    st.info("You've finished every lab in this part. Open **✅ Your progress** in the "
                            "sidebar and expand **📄 Get my report to submit** to download the report "
                            "to upload to this part's LMS assignment."
                            + ("  When you're ready, switch to **Part 2** at the top of the sidebar."
                               if _part == LAB_PART_ORDER[0] else ""))
                else:
                    st.success("🎉 **Lab complete!** " + _md_escape(lab["closer"]))
                    nxt = next_incomplete_lab(prefix)
                    if nxt:
                        st.caption("Pick another lab from **🧭 Choose a lab** in the sidebar — or jump "
                                   "straight to the next one in this part:")
                        st.button("▸  Next lab: " + LAB_SHORT.get(nxt, nxt),
                                  use_container_width=True, on_click=_lab_nav_cb,
                                  args=(lab_go_to_lab, nxt), key=f"{prefix}_nextlab_{i}")
                    else:
                        st.caption("Pick another lab from **🧭 Choose a lab** in the sidebar, or flip "
                                   "to **Sandbox** to test your own lines.")
            elif not challenge:
                if answered:
                    st.caption("Press **Set up & run this step** to finish the lab.")
                else:
                    st.caption(f"🔒 {_lockword} above, then run the step to finish the lab.")


def render_glossary_card():
    """A collapsible plain-English glossary of every symbol and term the app uses."""
    with st.container(border=True, key="glossary_card"):
        st.markdown('<div class="card-title">📖 Glossary</div>', unsafe_allow_html=True)
        st.markdown('<div class="card-sub">Plain-English meaning of every symbol and abbreviation '
                    'in the labs and readouts.</div>', unsafe_allow_html=True)
        with st.expander("Show the glossary"):
            st.markdown(
                "**Throughput (λ, *lambda*)** — good bottles finished per hour; the line's output "
                "rate.\n\n"
                "**Work-in-process (WIP, L)** — bottles sitting between stations, waiting to be worked "
                "on.\n\n"
                "**Flow time / lead time (W)** — how long an average bottle spends in the line, start "
                "to finish.\n\n"
                "**Little's Law (L = λ · W)** — average work-in-process equals throughput times flow "
                "time.\n\n"
                "**Bottleneck / constraint** — the slowest station; it sets the pace for the whole "
                "line.\n\n"
                "**Line efficiency** — actual output ÷ the bottleneck's top speed; the gap is lost to "
                "fluctuation and the way stations depend on each other.\n\n"
                "**Finished-goods inventory (FGI)** — completed bottles waiting to be sold.\n\n"
                "**Service level (raw-material availability)** — in this app, the share of hours "
                "Operation 1 had raw material to work with and was **not starved**. It measures the "
                "*supplier/line* side, not the customer side. (Technically this is production "
                "availability, not a customer service level.)\n\n"
                "**Fill rate (customer)** — a different measure, shown only when a fluctuating market "
                "is on: the share of **customer demand** actually sold from finished goods. A line can "
                "have high raw-material availability and still miss demand, so the two can differ.\n\n"
                "**Reorder point (ROP)** — the raw-material level that triggers a new supplier "
                "order.\n\n"
                "**Safety stock** — extra inventory kept to cover variability, such as an unreliable "
                "supplier.\n\n"
                "**Yield / scrap** — the share of units that pass (or fail) quality. Good output = "
                "speed × yield.\n\n"
                "**EOQ (Economic Order Quantity)** — the order size with the lowest total ordering + "
                "holding cost: EOQ = √(2·D·S ÷ H).\n\n"
                "**Demand (D)** — bottles needed per year.  **Ordering cost (S)** — the cost to place "
                "one order.  **Holding cost (H)** — the cost to keep one bottle in stock for a "
                "period.\n\n"
                "**P&L (Profit and Loss)** — a simple income statement for the line: the revenue from bottles sold minus every cost (materials, holding, ordering, and operating expense) equals profit.\n\n"
                "**Throughput Accounting** — **T** (throughput) = revenue − material cost;  **I** = "
                "money tied up in inventory and equipment;  **OE** = operating expense (the other "
                "running costs). Net profit = T − OE; return on investment = (T − OE) ÷ I.\n\n"
                "**Pull vs. push** — a **pull** line caps work-in-process and lets new work in only "
                "when there's room; a **push** line makes all it can and piles up inventory."
            )


def fin_number_input(container, label, canon_key, default, *, is_int=False, wprefix="w", **kw):
    """Number input whose displayed value is read explicitly from a plain (non-widget)
    session key and written straight back to it. Passing value= directly guarantees the
    field shows the stored figure — binding by key alone was leaving fields at 0 inside the
    dialog. The widget uses its own key (wprefix + '_' + canon_key) so it never collides
    with the canonical value (or with another rendering of the same field elsewhere)."""
    cur = st.session_state.get(canon_key, default)
    try:
        cur = int(cur) if is_int else float(cur)
    except (TypeError, ValueError):
        cur = default
    out = container.number_input(label, value=cur, key=f"{wprefix}_{canon_key}", **kw)
    st.session_state[canon_key] = int(out) if is_int else float(out)
    return out


@st.dialog("💵 Set Financials", width="large")
def financials_dialog():
    st.caption("Set the revenue and costs that turn the line's throughput into profit. "
               "Changes apply as soon as you save — the results recompute without re-running "
               "the simulation.")

    _fnum = fin_number_input

    _fnum(st, "Revenue per unit sold ($)", "fin_revenue_per_unit", DEFAULT_REVENUE_PER_UNIT,
          min_value=0.0, step=0.50, format="%.2f")

    st.markdown("**Per-die costs** — bigger dice cost more to buy but less to run per unit. "
                "Costs for in-between face counts are interpolated from this table.")
    hc = st.columns([1.3, 1, 1])
    hc[0].caption("Die")
    hc[1].caption("Fixed cost / die ($)")
    hc[2].caption("Production cost / unit ($)")
    for f in FACE_ROWS:
        rc = st.columns([1.3, 1, 1])
        label = "Constant (no die)" if f == 0 else f"{f}-sided die"
        rc[0].markdown(
            f"<div style='padding-top:0.5rem;font-weight:600;color:#334155'>{label}</div>",
            unsafe_allow_html=True)
        dfx, dpx = DEFAULT_FIN_LOOKUP[f]
        _fnum(rc[1], f"Fixed cost for {f} faces", f"fin_fixed_{f}", dfx,
              min_value=0.0, step=10.0, format="%.2f", label_visibility="collapsed")
        _fnum(rc[2], f"Production cost for {f} faces", f"fin_prod_{f}", dpx,
              min_value=0.0, step=0.01, format="%.4f", label_visibility="collapsed")

    _fnum(st, "Yearly allocation of fixed cost (%)", "fin_alloc_pct", DEFAULT_ALLOC_PCT,
          is_int=True, min_value=0, max_value=100, step=1,
          help="Share of each die's purchase price charged to one year "
               "(33% ≈ a three-year asset life).")

    st.markdown("**Other costs**")
    oc1, oc2 = st.columns(2)
    _fnum(oc1, "WIP holding cost ($/unit/day)", "fin_wip_holding", DEFAULT_WIP_HOLDING,
          min_value=0.0, step=0.01, format="%.2f")
    _fnum(oc2, "Raw material cost ($/unit)", "fin_rmc", DEFAULT_RMC,
          min_value=0.0, step=0.05, format="%.2f")

    b1, b2 = st.columns([1, 1])
    if b1.button("Save & close", type="primary", use_container_width=True):
        st.rerun()
    b2.button("Reset financials", use_container_width=True, on_click=reset_financials)


initialize_state()

# Keep per-step answers alive across navigation. Streamlit discards a widget's stored value
# once that widget stops being rendered (i.e., when you move to another step), so without this
# a prediction/estimate/reflection/challenge result would be gone when you navigate back. Re-
# committing those keys each run converts them to ordinary session values that persist; the
# widgets then read them back when you return to the step.
for _ans_k in list(st.session_state.keys()):
    if not isinstance(_ans_k, str):
        continue
    if ("_pred_" in _ans_k or "_est_" in _ans_k
            or "_chal_passed_" in _ans_k or "_chal_attempts_" in _ans_k or "_chal_seen_" in _ans_k
            or ("_reflect_" in _ans_k and "_reflect_save_" not in _ans_k)):
        st.session_state[_ans_k] = st.session_state[_ans_k]

# Restore this student's saved progress once per session (transparent no-op when storage
# is unconfigured — load() returns {}). Runs before any widgets render, so the restored
# lab position, completed steps, reflections, and challenge results take effect cleanly.
if _STORE_SID and not st.session_state.get("_restored"):
    st.session_state["_restored"] = True
    _restore_progress(store.load(_STORE_GAME, _STORE_SID))

# Restore saved MC/estimate answers from the URL once (works with or without shared storage),
# before any widget renders, so a refresh keeps each step's actual selection.
_get_answers()

# Supplier reliability is stored as a percentage (0–100); the simulation takes a
# probability in [0, 1]. demand-stream dice are only active when the variable-market
# switch is on. These derived values feed the simulation everywhere, so both the labs
# and the Sandbox stay in sync.
st.session_state["supply_reliability"] = int(
    max(0, min(100, int(st.session_state["supply_reliability"]))))
SUPPLY_REL = st.session_state["supply_reliability"] / 100.0
st.session_state["unlimited_supply"] = SUPPLY_REL >= 1.0


def demand_params():
    if st.session_state["demand_variable"]:
        return int(st.session_state["demand_dice"]), int(st.session_state["demand_faces"])
    return 0, 0


# Which outputs are relevant right now. In Guided Lab we hide the Sandbox power-tools
# (replications, A/B scenarios) and show the financial P&L only for the Economics lab,
# so each lab's main panel stays focused on the lesson at hand.
def lab_prefix_from_choice(choice):
    choice = choice or ""
    if "money side" in choice:
        return "diag2"
    if "Capstone" in choice or "Diagnose" in choice:
        return "diag"
    if "Economics" in choice:
        return "fin"
    if "Throughput Accounting" in choice or "T, I" in choice:
        return "ta"
    if "Variability" in choice:
        return "var"
    if "Little" in choice:
        return "little"
    if "Pull" in choice or "Push" in choice:
        return "pull"
    if "Safety" in choice or "Reorder" in choice:
        return "ss"
    if "Quality" in choice or "Yield" in choice:
        return "qual"
    if "Drivers" in choice or "best order" in choice.lower():
        return "eoqd"
    if "Inventory" in choice or "EOQ" in choice:
        return "eoq"
    return "ops"


IS_LAB = st.session_state["app_mode"] == "Guided Lab"
LAB_PREFIX = lab_prefix_from_choice(st.session_state.get("lab_choice", ""))


def _lab_in(*prefixes):
    """A sidebar control / result is shown either in the Sandbox (everything) or in the specific
    labs that actually use it. Keeping each lab to just its relevant inputs and outputs is what
    makes the simulation followable for a first-time user."""
    return (not IS_LAB) or (LAB_PREFIX in prefixes)


SHOW_SANDBOX_TOOLS = not IS_LAB                       # A/B pins & comparison
# Replications are available in the guided labs too, not just Sandbox. Judging a design from a
# single run is the mistake the dice game exists to cure, and the Variability lab in particular
# is unteachable without a distribution: one run gives a number, many runs give the process.
SHOW_REPS = True
# Inputs (sidebar cards / sub-controls)
SHOW_WIP      = _lab_in("ops", "little", "pull", "diag")
SHOW_SUPPLY   = _lab_in("var", "ss", "diag", "diag2")           # supplier reliability
SHOW_DEMAND   = _lab_in("var", "diag")                          # variable market demand
SHOW_ORDERSZ  = _lab_in("var", "ss", "eoq", "eoqd", "diag2")    # raw-material order size
SHOW_EOQ      = _lab_in("eoq", "eoqd", "diag2")                 # order & holding cost + EOQ curve
SHOW_SAFETY   = _lab_in("ss", "diag", "diag2")                  # reorder point
SHOW_QUALITY  = _lab_in("qual", "diag")                         # scrap / yield
SHOW_FINANCIALS = _lab_in("fin", "ta", "diag2")                # economics / P&L
SHOW_STARTINV = _lab_in("var", "ss", "diag", "diag2")          # starting inventory setting
SHOW_YEARS    = (not IS_LAB)                                    # labs run a single year
IS_EOQ_LAB    = IS_LAB and LAB_PREFIX in ("eoq", "eoqd")
# Outputs
SHOW_FLOWTIME = _lab_in("ops", "var", "little", "pull", "diag")
SHOW_LINE_DETAIL = _lab_in("ops", "little", "pull", "var", "qual", "diag")  # per-op panel + WIP charts


# =========================================================
# Styling
# =========================================================
st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

        /* ---------- Force a readable LIGHT appearance regardless of the viewer's browser or
           Streamlit theme. config.toml pins a light theme, but not every deployment honours it,
           so we force light surfaces + dark text with !important here, then restore WHITE text
           on the only two things that sit on a dark/gradient background: the hero and the
           primary button. This keeps the whole app readable even when the viewer is in dark mode. */
        :root, .stApp { color-scheme: light !important; }
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], .main,
        [data-testid="stHeader"], section[data-testid="stSidebar"],
        [data-testid="stSidebarContent"] { background-color: #f4f6fb !important; }
        [data-testid="stHeader"] { background: transparent !important; }
        .stApp, .stApp p, .stApp li, .stApp label, .stApp span,
        [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
        [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * { color: #1f2a44; }

        /* Inputs: light fields with dark text, even in dark mode (otherwise they render as
           unreadable black boxes). Cover the baseweb wrappers AND the real input element. */
        div[data-testid="stNumberInput"] [data-baseweb="input"],
        div[data-testid="stNumberInput"] [data-baseweb="base-input"],
        div[data-testid="stNumberInput"] input,
        div[data-testid="stTextInput"] [data-baseweb="input"],
        div[data-testid="stTextInput"] [data-baseweb="base-input"],
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] [data-baseweb="base-input"],
        div[data-testid="stTextArea"] textarea,
        div[data-baseweb="select"] > div,
        div[data-baseweb="menu"], div[data-baseweb="popover"] li {
            background-color: #f7f9ff !important; color: #1f2a44 !important;
        }
        div[data-testid="stNumberInput"] input,
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea {
            -webkit-text-fill-color: #1f2a44 !important;   /* some engines override `color` here */
        }

        /* Secondary (non-primary) buttons: light with dark text, readable in any theme. */
        div[data-testid="stButton"] > button:not([data-testid="stBaseButton-primary"]) {
            background-color: #ffffff !important; color: #1f2a44 !important;
        }

        /* The ONLY elements on a dark/gradient background keep WHITE text (incl. their children,
           which otherwise inherit the dark default above). */
        .hero, .hero * { color: #fff !important; }
        button[data-testid="stBaseButton-primary"],
        button[data-testid="stBaseButton-primary"] * {
            color: #fff !important; -webkit-text-fill-color: #fff !important;
        }

        /* Expanders & message boxes readable on light. */
        [data-testid="stExpander"] { background: #ffffff !important; }

        .stMainBlockContainer, .block-container { max-width: 1080px; padding-top: 1.4rem; }

        /* Wider sidebar so the stepper rows breathe */
        section[data-testid="stSidebar"] { min-width: 470px !important; }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f7f9fe 0%, #eaeefb 100%);
            border-right: 1px solid #e2e8f5;
        }
        /* Hide native number-input steppers — we use our own −/+ buttons */
        div[data-testid="stNumberInputStepUp"],
        div[data-testid="stNumberInputStepDown"] { display: none !important; }

        /* ---------- Hero ---------- */
        .hero {
            background: linear-gradient(135deg, #243b6e 0%, #ea580c 100%);
            padding: 1.7rem 1.9rem; border-radius: 20px; color: #fff;
            box-shadow: 0 14px 34px rgba(234,88,12,0.28); margin-bottom: 1.3rem;
        }
        /* The hero text sits on the dark gradient, so force white explicitly — the light-theme
           safety net above sets a dark default on every <p>, which would otherwise override the
           hero subtitle's inherited white and make it hard to read. */
        .hero h1, .hero p { color: #fff !important; }
        .hero h1 { margin: 0; font-size: 1.95rem; font-weight: 800; letter-spacing: -0.02em; }
        .hero p  { margin: 0.4rem 0 0; opacity: 0.95; font-size: 1.0rem; }

        /* ---------- Cards (keyed containers -> .st-key-<key>) ---------- */
        .st-key-ops_card, .st-key-settings_card, .st-key-actions_card,
        .st-key-results_card, .st-key-config_card, .st-key-opdetail_card,
        .st-key-fin_card, .st-key-finresult_card, .st-key-eoqresult_card, .st-key-wip_card,
        .st-key-flow_card, .st-key-rep_card, .st-key-cmp_card, .st-key-lab_card, .st-key-var_card, .st-key-labpick_card, .st-key-inv_card, .st-key-eoqcost_card, .st-key-prog_card, .st-key-decode_card, .st-key-safety_card, .st-key-quality_card, .st-key-glossary_card {
            background: #ffffff; border: 1px solid #e6ebf7 !important;
            border-radius: 16px !important; box-shadow: 0 6px 20px rgba(31,42,68,0.07);
            padding: 1.0rem 1.0rem 1.0rem !important; margin-bottom: 1.0rem;
            position: relative; overflow: hidden;
        }
        .st-key-ops_card::before, .st-key-settings_card::before, .st-key-actions_card::before,
        .st-key-results_card::before, .st-key-config_card::before, .st-key-opdetail_card::before,
        .st-key-fin_card::before, .st-key-finresult_card::before, .st-key-eoqresult_card::before, .st-key-wip_card::before,
        .st-key-flow_card::before, .st-key-rep_card::before, .st-key-cmp_card::before,
        .st-key-lab_card::before, .st-key-var_card::before, .st-key-labpick_card::before,
        .st-key-inv_card::before, .st-key-eoqcost_card::before,
        .st-key-prog_card::before, .st-key-decode_card::before,
        .st-key-safety_card::before, .st-key-quality_card::before, .st-key-glossary_card::before {
            content: ""; position: absolute; top: 0; left: 0; right: 0; height: 4px;
            background: linear-gradient(90deg, #ea580c, #fb923c);
        }
        .card-title { font-size: 1.0rem; font-weight: 800; color: #1f2a44; margin: 0.1rem 0 0.15rem; }
        .card-sub   { color: #667085; font-size: 0.84rem; margin-bottom: 0.6rem; line-height: 1.4; }
        .col-head   { color: #8a93a6; font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
                      letter-spacing: 0.06em; text-align: center; }
        .op-name    { font-weight: 700; color: #334155; padding-top: 0.45rem; font-size: 0.86rem; }
        .op-dim     { color: #aab2c5; }
        .op-read    { text-align: center; padding-top: 0.40rem; line-height: 1.05; }
        .op-read b  { display: block; font-weight: 800; color: #3f6212; font-size: 0.9rem; }
        .op-read span { font-size: 0.58rem; color: #aab2c5; font-weight: 600; }
        .op-read.off { color: #c0c6d4; font-weight: 700; font-size: 0.76rem; padding-top: 0.55rem; }

        /* ---------- Number inputs ---------- */
        div[data-testid="stNumberInput"] input {
            text-align: center; font-weight: 700; color: #1f2a44; height: 36px;
            padding-left: 4px; padding-right: 4px;
        }
        div[data-testid="stNumberInput"] div[data-baseweb="input"] {
            border-radius: 9px; border-color: #d7deee; transition: border-color .15s, box-shadow .15s;
        }
        div[data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within {
            border-color: #ea580c; box-shadow: 0 0 0 3px rgba(234,88,12,0.16);
        }

        /* ---------- Make editable fields obviously input-like ---------- */
        div[data-testid="stNumberInput"] div[data-baseweb="input"],
        div[data-testid="stTextInput"] div[data-baseweb="input"],
        div[data-testid="stTextInput"] div[data-baseweb="base-input"],
        div[data-testid="stTextArea"] textarea {
            border-width: 1.6px; border-color: #adbbde; background: #f7f9ff;
        }
        div[data-testid="stTextArea"] textarea:focus {
            border-color: #ea580c; box-shadow: 0 0 0 3px rgba(234,88,12,0.16); background:#fff; }
        div[data-testid="stNumberInput"] div[data-baseweb="input"]:hover,
        div[data-testid="stTextInput"] div[data-baseweb="input"]:hover { border-color: #ea580c; }
        div[data-testid="stTextInput"] input { font-weight: 600; color: #1f2a44; }
        /* Toggles/sliders read as controls: give their labels a touch more weight */
        div[data-testid="stToggle"] label p, div[data-testid="stSlider"] label p { font-weight: 600; }

        /* ---------- "The next step is locked" explainer ---------- */
        .lab-lock { background: #fff8e6; border: 1px solid #fde3a7; border-left: 5px solid #f59e0b;
            border-radius: 9px; padding: 10px 14px; margin-top: 10px; color: #8a5300;
            font-size: 0.92rem; font-weight: 500; line-height: 1.4; }
        .lab-lock b { color: #7a3e00; }

        /* ---------- Up/down spinner buttons inside the operations card ---------- */
        .st-key-ops_card div[data-testid="stButton"] > button {
            min-height: 17px; height: 17px; width: 100%; padding: 0 !important;
            font-size: 0.55rem; font-weight: 800; line-height: 1;
            border: 1px solid #d8e0f3; color: #ea580c !important; background: #fff !important;
        }
        .st-key-ops_card div[data-testid="stButton"] > button:hover {
            background: #eef2ff !important; border-color: #ea580c; transform: none; box-shadow: none;
            color: #9a3412 !important;
        }
        /* join the two arrows into one spinner: up = top corners, down = bottom corners */
        .st-key-ops_card [data-testid="stColumn"]
            div[data-testid="stElementContainer"]:nth-of-type(1) button {
            border-radius: 6px 6px 0 0; border-bottom: none;
        }
        .st-key-ops_card [data-testid="stColumn"]
            div[data-testid="stElementContainer"]:nth-of-type(2) button {
            border-radius: 0 0 6px 6px;
        }
        /* tighten the gap so the two arrows sit flush, and the row columns */
        .st-key-ops_card [data-testid="stColumn"] div[data-testid="stVerticalBlock"] { gap: 0; }
        .st-key-ops_card div[data-testid="stHorizontalBlock"] { gap: 0.3rem; }

        /* ---------- Generic buttons (Run / Reset / settings) ---------- */
        div[data-testid="stButton"] > button {
            border-radius: 10px; font-weight: 700; min-height: 2.6rem;
            border: 1px solid #d8e0f3; transition: transform .06s ease, box-shadow .12s ease,
                                                    background-color .12s ease, filter .12s ease;
        }
        /* Immediate click feedback (no server round-trip needed): the button visibly presses in
           and flashes an orange ring the instant it's clicked, and holds a "busy" look while the
           app reruns, so a student always knows their click registered. */
        div[data-testid="stButton"] > button:hover { border-color: #ea580c; }
        div[data-testid="stButton"] > button:active {
            transform: translateY(1px) scale(0.98) !important;
            box-shadow: 0 0 0 3px rgba(234,88,12,0.45), inset 0 2px 5px rgba(0,0,0,0.12) !important;
            filter: brightness(0.96);
        }
        /* After the click, the button keeps keyboard/mouse focus during the rerun — show that as
           a steady orange ring so "I pressed it, it's working" is obvious. */
        div[data-testid="stButton"] > button:focus:not(:active) {
            box-shadow: 0 0 0 3px rgba(234,88,12,0.35) !important; outline: none !important;
        }
        button[data-testid="stBaseButton-primary"] {
            background: linear-gradient(135deg, #ea580c, #6d83f8) !important;
            border: none !important; color: #fff !important;
            box-shadow: 0 8px 18px rgba(234,88,12,0.32) !important;
        }
        button[data-testid="stBaseButton-primary"]:hover {
            filter: brightness(1.06); color: #fff !important; transform: translateY(-1px);
        }
        button[data-testid="stBaseButton-primary"]:active {
            transform: translateY(1px) scale(0.985) !important;
            box-shadow: 0 0 0 3px rgba(234,88,12,0.55), inset 0 2px 6px rgba(0,0,0,0.25) !important;
            filter: brightness(0.94) !important;
        }

        /* ---------- "Show charts" switch — made deliberately prominent ---------- */
        .st-key-charts_toggle_card {
            background: linear-gradient(135deg, #fff7ed 0%, #ffffff 70%) !important;
            border: 1.5px solid #fdba74 !important; border-left: 5px solid #ea580c !important;
            border-radius: 14px !important; box-shadow: 0 4px 14px rgba(234,88,12,0.10) !important;
            padding: 0.7rem 1.0rem !important; margin: 0.2rem 0 0.8rem !important;
        }
        .st-key-charts_toggle_card::before { content: none !important; }  /* no top accent bar */
        .charts-toggle-lead { color: #7a3e00; font-size: 0.92rem; line-height: 1.35; }
        .charts-toggle-lead b { color: #9a3412; font-size: 1.0rem; }
        .charts-toggle-lead span { color: #9a5a33; font-size: 0.82rem; }
        /* Enlarge the toggle control itself and colour it orange so the switch is unmissable. */
        .st-key-charts_toggle_card div[data-testid="stToggle"] { transform: scale(1.25);
            transform-origin: right center; margin-top: 0.4rem; }
        .st-key-charts_toggle_card div[data-testid="stToggle"] label { justify-content: flex-end; }
        .charts-off-note { background: #fffaeb; border: 1px solid #fde3a7; border-left: 4px solid #f59e0b;
            border-radius: 9px; padding: 8px 12px; margin: 2px 0 10px; color: #8a5300;
            font-size: 0.88rem; font-weight: 500; }
        .charts-off-note b { color: #7a3e00; }

        /* ---------- Metrics & pills ---------- */
        div[data-testid="stMetric"] {
            background: linear-gradient(180deg, #ffffff, #f7f9fe); border: 1px solid #e6ebf7;
            border-radius: 14px; padding: 0.85rem 1rem; box-shadow: 0 3px 12px rgba(16,24,40,0.05);
        }
        .pill { border-radius: 12px; padding: 0.8rem 1rem; font-weight: 650; margin-bottom: 1.0rem; }
        .pill-run  { background: #eef2ff; color: #9a3412; border: 1px solid #c7d0fe; }
        .pill-warn { background: #fffaeb; color: #b54708; border: 1px solid #fedf89; }
        .pill-info { background: #f4f6fb; color: #475467; border: 1px solid #e2e6ef; }

        /* ---------- Per-operation results panel (Excel-style) ---------- */
        .opd-row { display: flex; align-items: center; gap: 14px; padding: 11px 2px;
                   border-bottom: 1px solid #eef1f7; }
        .opd-row:last-child { border-bottom: none; }
        .opd-left { flex: 0 0 168px; }
        .opd-h    { font-weight: 800; color: #1f2a44; font-size: 0.96rem; }
        .opd-cap  { display: flex; justify-content: space-between; font-size: 0.72rem;
                    color: #8a93a6; margin-top: 2px; }
        .opd-cap b { color: #334155; }
        .opd-bottleneck { display: inline-block; margin-top: 3px; font-size: 0.62rem; font-weight: 700;
                          color: #b54708; background: #fffaeb; border: 1px solid #fedf89;
                          border-radius: 6px; padding: 1px 6px; }
        .opd-rawtag { display: inline-block; margin-top: 3px; font-size: 0.62rem; font-weight: 700;
                      color: #0369a1; background: #f0f9ff; border: 1px solid #bae6fd;
                      border-radius: 6px; padding: 1px 6px; }
        .opd-track { flex: 1; position: relative; height: 28px; background: #f1f4fa;
                     border: 1px solid #e6ebf7; border-radius: 8px; overflow: hidden; }
        .opd-bar  { height: 100%; background: linear-gradient(90deg, #8dc63f, #aadb5e);
                    border-radius: 7px 0 0 7px; min-width: 2px; }
        .opd-val  { position: absolute; top: 50%; transform: translateY(-50%);
                    font-weight: 800; font-size: 0.8rem; color: #3f6212; }
        .opd-stats { display: flex; gap: 8px; flex: 0 0 auto; }
        .opd-stat  { border: 1px solid #e6ebf7; border-radius: 9px; padding: 6px 11px;
                     background: #fff; min-width: 138px; }
        .opd-stat-h { font-size: 0.62rem; color: #8a93a6; font-weight: 700;
                      text-transform: uppercase; letter-spacing: 0.04em; }
        .opd-stat-v { font-size: 1.05rem; font-weight: 800; color: #1f2a44; }
        .opd-axis  { display: flex; justify-content: space-between; color: #aab2c5;
                     font-size: 0.66rem; padding: 0 0 4px 182px; }
        /* raw-material & finished-goods bookend rows */
        .opd-raw, .opd-fgi { background: #f7fbff; border-radius: 10px; margin: 4px 0;
                             border-bottom: none; }
        .opd-fgi { background: #faf7ff; }
        .opd-raw .opd-h { color: #0369a1; }
        .opd-fgi .opd-h { color: #7e22ce; }
        .opd-bar-raw { background: linear-gradient(90deg, #38bdf8, #7dd3fc) !important; }
        .opd-bar-fgi { background: linear-gradient(90deg, #c084fc, #d8b4fe) !important; }
        .opd-raw .opd-val { color: #0369a1; }
        .opd-fgi .opd-val { color: #7e22ce; }

        /* ---------- Live dashboard: header + sparklines + mini bars ---------- */
        .anim-card { background: #fff; border: 1px solid #e6ebf7; border-radius: 16px;
                     box-shadow: 0 6px 20px rgba(31,42,68,0.07); padding: 1.0rem 1.1rem 0.8rem;
                     position: relative; overflow: hidden; margin-bottom: 1.0rem; }
        .anim-card::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 4px;
                             background: linear-gradient(90deg, #ea580c, #fb923c); }
        .anim-head { margin-bottom: 10px; }
        .anim-top  { display: flex; align-items: center; justify-content: space-between; }
        .anim-day  { font-weight: 800; color: #1f2a44; font-size: 1.15rem; }
        .anim-day span { color: #aab2c5; font-weight: 600; font-size: 0.9rem; }
        .anim-metrics { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
        .anim-m { border: 1px solid #e6ebf7; border-radius: 9px; padding: 5px 11px; background: #fff; }
        .anim-m span { font-size: 0.6rem; color: #8a93a6; font-weight: 700;
                       text-transform: uppercase; letter-spacing: 0.04em; display: block; }
        .anim-m b { font-size: 1.0rem; color: #1f2a44; }
        .anim-prog { margin-top: 8px; height: 7px; background: #eef1f7; border-radius: 6px; overflow: hidden; }
        .anim-prog-fill { height: 100%; background: linear-gradient(90deg, #ea580c, #fb923c);
                          transition: width .1s linear; }

        .live-charts { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
        .live-card { flex: 1 1 calc(33.333% - 8px); min-width: 150px;
                     border: 1px solid #e6ebf7; border-radius: 11px;
                     padding: 8px 10px 6px; background: #fbfcff; }
        .live-card h4 { margin: 0 0 4px; font-size: 0.62rem; text-transform: uppercase;
                        letter-spacing: 0.05em; color: #8a93a6; font-weight: 700; }
        .live-card .lc-val { font-size: 0.95rem; font-weight: 800; color: #1f2a44; margin-bottom: 4px; }
        .live-wide { flex: 1 1 100%; }
        .inv-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 4px; }
        .inv-key { font-size: 0.7rem; font-weight: 700; color: #475467; display: flex;
                   align-items: center; gap: 5px; }
        .inv-key i { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
        .inv-key b { color: #1f2a44; }
        .mini-wrap { display: flex; align-items: flex-end; gap: 8px; height: 64px; padding-top: 2px; }
        .mini-col { flex: 1; display: flex; flex-direction: column; align-items: center;
                    justify-content: flex-end; height: 100%; }
        .mini-track { width: 100%; height: 42px; background: #f1f4fa; border: 1px solid #e6ebf7;
                      border-radius: 5px; display: flex; align-items: flex-end; overflow: hidden; }
        .mini-bar { width: 100%; background: linear-gradient(180deg, #aadb5e, #8dc63f); }
        .mini-bar.bn { background: linear-gradient(180deg, #fec84b, #f79009); }
        .mini-lab { font-size: 0.6rem; color: #8a93a6; font-weight: 700; margin-top: 2px; }
        .mini-num { font-size: 0.62rem; color: #334155; font-weight: 800; }
        .mini-empty { color: #aab2c5; font-size: 0.74rem; padding: 14px 2px; }

        /* ---------- Profit & loss panel ---------- */
        .pnl-row { display: flex; align-items: center; justify-content: space-between;
                   padding: 9px 4px; border-bottom: 1px solid #eef1f7; }
        .pnl-lbl { color: #334155; font-weight: 600; font-size: 0.92rem; }
        .pnl-sub { display: block; color: #98a2b3; font-weight: 500; font-size: 0.68rem; margin-top: 1px; }
        .pnl-amt { font-weight: 800; font-size: 0.96rem; font-variant-numeric: tabular-nums; }
        .pnl-amt.pos { color: #15803d; }
        .pnl-amt.neg { color: #b42318; }
        .pnl-row.strong { border-top: 2px solid #1f2a44; border-bottom: none; margin-top: 4px;
                          padding-top: 12px; }
        .pnl-row.strong .pnl-lbl { font-weight: 800; font-size: 1.02rem; color: #1f2a44; }
        .pnl-amt.profit-pos { color: #15803d; font-size: 1.12rem; }
        .pnl-amt.profit-neg { color: #b42318; font-size: 1.12rem; }

        /* ---------- Profit vs. die-size bar chart (HTML) ---------- */
        .pc-wrap { display: flex; gap: 8px; align-items: stretch; padding-top: 20px; }
        .pc-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
        .pc-pos { display: flex; flex-direction: column; justify-content: flex-end; align-items: center; }
        .pc-neg { display: flex; flex-direction: column; justify-content: flex-start; align-items: center;
                  border-top: 2px solid #cbd2e0; }
        .pc-bar { width: 62%; min-width: 12px; }
        .pc-bar.pos { background: linear-gradient(180deg, #aadb5e, #8dc63f); border-radius: 4px 4px 0 0; }
        .pc-bar.neg { background: linear-gradient(180deg, #fda29b, #f04438); border-radius: 0 0 4px 4px; }
        .pc-bar.cur { outline: 2.5px solid #1f2a44; outline-offset: -1px; }
        .pc-v { font-size: 0.7rem; font-weight: 800; line-height: 1.3; white-space: nowrap; }
        .pc-v.pos { color: #15803d; }
        .pc-v.peak { color: #9a3412; }
        .pc-v.neg { color: #b42318; }
        .pc-x { text-align: center; font-size: 0.7rem; font-weight: 700; color: #667085; margin-top: 6px; }

        /* ---------- Box-and-whisker (replications) ---------- */
        .bx-row { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
        .bx-label { flex: 0 0 150px; font-size: 0.78rem; font-weight: 700; color: #334155; text-align: right; }
        .bx-track { position: relative; flex: 1; height: 40px; }
        .bx-whisker { position: absolute; top: 50%; height: 2px; background: #cbd2e0; transform: translateY(-50%); }
        .bx-cap { position: absolute; top: 50%; width: 2px; height: 12px; background: #98a2b3;
                  transform: translate(-50%, -50%); }
        .bx-box { position: absolute; top: 50%; height: 18px; transform: translateY(-50%);
                  border-radius: 3px; border: 1.5px solid; }
        .bx-med { position: absolute; top: 50%; width: 2.5px; height: 20px; transform: translate(-50%, -50%); }
        .bx-vmin, .bx-vmax { position: absolute; top: calc(50% + 11px); font-size: 0.6rem;
                             color: #98a2b3; font-weight: 600; transform: translateX(-50%); white-space: nowrap; }
        .bx-vmed { position: absolute; bottom: calc(50% + 11px); font-size: 0.64rem; font-weight: 800;
                   transform: translateX(-50%); white-space: nowrap; }

        /* ---------- Little's Law equation strip ---------- */
        .ll-eq { background: #f4f6fb; border: 1px solid #e2e6ef; border-radius: 10px;
                 padding: 9px 12px; font-size: 0.9rem; color: #334155; margin: 6px 0 4px;
                 font-variant-numeric: tabular-nums; }
        .ll-eq b { color: #1f2a44; }

        /* ---------- A/B comparison table ---------- */
        .cmp { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
        .cmp th { text-align: right; font-size: 0.64rem; text-transform: uppercase; letter-spacing: 0.04em;
                  color: #8a93a6; font-weight: 700; padding: 6px 10px; border-bottom: 2px solid #e6ebf7; }
        .cmp th:first-child { text-align: left; }
        .cmp td { text-align: right; padding: 7px 10px; border-bottom: 1px solid #eef1f7;
                  font-weight: 700; color: #1f2a44; font-variant-numeric: tabular-nums; }
        .cmp td.cmp-m { text-align: left; font-weight: 600; color: #475467; }
        .cmp td.cmp-up { color: #15803d; }
        .cmp td.cmp-dn { color: #b42318; }
        .cmp td.cmp-flat { color: #98a2b3; }
        .cmp-cfg { font-size: 0.74rem; color: #667085; margin-top: 8px; }
        .cmp-cfg b { color: #334155; }

        /* ---------- Guided Lab ---------- */
        .st-key-lab_card::before { background: linear-gradient(180deg, #ea580c, #c2410c) !important; }
        .lab-road { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 14px; }
        .lab-chip { font-size: 0.68rem; font-weight: 700; padding: 4px 9px; border-radius: 999px;
                    background: #f1f3f9; color: #98a2b3; border: 1px solid #e6ebf7; }
        .lab-chip-on { background: #ede9fe; color: #6d28d9; border-color: #ddd6fe; }
        .lab-chip-done { background: #eafaf0; color: #15803d; border-color: #cdeed8; }
        .lab-phase { font-size: 0.72rem; font-weight: 800; letter-spacing: 0.05em;
                     text-transform: uppercase; color: #ea580c; }
        .lab-h { font-size: 1.18rem; font-weight: 800; color: #1f2a44; margin: 2px 0 6px; }
        .lab-intro { font-size: 0.94rem; color: #344054; line-height: 1.5; margin-bottom: 8px; }
        .lab-setup { font-size: 0.82rem; color: #475467; background: #f7f8fc; border: 1px dashed #d8deee;
                     border-radius: 8px; padding: 7px 11px; margin-bottom: 6px; }
        .lab-setup b { color: #1f2a44; }
        .lab-fb { font-size: 0.86rem; font-weight: 700; margin: 6px 0 2px; }
        .lab-fb-ok { color: #15803d; }
        .lab-fb-no { color: #b54708; }
        .lab-fb-why { font-weight: 600; color: #7c2d12; background: #fff7ed;
            border-left: 3px solid #f97316; border-radius: 6px; padding: 6px 10px; margin: 5px 0; }
        .lab-fb-then { font-weight: 700; color: #b54708; margin-top: 4px; }
        .chal-inst { font-size: 0.9rem; color: #344054; background: #fff7ed;
            border: 1px solid #fed7aa; border-radius: 9px; padding: 9px 12px; margin: 4px 0 10px; }
        .chal-board { display: flex; flex-direction: column; gap: 6px; margin: 6px 0 8px; }
        .chal-row { display: grid; grid-template-columns: 22px 1fr auto auto; align-items: center;
            gap: 10px; padding: 8px 12px; border-radius: 9px; background: #f7f8fc;
            border: 1px solid #eef1f7; }
        .chal-row.chal-ok { background: #eafaf0; border-color: #cdeed8; }
        .chal-row.chal-no { background: #fef3f2; border-color: #fecdca; }
        .chal-row.chal-idle { background: #f7f8fc; border-color: #eef1f7; }
        .chal-ic { font-weight: 900; font-size: 1rem; text-align: center; }
        .chal-row.chal-ok .chal-ic { color: #15803d; }
        .chal-row.chal-no .chal-ic { color: #d92d20; }
        .chal-row.chal-idle .chal-ic { color: #98a2b3; }
        .chal-lab { font-weight: 700; color: #344054; }
        .chal-goal { font-size: 0.8rem; font-weight: 600; color: #667085;
            background: #fff; border: 1px solid #e4e7ec; border-radius: 999px; padding: 2px 9px; }
        .chal-val { font-weight: 800; font-variant-numeric: tabular-nums; color: #0f172a;
            min-width: 64px; text-align: right; }
        .chal-tries { font-size: 0.82rem; font-weight: 600; color: #667085; margin: 2px 0 8px; }
        .reflect-q { font-size: 0.92rem; color: #3730a3; background: #eef2ff;
            border: 1px solid #c7d2fe; border-radius: 9px; padding: 9px 12px; margin: 10px 0 6px; }
        .lab-count { text-align: center; font-size: 0.78rem; font-weight: 700; color: #667085;
                     padding-top: 8px; }

        /* ---------- Progress tracker ---------- */
        .prog-top { display: flex; justify-content: space-between; align-items: baseline;
                    font-size: 0.82rem; color: #475467; margin-top: 2px; }
        .prog-top b { font-size: 1.05rem; color: #ea580c; font-variant-numeric: tabular-nums; }
        .prog-bar { height: 8px; background: #eef1f7; border-radius: 999px; overflow: hidden;
                    margin: 5px 0 4px; }
        .prog-fill { height: 100%; background: linear-gradient(90deg, #f97316, #ea580c);
                     border-radius: 999px; transition: width 0.3s ease; }
        .prog-sub { font-size: 0.72rem; color: #98a2b3; margin-bottom: 8px; }
        .prog-rows { display: flex; flex-direction: column; gap: 3px; }
        .prog-row { display: flex; justify-content: space-between; align-items: center;
                    font-size: 0.8rem; padding: 4px 8px; border-radius: 7px; background: #f7f8fc;
                    border: 1px solid #eef1f7; }
        .prog-row.prog-part { background: #fff7ed; border-color: #fed7aa; }
        .prog-row.prog-done { background: #eafaf0; border-color: #cdeed8; }
        .prog-name { font-weight: 600; color: #344054; }
        .prog-count { font-weight: 800; color: #667085; font-variant-numeric: tabular-nums; }
        .prog-row.prog-done .prog-count { color: #15803d; }
        .prog-receipt { font-size: 0.8rem; color: #344054; background: #f7f8fc;
                        border: 1px dashed #d8deee; border-radius: 8px; padding: 7px 10px;
                        margin-bottom: 6px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# Reusable stepper row (label + −/value/+  ×2 + live avg/hr readout)
# =========================================================
def stepper_row(i):
    c = int(st.session_state[f"capacity_{i}"])
    s = int(st.session_state[f"sides_{i}"])
    active = c > 0 and s > 0
    dim = "" if active else " op-dim"

    cols = st.columns([0.62, 0.92, 0.30, 0.92, 0.30, 0.92])
    cols[0].markdown(f'<div class="op-name{dim}">Op #{i + 1}</div>', unsafe_allow_html=True)

    # Dice (capacity): value + up/down spinner
    cols[1].number_input(f"Dice for operation {i + 1}", min_value=CAP_MIN, max_value=CAP_MAX, step=1,
                         key=f"capacity_{i}", label_visibility="collapsed")
    with cols[2]:
        st.button("▲", key=f"cap_{i}_up", use_container_width=True,
                  on_click=step_value, args=(f"capacity_{i}", 1, CAP_MIN, CAP_MAX))
        st.button("▼", key=f"cap_{i}_dn", use_container_width=True,
                  on_click=step_value, args=(f"capacity_{i}", -1, CAP_MIN, CAP_MAX))

    # Faces (sides): value + up/down spinner
    cols[3].number_input(f"Faces for operation {i + 1}", min_value=SIDES_MIN, max_value=SIDES_MAX, step=1,
                         key=f"sides_{i}", label_visibility="collapsed")
    with cols[4]:
        st.button("▲", key=f"sid_{i}_up", use_container_width=True,
                  on_click=step_value, args=(f"sides_{i}", 1, SIDES_MIN, SIDES_MAX))
        st.button("▼", key=f"sid_{i}_dn", use_container_width=True,
                  on_click=step_value, args=(f"sides_{i}", -1, SIDES_MIN, SIDES_MAX))

    # Live readout: average output per hour, with the min–max range
    if active:
        avg = c * (s + 1) / 2
        readout = (f'<div class="op-read"><b>{avg:g}</b>'
                   f'<span>avg/hr · {c}–{c * s} range</span></div>')
    else:
        readout = '<div class="op-read off">off</div>'
    cols[5].markdown(readout, unsafe_allow_html=True)


# =========================================================
# SIDEBAR — all setup
# =========================================================
with st.sidebar:
    st.markdown("## ⚙️ Setup")

    st.radio("Mode", ["Guided Lab", "Sandbox"], key="app_mode", horizontal=True,
             on_change=lab_on_mode_change,
             help="Guided Lab walks you through a directed, predict-then-run exercise. "
                  "Sandbox is the full free-play simulator with every control — financials, "
                  "replications, A/B comparison, and the supply & demand switches.")

    # Defined here so they exist even when the Run card is hidden (Guided Lab mode);
    # the lab panel triggers runs through the lab_autorun flag instead.
    run_clicked = False
    reps_clicked = False

    is_lab = st.session_state["app_mode"] == "Guided Lab"

    # ---- Lab chooser (Guided Lab only) ----
    if is_lab:
        with st.container(border=True, key="labpick_card"):
            # Keep the part selector in sync with the current lab, then let it filter the picker.
            _cur_part = _part_of_lab(lab_prefix_from_choice(st.session_state.get("lab_choice", "")))
            st.session_state["lab_part"] = _cur_part
            st.markdown('<div class="card-title">📚 Lab part</div>', unsafe_allow_html=True)
            st.radio("Which part?", LAB_PART_ORDER, key="lab_part",
                     label_visibility="collapsed", on_change=lab_on_part_change)
            st.caption("**Part 1** is the default. **Part 2** is a separate assignment — select it when "
                       "you're ready. Each part is submitted as its own report (below).")
            st.markdown('<div class="card-title" style="margin-top:8px">🧭 Choose a lab</div>',
                        unsafe_allow_html=True)
            _part_options = [LAB_CHOICE_LABEL[p] for p in _part_labs(_cur_part)]
            if st.session_state.get("lab_choice") not in _part_options:
                st.session_state["lab_choice"] = _part_options[0]
            st.radio("Which guided lab?", _part_options,
                     key="lab_choice", label_visibility="collapsed",
                     on_change=lab_on_choice_change)
            st.caption("Each step **sets the line up for you** the moment you open it — just press "
                       "**Set up & run this step** to see it go, or tweak the controls below and re-run "
                       "to experiment with your own \"what if\".")

        # ---- Progress tracker (this part) + report download ----
        with st.container(border=True, key="prog_card"):
            st.markdown(f'<div class="card-title">✅ Your progress — {_cur_part}</div>',
                        unsafe_allow_html=True)
            _icons = {"ops": "🧭", "little": "⏱️", "pull": "🔄", "var": "🎰",
                      "qual": "✅", "fin": "💰", "ta": "📊", "eoqd": "🧮", "eoq": "📦",
                      "ss": "🚚", "diag": "🔬", "diag2": "🔬"}
            _all_rows, _, _, _ = progress_summary()
            _row_map = {pre: (lbl, done, n) for pre, lbl, done, n in _all_rows}
            rows = [(pre, *_row_map[pre]) for pre in _part_labs(_cur_part) if pre in _row_map]
            tdone = sum(d for _p, _l, d, _n in rows)
            ttotal = sum(n for _p, _l, _d, n in rows)
            pct = round(100 * tdone / ttotal) if ttotal else 0
            row_html = []
            for pre, lbl, done, n in rows:
                complete = (n > 0 and done >= n)
                cls = "prog-row prog-done" if complete else (
                    "prog-row prog-part" if done > 0 else "prog-row")
                tick = " ✓" if complete else ""
                row_html.append(
                    f'<div class="{cls}"><span class="prog-name">{_icons.get(pre, "•")} {lbl}</span>'
                    f'<span class="prog-count">{done}/{n}{tick}</span></div>')
            labs_done = sum(1 for _p, _l, d, n in rows if n > 0 and d >= n)
            st.markdown(
                f'<div class="prog-top"><span>This part</span><b>{pct}%</b></div>'
                f'<div class="prog-bar"><div class="prog-fill" style="width:{pct}%"></div></div>'
                f'<div class="prog-sub">{tdone} of {ttotal} steps · {labs_done} of {len(rows)} labs complete</div>'
                f'<div class="prog-rows">{"".join(row_html)}</div>',
                unsafe_allow_html=True)

            # Assessment honesty: a challenge step counts as "done" once it's resolved — which
            # includes running out of tries. Report PASSED separately so "out of tries" is never
            # shown as a pass. (Completed ≠ passed ≠ mastery.)
            _chal_total = _chal_pass = _chal_attempted = 0
            for _pre in _part_labs(_cur_part):
                if _pre not in LABS:
                    continue
                for _ci, _cs in enumerate(LABS[_pre]["steps"]):
                    if _cs.get("challenge"):
                        _chal_total += 1
                        if st.session_state.get(f"{_pre}_chal_passed_{_ci}"):
                            _chal_pass += 1
                        elif int(st.session_state.get(f"{_pre}_chal_attempts_{_ci}", 0) or 0) > 0:
                            _chal_attempted += 1
            if _chal_total:
                _msg = f"🏆 Challenges passed: **{_chal_pass} of {_chal_total}**"
                if _chal_attempted:
                    _msg += f" · ⚠️ {_chal_attempted} attempted but not yet passed (you can Reset & try again)"
                st.caption(_msg)

            with st.expander("📄 Get my report to submit"):
                st.caption(f"Enter your name and download your **{_cur_part}** report — then upload "
                           "the file to this part's assignment on the LMS. (Part 1 and Part 2 are two "
                           "separate assignments; switch parts above to get the other report.)")
                st.text_input("Your name", key="student_name", placeholder="e.g., Jordan Lee")
                _name = st.session_state.get("student_name", "").strip()
                if not _name:
                    st.info("Type your name above to enable the download.")
                else:
                    _pnum = "1" if _cur_part == LAB_PART_ORDER[0] else "2"
                    _slug = re.sub(r"[^A-Za-z0-9]+", "_", _name).strip("_")[:30] or "student"
                    # Build the report only when asked. Streamlit reruns this whole script on every
                    # widget click, so building the PDF and writing the completion record inline
                    # meant ~every click did that work (and a Dropbox upload) for every student.
                    _rk = f"_rpt_{_pnum}"
                    if st.button("📄 Prepare my report", use_container_width=True, key=f"prep_{_rk}"):
                        try:
                            _rep = {"name": _name, "data": build_part_report_pdf(_cur_part, _name),
                                    "file": f"CapacityCrush_Part{_pnum}_{_slug}.pdf",
                                    "mime": "application/pdf", "pdf": True}
                        except Exception:
                            _rep = {"name": _name,
                                    "data": build_part_report_html(_cur_part, _name).encode("utf-8"),
                                    "file": f"CapacityCrush_Part{_pnum}_{_slug}.html",
                                    "mime": "text/html", "pdf": False}
                        st.session_state[_rk] = _rep
                        # Record this part's completion to the shared roster (no-op when storage
                        # is off); skipped when identical to the last record this session wrote.
                        _rd = _report_data(_cur_part)
                        _code = f"Part {_pnum}: {_name}"
                        _score = (round(100 * _rd["steps_done"] / _rd["steps_total"])
                                  if _rd["steps_total"] else 0)
                        if st.session_state.get("_last_completion") != (_code, _score):
                            try:
                                store.record_completion(_STORE_GAME, _STORE_SID,
                                                        completion_code=_code, score=_score)
                                st.session_state["_last_completion"] = (_code, _score)
                            except Exception:
                                pass
                    _rep = st.session_state.get(_rk)
                    if _rep and _rep["name"] == _name:
                        st.download_button(
                            "⬇️ Download my report (PDF)" if _rep["pdf"] else "⬇️ Download my report (.html)",
                            data=_rep["data"], file_name=_rep["file"], mime=_rep["mime"],
                            use_container_width=True, key="dl_report")
                        if _rep["pdf"]:
                            st.caption("Download the PDF and upload it to this part's assignment on the LMS.")
                        else:
                            st.caption("Download and upload it to this part's assignment on the LMS "
                                       "(Print → Save as PDF in your browser if PDF is required).")
                    else:
                        st.caption("Press **Prepare my report** to build it with your latest progress.")

            # Start the whole simulation over — clears all progress and answers. Two-step
            # confirm so it can't wipe a student's work on a stray click.
            if not st.session_state.get("_confirm_reset"):
                st.button("↺  Start over", use_container_width=True, key="start_over_btn",
                          on_click=lambda: st.session_state.update(_confirm_reset=True),
                          help="Clear all progress and answers and go back to the very first step.")
            else:
                st.warning("This clears **all** progress, answers and reflections and returns you to "
                           "the first step. This can't be undone.")
                _co1, _co2 = st.columns(2)
                _co1.button("Yes, start over", type="primary", use_container_width=True,
                            key="start_over_yes", on_click=reset_simulation)
                _co2.button("Cancel", use_container_width=True, key="start_over_no",
                            on_click=lambda: st.session_state.update(_confirm_reset=False))

    # ---- Operations: the production line (both modes) ----
    with st.container(border=True, key="ops_card"):
        st.markdown('<div class="card-title">🎲 Operations — the production line</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<div class="card-sub">Each hour, every active operation rolls its <b>Dice</b> '
            '(each die has the chosen number of <b>Faces</b>) and may pass on the smaller of its '
            'roll and the inventory waiting in front of it. <b>Avg/hr</b> shows that station\'s '
            'designed average; the range is its worst case (all 1s) to best case (all max faces). '
            'Set Dice or Faces to 0 to switch an operation off.</div>',
            unsafe_allow_html=True)

        head = st.columns([0.62, 0.92, 0.30, 0.92, 0.30, 0.92])
        head[1].markdown('<div class="col-head">Dice</div>', unsafe_allow_html=True)
        head[3].markdown('<div class="col-head">Faces</div>', unsafe_allow_html=True)
        head[5].markdown('<div class="col-head">Avg / hr</div>', unsafe_allow_html=True)

        for i in range(N_OPS):
            stepper_row(i)

    # ---- Run settings (both modes) ----
    with st.container(border=True, key="settings_card"):
        st.markdown('<div class="card-title">📊 Run settings</div>', unsafe_allow_html=True)
        if SHOW_STARTINV:
            st.number_input(
                "Starting inventory in front of every station (bottles)",
                min_value=0, max_value=99999, step=1, key="starting_inventory",
                help="How many bottles are already waiting in front of each station when the clock "
                     "starts. 0 reproduces the textbook empty-line case.")
        st.selectbox(
            "Run length", options=list(HORIZONS.keys()), key="horizon",
            help="How long the line runs. A full year averages the dice out and shows the "
                 "line's steady rate; a shift or a week shows the swings a real shop floor "
                 "lives with — the same line, seen up close.")
        if not is_full_year_run():
            st.caption("⏱️ Short run: expect big swings between runs, and a slow start while the "
                       "line fills up. Challenges are still judged over a full year.")
        if SHOW_YEARS and is_full_year_run():
            st.number_input(
                "Years to simulate (1–5)",
                min_value=1, max_value=MAX_YEARS, step=1, key="simulation_years",
                help=f"Each year is {DAYS_PER_YEAR} working days × {HOURS_PER_DAY} hours = "
                     f"{HOURS_PER_YEAR:,} dice rolls per operation.")
        st.checkbox(
            "Play the run animation", key="animate",
            help="Plays the line back day by day when you press Run. Uncheck for a faster, lighter "
                 "run that jumps straight to the results — recommended when many people are using the "
                 "app at the same time.")
        if st.session_state.get("animate", True):
            st.select_slider(
                "Playback speed",
                options=["Instant", "Fast", "Normal", "Slow"],
                key="anim_speed",
                help="How fast the live dashboard plays the days back. Instant skips straight to the "
                     "finished results.")

    # ---- Variability switches — only the sub-controls this lab actually uses ----
    _ordersz_here = SHOW_ORDERSZ and not SHOW_EOQ   # EOQ labs show order size in the cost card
    if SHOW_SUPPLY or SHOW_DEMAND or _ordersz_here:
        with st.container(border=True, key="var_card"):
            st.markdown('<div class="card-title">🎰 Supply, demand & order size</div>',
                        unsafe_allow_html=True)
            if SHOW_SUPPLY:
                st.slider(
                    "🚚 Supplier reliability", min_value=0, max_value=100, step=5,
                    key="supply_reliability", format="%d%%",
                    help="How often the raw-material supplier delivers on time. At 100% Operation 1 "
                         "always has bottles and is never starved. Below 100%, each delivery is missed "
                         "at random with that probability; the line re-orders and the next attempt may "
                         "arrive, so material is delayed, not lost. While it's late, the raw buffer "
                         "drains and Operation 1 idles once it runs dry.")
                if int(st.session_state["supply_reliability"]) >= 100:
                    st.caption("Supplier is fully reliable — Operation 1 is never starved (just-in-time).")
                else:
                    st.caption(f"≈ {100 - int(st.session_state['supply_reliability'])}% of hours the "
                               f"delivery is missed; raw material in front of Operation 1 swings and the "
                               f"line can starve.")
            if _ordersz_here:
                st.number_input(
                    "📦 Raw-material order size (bottles/order)", min_value=1, step=10,
                    key="fin_order_size",
                    help="How many bottles the supplier delivers per purchase order — this sets how much "
                         "raw material sits in front of Operation 1. Order size 1 is just-in-time "
                         "(almost no raw inventory, but exposed to a missed delivery). Larger orders "
                         "arrive as batches of cycle stock that cushion an unreliable supplier — but tie "
                         "up inventory.")
                _osz = int(st.session_state["fin_order_size"])
                if _osz <= 1:
                    st.caption("Just-in-time: deliveries match consumption, so raw inventory stays near "
                               "zero — lean, but fully exposed to a shaky supplier.")
                else:
                    st.caption(f"Batches of {_osz:,} arrive and draw down as the line consumes them, so "
                               f"raw inventory averages ≈ {max(1, _osz // 2):,} bottles of cycle stock.")
            if SHOW_DEMAND:
                st.toggle(
                    "📉 Variable demand (finite, fluctuating market)", key="demand_variable",
                    help="On: each hour the market orders a random number of bottles. Unsold bottles "
                         "wait in finished-goods inventory (holding cost) and orders you can't fill are "
                         "lost. Off: every bottle produced is sold.")
                if st.session_state["demand_variable"]:
                    dcol = st.columns(2)
                    dcol[0].number_input("Demand dice", min_value=1, max_value=9, step=1,
                                         key="demand_dice")
                    dcol[1].number_input("Demand faces", min_value=2, max_value=12, step=1,
                                         key="demand_faces")
                    dmean = int(st.session_state["demand_dice"]) * (int(st.session_state["demand_faces"]) + 1) / 2
                    st.caption(f"Average demand ≈ {dmean:.1f} bottles/hr "
                               f"({dmean * HOURS_PER_YEAR:,.0f}/yr). Match this to the line's capacity, or "
                               f"mismatch it to see lost sales and finished-goods pile-ups.")

    # ---- EOQ: order size + its two cost drivers (EOQ labs, the money capstone, and Sandbox) ----
    if SHOW_EOQ:
        with st.container(border=True, key="eoqcost_card"):
            st.markdown('<div class="card-title">📦 Order size & its costs</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Set how many bottles you buy per order, then the two '
                        'costs that pull against each other. Re-run to see the best order size (EOQ) and '
                        'the total-cost curve move.</div>', unsafe_allow_html=True)
            st.number_input(
                "📦 Raw-material order size (bottles/order)", min_value=1, step=10,
                key="fin_order_size",
                help="How many bottles you buy per purchase order. Small orders mean many orders "
                     "(high ordering cost); large orders sit as inventory (high holding cost). The EOQ "
                     "is the size that balances the two.")
            fin_number_input(
                st, "Ordering cost — S ($/order)", "fin_order_cost", DEFAULT_ORDER_COST,
                wprefix="sb", min_value=1.0, step=5.0, format="%.2f",
                help="Must be at least $1 — a zero ordering cost makes the EOQ math meaningless.")
            fin_number_input(
                st, "Holding cost — H ($/bottle/day)", "fin_raw_holding", DEFAULT_RAW_HOLDING,
                wprefix="sb", min_value=0.005, step=0.01, format="%.3f",
                help="Must be positive — a zero holding cost makes the EOQ math meaningless.")
            _hy = float(st.session_state.get("fin_raw_holding", DEFAULT_RAW_HOLDING)) * DAYS_PER_YEAR
            st.caption(f"Holding works out to ≈ \\${_hy:,.2f} per bottle per year. "
                       f"EOQ = √(2·D·S ÷ H): bigger with demand and ordering cost, smaller with holding.")

    # ---- WIP limits (flow labs + Sandbox) ----
    if SHOW_WIP:
        with st.container(border=True, key="wip_card"):
            st.markdown('<div class="card-title">🚧 WIP limits</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Cap the inventory allowed to wait in front of each '
                        'station — a pull / Kanban control. A tight cap slashes work-in-process '
                        '(and its holding cost) but can throttle throughput; leave it off for an '
                        'uncapped push line.</div>', unsafe_allow_html=True)
            st.toggle("Cap work-in-process per station", key="wip_limit_on")
            if st.session_state["wip_limit_on"]:
                st.caption("Max bottles allowed in front of each active station. **0 blocks flow "
                           "entirely**; a large number is effectively unlimited.")
                caps_tmp = [int(st.session_state[f"capacity_{i}"]) for i in range(N_OPS)]
                sides_tmp = [int(st.session_state[f"sides_{i}"]) for i in range(N_OPS)]
                active_tmp = [(c > 0 and s > 0) for c, s in zip(caps_tmp, sides_tmp)]
                if any(active_tmp):
                    for i in range(N_OPS):
                        if active_tmp[i]:
                            st.number_input(
                                f"Op #{i + 1} — max units of WIP",
                                min_value=WIP_CAP_MIN, max_value=WIP_CAP_MAX, step=1,
                                key=f"wip_cap_{i}")
                else:
                    st.caption("Configure at least one operation above to set its WIP cap.")

    # ---- Reorder point / safety stock (Safety-Stock lab + Sandbox) ----
    if SHOW_SAFETY:
        with st.container(border=True, key="safety_card"):
            st.markdown('<div class="card-title">🚚 Reorder point</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">When raw material in front of Operation 1 falls to this '
                        'level, a new supplier order is placed. Raise it to hold more <b>safety '
                        'stock</b> — insurance against an unreliable supplier — at the cost of more raw '
                        'inventory. Off = automatic (≈ one hour of Operation 1\'s needs).</div>',
                        unsafe_allow_html=True)
            st.toggle("Set the reorder point manually", key="reorder_point_on")
            if st.session_state["reorder_point_on"]:
                st.number_input("Reorder point (bottles)", min_value=1, max_value=2000, step=5,
                                key="reorder_point")
                st.caption("Higher ⇒ order earlier, fatter cushion, higher service level — but more "
                           "raw-material holding cost.")

    # ---- Quality / yield (Quality lab + Sandbox) ----
    if SHOW_QUALITY:
        with st.container(border=True, key="quality_card"):
            st.markdown('<div class="card-title">✅ Quality &amp; yield</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Give any station a <b>scrap rate</b>: that share of the '
                        'units it works on fail inspection and are discarded. A station\'s good output '
                        'is its speed × its yield, so a fast station with high scrap can quietly become '
                        'the real constraint.</div>', unsafe_allow_html=True)
            st.toggle("Enable per-station scrap", key="scrap_on")
            if st.session_state["scrap_on"]:
                caps_q = [int(st.session_state[f"capacity_{i}"]) for i in range(N_OPS)]
                sides_q = [int(st.session_state[f"sides_{i}"]) for i in range(N_OPS)]
                active_q = [(c > 0 and s > 0) for c, s in zip(caps_q, sides_q)]
                if any(active_q):
                    for i in range(N_OPS):
                        if active_q[i]:
                            st.number_input(f"Op #{i + 1} — scrap %", min_value=0, max_value=95,
                                            step=5, key=f"scrap_pct_{i}")
                else:
                    st.caption("Configure at least one operation above to set its scrap rate.")

    # ---- Financials editor (Sandbox only) ----
    if not is_lab:
        with st.container(border=True, key="fin_card"):
            st.markdown('<div class="card-title">💵 Financials</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Set revenue and costs to turn throughput into a '
                        'profit-and-loss statement.</div>', unsafe_allow_html=True)
            if st.button("💵 Set Financials", use_container_width=True):
                financials_dialog()
            st.caption(
                f"Now: ${float(st.session_state['fin_revenue_per_unit']):.2f}/bottle revenue · "
                f"{float(st.session_state['fin_alloc_pct']):g}% allocation · "
                f"${float(st.session_state['fin_wip_holding']):.2f}/bottle/day WIP")

    # ---- Run / actions (both modes, replications included) ----
    with st.container(border=True, key="actions_card"):
        st.markdown('<div class="card-title">🚀 Run</div>', unsafe_allow_html=True)
        caps_now = [int(st.session_state[f"capacity_{i}"]) for i in range(N_OPS)]
        sides_now = [int(st.session_state[f"sides_{i}"]) for i in range(N_OPS)]
        errs_now = validation_errors(caps_now, sides_now)
        _runlabel = "▶  Run this line" if is_lab else "▶  Run Simulation"
        run_clicked = st.button(_runlabel, type="primary",
                                use_container_width=True, disabled=bool(errs_now))
        if is_lab:
            st.caption("Re-runs the line as currently configured — handy after you tweak a control "
                       "to test your own \"what if\".")
        if SHOW_REPS:
            st.number_input("Replications (run the line many times)", min_value=2, max_value=100,
                            step=1, key="n_reps",
                            help="Run the same line this many times to see the spread of outcomes. "
                                 "Each replication is one run of the chosen length with fresh "
                                 "randomness. One run is a sample; the spread is the process.")
            reps_clicked = st.button(f"🎲  Run {int(st.session_state['n_reps'])} replications",
                                     use_container_width=True, disabled=bool(errs_now))
            if is_lab:
                st.caption("Use this when you want to know whether a difference you're seeing is "
                           "real or just the dice. Replications never count as a challenge try.")
        st.button("↺ Reset to defaults", use_container_width=True, on_click=reset_defaults)

    # ---- Plain-English glossary (both modes) ----
    render_glossary_card()

# =========================================================
# MAIN WINDOW — simulation & results
# =========================================================
# If the student just navigated (Previous/Next, a new lab, mode change), snap the view back
# to the top once. No-op on ordinary reruns (typing, running a step).
_scroll_to_top_on_nav()

st.markdown(
    """
    <div class="hero">
        <h1>🧃 Juicetification: Capacity Crush</h1>
        <p>Run a juice-bottling line and watch throughput (bottles/hour), work-in-process, and the
        line's constraint emerge live — every output updates day by day as the line runs.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---- Honest save / progress status + a storage-independent backup & restore ----
# Three cases are reported truthfully: (a) shared storage on and saving; (b) a save that
# FAILED (never claim saved work was saved); (c) no server storage configured, in which case
# progress still rides in the page URL and a downloadable backup is the portable fallback.
def _save_status_line():
    if store.enabled() and _STORE_SID:
        if st.session_state.get("_save_failed"):
            st.warning(f"⚠️ Signed in as **{_STORE_SID}**, but the last save did **not** go through. "
                       "Your latest changes are still in this browser (and in the page link); "
                       "use **Back up my progress** below so nothing is lost.")
        else:
            _ts = st.session_state.get("_last_saved_ts")
            if _ts:
                _ago = max(0, int(time.time() - _ts))
                _when = ("just now" if _ago < 5 else f"{_ago}s ago" if _ago < 60
                         else f"{_ago // 60}m ago" if _ago < 3600 else "earlier today")
                st.caption(f"✅ Signed in as **{_STORE_SID}** · progress saved to the course server ({_when}).")
            else:
                st.caption(f"✅ Signed in as **{_STORE_SID}** · progress saves automatically to the course server.")
    else:
        st.caption("💾 Progress is kept in this page's link (bookmark it to resume on this device). "
                   "No course server is configured, so use **Back up my progress** below to save a file "
                   "you can restore anywhere.")

_save_status_line()

with st.expander("💾 Back up / restore my progress"):
    st.caption("Download a backup file of everything you've done (predictions, answers, reflections, "
               "challenge attempts and your place in the labs). You can restore it later on any device — "
               "this works even if the course server storage is off.")
    try:
        _backup = {
            "_backup_kind": "juicetification_progress",
            "_backup_version": 1,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "snapshot": _progress_snapshot(),
            "answers": {k: st.session_state[k] for k in list(st.session_state.keys())
                        if isinstance(k, str) and ("_pred_" in k or "_est_" in k)
                        and st.session_state[k] is not None},
        }
        st.download_button(
            "⬇️ Back up my progress (.json)",
            data=json.dumps(_backup, separators=(",", ":")).encode("utf-8"),
            file_name="capacity_crush_progress.json", mime="application/json",
            use_container_width=True, key="backup_dl")
    except Exception:
        st.info("Backup isn't available right now — try again after your next step.")

    _up = st.file_uploader("Restore from a backup file", type=["json"], key="backup_up",
                           label_visibility="collapsed")
    if _up is not None:
        import hashlib as _hl
        _raw = _up.getvalue()
        _sig = _hl.sha256(_raw).hexdigest()
        if st.session_state.get("_backup_applied") != _sig:
            try:
                _data = json.loads(_raw.decode("utf-8"))
                if isinstance(_data, dict) and _data.get("_backup_kind") == "juicetification_progress":
                    _restore_progress(_data.get("snapshot", {}))
                    for _k, _v in (_data.get("answers", {}) or {}).items():
                        if isinstance(_k, str) and ("_pred_" in _k or "_est_" in _k):
                            st.session_state[_k] = _v
                    st.session_state["_backup_applied"] = _sig
                    _save_answers()
                    _autosave()
                    st.success("Progress restored from your backup. ✅")
                    _nav_bump()
                    st.rerun()
                else:
                    st.error("That file isn't a Capacity Crush progress backup.")
            except Exception:
                st.error("Couldn't read that backup file — it may be incomplete or from another app.")

with st.expander("How this works & what each output means"):
    st.markdown(
        f"""
This is a juice-bottling line built on Goldratt's classic **dice game**. It shows why a line of
bottling stations with the *same* average capacity still finishes fewer bottles than that average —
because of **statistical fluctuation** (each station bottles a different number each hour) combined
with **dependency** (a station can never pass on more bottles than are waiting in front of it).

**Inputs**
- **Dice (capacity)** — how many dice a station rolls each hour to set its bottling rate.
- **Faces (sides)** — how many faces each die has. A station's average output per hour is
  `dice × (faces + 1) / 2` **bottles/hour**, from `dice` (all 1s) to `dice × faces` (all max).
- **Starting inventory** — bottles already waiting in front of each station at hour 0.
- **Supplier reliability** — how often the raw-material supplier delivers; at 100% Operation 1 is
  never starved, below 100% it can run dry.
- **Order size** (in the **Variability** card, next to supplier reliability) — bottles per raw-material
  purchase order. Order size 1 is just-in-time (almost no raw inventory); larger orders arrive in batches
  that sit as cycle stock in front of Operation 1 (≈ half the order size on average) and cushion an
  unreliable supplier.
- **Years** — each year = {DAYS_PER_YEAR} days × {HOURS_PER_DAY} hrs = {HOURS_PER_YEAR:,} hours of bottling.

**Outputs**
- **Total finished** — bottles that left the last station over the whole run.
- **Avg bottles / hr** — finished bottles ÷ hours; the line's *actual* throughput.
- **Constraint / hr** — the average capacity of the slowest station (the theoretical ceiling).
- **Efficiency** — actual throughput ÷ constraint ceiling. For any line of two or more stations it
  stays **below 100%**, and that gap is the whole point of the exercise.
- **Per-station panel & charts** — where WIP piles up (it gathers in front of the constraint),
  how cumulative output grows, and how total WIP drifts over time.
"""
    )

caps = [int(st.session_state[f"capacity_{i}"]) for i in range(N_OPS)]
sides = [int(st.session_state[f"sides_{i}"]) for i in range(N_OPS)]
errs = validation_errors(caps, sides)
results = st.session_state["sim_results"]

# Whether to render the charts/curves (the heaviest visuals). Defined unconditionally here so
# every section — the dashboard, the replication card, A/B comparison — can read it; the actual
# on/off checkbox is drawn in the results area below and writes this same session key.
st.session_state.setdefault("show_charts", CHARTS_DEFAULT_ON)
SHOW_CHARTS = bool(st.session_state["show_charts"])

active = [(c > 0 and s > 0) for c, s in zip(caps, sides)]
exp_avg = [round(c * (s + 1) / 2, 2) if a else 0.0 for c, s, a in zip(caps, sides, active)]
exp_min = [c if a else 0 for c, a in zip(caps, active)]
exp_max = [c * s if a else 0 for c, s, a in zip(caps, sides, active)]
active_exp = [e for e, a in zip(exp_avg, active) if a]
design_bottleneck = min(active_exp) if active_exp else 0.0

# The planned-line summary is sandbox context; in a lab the step already sets the line and
# the results below report the constraint, so it's just noise there.
if not IS_LAB:
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Active operations", sum(active))
    d2.metric("Total dice in line", sum(caps))
    d3.metric("Hours to simulate",
              f"{int(st.session_state['simulation_years']) * HOURS_PER_YEAR:,}")
    d4.metric("Slowest station avg / hr", f"{design_bottleneck:g}" if design_bottleneck else "—",
              help="The slowest station's average output. It sets the pace for the whole line — this is "
                   "the bottleneck (the constraint).")

# A Run button in the MAIN window (not only the sidebar), so it is always reachable without
# scrolling the sidebar. It requests a run via a flag the trigger picks up below.
_mrc1, _mrc2 = st.columns([2, 1])
with _mrc2:
    st.button("▶  Run this line" if IS_LAB else "▶  Run Simulation",
              type="primary", use_container_width=True, disabled=bool(errs),
              key="main_run_btn", on_click=_request_main_run)

# ---- Run trigger: simulate, then play every output back live into a placeholder ----
SPEED_DELAY = {"Instant": 0.0, "Fast": 0.012, "Normal": 0.03, "Slow": 0.07}
MAX_ANIM_FRAMES = 90    # cap animation steps: each frame is a websocket push and holds a server thread
if st.session_state.pop("lab_autorun", False):
    run_clicked = True
if st.session_state.pop("_main_run", False):
    run_clicked = True
if run_clicked and not errs:
    sim_hours = run_hours()
    if st.session_state["wip_limit_on"]:
        wip_limits = [int(st.session_state[f"wip_cap_{i}"]) for i in range(N_OPS)]
    else:
        wip_limits = [math.inf] * N_OPS
    dd, df = demand_params()
    _rop = (int(st.session_state["reorder_point"])
            if st.session_state.get("reorder_point_on") else None)
    _scrap = ([st.session_state.get(f"scrap_pct_{i}", 0) / 100.0 for i in range(N_OPS)]
              if st.session_state.get("scrap_on") else None)
    # Seed the run so a student's scenario is reproducible, but advance it every run so that
    # re-running the same line shows fresh variability (the whole point of the dice game) rather
    # than an identical result. run_counter starts at 0, so a student's/section's *first* run is
    # still identical for fair setup; each repeat draws a new (but deterministic) sample.
    if SCENARIO_SEED is not None:
        _session_rng().seed(SCENARIO_SEED + int(st.session_state.get("run_counter", 0)))
    # Show a spinner while the run computes, so the previous dashboard reads as "updating" rather
    # than lingering as a confusing ghost of the last run.
    with st.spinner("Running the line…"):
        full = run_simulation(
            caps, sides,
            int(st.session_state["starting_inventory"]),
            sim_hours,
            SUPPLY_REL,
            wip_limits,
            demand_dice=dd, demand_faces=df,
            order_size=int(st.session_state["fin_order_size"]),
            reorder_point=_rop, scrap=_scrap, rng=_session_rng(),
        )
    if full:
        if SHOW_EOQ:
            # EOQ labs, the money capstone, and Sandbox show the inventory cost-vs-order-size curve.
            _eoq_fin = get_fin()
            _eoq_margin = eoq_unit_margin(
                full, caps, sides, run_years(), _eoq_fin)
            full["eoq_scan"] = compute_eoq_scan(
                caps, sides, sim_hours, SUPPLY_REL,
                float(_eoq_fin["order_cost"]), _eoq_margin,
                include_q=int(st.session_state["fin_order_size"]),
                hold_per_day=float(st.session_state.get("fin_raw_holding", DEFAULT_RAW_HOLDING)))
        if not IS_EOQ_LAB:
            full["die_scan"] = compute_die_scan(
                caps, sides,
                int(st.session_state["starting_inventory"]),
                sim_hours,
                SUPPLY_REL,
                wip_limits,
                demand_dice=dd, demand_faces=df,
                order_size=int(st.session_state["fin_order_size"]),
            )
        full["config"] = {
            "dice": caps[:], "faces": sides[:],
            "supply_unlimited": st.session_state["unlimited_supply"],
            "supply_reliability": int(st.session_state["supply_reliability"]),
            "demand_variable": bool(st.session_state["demand_variable"]),
            "wip_on": bool(st.session_state["wip_limit_on"]),
            "wip_caps": ([int(st.session_state[f"wip_cap_{i}"]) for i in range(N_OPS)]
                         if st.session_state["wip_limit_on"] else None),
            "start_inv": int(st.session_state["starting_inventory"]),
            "order_size": int(st.session_state["fin_order_size"]),
            "revenue_per_unit": float(st.session_state.get("fin_revenue_per_unit", DEFAULT_REVENUE_PER_UNIT)),
            "order_cost": float(st.session_state.get("fin_order_cost", DEFAULT_ORDER_COST)),
            "raw_holding": float(st.session_state.get("fin_raw_holding", DEFAULT_RAW_HOLDING)),
            "reorder_on": bool(st.session_state.get("reorder_point_on")),
            "reorder_point": (int(st.session_state["reorder_point"])
                              if st.session_state.get("reorder_point_on") else None),
            "scrap_on": bool(st.session_state.get("scrap_on")),
            "scrap_pct": ([int(st.session_state.get(f"scrap_pct_{i}", 0)) for i in range(N_OPS)]
                          if st.session_state.get("scrap_on") else None),
            "years": run_years(),
            "horizon": st.session_state.get("horizon", DEFAULT_HORIZON),
        }
    delay = SPEED_DELAY.get(st.session_state["anim_speed"], 0.03)
    if st.session_state.get("animate", True) and full and delay > 0 and full["frames"]:
        frames = full["frames"]
        stride = max(1, len(frames) // MAX_ANIM_FRAMES)
        shown = frames[::stride]
        if shown[-1] is not frames[-1]:
            shown.append(frames[-1])
        if IS_LAB:
            # In a guided lab the student clicks Run at the bottom of the lab panel, so an
            # animation up here would play off-screen. Hand the frames to the lab panel, which
            # plays them right below the Run button (see render_lab) so the line is actually seen.
            st.session_state["_lab_anim"] = (shown, delay)
        else:
            frame_ph = st.empty()
            for fr in shown:
                frame_ph.html(build_live_dashboard(fr, full))
                time.sleep(delay)
            frame_ph.empty()          # clear the animation; final dashboard renders below
    if full:
        # Frames are only needed for the playback above (already sampled into `shown`); don't keep
        # the full per-hour frame list alive in session_state for the rest of the session.
        full["frames"] = []
    st.session_state["sim_results"] = full
    st.session_state["run_counter"] = st.session_state.get("run_counter", 0) + 1
    results = full
    if full:
        save_run_snapshot()

# ---- Replications trigger: run the line many times, collect the spread ----
if reps_clicked and not errs:
    sim_hours = run_hours()
    if st.session_state["wip_limit_on"]:
        wip_limits = [int(st.session_state[f"wip_cap_{i}"]) for i in range(N_OPS)]
    else:
        wip_limits = [math.inf] * N_OPS
    n_reps = int(st.session_state["n_reps"])
    dd, df = demand_params()
    prog = st.progress(0.0, text=f"Running {n_reps} replications…")
    rep = run_replications(
        caps, sides,
        int(st.session_state["starting_inventory"]),
        sim_hours,
        SUPPLY_REL,
        wip_limits,
        run_years(),
        get_fin(),
        n_reps,
        progress=lambda k, n: prog.progress(k / n, text=f"Replication {k} of {n}…"),
        demand_dice=dd, demand_faces=df,
        order_size=int(st.session_state["fin_order_size"]),
        rng=_session_rng(),
    )
    prog.empty()
    rep["meta"] = {
        "n": n_reps,
        "config": {
            "dice": caps[:], "faces": sides[:],
            "supply_unlimited": st.session_state["unlimited_supply"],
            "supply_reliability": int(st.session_state["supply_reliability"]),
            "demand_variable": bool(st.session_state["demand_variable"]),
            "wip_on": bool(st.session_state["wip_limit_on"]),
            "years": run_years(),
            "horizon": st.session_state.get("horizon", DEFAULT_HORIZON),
        },
    }
    st.session_state["rep_results"] = rep

# ---- Guided Lab panel (directed exercise) sits above the dashboard ----
if st.session_state.get("app_mode") == "Guided Lab":
    _prefix = lab_prefix_from_choice(st.session_state.get("lab_choice", ""))
    # Anchor the top of the lab panel so navigation scrolls the tasks into view (not the
    # title/metrics above them). scroll-margin-top keeps it clear of the fixed header.
    st.markdown('<div id="jcc-labtop" style="scroll-margin-top: 5rem;"></div>',
                unsafe_allow_html=True)
    render_lab(results, _prefix)

if errs:
    st.markdown('<div class="pill pill-warn">⚠️ Fix the setup in the sidebar to enable Run.</div>',
                unsafe_allow_html=True)
    for e in errs:
        st.warning(e)

elif results is None:
    st.markdown('<div class="pill pill-info">Configure the line in the sidebar, then press '
                '<b>Run Simulation</b>. Every output below will fill in live as the days play.</div>',
                unsafe_allow_html=True)
    with st.container(border=True, key="config_card"):
        st.markdown('<div class="card-title">Planned line</div>', unsafe_allow_html=True)
        st.dataframe(
            pd.DataFrame({
                "Operation": [f"#{i + 1}" for i in range(N_OPS)],
                "Dice": caps,
                "Faces": sides,
                "Status": ["Active" if a else "Inactive" for a in active],
                "WIP cap": [
                    (str(int(st.session_state[f"wip_cap_{i}"]))
                     if st.session_state["wip_limit_on"] and active[i] else "∞")
                    for i in range(N_OPS)
                ],
                "Min / hr": exp_min,
                "Avg / hr": exp_avg,
                "Max / hr": exp_max,
            }),
            use_container_width=True, hide_index=True,
        )

else:
    st.markdown('<div class="pill pill-run">🚀 Simulation complete.</div>', unsafe_allow_html=True)
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Total bottles finished", f"{results['total_output']:,}")
    r2.metric("Avg bottles / hr", f"{results['avg_out_hr']:.2f}",
              help="The line's average output rate — its throughput (λ).")
    r3.metric("Constraint / hr (max)", f"{results['theoretical_hr']:.2f}",
              help="The bottleneck's speed — the most bottles per hour the line could ever make.")
    r4.metric("Line efficiency", f"{results['efficiency'] * 100:.1f}%",
              help="Actual output ÷ the bottleneck ceiling. The gap is lost to fluctuation and "
                   "the way stations depend on each other.")

    cap_msg = (f"Constraint (slowest station): Operation {results['bottleneck_label']}. "
               f"The line finished {results['avg_out_hr']:.2f} bottles/hr against a ceiling of "
               f"{results['theoretical_hr']:.2f} — the {100 - results['efficiency'] * 100:.1f}% gap "
               f"is the cost of fluctuation plus dependency.")
    if not results["unlimited_supply"]:
        cap_msg += (f" Operation 1 was starved of raw material in "
                    f"{results['starved_hours']:,} of {results['hours']:,} hours.")
    if results.get("wip_capped"):
        cap_msg += (" WIP is capped per station, so a full downstream buffer can block an upstream "
                    "station — throughput is held down by the tightest cap as well as the constraint.")
    st.caption(cap_msg)

    _ss = steady_state_note(results)
    if _ss:
        st.info(_ss)

    # Charts are the heaviest thing to draw and redraw every interaction, so they're OFF by
    # default. A prominent switch (contextually placed with the results) turns them on. SHOW_CHARTS
    # was already read from session above so every section agrees on it this run.
    with st.container(border=True, key="charts_toggle_card"):
        _tc1, _tc2 = st.columns([0.62, 0.38])
        with _tc1:
            st.markdown('<div class="charts-toggle-lead">📊 <b>Charts &amp; graphs</b><br>'
                        '<span>Line/bar charts and the cost-curve pictures. Off by default to keep the '
                        'app fast for everyone; flip the switch to see them.</span></div>',
                        unsafe_allow_html=True)
        with _tc2:
            st.toggle("Show charts", key="show_charts",
                      help="Turn on to draw the graphs for this run. The numbers, tables and the "
                           "per-operation panel always show either way.")
    if not SHOW_CHARTS:
        st.markdown('<div class="charts-off-note">📊 Charts are <b>off</b> — the numbers and the '
                    'per-operation panel below are live. Flip <b>Show charts</b> above to add the graphs.'
                    '</div>', unsafe_allow_html=True)

    # ---- Raw-material availability, customer fill rate & yield readout ----
    # These are DISTINCT measures (see glossary): availability is the supplier/line side (hours
    # Op 1 wasn't starved); fill rate is the customer side (demand actually sold).
    _cfg = results.get("config", {})
    _extra = []
    if _cfg.get("reorder_on") or not results["unlimited_supply"]:
        _extra.append(("Raw-material availability",
                       f"{results.get('service_level', 1.0) * 100:.1f}%"))
    if results.get("demand_on"):
        _extra.append(("Customer fill rate", f"{results.get('fill_rate', 1.0) * 100:.1f}%"))
    if _cfg.get("reorder_on"):
        _extra.append(("Reorder point", f"{results.get('reorder_point', 0):,}"))
    if _cfg.get("scrap_on"):
        _extra.append(("Yield (good units)", f"{results.get('yield_rate', 1.0) * 100:.1f}%"))
        _extra.append(("Bottles scrapped", f"{results.get('scrap_total', 0):,}"))
    if _extra:
        _cols = st.columns(len(_extra))
        for _c, (_lbl, _val) in zip(_cols, _extra):
            _c.metric(_lbl, _val)
        if _cfg.get("scrap_on"):
            _eff = results.get("eff_bottleneck_label", "?").strip("#")
            _ceil = results.get("eff_bottleneck_rate", 0) * results.get("hours", 0)
            st.caption(f"Accounting for yield, the effective constraint is Operation {_eff} — its "
                       f"good-output ceiling is ≈ {_ceil:,.0f} bottles, well below what nominal speeds "
                       f"suggest. A station's real capacity is its speed × its yield.")

    if SHOW_LINE_DETAIL:
        with st.container(border=True, key="opdetail_card"):
            st.markdown('<div class="card-title">Per-operation results</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Green bar = bottles of inventory left waiting in front of '
                        'each operation at the end; boxes show that station\'s hourly averages over the '
                        'whole run. Inventory piles up in front of the constraint.</div>',
                        unsafe_allow_html=True)
            st.html(render_op_panel(
                results["op_detail"],
                # When a station scraps, the real constraint is the yield-adjusted one (its good
                # output is what limits the line). Label that so the picture matches the text.
                (results.get("eff_bottleneck_label") if results.get("config", {}).get("scrap_on")
                 else results["bottleneck_label"]),
                raw_inv=(results.get("end_raw", results.get("raw_series", [0])[-1] if results.get("raw_series") else 0)
                         if results.get("show_raw", True) else None),
                fgi=(results.get("end_fgi", 0) if results.get("show_fgi", results.get("demand_on")) else None),
            ))

        if SHOW_CHARTS:
            with st.container(border=True, key="results_card"):
                st.markdown('<div class="card-title">Cumulative bottles finished</div>', unsafe_allow_html=True)
                st.line_chart(results["df_cum"], height=240)

                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown('<div class="card-title">Total WIP over time</div>', unsafe_allow_html=True)
                    st.line_chart(results["df_wip"], height=220)
                with cc2:
                    st.markdown('<div class="card-title">Ending WIP by station</div>', unsafe_allow_html=True)
                    if len(results["df_end"]) > 0:
                        st.bar_chart(results["df_end"], height=220)
                    else:
                        st.info("Single-station line — no inter-station WIP.")

            with st.expander("Bottles finished each day"):
                st.line_chart(results["df_daily"], height=220)

    # ---- Inventory at the ends of the line — raw material and finished goods on
    #      their own charts (raw always; finished goods only when demand fluctuates) ----
    show_raw = results.get("show_raw", True)
    show_fgi = results.get("show_fgi", results.get("demand_on", False))
    if show_raw or show_fgi:
        with st.container(border=True, key="inv_card"):
            st.markdown('<div class="card-title">📦 Inventory at the ends of the line</div>',
                        unsafe_allow_html=True)
            osz = results.get("order_size", results.get("config", {}).get("order_size", 1))
            avg_raw = (sum(results["raw_series"]) / len(results["raw_series"])
                       if results.get("raw_series") else 0)
            if show_raw:
                jit = " (just-in-time — almost no raw inventory)" if osz <= 1 else ""
                st.markdown(
                    f'<div class="card-sub"><b style="color:#0284c7">Raw material</b> waiting in front '
                    f'of Operation 1 — this is the inventory at Op 1. With an order size of '
                    f'<b>{osz:,}</b> bottles{jit}, the supplier delivers in batches that draw down as '
                    f'the line consumes them.</div>', unsafe_allow_html=True)
                if SHOW_CHARTS:
                    st.line_chart(results["df_raw"], height=240, color="#0ea5e9")
                rc = st.columns(3)
                rc[0].metric("Avg raw material on hand", f"{avg_raw:,.0f}")
                rc[1].metric("Peak raw material",
                             f"{max(results['raw_series']):,}" if results.get("raw_series") else "0")
                rc[2].metric("Op 1 starved", f"{results.get('starved_hours', 0):,} hrs",
                             help="Hours Operation 1 sat idle waiting on raw material it hadn't received.")
            if show_fgi:
                st.markdown(
                    '<div class="card-sub" style="margin-top:14px"><b style="color:#7c3aed">Finished '
                    'goods</b> (bottled juice) piling up after the last station whenever the line '
                    'out-produces a fluctuating market.</div>', unsafe_allow_html=True)
                if SHOW_CHARTS:
                    st.line_chart(results["df_fgi"], height=240, color="#9333ea")
                fc = st.columns(3)
                fc[0].metric("Avg finished goods", f"{results.get('avg_fgi', 0):,.0f}",
                             help="Finished bottles made but not yet sold — finished-goods inventory "
                                  "(FGI). This inventory costs money to hold.")
                fc[1].metric("Peak finished goods",
                             f"{max(results['fgi_series']):,}" if results.get("fgi_series") else "0")
                fc[2].metric("Lost sales", f"{results.get('lost_sales', 0):,} bottles",
                             help="Demand the line couldn't fill because it ran out of finished goods.")

    # ---- Flow time: Little's Law (derived) vs measured, with a distribution ----
    if SHOW_FLOWTIME and "flow_time_derived" in results:
        Wd = results["flow_time_derived"]
        Wm = results["flow_time_measured"]
        L = results["wip_L"]
        lam = results["throughput_rate"]
        gap = (abs(Wd - Wm) / Wd * 100) if Wd else 0.0
        with st.container(border=True, key="flow_card"):
            st.markdown('<div class="card-title">⏱️ Flow time — Little\'s Law (L = λ × W)</div>',
                        unsafe_allow_html=True)
            st.markdown('<div class="card-sub">How long an average unit spends in the line from '
                        'release to finish. <b>Derived</b> comes from Little\'s Law (average WIP ÷ '
                        'throughput); <b>measured</b> tags each unit and averages its actual '
                        'time-in-system. In a stable line the two agree.</div>',
                        unsafe_allow_html=True)
            fcols = st.columns(4)
            fcols[0].metric("Work-in-process (L)", f"{L:,.1f}",
                            help="Bottles sitting between stations, waiting to be worked on "
                                 "(work-in-process, L).")
            fcols[1].metric("Throughput (λ)", f"{lam:.2f}/hr",
                            help="Good bottles finished per hour — the line's output rate (λ, lambda).")
            fcols[2].metric("Flow time (W), predicted", f"{Wd / HOURS_PER_DAY:.2f} d",
                            help=f"How long an average bottle spends in the line, from Little's Law "
                                 f"W = L ÷ λ = {Wd:.1f} hours.")
            fcols[3].metric("Flow time (W), measured", f"{Wm / HOURS_PER_DAY:.2f} d",
                            help=f"How long an average bottle actually spent in the line, timed "
                                 f"directly = {Wm:.1f} hours.")
            st.markdown(
                f'<div class="ll-eq">L ÷ λ = {L:,.1f} ÷ {lam:.2f} = <b>{Wd:.1f} hours</b> '
                f'({Wd / HOURS_PER_DAY:.2f} days) &nbsp;•&nbsp; measured = <b>{Wm:.1f} hours</b> '
                f'({Wm / HOURS_PER_DAY:.2f} days)</div>', unsafe_allow_html=True)

            if gap <= 8:
                st.success(f"Derived and measured agree within {gap:.1f}% — the line is stable, so "
                           f"Little's Law holds: less WIP means a shorter, more predictable lead time.")
            else:
                st.warning(f"Derived and measured differ by {gap:.0f}%. WIP is still piling up (the "
                           f"constraint can't keep up), so the line never reaches steady state — much "
                           f"of the WIP hasn't finished yet, and measured flow time lags the L ÷ λ "
                           f"projection. That gap *is* the instability.")

            hist = flow_histogram_series(results.get("flow_counts"))
            if SHOW_CHARTS and hist is not None and len(hist) > 1:
                st.markdown('<div class="card-title" style="margin-top:0.6rem">Flow-time distribution'
                            '</div>', unsafe_allow_html=True)
                st.markdown('<div class="card-sub">Spread of actual lead times — what customers '
                            'feel. A long right tail means some bottles wait far longer than the '
                            'average.</div>', unsafe_allow_html=True)
                st.bar_chart(hist, height=220)
                st.caption(
                    f"Lead time — fastest {results['flow_min']} h · median {results['flow_median']} h · "
                    f"90th percentile {results['flow_p90']} h · slowest {results['flow_max']} h "
                    f"({results['flow_min'] / HOURS_PER_DAY:.1f}–{results['flow_max'] / HOURS_PER_DAY:.1f} days).")

    if results.get("eoq_scan"):
        scan = results["eoq_scan"]
        cur_q = results.get("config", {}).get("order_size")
        cur = eoq_row_for(scan, cur_q) if cur_q is not None else None
        unreliable = scan.get("reliability", 1.0) < 1.0
        with st.container(border=True, key="eoqresult_card"):
            st.markdown('<div class="card-title">📦 Inventory cost vs. order size</div>',
                        unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Total raw-material cost split into <b>ordering</b> '
                        '(orange) and <b>holding</b> (sky)' +
                        (', plus <b>stockout</b> (red) when an unreliable supplier starves the line'
                         if unreliable else '') +
                        '. ★ marks the cheapest order size; the outlined bar is your current line. '
                        'The classic EOQ formula predicts the bottom of this curve. The line is held '
                        'fixed — only the raw-material order size varies.</div>',
                        unsafe_allow_html=True)

            em1, em2, em3, em4 = st.columns(4)
            em1.metric("EOQ formula", f"{scan['eoq']:.0f}",
                       help="Economic Order Quantity — the order size that makes total ordering + "
                            "holding cost as small as possible. EOQ = √(2·D·S ÷ H), where D is yearly "
                            "demand, S is the cost per order, and H is the holding cost per bottle.")
            em2.metric("Cheapest order (simulated)", f"{scan['best_q']:,}")
            em3.metric("Cheapest total cost", f"${scan['best_total']:,.0f}")
            if cur is not None:
                em4.metric("Your order size", f"{cur['Q']:,}",
                           help="The order size currently set in the Variability card.")

            if SHOW_CHARTS:
                st.html(build_eoq_curve_html(scan, current_q=cur_q))

            if unreliable:
                st.caption(f"Supplier is {int(scan['reliability'] * 100)}% reliable, so missed "
                           f"deliveries add a stockout cost the EOQ formula ignores — pushing the "
                           f"cheapest order size up to {scan['best_q']:,}, above the textbook EOQ of "
                           f"{scan['eoq']:.0f}.")
            else:
                st.caption(f"Reliable supplier: the simulated optimum ({scan['best_q']:,}) lands right "
                           f"on the EOQ formula's prediction ({scan['eoq']:.0f}), and the flat bottom "
                           f"means nearby order sizes cost only a little more.")

    if SHOW_FINANCIALS:
        # ---- Financial results: profit & loss for this run ----
        fin = get_fin()
        fr = compute_financials(results, caps, sides,
                                run_years(), fin)
        with st.container(border=True, key="finresult_card"):
            st.markdown('<div class="card-title">💵 Financial results</div>', unsafe_allow_html=True)
            st.markdown('<div class="card-sub">Profit and loss for this run, using the figures in '
                        '<b>Set Financials</b> (sidebar). Edit them there and these numbers update '
                        'instantly — no need to re-run the simulation.</div>', unsafe_allow_html=True)

            fm1, fm2, fm3, fm4 = st.columns(4)
            fm1.metric("Net profit", f"${fr['profit']:,.0f}")
            fm2.metric("Profit / unit", f"${fr['profit_per_unit']:,.2f}")
            fm3.metric("Revenue", f"${fr['revenue']:,.0f}")
            fm4.metric("Margin", f"{fr['margin']:.1f}%")

            st.html(render_pnl(fr))

            if SHOW_CHARTS:
                st.markdown('<div class="card-title" style="margin-top:0.8rem">Where the money goes</div>',
                            unsafe_allow_html=True)
                st.bar_chart(
                    pd.Series({
                        "Production": fr["prod_cost"],
                        "Fixed (dies)": fr["fixed_alloc"],
                        "WIP holding": fr["wip_cost"],
                        "Raw material": fr["raw_cost"],
                        "Ordering": fr["order_cost"],
                    }, name="Cost ($)"),
                    height=220,
                )

            # ---- Profit vs. die size: the sweet-spot curve ----
            scan = results.get("die_scan")
            if scan and SHOW_CHARTS:
                curve = [
                    {"faces": e["faces"],
                     "profit": compute_financials(
                         e["res"], caps, e["sides"],
                         run_years(), fin)["profit"]}
                    for e in scan
                ]
                act_sides = [s for c, s in zip(caps, sides) if c > 0 and s > 0]
                current_faces = act_sides[0] if act_sides and len(set(act_sides)) == 1 else None
                best = max(curve, key=lambda c: c["profit"])

                st.markdown('<div class="card-title" style="margin-top:0.8rem">Profit vs. die size '
                            '— the sweet spot</div>', unsafe_allow_html=True)
                st.markdown('<div class="card-sub">Annual profit if <b>every active station</b> used the '
                            'same die (keeping each station\'s number of dice). Bigger dice add throughput '
                            'but cost far more capital — so profit peaks in the middle. '
                            '★ marks the best size; the outlined bar is your current line.</div>',
                            unsafe_allow_html=True)
                st.html(build_profit_curve_html(curve, current_faces))
                st.caption(f"Most profitable uniform die here: {best['faces']}-sided "
                           f"(≈ ${best['profit']:,.0f}/yr). Curve is computed at your current dice count, "
                           f"starting inventory, supply mode, and "
                           f"{st.session_state.get('horizon', DEFAULT_HORIZON).lower()} "
                           f"horizon; it updates instantly when you change the financials.")

    if SHOW_SANDBOX_TOOLS:
        # ---- Pin this run for A/B comparison ----
        st.markdown('<div class="card-title" style="margin-top:0.4rem">📌 Compare scenarios</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="card-sub">Freeze this run as A or B, change one lever in the sidebar, '
                    'run again, and pin the other slot to see the before/after side by side.</div>',
                    unsafe_allow_html=True)
        pcol1, pcol2, pcol3 = st.columns(3)
        pcol1.button("📌 Pin as A", use_container_width=True, on_click=pin_scenario, args=("A",))
        pcol2.button("📌 Pin as B", use_container_width=True, on_click=pin_scenario, args=("B",))
        pcol3.button("✕ Clear A/B", use_container_width=True, on_click=clear_scenarios)

# =========================================================
# Replications — distribution of outcomes (independent of a single run)
# =========================================================
rep = st.session_state.get("rep_results")
if SHOW_REPS and rep and rep.get("profit"):
    with st.container(border=True, key="rep_card"):
        n = rep["meta"]["n"]
        _rep_h = rep["meta"]["config"].get("horizon", HORIZON_YEAR)
        st.markdown(f'<div class="card-title">🎲 Distribution across {n} replications '
                    f'({_rep_h.lower()} each)</div>', unsafe_allow_html=True)
        st.markdown('<div class="card-sub">One run is a single noisy sample. Across many runs the '
                    'spread <i>is</i> the business risk — a thin average profit can hide plenty of '
                    'losing periods. Compare the spread here with the single number the dashboard '
                    'showed: judging a design from one run is the mistake this game exists to '
                    'cure.</div>', unsafe_allow_html=True)

        prof = rep["profit"]
        sp = _stat_block(prof)
        st_thr = _stat_block(rep["throughput"])
        pct_profit = sum(1 for p in prof if p > 0) / len(prof) * 100
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Mean profit", f"${sp['mean']:,.0f}", help=f"Std ±${sp['std']:,.0f}")
        m2.metric("Years profitable", f"{pct_profit:.0f}%")
        m3.metric("Worst / best", f"${sp['min']:,.0f} / ${sp['max']:,.0f}")
        m4.metric("Mean throughput", f"{st_thr['mean']:,.0f}/yr")

        st.markdown('<div class="card-title" style="margin-top:0.6rem">Profit distribution</div>',
                    unsafe_allow_html=True)
        hp = numeric_histogram_series(prof, name="Years")
        if SHOW_CHARTS and hp is not None:
            st.bar_chart(hp, height=200)
        st.caption(f"{pct_profit:.0f}% of the {n} simulated years turned a profit; "
                   f"the rest lost money. Break-even is $0.")

        if SHOW_CHARTS:
            st.markdown('<div class="card-title" style="margin-top:0.6rem">Spread of each outcome '
                        '(box = middle 50%, line = median, whiskers = full range)</div>',
                        unsafe_allow_html=True)
            st.html(build_boxplots_html([
                {"label": "Net profit ($)", "values": prof,
                 "fmt": lambda v: f"${v:,.0f}", "color": "#15803d"},
                {"label": "Throughput (bottles/yr)", "values": rep["throughput"],
                 "fmt": lambda v: f"{v:,.0f}", "color": "#ea580c"},
                {"label": "Avg WIP (units)", "values": rep["wip"],
                 "fmt": lambda v: f"{v:,.0f}", "color": "#f79009"},
                {"label": "Flow time (days)", "values": rep["flow"],
                 "fmt": lambda v: f"{v:.1f}", "color": "#9b6dff"},
                {"label": "Efficiency (%)", "values": rep["efficiency"],
                 "fmt": lambda v: f"{v:.0f}%", "color": "#9a3412"},
            ]))
        st.caption(f"Line: {config_summary(rep['meta']['config'])}. Profit uses the current "
                   f"financials, so editing them re-prices every replication on the next run.")

# =========================================================
# A/B scenario comparison (persists across config changes)
# =========================================================
scA = st.session_state.get("scenario_A")
scB = st.session_state.get("scenario_B")
if SHOW_SANDBOX_TOOLS and (scA or scB):
    with st.container(border=True, key="cmp_card"):
        st.markdown('<div class="card-title">⚖️ Scenario A vs B</div>', unsafe_allow_html=True)
        st.markdown('<div class="card-sub">Green Δ = the better direction for that metric '
                    '(more profit/throughput, less WIP/flow time). Pin both slots to fill the '
                    'comparison.</div>', unsafe_allow_html=True)
        st.html(build_comparison_html(scA, scB))
