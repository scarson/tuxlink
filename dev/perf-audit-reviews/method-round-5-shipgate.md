# Method Review (round 5, ship-gate)

- **Reviewer:** Opus subagent (agent `glade-knoll-shoal`)
- **Date:** 2026-06-04
- **Target:** `.claude/skills/performance-audit-cycle/whole-repo-scoping.md` (v2)
  + the "Whole-repo / oversized scope" paragraph in
  `.claude/skills/performance-audit-cycle/SKILL.md`
- **Lens:** SHIP-GATE consolidation — self-consistency, no dead refs, SKILL.md
  routing correctness, autonomous-execution completeness, the one open
  (data/ML) item, and a litmus replay against this session's actual partition.
- **Mode:** READ-ONLY except this report.

---

## Verdict: SHIP-WITH-LISTED-FIXES

The method is structurally sound, internally consistent on every *numeric*
threshold, and complete enough to run autonomously. Two **must-fix** defects
block a clean ship — both are documentation-precision issues, not method gaps:
(1) a dead "Sizing" cross-reference that points at a section/table that does not
exist, and (2) a SKILL.md trigger threshold (>4k LOC) that contradicts the
doc's own lightweight-path ceiling (~8k LOC) and will make a reader trip. The
one known open item (data/ML notebook/DAG caveat) is **confirmed absent** and
should be added as a one-liner. None of these change the method's logic; they
are surface defects that a first-time operator on a real repo would stumble on.

---

## 1. Self-consistency & dead references

### Phases — all defined, no dead phase references ✅

Every referenced phase resolves to a heading: `S0` (L25), `S0.5` (L37), `S1`
(L50), `S2` (L75), `S3` (L122), `S4` (L163), `S5` (L191), `S6` (L212),
`REVIEW GATE` (L243). In-text forward references (S1, S4, S6, the gate) all
land. No orphan phase.

### Numeric thresholds — all internally consistent ✅

- **Lightweight trigger:** S0 (L31–32) `≤2 natural slices OR <~8k production
  LOC across ≤2 languages`. Review-gate table (L254) `1–2 (lightweight)` and
  the self-check duration "5-min self-check vs the heuristics table" matches S0's
  "5-minute self-check against the heuristics table" (L32). **Consistent.**
- **Full-method trigger:** S0 (L34) `repo / multi-package / >~8k LOC / >2
  languages`. The boundary (≤2 langs lightweight vs >2 langs full) is internally
  clean — no overlap, no gap. **Consistent.**
- **Review-depth slice counts** (L253–256): `1–2 → none`, `3–5 → 1 general`,
  `6–12 → 1 + partition-design REQUIRED`, `13+ → ≥2`. Contiguous, no overlap.
  Prose at L248–249 ("5-round case is the CEILING, not the default") agrees with
  the table's escalation. The "≥1 partition-design lens at 6+ slices" heuristics
  row (L290) matches the table's `6–12` row. **Consistent.**
- **Sizing bands** (heuristics L279): `Python/Ruby ~0.5–2k, Rust/TS ~1–4k,
  Go/Java/C# ~2–6k`. These are per-slice bands; they do not contradict the
  per-repo S0 ~8k lightweight ceiling (a lightweight repo of ≤2 slices in the
  1–4k band each tops out ≈8k — the numbers are mutually coherent). **Consistent.**
- **COLD SWEEP lane count:** S5 (L197) "~3 lanes: complexity + allocation +
  data-access"; DEFER heading the same method elsewhere ("3 lanes only:
  complexity + allocation + data-access"); the produces section (L296) "one
  trimmed `performance-audit` run". **Consistent.**

### DEAD REFERENCE — "Sizing" (MUST-FIX #1) ❌

Three sites point to a section/table named "Sizing" that **does not exist** as a
heading:

- **L7** (intro): "one coherent subsystem — **see sizing below**".
- **L139–140** (S3 rule 3): "Size to the sweet-spot … LOC as a sanity check
  **(see Sizing)**".
- **L157–158** (SPLIT/KEEP rule): "it exceeds **the sized band** with a real
  seam … fit **the band**".

The actual per-language band data lives **only** in the Heuristics table row at
**L279** ("One LOC band for all languages | Scale by verbosity (Python/Ruby
~0.5–2k …)"). There is no `## Sizing` heading and no dedicated sizing table
(confirmed against the full heading list: S0, S0.5, S1–S6, REVIEW GATE, When in
doubt, Heuristics, What this method produces). A reader who follows "(see
Sizing)" finds nothing. This is a navigability defect in a doc that will be
followed literally by an autonomous agent.

**Fix (pick one):**
- **Cheapest:** repoint the three references to the existing data — change "(see
  Sizing)" → "(see the LOC-band row in Heuristics, below)" and "see sizing
  below" → "see the per-language LOC bands in Heuristics". Zero new content.
- **Better:** promote the band data into a named **`Sizing`** subsection under
  S3 (or a short table right after S3 rule 3) and keep the heuristics row as the
  quick-reference echo. This is what the prose already implies exists.

I recommend the **cheaper repoint** for ship (lower risk of introducing a
second copy that can drift — the propagation contract favors a single canonical
statement). Exact edits:
- L7: `see sizing below` → `see the per-language LOC bands in Heuristics, below`
- L140: `(see Sizing)` → `(see the LOC-band heuristics row, below)`

(L157–158 "sized band"/"the band" read fine once the canonical band location is
unambiguous; no edit strictly required there.)

### Tables vs prose — no contradictions ✅

The sizing table (L61–68, ecosystem exclude-globs), review-depth table
(L251–256), and heuristics table (L276–290) were each cross-checked against the
surrounding prose. The only mismatch is the missing "Sizing" target above; the
exclude-globs table agrees with S1's prose, and every heuristics row maps to a
prose rule (verified: production-LOC→S1, workload-shape→S2, no-in-tree-caller→S2,
never-mix→S3.1, mega-run→S3+SPLIT/KEEP, cold-sweep→S5, latent/external→S2/S5,
impl+caller→S4, measurement→S5, repo-as-unit→S0.5, single-pass→REVIEW GATE).

---

## 2. SKILL.md routing paragraph

### Does it point here? ✅ — sufficiently and correctly

The "Whole-repo / oversized scope" paragraph (SKILL.md L25) links
`whole-repo-scoping.md` and accurately summarizes the skeleton: survey + measure
production LOC → cheap hot-path/reachability map → bounded slices by
perf-relevance → cross-slice frequency calibration → depth tiers (full / reduced
/ one batched cold sweep / overlay) → **adversarially review the partition before
executing** → run the cycle per slice with a persistent progress ledger. That is
a faithful, complete pointer. The hand-off ("run this cycle once per slice with a
persistent progress ledger") matches the doc's "What this method produces"
section (L292–297).

### TRIGGER-THRESHOLD MISMATCH (MUST-FIX #2) ❌

SKILL.md L25 sets the trigger at **"roughly >4k production LOC or spanning more
than one language/package."** The doc's own S0 puts the **lightweight path** up
to **~8k production LOC across ≤2 languages** (L31–32), and only routes to the
**full S1–S6 + review-gate method at >~8k LOC / >2 languages** (L34).

A reader on, say, a 5k-LOC single-language repo is told by SKILL.md "this is
oversized — follow whole-repo-scoping.md," opens the doc, and S0 immediately
routes them to the **lightweight path** (eyeball 1–2 slices, no formal review
round). The two documents disagree on what ">oversized" means. This is exactly
the trip-hazard the mandate flags: SKILL.md's 4k number and the doc's 8k
lightweight ceiling will read as a contradiction.

**Why it isn't actually a logic bug:** the method *does* handle the 4k–8k case —
it routes it to the lightweight path. SKILL.md is right to send 4k+ *into the
doc* (the doc's S0 is the correct router). The defect is purely that SKILL.md's
phrasing implies the *full* method fires at 4k, when the doc says the full
method fires at 8k.

**Precise fix — reword SKILL.md L25** so the threshold is "consult the doc's S0
router," not "the full heavy method." Replace:

> "...or any surface materially larger than one run is optimized for (roughly
> >4k production LOC or spanning more than one language/package) — do NOT cram
> it into one run..."

with:

> "...or any surface materially larger than one run is optimized for (roughly
> >4k production LOC, or spanning more than one language/package) — do NOT cram
> it into one run... Instead follow [`whole-repo-scoping.md`](whole-repo-scoping.md),
> **starting at its S0 size-router**, which decides between a lightweight pass
> (≤2 slices / <~8k LOC: measure, eyeball, no formal review round) and the full
> S1–S6 + review-gate method (a repo / multi-package / >~8k LOC / >2 languages)."

This keeps >4k as the "stop and consult the method" line (correct — that's where
naive one-run cramming starts to hurt) while making explicit that the method
itself sub-routes 4k–8k to the lightweight path. No number in the doc changes;
SKILL.md gains one clause that defers the lightweight/full split to S0.

(Per the propagation contract, the canonical thresholds stay in the doc's S0;
SKILL.md becomes a pointer to S0 rather than a second, conflicting statement.)

---

## 3. Completeness for autonomous execution ✅ (no blocking gaps)

An agent can run this end-to-end on a real repo and emit a reviewed slice plan +
ledger without inventing method:

- **Measure:** S1 gives concrete tooling (`tokei --output json`/`scc`) and a
  per-ecosystem exclude-glob table → production LOC is computable, not hand-waved.
- **Classify hot/warm/cold:** S2 gives the workload-shape taxonomy + an explicit
  HOT/WARM/COLD checklist with a default-WARM tie-breaker → no undefined branch.
- **Cut:** S3 gives a SPLIT-IFF/KEEP-IFF decision rule + god-file fallback
  (synthetic seams + OVERLAY) → the "no seam" branch is defined.
- **Calibrate:** S4 is demand-driven, bounded (3 frames / nameable class / entry
  point), and fail-safe (assume-hot tag) → terminates, no global analysis.
- **Tier + verify:** S5 maps checklist→tier and defines the deferred verification
  mode → no fabricated-number path.
- **Persist + resume:** S6 specifies slice plan + progress ledger + run ledger
  with concrete schemas and a "how to resume" header → survives a context reset.
- **Review:** the gate table picks a review depth from the slice count → the
  agent knows whether/how much to review without a human decision.
- **Degenerate cases:** "No hot path is a valid outcome" (L109), seamless-oversize
  fallback (L142–143), and the "When in doubt" section close the edge branches.

**No gap blocks autonomous execution.** The single nice-to-have: the doc never
spells out the *exact filename/path convention* for the slice-plan and
progress-ledger artifacts (S6 names them but not a canonical path like
`docs/perf-audits/<date>-slice-plan.md`). An agent will invent a reasonable path;
harmless. Not a blocker.

---

## 4. The one known open item — data/ML notebook/DAG caveat

**Status: CONFIRMED ABSENT from v2.** Grep for `notebook|DAG|pandas|Spark|
data-loader|data/ML|Airflow|Jupyter` over the method doc returns **zero matches.**
The caveat was raised in round-1 generalizability
(`method-round-1-generalizability.md` L278–281, L310: "Data/ML repos
(notebooks, pipelines) are unaddressed entirely… add a one-line caveat that
notebooks/pipelines need a DAG-stage partition, not a [LOC-band] one") and was
**not** carried into v2. This is the open item the mandate flagged; it is real.

This is a **nice-to-have for ship, not a blocker** (the method ships against
Rust/TS/Go/Java/.NET/Python-service repos today; data/ML is an un-handled
*class*, not a defect in the handled classes). But it's a cheap, high-value
addition and was already vetted by an earlier round, so I recommend including it.

**Exact text to add** — append as a fourth bullet in **S0.5** (L37–46, the
"what is the audit unit" router, which is the natural home for "the unit isn't
the package"):

> - **Data/ML repos (notebooks + pipelines):** the audit unit is the **DAG
>   stage / pipeline step**, not the package or notebook file, and the hot path
>   is usually a single dataframe/Spark op or a data-loader — size by
>   stage-and-data-volume, not LOC band. Partition along DAG-stage seams (read
>   the orchestrator's DAG, à la the event-driven entry-point rule in S2), and
>   treat notebook cells as one unit per logical stage.

(Two lines as rendered; folds the DAG-stage-partition + "notebooks aren't LOC"
points into one bullet, and reuses S2's existing "entry points live in config"
machinery so it doesn't introduce orphan method.)

Alternative placement if S0.5 is judged too narrow: add a one-row entry to the
Heuristics table — `| Data/ML notebooks/pipelines sized by LOC | Unit is the DAG
stage; hot path is a single dataframe/Spark op or data-loader; partition along
DAG-stage seams. |`. Either site satisfies the propagation contract (one
canonical statement). **S0.5 bullet preferred** — it's where the "unit selection"
logic already lives.

---

## 5. Litmus — would THIS method reproduce THIS session's partition? ✅ YES

Replaying the method against tuxlink (~96k LOC Rust+TS) reproduces the
session's good partition (DSP-full / FEC-latent / search-warm / cold-sweep /
cross-slice frequency map):

| Session artifact | Method phase that produces it | Reproduced? |
|---|---|---|
| **DSP-full (M1: OFDM PHY + audio)** | S2 CPU-bound/real-time hot-path class (inner loops, per-symbol allocs, real-time callback) → HOT → S5 FULL | ✅ The CPU-bound bullet (L78–80) names "per-frame/per-message/callback handlers" and "DSP" explicitly; M1's H0/H1/H4 are exactly these. |
| **FEC-latent (M2)** | S2 "Latent / dead code … no live callers; findings are reachability ≈ 0 today, flag 'fires once wired in'" (L94–96) | ✅ The method's latent-code rule is *verbatim* the M2 framing ("fires once FEC is wired in," reachability ≈ 0). The round-5 FEC-no-caller finding IS this rule applied. |
| **search-warm (M4)** | S2/S5: on a live path, bounded work → WARM → REDUCED; or HOT if FTS loops scale with load | ✅ M4 (SQLite FTS + extractor loops) lands warm/full per the checklist; the method's HOT/WARM checklist (L113–120) routes it correctly. |
| **cold-sweep** | S5 COLD SWEEP "one batched run, ~3 lanes over all COLD glue" + S2 "cold glue … JVM/.NET add DI wiring" + degenerate-case rule | ✅ The method's COLD SWEEP and the L90–92 cold-glue catalog reproduce the tuxlink cold bucket (ui_commands, wizard, forms, grib, etc.). |
| **cross-slice frequency map (W0)** | S4 — the centerpiece. "frequency often set by a caller in a different slice … (a) a ≤1-page frequency-map pre-artifact (impl symbol → caller file:line → multiplier class → N)" (L177–180) | ✅ W0 is *literally* S4's mitigation (a). The lzhuf/Outbox-loop case is even cited in S4 as the `[case]` (L178–179: "a compression routine's real driver was an Outbox-loop in a cold-swept file; a one-page map fixed calibration"). |
| **REVIEW GATE / 5 rounds** | The gate table: 16 slices → `13+ / high-stakes → ≥2 rounds, ≥1 partition-design`; 5 was the CEILING | ✅ The method correctly says 5 rounds is the ceiling, not mandated; for 16 units it requires ≥2 with ≥1 partition-design lens — which is exactly the partition-design (round 4) + final-gate (round 5) pattern this session ran. |

**The method reproduces the partition, including the subtle win** — the
cross-slice calibration defect (lzhuf driver exiled to the cold sweep) that four
hot-path-hunting rounds missed and round-4's partition-design lens caught. S4 +
the REVIEW GATE's "partition-design lens REQUIRED at 6+" together force exactly
that catch. The latent-FEC reframing (round-5's headline) is likewise produced
by S2's latent-code rule.

**One litmus caveat (not a gap, a calibration note):** the method would
reproduce the partition *if the operator runs the partition-design review round.*
The gate makes that round REQUIRED at 6+ slices, so the method does enforce it —
but the catch depended on a reviewer actually adopting the partition-design lens
(not finding-hunting). The doc's REVIEW GATE prose (L255, L258–263) makes the
lens's job explicit ("its explicit job is cross-slice calibration, not
finding-hunting"), so this is handled. No gap.

---

## Final ordered edit list

### MUST-FIX before ship (2)

1. **Repoint the dead "Sizing" reference.** L7 `see sizing below` → `see the
   per-language LOC bands in Heuristics, below`; L140 `(see Sizing)` → `(see the
   LOC-band heuristics row, below)`. (Canonical band data stays the single
   heuristics row L279 — no second copy.)
2. **Fix the SKILL.md trigger threshold.** Reword SKILL.md L25 so >4k means
   "stop and consult whole-repo-scoping.md's S0 router," and S0 sub-routes
   4k–8k to the lightweight path vs >8k to the full method (exact replacement
   text in §2 above). Removes the 4k-vs-8k contradiction.

### SHOULD-FIX before ship (1, pre-vetted, cheap)

3. **Add the data/ML DAG-stage caveat** as the S0.5 fourth bullet (exact two-line
   text in §4 above). Closes the one known open item; vetted in round-1.

### NICE-TO-HAVE (non-blocking)

4. Optionally promote the LOC bands into a named `Sizing` subsection under S3
   (instead of the repoint in #1) if a fuller sizing treatment is wanted later.
5. Optionally state a canonical artifact path convention in S6 for the slice
   plan / progress ledger (e.g. `docs/perf-audits/<date>-slice-plan.md`).

With edits 1–3 applied, **SHIP.** None of the three touch the method's logic;
all three are surface-precision fixes a literal-minded autonomous agent would
otherwise trip on.
