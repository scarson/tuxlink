# Method Review (robustness/edge-cases)

**Reviewer:** glade-knoll-shoal
**Angle:** Robustness, edge cases, completeness — where does the partition method
produce a BROKEN or BAD partition, degenerate, loop, drift, or silently omit a
phase? Independent of round-1 (generalizability across ecosystems); this round
attacks the *shapes the method must survive* and the *executability of its hardest
phase (S4)*.
**Verdict:** **Needs-edits — structurally sound but missing a top-level partition
decision, with three under-specified phases (S4 executability, coverage-drift
reconciliation, mid-execution re-slice) and four degenerate shapes that fall through
the cracks.** The six-phase skeleton + review gate is good and the case-grounded
traps are real. But the method silently assumes **one coherent codebase with a
findable hot path and real seams**, and several common repo shapes violate that
assumption with no documented fallback. S4 — the method's subtlest and self-declared
"the subtle one" phase — is the least executable: it gestures at a "call-frequency
map" that could cost as much as the audit and has no bounded-effort recipe and no
regress guard.

---

## Summary table — each attacked shape, does the method handle it, and the fix

| # | Shape / edge case | Handled? | Gap | Fix (one line) |
|---|---|---|---|---|
| 1 | Monorepo, N independent apps/services | **No** | No "one codebase or many?" step; "disjoint repo coverage" is the wrong frame across deploy units | Add **S0.5 top-level partition**: per-app/service first, each app its own scoping problem |
| 2 | Single 10k-LOC god-file, no internal seams | **No** | S3.3 says "split along real seams" — undefined behavior when there are none | Add **seamless-oversize fallback**: synthetic seams by symbol/region + flag for refactor; never silently exceed band |
| 3 | Uniformly flat cold CRUD, no hot path | **Partial** | COLD SWEEP exists but the "whole repo degenerates to one cold sweep" terminal case is never stated | State the degenerate case explicitly as a **valid** outcome; give the "no hot path found" exit |
| 4 | Generated/vendored/migration at scale; perf-relevant generated code | **Partial** | S1 says "exclude" but no detection recipe; no carve-out for generated-but-hot (parsers, serializers) | Add **detection signals** + a **"generated AND on hot path → audit as FULL, don't edit source"** rule |
| 5 | S4 cross-slice frequency calibration | **Weak** | Executability hand-wavy; no effort bound; infinite "what calls the caller" regress unguarded | Add **bounded recipe** (depth-limited, demand-driven) + **lightweight fallback** (assume-hot tag) + regress stop rule |
| 6 | Coverage drift (files move/added between plan & exec) | **No** | Coverage ledger is built once at plan time; no reconciliation at execution | Add **drift reconciliation** step at each slice run + a final ledger re-diff |
| 7 | Per-run discovers mis-scope mid-execution | **No** | Partition frozen after review gate; no feedback loop | Add **re-slice trigger** + a cap on re-review to avoid thrash |
| 8 | Over-fragment vs mega-run in-between | **Partial** | Warns against both extremes; "rules of thumb" exist but no crisp decision rule | Promote the split/merge test to a **decision rule** with a tie-breaker |
| 9 | Missing phases (dep ordering, shared libs, roll-up optional) | **Partial** | Shared-lib-used-by-many has no home; roll-up is "optional"; slice dep graph implicit | Add **shared-substrate handling** + make roll-up **conditionally mandatory** |

---

## Edge case 1 — Monorepo with N independent apps/services: the frame is wrong, not just incomplete

**The defect.** S3.5 demands "**complete, disjoint coverage of the repo** — every code
unit in exactly one slice." For a monorepo of N independently deployed services this
is the wrong unit of analysis. Each service has its **own load profile, own hot path,
own frequency drivers, own runtime**. "Disjoint coverage of the whole repo" invites an
operator to LOC-slice *across* service boundaries — producing slices that straddle two
deploy units that never share a call edge. A slice spanning service-A's handler and
service-B's util is incoherent: there is no single "realistic load" to calibrate Impact
against (the cycle's Phase 1 input is undefined for it).

**Why the method misses it.** S1 enumerates "apps" as a build-unit type, but S3 then
treats all build units as peers to be packed into ~1–4k slices by the same rules. The
method has **no step that asks "is this one codebase or many independent programs?"**
and branches on the answer. The provenance case (one desktop app, one process tree)
never forced the question.

**Fix — add S0.5, the top-level partition decision (BEFORE S1's detailed survey):**

> **S0.5 — One program or many?** Determine whether the scope is a single coherent
> program or a set of independently deployed/runnable units (services in a monorepo,
> published packages, plugins, CLIs). If many: **the first partition is per-unit** —
> each independently-deployed unit becomes its **own** whole-repo-scoping problem with
> its own survey, hot-path map, load profile, and slice plan. Shared/common
> libraries are handled per Edge-Case-9 (shared substrate). Disjoint-coverage and
> cross-slice-frequency reasoning apply **within** a unit, not across deploy
> boundaries (call frequency does not cross a process/deploy boundary coherently —
> an RPC is an external-process boundary per S2). Only collapse to a single
> partition when the units genuinely share one runtime and load.

This also fixes the "100k → 10–20 units" rule of thumb, which silently assumes the
repo is one audit.

---

## Edge case 2 — The 10k-LOC god-file with no internal seams: undefined behavior

**The defect.** S3.3: "Split anything larger along **real module/file seams** (name the
files in each sub-slice), not by line count." A single 10k-production-LOC file that
exceeds the band but has **no module boundaries to split on** is a hole in the method.
Three bad outcomes are all "allowed" by the current text:
- (a) Leave it as one over-band slice → the mega-run failure mode the method exists to
  prevent (lane precision collapses).
- (b) Split it by line count anyway → exactly what S3.3 forbids ("not by line count"),
  and it manufactures arbitrary cuts that sever call edges → self-inflicted S4 splits.
- (c) Punt / silently drop → violates S3.5 disjoint coverage.

The method names no fourth, correct option, so an operator hits an undefined branch on a
very common shape (god-files are endemic in legacy code — the exact code most worth
auditing).

**Fix — add a seamless-oversize fallback to S3.3:**

> **When an over-band unit has no real module/file seams**, cut on the next-best
> *structural* boundary in priority order: (1) top-level symbol clusters (a group of
> functions + the state they mutate — a de-facto class); (2) call-graph communities
> (functions that call each other far more than they call out); (3) distinct
> responsibilities visible as comment-banded regions. Name the symbol ranges, not
> line numbers, so the cut survives edits (Edge-Case-6). **Treat these as one OVERLAY
> family**: because the synthetic sub-slices share state, run an end-to-end overlay
> over the whole file after the sub-slices (S5 OVERLAY) so compounding cost isn't lost
> across the artificial cut. **Flag the file itself as a finding** ("un-sliceable
> god-file — its lack of seams is a maintainability/perf-reasoning hazard") — the
> partition difficulty is itself signal. Never silently leave it over-band and never
> cut on bare line count.

---

## Edge case 3 — Uniformly flat cold code: the degenerate "one cold sweep" outcome is valid but unstated

**The defect.** A pure CRUD app that is *all* cold glue — no hot path anywhere — is a
real and common shape. The method *can* handle it (COLD SWEEP exists in S5), but it
**never states that "the entire repo collapses to one or a few cold sweeps" is a valid
terminal outcome.** S2 frames cold glue as the *exception* to be pulled out of hot
neighborhoods; it never contemplates the case where there is no hot neighborhood at all.
An operator running S2 by its examples (loops, frames, RAF, real-time callbacks) and
finding none may either (a) conclude "I must be missing the hot path" and burn budget
hunting an imaginary one, or (b) force-tier cold slices to FULL to feel thorough —
wasting cycles, the exact anti-pattern the method warns about.

**Fix — add an explicit "no hot path" exit to S2:**

> **If the hot-path map finds no genuine hot path** (all-cold CRUD/glue, an
> IO-bound orchestration shell, a thin API over a DB engine), that is a **valid
> result, not a search failure** — do not invent one (cross-ref the "verify, never
> infer from names" rule). The correct partition is then **one or a few COLD SWEEPs
> sized to the band**, and possibly a single OVERLAY for end-to-end IO cost. Record
> "no hot path detected; basis: <signals checked>" in the slice plan so the review
> gate can challenge the *absence* (a missed hot path is the failure mode here), not
> just the presence, of hot slices.

This makes the review gate's job symmetric: it should attack both imaginary hot paths
*and* missed ones — the current REVIEW GATE bullet says "find missed ones" but only in
the hot-path-accuracy line; the all-cold case deserves its own callout.

---

## Edge case 4 — Generated/vendored/migration at scale, and the generated-but-hot trap

**The defect — two halves.**

**(a) Detection is asserted, not specified.** S1 says "exclude generated code, vendored
deps, fixtures." At scale, *detecting* these is non-trivial and the method gives no
recipe. Mis-detection corrupts the production-LOC measurement that the whole partition
is sized on (S1's "#1 trap"). A 50k-LOC vendored directory counted as production blows
the unit count; a generated 8k-LOC protobuf stub counted as one "module" produces a
slice that is a non-audit.

**(b) The generated-but-perf-relevant carve-out is missing entirely.** S1 says
"exclude generated code" *unconditionally*. But generated code is frequently **the
hot path**: a generated parser/lexer, generated serializers (protobuf/flatbuffers
codecs), a generated state machine, a query-builder's emitted SQL, ORM-generated
queries, a compiled regex/grammar table. Excluding it wholesale **silently drops the
most-executed code in the repo from coverage** — a coverage hole the method's own
disjoint-coverage principle would otherwise forbid, granted an exception it never
justifies.

**Fix — replace the unconditional exclusion with a detect-then-classify rule:**

> **Detecting generated/vendored/migration code (S1):** look for, in priority order —
> codegen headers/markers (`// Code generated … DO NOT EDIT`, `@generated`,
> `autogenerated`), tool config naming the output dir (`protoc`, `openapi-generator`,
> `prisma generate`, `bindgen`), vendoring manifests/dirs (`vendor/`, `node_modules/`,
> `third_party/`, `Godeps`), lockfile-adjacent paths, and migration directory
> conventions (`migrations/`, timestamped filenames). When unsure, sample the file —
> generated code has tell-tale uniformity. Record the exclusion basis per directory
> in the **out-of-scope list** so the review gate can challenge it.
>
> **Generated code that is ON the hot path is NOT excluded.** If the hot-path map (S2)
> shows execution concentrating in generated code (a generated parser, codec, query
> emitter, state machine), it gets a **FULL or REDUCED slice like any hot code** — but
> tag it **`generated-source`**: findings target the **generator config / schema /
> template**, never hand-edits to the emitted file (which the next codegen run would
> clobber). This is the generated analogue of S5's verification-mode tag: state where
> the fix can legitimately land.

Vendored deps stay excluded from *editing* but if a vendored hot path dominates, note it
as an OVERLAY/out-of-scope finding ("hot cost lives in dep X — upgrade/replace, don't
fork") rather than dropping it from awareness.

---

## Edge case 5 — S4 is the least executable phase. It needs an effort bound, a regress guard, and a lightweight fallback.

This is the method's self-declared "subtle one," and on inspection it is also the one
most likely to either **not get done** or **balloon into a second audit**.

**Problem 5a — "Frequency-map pre-artifact" has no effort bound.** S4 says "write a
short 'what calls this, how often' note … and hand it as adjacent context." Building a
genuine **call-frequency map across slices** is, in the general case, a whole-program
call-graph analysis with loop-multiplicity reasoning — *as much work as the audit it
feeds*. "Short note" and "call-frequency map" are doing a lot of undefended work in one
sentence. Without a bound, an operator either skips it (and the calibration defect the
whole phase exists to catch re-appears) or over-invests.

**Problem 5b — infinite "what calls the caller" regress.** Frequency is established by
a caller, but that caller's frequency is established by *its* caller, transitively to an
entry point. "Write what calls this, how often" invites unbounded upward traversal. The
method gives **no stop rule.** In a deep call stack the analyst can climb forever.

**Problem 5c — it's all-or-nothing.** S4 offers three mitigations (pre-artifact / order
caller-first / merge) but no **cheapest-possible** option for when even the short note
is too expensive or the caller is unknown.

**Is S4 practically executable?** *As written, only marginally* — it works in the
provenance case because that analyst already knew the Outbox-loop driver. For a cold
read of an unfamiliar repo it is under-specified and at risk of the two failure modes
above. The *idea* is correct and load-bearing; the *procedure* is missing.

**Fix — make S4 a bounded, demand-driven recipe with a fallback floor:**

> **Trigger only on detected impl/caller splits**, not every slice (demand-driven, not
> a global map — this is the key cost-control: you do NOT build a repo-wide
> call-frequency map; you build a one-paragraph note *only* for impls whose Impact you
> can't rank from within their own slice).
>
> **Bounded upward traversal — stop at the first of:** (1) you reach a frequency
> *class* you can name (per-request / per-frame / per-message / per-item-in-a-loop-of-N
> / once-at-startup) — you do **not** need the absolute number, only the class; (2) you
> reach an entry point (handler, `main`, scheduler, event source); (3) you have climbed
> **3 call frames** without resolving the class — then stop and apply the fallback.
> The class, not a count, is what calibrates Impact, so the traversal terminates fast.
>
> **Lightweight fallback (the floor):** when the caller is in an unaudited slice, is
> unknown, or traversal hit the depth cap, **tag the finding `frequency-unresolved —
> assume hot` and rank it at the optimistic (higher) Impact**, with a one-line note for
> the cross-validation step (cycle Phase 3) to confirm reachability. Rationale:
> under-ranking a real hot finding (the failure S4 exists to prevent) is worse than
> over-ranking a cold one, which Phase 3's cross-validation will catch and demote. This
> converts S4 from "do hard analysis or silently mis-rank" into "do bounded analysis or
> fail safe loud."
>
> **Escalate to order-caller-first or merge only when** the split is between two slices
> *both already in the plan* (cheap to order) or both small and tightly coupled (cheap
> to merge). Don't build the pre-artifact note when ordering/merging already resolves
> the split.

This keeps S4's correct insight while bounding its cost and removing the regress.

---

## Edge case 6 — Coverage drift: the ledger is built once and never reconciled at execution

**The defect.** S3.5 / S6 build a coverage ledger and "reconcile it against an actual
file listing" — **at plan time.** But the method explicitly targets long-running,
**resumable-across-sessions, ephemeral-container** jobs (S6 "mandatory for resumability").
Between partition planning and the execution of slice #12, files **move, get added,
get deleted, get renamed.** Slices are defined by **paths**. A renamed file is now in
*no* slice (coverage hole); a new file added to a package is silently outside the
partition; a moved file may land in *two* slices' path globs (double-count). The method
has **no reconciliation step at execution time** and no final re-diff.

This is the exact class of bug S3.5's own case warns about (the orphaned command module
caught only by the ledger) — but the method only runs that check *once*, at the moment
the drift is smallest.

**Fix — add drift reconciliation to S6 (and a guard at each slice run):**

> **Per-slice drift check (at the start of each slice run):** re-list the slice's paths
> against the live tree. New/moved/deleted files in that path scope are reconciled
> *then* — assign new files to the slice or to out-of-scope, flag moved-out files for
> their new home. Define slices by **stable anchors where possible** (package/module
> identity, symbol names) rather than brittle file paths.
> **Final coverage re-diff (after the last slice, before roll-up):** re-run the
> S3.5 ledger reconciliation against the *current* tree. Any file that entered the
> tree after planning and landed in no slice is a coverage hole — sweep it (a small
> COLD SWEEP or fold into the nearest slice) before declaring the repo covered. State
> the planning commit SHA in the slice plan so drift is *measurable* (diff plan-SHA..HEAD).

---

## Edge case 7 — A slice run discovers it's mis-scoped mid-execution: the partition is frozen with no feedback loop

**The defect.** The REVIEW GATE finalizes the partition "*before* executing runs," and
S6 then executes. But a slice's own cycle run can **discover** it's mis-scoped: too big
(the lane reports lose precision, or the run blows context), too small (the cycle finds
the real hot path lives just outside the slice's paths), or mis-tiered (a "cold sweep"
slice turns out to contain a genuine hot loop). The method has **no documented feedback
loop** to re-slice mid-execution. The partition is treated as immutable after the gate.
This is a robustness gap: the partition is a *hypothesis*, and execution is the test
that can refute it.

The risk of the opposite — an *unbounded* re-slice loop (every run re-partitions,
nothing ever finishes) — is real too, so the fix must be bounded.

**Fix — add a bounded re-slice trigger to S6:**

> **Re-slice triggers (bounded).** A slice run MAY signal "mis-scoped" when it
> concretely finds: it exceeds the band by >~50% in production LOC actually read; its
> real hot path crosses the slice boundary (an S4 split discovered late); or a
> COLD/REDUCED tier was wrong (genuine hot loop found). On such a signal: **adjust the
> affected slice(s) and their immediate neighbors only** — do **not** re-partition the
> whole repo. The adjustment gets a **lightweight delta-review** (the partition-design
> reviewer re-checks only the changed slices), not a full re-run of the gate.
> **Thrash guard:** a slice may be re-sliced at most **once**; a second mis-scope
> signal on the same region is escalated to the operator, not auto-re-sliced. Record
> each re-slice in the progress ledger so resumption sees the current, not the
> planned, partition.

---

## Edge case 8 — Over-fragment vs mega-run: the in-between needs a crisp decision rule, not just two warnings

**The defect.** The method warns against both extremes (mega-run collapses precision;
one-slice-per-file mis-calibrates S4) and the "Sizing rules of thumb" offers "split if
two halves have *different* hot-path character or *different* languages; keep together
if they share a data flow and a frequency driver." That's good guidance but it is
buried in a rules-of-thumb appendix, is phrased as advice, and has **no tie-breaker**
for the genuinely ambiguous middle (two subsystems that share *some* data flow but have
*somewhat* different hot-path character — the common real case).

**Fix — promote it to a decision rule in S3 with a tie-breaker:**

> **Split/merge decision rule (S3):** Default to the **coarsest** partition that keeps
> every slice (a) under the band and (b) free of an *internal* hot-path/cold-glue
> mix that would make one tier wrong for part of it. Then **split** a candidate slice
> iff at least one holds: different language/runtime (per the round-1 carve-out);
> different hot-path character (one half hot, other cold); the slice exceeds the band.
> **Merge** two candidates iff all hold: same runtime; shared data flow; one
> establishes the other's frequency (an S4 split you'd otherwise have to repair). 
> **Tie-breaker when neither cleanly applies:** prefer **fewer, larger** slices up to
> the band ceiling — over-fragmentation's S4 mis-calibration is the costlier and
> subtler failure (it silently mis-ranks), whereas a slightly-large slice fails
> *loudly* (you notice lane precision dropping) and is cheaply re-sliced (Edge-Case-7).

---

## Edge case 9 — Missing phases: shared substrate, slice dependency ordering, and an over-optional roll-up

**9a — Shared library used by many slices has no home.** A `common/` / `utils/` /
`core` crate called by every slice is a coverage and calibration problem the method
doesn't address. Put it in its own slice and its findings can't see their frequency
(it's set by N callers in N other slices — the S4 split, at maximum fan-out).
Distribute it across callers and you double-count and violate disjoint coverage.

> **Fix — shared-substrate handling (S3):** a unit called by many slices gets its
> **own slice**, tier it by the *hottest* caller's path, and feed it the **union** of
> its callers' frequency classes as adjacent context (the S4 pre-artifact, fanned in).
> Run it **after** at least its hottest caller so the frequency is known, OR tag its
> findings `frequency = max over callers` via the Edge-Case-5 fallback. It is the
> canonical many-to-one S4 case and deserves a named pattern.

**9b — Slice dependency ordering is implicit.** S6 says order "frequency-establishers
before impl slices; overlays after members; cold sweep last" — that *is* a dependency
order, but it's prose, not a maintained graph. For a large partition the ordering
constraints form a DAG (overlay-after-members, caller-before-impl, shared-substrate
timing). The method has no artifact for it; the project's own CLAUDE.md uses `bd dep
add` edges for exactly this.

> **Fix:** make the slice plan carry **explicit dependency edges** (impl←caller,
> overlay←members, substrate←hottest-caller) so execution order is derived from the
> DAG, not re-reasoned each session. This also makes resumption deterministic.

**9c — The repo-level roll-up is "optional" but is sometimes the whole point.** S6's
roll-up (cross-slice root causes, repo-wide themes, heat map) is marked **optional**.
But "audit the whole repo" requests frequently *want* exactly that synthesis — a
per-slice pile of reports without a roll-up arguably fails to answer "how's the repo?"
And the roll-up is where compounding cross-slice costs and shared root causes surface —
precisely the cross-slice class single passes miss.

> **Fix:** make the roll-up **conditionally mandatory** — REQUIRED when the original
> request was whole-repo / "everything" / "how's the perf of <X>" (a posture question),
> OPTIONAL when the request was "cover all of <bounded set>" (a coverage question).
> Minimum roll-up: shared root causes across slices, the top cross-slice compounding
> path(s), and a one-screen heat map. The OVERLAY tier feeds it.

**9d (minor) — No "abort/escalate" exit.** If S1's survey reveals the scope is
genuinely too large or too tangled to partition responsibly within budget, the method
has no documented "stop and scope down with the operator" exit. Add one to S0/S1: a
partition that would exceed ~20–30 units, or whose survey can't establish build units,
is escalated to the operator for scope-narrowing before lane budget is spent.

---

## Top 3–5 edits, ranked

1. **Add S0.5 "one program or many?"** (Edge-Case-1). Highest leverage: without it the
   method produces *incoherent* slices on the single most common large-repo shape
   (service/package monorepo), and every downstream phase (load profile, S4 frequency,
   disjoint coverage) inherits the wrong frame. One short phase fixes it.
2. **Make S4 a bounded, demand-driven recipe with a fail-safe fallback** (Edge-Case-5).
   This is the method's subtlest and least-executable phase; as written it either
   doesn't get done or balloons. The depth-3 stop rule + "frequency class not count" +
   `assume-hot` fallback make it both finite and safe, preserving its correct insight.
3. **Add coverage-drift reconciliation at execution + final re-diff** (Edge-Case-6).
   The method *targets* long-running resumable jobs but only checks coverage at plan
   time — the moment drift is smallest. This is a latent correctness bug in the
   method's headline guarantee ("disjoint, complete coverage").
4. **Add the seamless-oversize fallback** (Edge-Case-2) — closes an undefined branch on
   god-files (endemic, high-value-to-audit code) where all three "allowed" outcomes are
   wrong.
5. **Add a bounded mid-execution re-slice loop** (Edge-Case-7) + **promote the
   split/merge decision rule with a fewer-larger tie-breaker** (Edge-Case-8). Together
   they make the partition a falsifiable hypothesis with a safe correction path rather
   than a frozen guess, and resolve the ambiguous middle the warnings currently leave open.

**Lower-priority but real:** generated-but-hot carve-out + detection signals (Edge-Case-4),
shared-substrate pattern + explicit dep DAG + conditionally-mandatory roll-up
(Edge-Case-9), explicit "no hot path is a valid outcome" exit (Edge-Case-3), and an
abort/escalate exit for un-partitionable scope (9d).

---

## What is genuinely robust (keep, do not touch)

- The **production-LOC-not-raw-LOC** insight with a non-uniform multiplier is the
  method's sizing backbone and is correct.
- The **REVIEW GATE with a dedicated partition-design lens** is the right structural
  defense and its case (4 finding-hunting rounds missed what 1 partition round caught)
  is compelling. My fixes mostly give that reviewer *more concrete things to attack*
  (absence of hot path, drift, generated-but-hot, shared substrate).
- The **depth-tier vocabulary** (FULL/REDUCED/COLD-SWEEP/OVERLAY) is a good
  controllable knob; the gaps are in *when* to pick each on edge shapes, not the knob.
- **Verification-mode tagging** (hardware-deferred → complexity argument, never fake
  numbers) is excellent and generalizes (it's the model my generated-source and
  frequency-unresolved tags follow).
