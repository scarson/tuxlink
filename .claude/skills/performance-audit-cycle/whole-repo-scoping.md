# Whole-Repo / Oversized-Scope Slicing (method)

**Load this when:** the requested audit scope is a whole repository, a top-level
directory/package set, "everything", "all of `<X>`", or any surface materially
larger than one `performance-audit-cycle` run is optimized for. A single cycle's
lanes perform best on a **precise, bounded, perf-relevant** surface (≈1–4k
*production* LOC of one coherent subsystem). This method turns "audit the whole
thing" into a **reviewed partition** of bounded slices, each fed to its own
cycle run, that collectively cover all the code — without the two failure modes
of naïve approaches: mega-runs (lane precision collapses on huge scope) and
over-fragmentation (Impact mis-calibrates when a finding's caller lives in
another slice).

> **Why this exists / provenance.** Distilled from a real whole-repo application
> (a ~96k-LOC Rust+TS desktop modem app) that was partitioned by this method and
> hardened over **five independent adversarial review rounds**. The lessons
> below are the defects those rounds caught — each is a trap a single pass falls
> into. The worked example is referenced throughout as *[case]*.

---

## The procedure (six phases + a review gate)

### S0 — Detect that you need this
Trigger when scope is a repo/dir/"everything", or when your size estimate for
the named scope exceeds ~4k production LOC or spans >1 language/package. If the
user named a precise bounded surface, DON'T use this — run the cycle directly.

### S1 — Survey (measure the real surface)
Enumerate the codebase before slicing:
- **Build units**: packages / crates / modules / apps (from manifests —
  `Cargo.toml`, `package.json`, `go.mod`, `pyproject.toml`, `*.csproj`, …).
- **Languages/ecosystems** per area (decides which profile packs + lanes apply).
- **Size on PRODUCTION LOC, not raw LOC.** Exclude inline tests
  (`#[cfg(test)]`, `*_test.go`, `*.test.tsx`, `__tests__`), generated code,
  vendored deps, fixtures, and non-code (`.css`, snapshots). **This is the #1
  trap:** in test-heavy code raw LOC ran ~2× production and the ratio is *not
  uniform per file* (observed 0.1×–6.9× across files) *[case: a 9.1k-LOC "module"
  was 4.5k production; sizing on raw LOC produced a partition twice too granular]*.
  Measure each candidate slice's production LOC; don't assume a flat multiplier.
- Output a **survey table**: unit → language → production LOC → one-line purpose.

### S2 — Hot-path & reachability map (cheap, structural)
Locate where the program concentrates work, and where it doesn't. Use cheap
signals, not a full audit: entry points (request/render/frame/message handlers,
inner loops, real-time callbacks), allocation/IO on those paths, and call-graph
sketches. Crucially, classify three calibration hazards that re-tier slices:
- **Cold glue** — CRUD, IPC marshalling, config, string assembly, form rendering.
  A full 8-phase cycle yields ~nothing here; batch it (see depth tiers).
- **Latent / dead code** — modules with **no in-tree callers** (a commented-out
  dep, a feature "not wired in yet"). Audit them, but their findings are
  *intrinsic-cost / reachability ≈ 0 today* — flag "fires once wired in", don't
  false-rank them CRITICAL *[case: an LDPC crate had zero callers; the live path
  bypassed it — a blind lane that ignored this would have screamed CRITICAL on
  dead code]*.
- **External-process boundaries** — work done in a child process / DB engine /
  GPU / remote service. The audited code there is **I/O + orchestration**, not
  the compute; tier it reduced, not full *[case: a modem "DSP" module was really
  TCP plumbing to an external TNC process]*.
- **VERIFY hot-path hypotheses against code; never infer from names** *[case: a
  "radio/waterfall UI" had zero canvas/`requestAnimationFrame` — it was ordinary
  React; the "render hot path" was imaginary]*.

### S3 — Cut the slices (the five principles)
1. **Language/ecosystem-homogeneous.** Never mix (e.g.) Rust and TS in one slice
   — the lanes, profile packs, and idiom-currency index differ.
2. **Coherent subsystem + shared data flow** per slice (one pipeline stage, one
   feature, one package), not an arbitrary LOC chunk.
3. **Size to the sweet-spot band** (~1–4k production LOC). Split anything larger
   along **real module/file seams** (name the files in each sub-slice), not by
   line count.
4. **Slice by perf-relevance, not raw size.** Carve genuine hot paths into their
   own slices; pull a warm exception out of a cold neighborhood (e.g. a
   text-index loop out of a cold feature-backend bucket); batch the rest.
5. **Complete, disjoint coverage.** Every code unit lands in **exactly one**
   slice; maintain a coverage ledger and reconcile it against an actual file
   listing. Explicitly list **out-of-scope** (tests, `bin/` probes, generated) —
   never silently drop *[case: a Rust command module was orphaned because its
   name collided with a same-named frontend dir; only the coverage ledger caught
   it]*.

### S4 — Cross-slice frequency calibration (the subtle one)
A finding's Impact = reachability × **frequency** × per-occurrence cost. The
frequency is often established by a **caller in a different slice** than the
implementation. When impl and hot caller are split, the slice auditing the impl
can't see how often it runs and **under-ranks** it. Detect these impl/caller
splits and mitigate (cheapest first):
- **Frequency-map pre-artifact**: before running the affected slices, write a
  short "what calls this, how often (per-request / per-frame / per-message / in a
  loop over N)" note for the domain, and hand it to those runs as **adjacent
  context** *[case: a compression routine's real driver was an Outbox-loop in a
  cold-swept orchestrator file; a one-page call-frequency map fed to the
  compression slice fixed the calibration without re-tiering]*.
- **Order runs** so the frequency-establishing slice precedes the impl slice.
- **Merge** the two slices if they're small and tightly coupled.
Do the same audit for the language you're in — e.g. a decode kernel whose
per-frame frequency is set by an orchestration crate run later.

### S5 — Assign depth tiers + verification modes
Not every slice deserves a full cycle. Tier by perf-value:
- **FULL** (all 8 phases, all core lanes) — genuine hot paths.
- **REDUCED** (trim lanes to algorithmic/memory/data-access/concurrency; skip
  idiom-currency/payload-startup unless flagged) — warm/secondary subsystems.
- **COLD SWEEP** (one batched run, ~3 lanes: complexity + allocation +
  data-access) over all the cold glue at once — coverage without waste.
- **OVERLAY** (analysis-only, not a code slice) — when a hot pipeline spans
  several slices and the compounding end-to-end cost would be missed per-slice;
  run it *after* its member slices.
Also tag each slice's **verification mode**: can the environment build+run it
(dynamic lane / measured confidence available), or is it static-only /
**hardware-deferred** (needs a device, a live service, a workload that doesn't
exist)? State this so downstream fix-plans don't promise measurements that can't
be taken *[case: rig/PTT timing findings were unfalsifiable without radio
hardware — flagged so the plan relied on complexity arguments, not fake numbers]*.

### S6 — Order, persist, execute
- **Execution order**: hottest/highest-signal first; frequency-establishers
  before their impl slices; overlays after members; cold sweep last.
- **Persistent artifacts** (mandatory for resumability across sessions /
  ephemeral containers): a **slice-plan** doc (the partition), a **progress
  ledger** (per-slice state + artifact paths + "how to resume"), and the per-run
  **run ledger** (`runs.jsonl`) for regression tracking.
- **Commit per unit.** Each slice's consolidated report + ledger update is a
  commit; never batch a whole repo's worth of audit into one.
- Optional **repo-level roll-up**: after the slices, a short cross-slice
  synthesis (shared root causes, repo-wide themes, a heat map).

### REVIEW GATE — adversarially review the partition *before* executing runs
**The partition is itself a substantive artifact and a single pass misses
cross-slice defects.** Run ≥1 independent adversarial review of the slice plan
(more for large or high-stakes repos) BEFORE spending lane budget. Each reviewer
should attack, grounded in the actual code:
- sizing (production vs raw LOC), and any slice still over/under the band;
- hot-path accuracy (verify, refute imaginary ones, find missed ones);
- mis-tiered slices (cold-as-warm, warm-as-cold, latent not flagged);
- **cross-slice frequency splits** (S4) — this is the class a hot-path-only
  review reliably misses;
- coverage gaps / double-counts / language mis-bucketing;
- the **fewer-larger vs finer-grained** tradeoff for this repo.
*[case: four "hot-path-hunting" rounds converged "clean"; the fifth, a
**partition-design** lens, found a cross-slice calibration defect that all four
missed — budget at least one review whose explicit job is partition design, not
finding-hunting.]* Revise between rounds; finalize when a round finds only nits.

---

## Heuristics & anti-patterns (quick reference)

| Trap | Reality / rule |
|------|----------------|
| Size on raw LOC | Measure **production** LOC; ratio is non-uniform per file. |
| Infer hot paths from names | **Verify against code** (grep for the loop/canvas/RAF/query that would make it hot). |
| One mega-run for "everything" | Lane precision collapses; bounded slices win. |
| One slice per file | Over-fragmentation; cross-slice frequency mis-calibrates. Group into coherent subsystems. |
| Full cycle on cold CRUD | Batch cold glue into one sweep; spend full cycles on hot paths. |
| Latent/dead code ranked hot | Audit it, flag reachability ≈ 0, "fires once wired in". |
| "External DSP/DB" code ranked hot | It's I/O+orchestration; the compute is in the other process. Reduced tier. |
| Impl + caller in different slices | Frequency-map pre-artifact, or order caller-first, or merge. |
| Promise measurements you can't take | Tag verification mode; hardware-deferred → complexity argument, never fabricated numbers. |
| Trust a single partition pass | ≥1 adversarial review whose explicit lens is **partition design**. |

## Sizing rules of thumb (tune per ecosystem)
- Sweet-spot slice: **~1–4k production LOC**, one coherent subsystem.
- A repo of ~100k production LOC → typically **~10–20 audit units** (a mix of
  full / reduced / one cold sweep), NOT one run and NOT one-per-file.
- If unsure whether to split: split if two halves have *different* hot-path
  character or *different* languages; keep together if they share a data flow
  and a frequency driver.

## What this method produces (hand-off to the cycle)
A **reviewed slice plan**: an ordered list of slices, each with {paths,
language, production LOC, depth tier, verification mode, adjacent-context /
frequency-map pointers}, plus the coverage ledger and the out-of-scope list.
Each FULL/REDUCED slice is then a normal `performance-audit-cycle` invocation;
the COLD SWEEP is one trimmed `performance-audit` run; OVERLAYS are analysis
passes. The progress ledger tracks completion so the whole-repo job survives
interruption.
