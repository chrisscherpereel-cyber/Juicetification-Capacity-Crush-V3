# 🧃 Juicetification: Capacity Crush

**An experiential, browser-based simulation for teaching the Theory of Constraints (TOC) and core operations-management concepts.**

Juicetification: Capacity Crush reimagines Goldratt's classic "dice game" as a juice-bottling
line. Students don't just *read* about bottlenecks, work-in-process, and yield — they run a line,
predict what will happen, watch it unfold, and then redesign it to hit a goal. The tool is built
around evidence-based learning mechanisms (prediction, elaborated feedback, self-explanation,
interleaving, and productive failure) so that the pedagogy is baked into the software rather than
left to facilitation.

![The simulation dashboard](figures/Figure1_Dashboard.png)

---

## Why it exists

Operations concepts are famously counterintuitive: a balanced line finishes *below* the average
capacity of its stations; adding capacity to a non-constraint does nothing; capping work-in-process
shortens lead time without costing output; and a *fast* station can be the real constraint if it
scraps most of what it makes. Told these results in a lecture, students often assimilate them to
prior (wrong) intuitions. Juicetification makes each result something the student **predicts, sees,
and then has to engineer** — which is where the idea actually lands.

---

## What's new in V3

V3 is V2 with a classroom-scale performance pass and three teaching changes. Every number below
was measured against a running server, not estimated.

**Faster with a class on it.** Streamlit serves every student from one process, so a click costs
whatever the script costs, multiplied by everyone clicking at once.

- **The guided-lab panel is an `st.fragment`.** Answering a prediction, estimate or reflection now
  redraws only that panel instead of re-executing the whole 7,300-line script. With 30 concurrent
  sessions, that interaction went from **1.93 s to 0.82 s**, and its payload from **43 KB to 5 KB**.
- **The EOQ cost curve caches on the line configuration alone.** It used to be keyed on a
  run-derived unit margin, so its ~11 simulations re-ran on every run for every student and were
  never shared. That is **≈250 ms per run** returned to the EOQ labs and Sandbox, now shared
  across the whole class.

**Three changes aimed at understanding.**

- **Run length.** The line can now run for **one shift (8 h), one week (40 h), six weeks (240 h)**
  or a full year. This is the point of a dice game and it was previously invisible: the spread
  between the best and worst run of the same balanced line is ~160% over one shift, 36% over a
  week, and **4% over a year**, and an empty line only reaches its steady rate once the pipeline
  fills (0.62 bottles/hr in the first shift against 3.37 over a year). Challenges are still judged
  over a full year — a short run is shown but never consumes a try — because a pass/fail threshold
  on a one-shift run would be a coin flip.
- **Replications work inside the guided labs,** not just Sandbox. Judging a design from a single
  run is the mistake the dice game exists to cure, and the Variability lab could not show a
  distribution at all. Replications never count as a challenge try.
- **Two costing corrections from the audit are now applied** (AUDIT.md §3). Ordering cost is
  counted from the purchase orders the supplier actually shipped rather than inferred from
  consumption, and raw material and processing are charged on the units a station *started* and
  *worked* rather than only the good ones — so a scrapped bottle is no longer free. A
  steady-state note now appears when the averages on screen describe a line that never settled.

The simulation itself is unchanged: for a given seed and configuration the line produces exactly
the same output as V2. The corrections above move money, not throughput, and the EOQ lesson still
lands — the textbook EOQ and the measured cost-curve minimum agree exactly on the default line.

---

## Features

- **Sandbox mode** — a full dashboard with every control: per-station dice (capacity), work-in-process
  caps, supply reliability, demand variability, scrap/yield, safety stock, and the line's economics
  (throughput, operating expense, inventory investment, EOQ). Replications show the *distribution*
  of outcomes, not just one run — and in V3 they are available in the guided labs too.
- **Guided Lab mode** — a sequenced set of laboratories that introduce one concept at a time:
  1. Operations — the Five Focusing Steps
  2. Little's Law
  3. Push vs. Pull
  4. Variability
  5. Quality & Yield
  6. Economics of the line
  7. Throughput Accounting
  8. EOQ Drivers
  9. EOQ Limits
  10. Safety Stock
  11. **Part 1 Capstone — Diagnose & Fix** (an interleaved, unlabeled flow diagnosis)
  - Part 2 adds a parallel **Part 2 Capstone — Diagnose & Fix: the money side** (losing money,
    wrong order size, or stockouts)
- **Predict → Run → Reveal** on every step (commit to a prediction before you see the answer).
- **Distractor-specific feedback** — each wrong answer gets a one-line explanation aimed at the exact
  misconception behind it.
- **Numeric-estimate steps** graded on a tolerance band with a number-line readout.
- **Open design challenges** with an automated pass/fail check and limited tries, always judged
  over a full year so a noisy short run can never decide a pass.
- **Selectable run length** — one shift, one week, six weeks or a full year, so the swings that a
  year-long average hides become visible.
- **Self-explanation prompts** after the key reveals; what the student writes is saved into their
  submission report.
- **Each lab shows only its relevant controls and results** — the sidebar and dashboard are
  filtered per lab so a first-time user isn't faced with unused inputs or off-topic feedback.
- **Twelve labs in two parts** — ten guided labs plus two diagnostic capstones. Part 1 (Constraints
  & Flow) holds labs 1–5 and the Part 1 Capstone; Part 2 (Economics & Inventory) holds labs 6–10 and
  the Part 2 Capstone. Each part produces a downloadable **PDF** report to upload to the LMS — there
  is **no completion code**; the report is the submission.
- **Challenge pass is tracked separately from completion** — running out of tries lets a student
  continue but is reported as *attempted, not passed* (never shown as a pass).
- **Honest save status + backup/restore** — the app says whether server storage is on, when it last
  saved, and warns if a save failed; a **JSON backup/restore** works even with no server configured.
- **Run from the main window or the sidebar** — a Run button sits in the main panel as well as the
  sidebar, so it's always reachable.
- **Answers persist across refresh** — each step's multiple-choice / estimate answer is saved (in the
  page URL, so it survives a browser refresh even without shared storage) and restored when you return.
- **Start over** — "✅ Your progress → ↺ Start over" clears all progress, answers and reflections and
  returns to the first step (two-step confirm).
- **Light theme pinned** — the app ships `.streamlit/config.toml` fixing a light theme (its visuals are
  designed for a light background); run it in light mode. *(If you re-upload to GitHub via the web UI,
  include the hidden `.streamlit/` folder — the web uploader silently drops dotfolders.)*

---

## Quick start

**Requirements:** Python 3.9+ and the packages in `requirements.txt` (Streamlit and pandas).

```bash
# 1. (optional) create a virtual environment
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run the app
streamlit run juicetification.py
```

Streamlit will open the simulation in your browser (usually at http://localhost:8501).

---

## Using it in a course

- **Students** work through the Guided Labs in order; the Capstone is best saved for last. See
  `instructions.pdf` for a one-page visual guide.
- **Progress is stored in the page URL**, so a student can bookmark or copy the link to resume.
  Work runs entirely in the browser session — no accounts, no server-side data.
- **Two parts, two assignments.** The twelve labs (ten guided + two capstones) are split into
  **Part 1 — Constraints & Flow** (default) and **Part 2 — Economics & Inventory**, chosen at the top
  of the sidebar. Each part is submitted separately, and the two reports are independent.
- **To collect work**, have each student open **Your progress → Get my report to submit**, enter their
  name, and **download the PDF report** for that part — then upload it to the matching LMS assignment
  (the PDF lists completed labs, design-challenge outcomes, and their self-explanations). There is no completion code to copy.
- **Everything is instructor-controllable**: the lab sequence, the difficulty of each challenge, and
  the number of tries are all defined in `juicetification.py`.

---

## Instructor configuration (Juicetification Director)

The app ships ready for the **Juicetification Director**, a shared system for setting up
configurable, per-section assignments without editing any code. Two small files provide it:
`juice_director.py` (identical across every simulation in the family — drop-in, never edited) and
`manifest.py` (this app's parameter schema). **If no Director parameters are present in the URL, the
app behaves exactly as it always has.**

- **Schema endpoint** — visiting `…/?manifest=1` returns this app's parameter schema as JSON, so the
  Director can discover the available settings (line configuration, WIP cap, supplier reliability,
  demand, scrap, and the economics) with no shared code.
- **Configured links** — the Director hands out a self-contained link (`…/?cfg=<encoded>`) that
  pre-fills the sidebar with the instructor's chosen starting values. The app accepts both the
  Director's parameter names and its own internal snapshot format, so older shared links keep working.
- **Scenario seed precedence** — the single source of truth is: **(1)** a seed set on the Director
  link pins one fixed scenario for everyone on that assignment (useful for fair grading); otherwise
  **(2)** each student gets their own unique seed, stored in the page URL as `?rs=` so it is stable
  across reloads while differing between students; otherwise **(3)** fully random. Within a seed, each
  *re-run* still varies (a per-run counter is added) so repeating a line shows real fluctuation.

No accounts or servers are involved; a configured assignment is just a URL.

### Per-student progress and stable scenarios

With storage configured (`student_store.py` — the same file every simulation in the family uses),
the app also saves each student's progress and gives each student a stable, unique line:

- **Sign-in gate** — when storage is on, students enter a student ID once (kept in the URL as
  `?sid=`), so a refresh or a return visit resumes exactly where they left off. When storage is off,
  there is no gate and nothing changes.
- **Automatic save/resume** — completed steps, reflections, challenge results, and lab position are
  written to encrypted per-student files after each meaningful step, and restored on load.
- **Unique per-student scenario** — each student gets their own scenario seed (kept in the page URL
  as `?rs=`), so every student sees a different line; see the seed-precedence note above for how a
  Director-set seed overrides this.
- **Completion roster** — when a student downloads their Part report, that completion is also recorded
  to storage (score and part), so an instructor can assemble a roster. There is no completion code.

### Performance with many simultaneous users

The live “line running” playback is the main per-run cost on the server (it holds the session for a couple of seconds and streams the day-by-day frames). Each user can turn it off with the **Play the run animation** checkbox in the sidebar — unchecking it jumps straight to the results. The animation is **off by default**; a student can turn it on with the **Play the run animation** checkbox in the sidebar. Set `JCC_ANIMATIONS=on` on the deployment to default it on for everyone instead.

Storage is enabled only when its secrets are set (`DB_ENCRYPTION_KEY` plus Dropbox credentials); it
requires the `dropbox` and `cryptography` packages, which are listed in `requirements.txt`. **With no
secrets set, every storage call is a safe no-op and the app runs exactly as it does standalone.**

## How the design maps to learning theory

The `paper/` folder contains the full manuscript describing the theoretical grounding and design
rationale, with figures. In brief, each guided step traverses Kolb's experiential learning cycle:

![Design features mapped to Kolb's cycle](figures/Figure8_Kolb_Cycle_Mapping.png)

Additional figures in `figures/` illustrate the guided step, distractor feedback, numeric estimation,
the design challenge, the capstone diagnosis, and the self-explanation prompt.

---

## Repository contents

```
Juicetification-Capacity-Crush/
├── juicetification.py     # the simulation (single-file Streamlit app)
├── juice_director.py      # shared Director config loader (identical across apps)
├── manifest.py            # this app's parameter schema for the Director
├── student_store.py       # shared per-student progress store (identical across apps)
├── instructions.pdf       # one-page visual student guide
├── requirements.txt
├── .streamlit/config.toml   # pins the light theme (the UI is designed for a light background)
├── README.md
├── AUDIT.md               # model & assessment audit: verified defects vs. assumptions
├── LICENSE
├── tests/
│   └── test_model.py      # headless invariant tests (no server needed)
├── paper/                 # academic write-up (design rationale + theory)
│   └── Juicetification_Capacity_Crush_Paper.docx
└── figures/               # publication-quality figures (PNG + editable SVG)
```

---

## Running the tests

The model invariants are checked headlessly — no Streamlit server or browser is needed (a tiny
stub stands in for `streamlit`):

```
python tests/test_model.py        # plain runner, or:
python -m pytest tests/ -q        # if pytest is installed
```

They cover conservation of units, deterministic reproducibility, scrap/yield reconciliation,
actual supplier-order counting, Little's-Law compatibility (stationary vs. non-stationary),
service-level vs. customer fill-rate, completion-vs-pass, Director-config expansion, and
Part 1/Part 2 report independence. See `AUDIT.md` for what is a verified defect vs. a documented
modeling assumption.

---

## Citing / attribution

If you use or adapt this simulation, please cite the accompanying paper (see `paper/`).
Author: Christopher M. Scherpereel, W. A. Franke College of Business, Northern Arizona University.

---

## License

Released under the MIT License (see `LICENSE`). You are free to use and adapt it for teaching and
research; please confirm this license suits your institution's requirements before publishing.

---

*Version 1.0*
