# Method Review (followability/proportionality)

Reviewer: glade-knoll-shoal. Angle: can an AI coding agent actually FOLLOW
`whole-repo-scoping.md` end-to-end, and is the cost proportionate to the task?
Read-only review of `whole-repo-scoping.md` + the S25 routing block in
`SKILL.md`. The document is conceptually excellent — the five principles, the
S4 frequency insight, and the heuristics table are genuinely load-bearing
distilled knowledge. The weakness is almost entirely **operational**: it tells
you WHAT to decide but rarely HOW to decide it mechanically, and it has **no
scaling rule** — so a 15k-LOC routine service inherits the same six-phase +
review-gate ceremony that a 96k-LOC five-round provenance case earned.

---

## VERDICT

**Conditionally followable, but under-operationalized and over-heavy for the
common case.** A strong agent can fake its way through S1–S6 by inventing
method, but two agents running the same repo will produce materially different
partitions because the crisp decision points (split/don't, which tier, hot/
warm/cold) are **vibes, not rules**. And the method front-loads
survey+map+slice+review before a single finding is produced — defensible at
96k LOC, disproportionate at 8–15k. Ship with: (1) concrete how-to for the four
measurement steps, (2) an explicit review-depth + method-depth **scaling table**
keyed to slice count, (3) a **lightweight path** for small-but-oversized scopes,
(4) if/then decision rules for split and tier, (5) a specified ledger schema.

---

## 1. UNDERSPECIFICATION — the four steps that lack a how-to

### 1a. "Production LOC" (S1) — named as the #1 trap, given NO method
The doc correctly insists you measure production-not-raw LOC and warns the ratio
is non-uniform (0.1×–6.9×). But it never says *how* to exclude tests/generated/
vendored across languages. An agent is left to invent it per-repo, which is
exactly where the non-uniform ratio bites. This is the single highest-leverage
fix: the trap the doc flags hardest is the one it equips you least to avoid.

**Proposed concrete text (new S1 sub-block "How to measure production LOC"):**
> Use `tokei`/`scc` if available (`tokei --output json`) for a per-language,
> comments-excluded baseline; otherwise `git ls-files`. Then subtract the
> excludes below per ecosystem. Do NOT trust a single repo-wide multiplier —
> compute per candidate slice.
> | Ecosystem | Exclude (path/glob/marker) |
> |---|---|
> | Rust | `#[cfg(test)]` mod blocks, `tests/`, `benches/`, `build.rs`, `target/`, `*/generated/*`, `OUT_DIR` codegen |
> | TS/JS | `*.test.ts(x)`, `*.spec.ts`, `__tests__/`, `__mocks__/`, `*.d.ts`, `dist/`, `build/`, `node_modules/`, `*.stories.tsx`, snapshots |
> | Go | `*_test.go`, `testdata/`, `vendor/`, `*.pb.go`/`*_gen.go` (generated) |
> | Python | `tests/`, `test_*.py`, `conftest.py`, `*_pb2.py`, `.venv/`, migrations |
> Generated-code tell: header banner (`// Code generated … DO NOT EDIT`,
> `@generated`), or a manifest `build`/`codegen` step that emits the dir.
> When a file mixes inline tests with prod code (Rust `#[cfg(test)]`,
> co-located TS), subtract the test block's line span, not the whole file.
> Record the raw→production delta per slice in the survey table so the reviewer
> can sanity-check the ratio.

### 1b. "Latent / dead code" (S2) — "no in-tree callers" with no detection recipe
The case (LDPC crate, zero callers, would have screamed CRITICAL) shows why
this matters, but "modules with no in-tree callers" is asserted as if cheap to
establish. It isn't, naïvely — and false-positives here are dangerous (flag live
code as dead → under-rank a real CRITICAL).

**Proposed text (S2, after the Latent bullet):**
> Cheap reachability check: for each candidate module's public entry symbols,
> grep the tree for call sites (`rg '\bsymbol\('`), import/use of the module
> (`use crate::x`, `import … from 'x'`), and manifest wiring (is the crate in a
> workspace member's `[dependencies]`? the package in `package.json`?). Zero
> call sites AND zero non-test imports AND not an entry binary ⇒ candidate dead.
> CONFIRM before flagging: dynamic dispatch, trait-object/registry, plugin,
> reflection, FFI export, and `pub` API consumed out-of-tree all defeat grep —
> if any apply, treat as LIVE-uncertain, not dead. Record the evidence
> ("0 call sites for `decode_ldpc`; crate absent from all workspace deps").

### 1c. The S4 frequency map — the subtlest step, the thinnest recipe
S4 mandates a "frequency-map pre-artifact" — a note on "what calls this, how
often (per-request/per-frame/per-message/in a loop over N)". But it never says
how to *build* it: where to look, what the artifact looks like, when it's
"done". An agent will either skip it (it's optional-feeling prose) or flail.

**Proposed text (S4, replace the bare Frequency-map bullet):**
> **Building the frequency map (per impl/caller split you detect):**
> 1. Identify the split: a hot finding-prone impl (codec, parse, alloc loop)
>    whose call frequency is set elsewhere. Find callers: `rg` the impl's public
>    symbols across the *other* slices.
> 2. For each caller, classify the multiplier by the loop/handler it sits in:
>    per-request, per-frame/RAF, per-message, per-row over N, startup-once.
>    Read the enclosing function — do NOT infer from the caller's name (S2 rule).
> 3. Write ≤1 page: `impl symbol → caller file:line → multiplier class → N
>    estimate if loop-bound`. That's the artifact; hand it to the impl slice as
>    adjacent context. Done when every external hot caller of the impl appears.
> Skip the map only when impl + caller share a slice (the cycle sees both).

### 1d. Hot / warm / cold — the central tiering judgment is undefined
S2/S3/S5 lean on "hot" vs "warm" vs "cold glue" everywhere, but there is **no
definition or checklist** — it's the most-used distinction in the doc and the
least specified. Two agents will tier the same slice differently.

**Proposed text (new box after S2, "Hot/warm/cold checklist"):**
> Classify a slice by answering, against code (not names):
> - **HOT** if ANY: sits on a per-request/per-frame/per-message handler; an
>   inner loop over user-scaled N; a real-time/latency-bound callback; alloc or
>   I/O inside such a loop. (→ FULL tier.)
> - **WARM** if: invoked per-operation but not in a tight loop; secondary
>   pipeline stage; bounded/small N; runs once per user action. (→ REDUCED.)
> - **COLD** if ALL: CRUD/IPC marshalling/config/string assembly/form render;
>   no loop over unbounded N; startup or rare-path only. (→ COLD SWEEP batch.)
> Tie-breaker: if you cannot find the loop/handler/callback that would make it
> hot, it is NOT hot — default WARM, and note the uncertainty for the reviewer.

---

## 2. PROPORTIONALITY OF THE REVIEW GATE — must scale, currently doesn't

The gate says "≥1 adversarial review (more for large or high-stakes), at least
one with a partition-design lens." The provenance case used FIVE rounds. For a
routine "audit my 15k-LOC service" (→ ~4–6 slices), demanding a partition-design
round on top of a general round is plausibly 2× the human/agent cost of just
*doing two of the slices*. "More for large" is too vague to act on — an agent
cannot tell whether its repo is "large." **The number of review rounds and
whether the design-lens round is mandatory must be a function of slice count.**

**Proposed explicit rule (replace "more for large or high-stakes repos"):**
> **Review depth scales with partition size (count audit units, not LOC):**
> | Audit units in plan | Required review |
> |---|---|
> | 1–2 | None mandatory; a 5-min self-check against the heuristics table suffices. (At ≤2 units you are barely partitioning.) |
> | 3–5 | **1 round**, general lens. Add the partition-design lens to that same round's checklist (it need not be a separate round). |
> | 6–12 | **1 round, partition-design lens REQUIRED** as its explicit job (the S4 cross-slice class is the one a finding-hunting pass misses). |
> | 13+ or high-stakes¹ | **≥2 rounds**, ≥1 dedicated partition-design. Revise between rounds; finalize on a nits-only round. |
> ¹ high-stakes = the audited code is on a revenue/safety/SLA-critical path, OR a wrong partition would waste a large lane budget (>12 units).
> The five-round provenance case was a 96k-LOC, ~15-unit, multi-language repo at
> the top of this table — it is the CEILING, not the default. Do not import its
> round count into a 5-unit job.

This also fixes a subtle credibility problem: the doc's own provenance ("five
rounds, the fifth caught the defect") reads as an *implicit minimum* to a
literal-minded agent. Stating that five was the ceiling for a top-tier repo
inoculates against cargo-culting it.

---

## 3. TOTAL COST — a lightweight path is needed

The method front-loads S1 survey → S2 map → S3 slice → S4 calibrate → review
gate, all BEFORE any finding is produced. At 96k LOC that overhead amortizes
across ~15 runs. At the S0 trigger floor (~4k production LOC, or "spans >1
language") it does not: an agent could be doing the whole ceremony to justify
cutting a 6k-LOC repo into two obvious slices. There is currently **no escape
hatch** — S0 is binary (use the method or don't), with nothing in between.

**Proposed new section "Lightweight path (small oversized scopes)":**
> **When the full method is too heavy.** If the survey (S1) yields **≤2 natural
> slices** OR total production LOC is **<~8k across ≤2 languages**, take the
> lightweight path instead of S2–S6 + gate:
> 1. Do S1 (survey + production-LOC) — always; it's cheap and gates this choice.
> 2. Eyeball the 2 most perf-relevant slices using the Hot/warm/cold checklist.
> 3. Run the cycle on each; skip the formal frequency-map UNLESS a hot impl's
>    caller is in the *other* slice (then write the ≤1-page map — that's the one
>    S4 lesson that still applies at small scale).
> 4. Self-check against the heuristics table (1 min); no formal review round.
> 5. Keep a 3-line ledger (which slices, done/pending) for resumability.
> **Do NOT take the lightweight path if** any slice looks hot AND its frequency
> is driven from another slice (S4 risk is real at any size), or the repo is
> high-stakes. When in doubt between paths, do the survey first — its slice
> count picks the path for you.

This makes S0 a three-way branch (run cycle directly / lightweight path / full
method) instead of binary, and gives the agent a stated "just do 2–3 obvious
slices" permission the user's brief explicitly asks about.

---

## 4. DECISION RULES vs PROSE — the crisp calls need if/then form

The five principles and the table are good *guidance* but the two recurring
**decisions** an agent must make mechanically are buried in prose:

**(a) Split or don't?** Currently scattered across S3.3, the sizing rules of
thumb, and "If unsure whether to split: split if two halves have different
hot-path character or different languages." Promote to one rule:
> **SPLIT a candidate IFF any:** production LOC >4k; spans >1 language/ecosystem;
> two halves have different hot/warm/cold character; impl and its frequency
> driver would land in different existing slices and can't be merged.
> **KEEP TOGETHER IFF all:** ≤4k production LOC; one language; one shared data
> flow + one frequency driver; same tier. Split only on **real file/module
> seams** — name the files per sub-slice; never cut by line count.

**(b) Which tier?** S5 lists FULL/REDUCED/COLD/OVERLAY but the *selection* is
prose. Bind it to the §1d checklist explicitly: HOT→FULL, WARM→REDUCED,
COLD→COLD SWEEP, cross-slice-compounding-pipeline→OVERLAY (after members),
dead-code→audit at REDUCED with reachability≈0 flag, external-process→REDUCED.
A one-line "checklist result → tier" mapping removes the residual judgment.

---

## 5. RESUMABILITY / BOOKKEEPING — ledger content is under-specified

S6 mandates a "progress ledger (per-slice state + artifact paths + how to
resume)" — but "how to resume" as free text won't reliably survive a context
reset to a fresh session. The next agent needs a **schema**, not a vibe.

**Proposed text (S6, replace the progress-ledger phrase with a spec):**
> **Progress ledger** — write `docs/perf-audits/<repo>-slice-progress.md` (or
> `.jsonl`) with, per slice: `id | slice name | paths (globs) | language |
> production LOC | tier | verification mode | status (pending/running/done/
> deferred) | artifact paths (validated report, plan) | adjacent-context/
> frequency-map pointer | exec order index`. Plus a header block: total slices,
> N done, the coverage-ledger reconciliation status (S3.5), and the out-of-scope
> list. **Resume rule:** a fresh session reads this file FIRST, finds the lowest
> exec-order index whose status≠done, and continues from there — no
> reconstruction from `git log`. Update the ledger in the SAME commit as each
> slice's report (S6 "commit per unit"), so ledger and artifacts never diverge.

Note: this also resolves a latent inconsistency — S6 names three artifacts
(slice-plan, progress ledger, run ledger `runs.jsonl`) but the cycle's Phase 8
only commits `runs.jsonl`. State where the slice-plan and progress ledger live
and that they're committed too, or the resumability guarantee is paper-only.

---

## 6. FAILURE / UNCERTAINTY HANDLING — no "when in doubt" rule exists

The doc gives zero guidance for the two predictable stuck states. An agent that
can't resolve them will either stall or guess silently.

**(a) Can't tell if code is hot (no visible entry point).** Proposed rule:
> If you cannot locate the handler/loop/callback that would make a slice hot
> after a reasonable grep, do NOT guess hot. Default it WARM (REDUCED tier),
> record "hot-path unverified: no entry point found" in the slice note, and
> raise it explicitly to the review gate as an open question. Never tier a slice
> HOT on a name or a hunch (S2 verify-against-code rule).

**(b) User disagrees with a slice.** Proposed rule:
> The partition is a presented artifact, not a fait accompli. If the user
> disputes a slice boundary or tier, treat their domain knowledge as
> authoritative on reachability/frequency (they know the real load), re-cut,
> and re-run the affected review tier — but preserve **complete disjoint
> coverage** (S3.5): every reassigned file still lands in exactly one slice;
> reconcile the coverage ledger after the change.

A general backstop is also worth one line in the heuristics table:
> **When in doubt:** prefer fewer-larger slices over finer (over-fragmentation
> mis-calibrates frequency — the worse failure mode); prefer WARM over HOT when
> the hot path is unverified; prefer doing the survey before choosing a path.

---

## TOP EDITS (ranked)

1. **Add the production-LOC how-to table (§1a).** Highest leverage: the doc's
   self-declared #1 trap currently ships with no method. Without this, S1 is
   guesswork and every downstream sizing decision inherits the error.
2. **Add the review-depth + method-depth scaling table (§2 + §3).** Turns
   "more for large" into a slice-count-keyed rule, caps the 5-round case as a
   ceiling not a default, and introduces the lightweight path. This is the
   proportionality fix the whole method needs to be safe for routine use.
3. **Add the Hot/warm/cold checklist (§1d) and bind tiers to it (§4b).** The
   most-used distinction in the doc is currently undefined; this makes tiering
   reproducible across agents.
4. **Specify the progress-ledger schema + resume rule (§5)**, and fix the
   slice-plan/ledger-vs-Phase-8-commit inconsistency. Makes the resumability
   guarantee real, not paper.
5. **Add "when in doubt" rules for unverifiable-hot and user-disagreement
   (§6),** plus the frequency-map build recipe (§1c) and dead-code detection
   recipe (§1b). These close the remaining "invent your own method" gaps.
