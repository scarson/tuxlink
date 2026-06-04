# Performance-Audit Scope Partition — Adversarial Review Log

**Decision under review:** how to partition the whole tuxlink repo into bounded
slices, each one `performance-audit-cycle` run, collectively covering all code.

**Operating mandate (operator, 2026-06-04):** autonomous operation; substantive
decisions require multiple perspectives + **≥5 rounds adversarial review** +
persistent documentation. Reviewers are independent Opus subagents (Codex
unavailable in this container). Commit+push after every unit (ephemeral
container). This log + `dev/perf-audit-reviews/round-N.md` are the persistent
record for later operator review.

**Plan artifact:** `dev/perf-audit-scope-plan.md` (versioned; git history holds
each revision). **Execution ledger:** `dev/perf-audit-progress.md`.
**Skill UX feedback:** `dev/perf-audit-skill-feedback.md`.

---

## Round status

| Round | Reviewer | Target | Full report | Result → revision |
|-------|----------|--------|-------------|-------------------|
| 1 | Opus subagent | v1 | `round-1.md` | major rework → v2 |
| 2 | Opus subagent | v2 | `round-2.md` | minor-edits → v3 |
| 3 | Opus subagent | v3 | `round-3.md` | minor-edits → v4 |
| 4 | Opus subagent | v4 | `round-4.md` | substantive (calibration) → v5 |
| 5 | Opus subagent | v5 | `round-5.md` | **GO** → v6 (final) |

Convergence rule: continue past 5 only if a round still finds a *substantive*
(tier-changing or coverage-breaking) defect. Finalize when ≥5 done and the last
round finds only nits.

**FINALIZED at v6 (2026-06-04).** Round 5 verdict GO; its findings are
calibration/accuracy refinements (FEC-latent reframing, R2 path fix), none
tier-changing or coverage-breaking, none blocking M1. Five independent Opus
rounds satisfy the operator's ≥5-round mandate. Execution proceeds; see
`dev/perf-audit-progress.md`.

---

## Round 1 — dispositions (v1 → v2)

Full report: `dev/perf-audit-reviews/round-1.md`. All five top recommendations
ACCEPTED:

1. **Re-size on production LOC** — ACCEPTED. Verified independently:
   `tuxmodem-phy` 1760 prod / 1358 test; raw ≈ 2× production. Dissolved most v1
   "oversized" flags.
2. **Delete frontend "render hot path" framing** — ACCEPTED. Verified: zero
   `canvas`/`getContext`/`WebGL`, only one one-shot `requestAnimationFrame`
   (`help/ReadingPane.tsx:41`). Tier E re-tiered warm/cold.
3. **Split D4 by perf-relevance** — ACCEPTED. `search/` → MUST-DO (M4);
   forms/grib/position/catalog → cold sweep.
4. **Add `audio_device.rs` + live-RX pipeline overlay** — ACCEPTED. M1 now =
   entire `tuxmodem-phy/src` incl. audio path; O1 overlay added.
5. **Close coverage gaps + exclude test/bin + recommend Criterion benches** —
   ACCEPTED. `main.rs`, `App.tsx`/`main.tsx`/`routing.ts` assigned; out-of-scope
   list added; benches flagged as the M1/M2 measurement gate.

Other accepted: ARDOP modem is I/O+state (not DSP) but still M3 full for
buffering/throughput; hf-channel-sim caches its FftPlanner, tiered M5 (lower
urgency); tx/rx are thin CLI drivers → R1 reduced depth.

Outcome: 22 slices → **5 full + 1 overlay + 9 reduced + 1 cold sweep = 16
units**. See v2 in plan artifact.

---

## Round 2 — dispositions (v2 → v3)

Full report: `dev/perf-audit-reviews/round-2.md`. Verdict: **minor-edits** — v2
fundamentally sound and NOT over-corrected. All findings ACCEPTED:

1. **Coverage gap `wizard.rs` (blocking)** — ACCEPTED. The 614-LOC Rust Tauri
   `WizardMutex` command module was conflated with TS `src/wizard/` and omitted
   from the cold-sweep Rust enumeration. Added to cold sweep.
2. **New hot path H1 (`constellations.rs::compute_llr:142`)** — ACCEPTED. Per-
   data-subcarrier `self.alphabet()` rebuild (up to 64 nested `map()` allocs),
   called at `receiver.rs:83` inside the RX demod inner loop — worse than the
   per-symbol planner. Added to M1 hot-path map as Critical.
3. **Demote M3 (ARDOP) full → reduced** — ACCEPTED. Verified
   `transport.rs`/`data.rs` are external-TNC socket I/O + framing + state
   machine (DSP in `ardopcf` child, `process.rs:74`); a full cycle's
   complexity/payload/startup lanes have nothing to bite. Reduced-depth with
   concurrency (split-borrow hazard `transport.rs:666`) + alloc + I/O focus.
4. **Promote storage backend out of cold sweep** — ACCEPTED. `native_mailbox.rs::list:99-104`
   does `read_dir` + `fs::read(body)` per message (N+1 / read-amplification) —
   the backend root of R9's non-virtualized `MessageList`. New reduced slice R10
   (native_mailbox + config + user_folders + session_log).
5. **Correct two soft claims** — ACCEPTED. (a) prod:test is per-file variable
   (~0.10×–6.89×), not a flat 2× (principle 2 reworded). (b) hf-sim caches its
   planner only in `channel.rs`; `fading.rs:49,86` + `analysis.rs:50` re-plan
   per call (added as H9, strengthens M5).
6. **New hot path H7 (`narrow_fsk.rs:83` per-call FftPlanner)** — ACCEPTED;
   added to M1 (Major). **Seam note:** `process.rs` shared M3↔R4, primary home
   R4 (M3 reads it as adjacent context).

Outcome: 16 → **4 full + 1 overlay + 11 reduced + 1 cold sweep = 17 units** (M3
moved full→reduced; R10 added). M5 down-ranked below M4. See v3 in plan artifact.

---

## Round 3 — dispositions (v3 → v4)

Full report: `dev/perf-audit-reviews/round-3.md`. Verdict: **minor-edits** —
v3 executable, coverage airtight, winlink-family reduced-tiering verified
correct (the area rounds 1–2 barely read). Directed at the under-examined
winlink protocol family, cold-sweep internals, and frontend.

1. **Round 2's 4 claims (H1/H7/H8/H9) — all CONFIRMED against source.** Two
   refined: H1 worse than stated (`receiver.rs:78` also allocs a fresh `Mapper`
   per subcarrier); H9 nuanced (`analysis.rs:50` is the genuinely uncached
   planner; `fading.rs` re-plans hit rustfft's internal cache).
2. **Factual error corrected (important):** R9's "`MessageList.tsx` is
   non-virtualized" is **FALSE** — it uses `react-virtuoso` (`:18,338`) and the
   sort is `useMemo`'d. Round 2 grepped for the wrong library (`react-window`).
   R9 mailbox half reframed as a light check; the real large-mailbox cost is the
   backend H8 (R10). ACCEPTED.
3. **H7 rank inflated** — it's per-*call* (planner already hoisted, `:83-95`),
   not per-symbol. Down-ranked Major → Secondary. ACCEPTED.
4. **Concrete M3 target named:** `ardop/data.rs:104,145` per-byte `VecDeque<u8>`
   drain + `leftover.extend(payload)` — the real cost in the demoted ARDOP
   slice; vindicates the full→reduced demote. ADDED.
5. **`modem_status.rs:392`** 4 Hz background broadcaster — borderline-warm but
   stays cold; sweep flagged to eyeball its per-tick work. NOTED (no tier change).
6. **Coverage:** verified airtight — every `src-tauri/src/*.rs` (all 18), every
   subdir, every crate, every `src/*/` lands once. No gaps. Structure: 17 units,
   no merge/split. Cosmetic: `hf-channel-sim` is a repo-root sibling (path in
   plan already correct).

Outcome: **17 units unchanged**; corrections are accuracy fixes (one false
claim removed) + rank/target refinements. Two clean rounds in a row (2 & 3 both
minor-edits) — convergence emerging; Rounds 4–5 will confirm or break it.

---

## Round 4 — dispositions (v4 → v5)

Full report: `dev/perf-audit-reviews/round-4.md`. Partition-DESIGN lens (a
different angle from rounds 1–3's hot-path hunting). Verdict: **minor-edits but
with one substantive defect** — so the round broke the "two clean in a row"
streak and **vindicates the ≥5-round mandate** (a pure hot-path lens would have
shipped this bug). All ACCEPTED:

1. **Cross-slice calibration defect (HIGH)** — lzhuf's frequency-establishing
   caller `winlink_backend.rs::build_outbound_proposals:233,250` (loops the whole
   Outbox compressing every message) sits in the cold sweep, so R3's lane can't
   see the call frequency and under-ranks. The plan even mis-attributed the
   driver to `session.rs`. Fix: **W0 winlink call-frequency-map pre-artifact**
   handed to R3/R8/R10 as adjacent context + **run R8 before R3**. (Lighter than
   merging R3+R8, which Round 4 floated as the alternative.)
2. **Demote R7 (listener gate) reduced→cold** — per-connection consent/auth
   state, no hot loop (`arms_record` density is doc comments; `fs::read` once per
   arm). 17→16 units.
3. **New finding H11** — `transfer.rs::read_block:100-102` per-byte `read_exact`
   loop (receive-side mirror of the ARDOP `data.rs` drain), consumed at R8
   frequency. Added to R3.
4. **Verification-mode flag** — added: M1-H4 (audio), M3, R1 run paths, R2 are
   hardware-deferred (no rig/TNC/audio → dynamic lane can't run). Explicitly
   noted **R2's allocation-argument fallback is weak** (no alloc concern), so R2
   findings are largely unfalsifiable without a rig — to be stated honestly in
   the R2 report, not over-claimed.
5. **Keep fine-grained 16, not fewer-larger** — Round 4 steelmanned mega-runs
   ("all Rust DSP", "all winlink", …) and rejected them: lane precision is the
   binding constraint rounds 1–3 paid to get; co-locate frequency-with-impl via
   W0 instead. ACCEPTED.
6. Minor: reword R1 (not "thin" — 2317 LOC orchestration); narrow R2 to
   timing-only. ACCEPTED. Shared abstraction check: `num_complex::Complex` is
   cleanly contained in M1 (no DSP split) — good.

Outcome: 17 → **16 units** (R7 demoted) + W0 pre-artifact. The substantive
calibration fix is the headline. **Round 5** is a genuine final gate, not a
rubber stamp.

---

## Round 5 — dispositions (v5 → v6, FINAL/GO)

Full report: `dev/perf-audit-reviews/round-5.md`. Final-gate verdict: **GO** —
v5 executable, start M1. All v5 fixes verified against source (W0 lzhuf driver
chain `winlink_backend.rs:233→:250→message.rs:161`; R7 listener hot-loop-free;
H11 `transfer.rs:100-104` per-byte; W0→R8→R3 order resolves the gap).

Substantive accuracy catch (nearly missed by all 5 rounds):

1. **`tuxmodem-fec` has ZERO in-tree callers** — dep commented at
   `tuxmodem-phy/Cargo.toml:23`; live path uses hard-decision slicing
   (`decode_symbol_bytes`) bypassing FEC; `coded_modulation.rs` says real FEC
   plugs in once `#4` lands. ⇒ M2's H2 is **intrinsic-cost / latent**
   (reachability ≈ 0 today). M2 still audited, but findings flagged "fires once
   FEC is wired in." **O1 overlay corrected** — its RX chain `…→Decoder::decode`
   does not exist in current source; reframed to the real chain
   `…→compute_llr→decode_symbol_bytes`, with the latent FEC stage annotated.
   ACCEPTED.
2. **DSP/FEC cross-slice frequency check (the new Round-5 angle): clean.** M1's
   per-symbol decode driver `receive_multi` (`wideband_lowdensity.rs:334`) is
   in-slice; the rx bin calls it once per WAV (R1 is not a multi-symbol driver).
   So M1 sees its own frequency — O1 suffices, no DSP analog of W0 needed for
   demod. (The only DSP-side gap was the FEC-latency one above.)
3. **R2 path bug** — `tux-rig-rts/src`/`tux-rig-cm108/src` don't exist as named;
   real paths `tuxmodem/crates/tux-rig-*/src`. FIXED in v6.
4. Holistic: totals 4+1+10+1 = 16 verified; no language-mix; no double-counted
   finding; coverage airtight; H1/H8/M3 line-refs reconfirmed. Cosmetic
   `process.rs` ledger wording left as-is (non-blocking).

## Convergence summary (for operator review)

| Round | Lens | Verdict | Headline outcome |
|-------|------|---------|------------------|
| 1 | sizing + DSP hot paths + cold batching | major rework | raw→production LOC; killed nonexistent frontend render loop; 22→16 |
| 2 | independent hot-path re-derivation + tiering | minor-edits | wizard.rs gap; H1/H7; M3 demote; native_mailbox promote |
| 3 | winlink family + cold internals + frontend | minor-edits | corrected false "MessageList non-virtualized"; H7 rerank |
| 4 | partition DESIGN (cross-slice calibration) | **substantive** | lzhuf frequency-caller in cold sweep → W0 pre-artifact; R7 demote; H11 |
| 5 | final gate + DSP/FEC analog | **GO** | FEC-latent reframing; O1 corrected; R2 paths |

The R4 substantive defect is the proof the ≥5-round bar earned its keep: a
hot-path-only review (rounds 1–3 converged "clean") would have shipped a
mis-calibrated winlink audit. Final plan: **16 units + W0 pre-artifact**,
execution order in `dev/perf-audit-scope-plan.md`.

---

## Method review — `whole-repo-scoping.md` (the generalizable scope-slicing method)

A second substantive decision (operator ask, 2026-06-04): codify the whole-repo
slicing method as a reusable skill artifact. Reviewed by independent Opus agents
from distinct angles (reports in `dev/perf-audit-reviews/method-round-*.md`).

| Round | Angle | Verdict | Folded into v2 |
|-------|-------|---------|----------------|
| 1 | generalizability across ecosystems | needs-edits (Rust-biased in 4 places) | workload-shape classifier (CPU/IO/event); "one primary pack, embedded langs stay with driver" reframe; verbosity-scaled sizing table; deployable-service partition root for monorepos; de-provenanced examples |
| 2 | robustness / edge-cases | needs-edits | S0.5 one-program-or-many; "no hot path is valid"; god-file synthetic-seam OVERLAY; generated-but-hot; coverage-drift reconcile + planning SHA; bounded re-slice loop; SPLIT/KEEP rule; fail-safe demand-driven S4 |
| 3 | followability / proportionality | conditionally-followable, under-operationalized + over-heavy | production-LOC how-to table; dead-code grep + confirm-before-flag; hot/warm/cold checklist; review-depth scaling table (5-round = CEILING not default); lightweight path; ledger schema; "when in doubt" rules |

**Convergence across the three:** dynamic-dispatch "no-caller≠dead" caveat (R1+R3);
SPLIT/KEEP decision rule (R2+R3); one-program-or-many (R1§4 + R2); production-LOC
operationalization (R1§1 + R3). All accepted into **v2**.

**Empirical validation:** the method also reproduces, as a worked example, this
session's own 5-round-reviewed whole-repo partition — i.e. it's distilled from a
real application that was itself hardened five rounds, not theory.

Rounds 4–5 (holistic re-attack + consolidation/contradiction-check on v2) pending
to satisfy the ≥5-pass bar for this high-leverage artifact. Known minor open
item: a one-line data/ML (notebook/DAG-stage) caveat (R1§5) not yet added.

### Method rounds 4–5 (v2 → v3, SHIP)

| Round | Lens | Verdict | Headline |
|-------|------|---------|----------|
| 4 | holistic re-attack on v2 (merge defects + interaction effects) | needs-edits | dead `## Sizing` cross-refs; **COLD SWEEP/OVERLAY had no capacity cap** (mega-run at the cold tier); S4 "Phase-3 demotes" was an unverified downstream claim; `.csproj`≠deployable; no TL;DR spine; shared-substrate frequency dropped |
| 5 | ship-gate / consolidation | **SHIP-WITH-FIXES**, litmus PASS | confirmed the dead-ref + SKILL.md trigger inconsistencies; confirmed the method reproduces this session's own partition |

**v3 applied all must-fixes** (subagent, verified): `## Sizing` section created + all
four cross-refs resolve to it; COLD SWEEP/OVERLAY capped at one run's capacity
(several sweeps on huge repos); S4 fail-safe softened (re-rank if caller
resolvable, else surface to operator — no asserted auto-demotion); shared-lib
fan-in frequency rule; two-level roll-up for service monorepos; `.csproj`≠deployable;
TL;DR 9-line spine; SKILL.md trigger points at S0's router (no competing number);
exclude-table + dead-code-list gaps closed. **Method status: v3 / SHIP.**

**Review accounting (operator's ≥5-round bar):** the method received **5 independent
Opus review passes** (generalizability, robustness, followability, holistic
re-attack, ship-gate) on top of being *distilled from* this session's 5-round-
reviewed real partition. Round 4 (a design-lens pass) again proved its keep — it
caught the cold-sweep capacity hole and the S4 downstream-assumption that the three
lane-bound angle-reviews each missed.
