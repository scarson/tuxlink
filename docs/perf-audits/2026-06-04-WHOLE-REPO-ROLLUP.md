# Whole-Repo Performance Audit — Cross-Slice Roll-Up

**Date:** 2026-06-04 · **Agent:** glade-knoll-shoal · **Scope:** all of tuxlink,
16 audit units (see `dev/perf-audit-progress.md` ledger + the per-unit
`docs/perf-audits/*-consolidated.md`). This is the **two-level roll-up** the
whole-repo-scoping method prescribes: per-unit synthesis already happened; this
rolls the units up into **systemic themes** + a **prioritized cross-cutting fix
list**, and records the regression baseline for future runs.

## Coverage

| Tier | Units | Result |
|------|-------|--------|
| FULL | M1 (phy, full cycle), M2 (fec), M4 (search), M5 (hf-sim) | 13/9/10/9 findings |
| OVERLAY / PRE | O1 (live pipeline), W0 (winlink freq map) | analysis |
| REDUCED | M3, R3, R8, R10, R9, R5, R6, R4, R1, R2 | warm→cold range |
| SWEEP | cold Rust + cold/warm TS (3 passes) | confirmed cold |

Every code directory audited exactly once (ledger coverage ledger). Lanes run
**blind** throughout (load context only, never the prior hot-path map).

## Systemic themes (the cross-unit story)

### T1 — Per-symbol/per-call reconstruction of mode-invariant DSP state  *(highest perf value)*
The marquee pattern. Hot loops rebuild state that depends only on frame-fixed
params: **M1** rebuilds the FFT planner, per-subcarrier `Mapper`+constellation
`alphabet`, pilot `HashSet`, and equalizer **every OFDM symbol** (H0/H1/H3/H5);
**M5** rebuilds the Doppler mask + planner + FFT scratch **every fading block**
(N1–N3); **R1-3** constructs `WidebandLowDensityFloor` per CLI call. **Fix
pattern:** hoist a frame/run-scoped context — M1's planned `OfdmContext` spine,
M5's `FadingShaper`. **M1 is the live-client decode path**, so this is the #1
perf investment.

### T2 — Transport writes are unbuffered across ALL FOUR transports  *(systemic, cheap)*
**M3-3** (ardop tiny B2F tokens), **R8-1** (session fragmented proposal-batch
writes), **R6-1** (telnet write half), **R4-1** (vara write half) — every
winlink/modem transport writes tiny protocol tokens without coalescing; on a
high-latency HF link, on-air bytes + round-trips are the scarce resource. **Fix:
one shared `BufWriter` + flush-at-turn-boundary convention** at the
`run_exchange` writer boundary (R8-2 warns the flush is mandatory — the protocol
strictly alternates).

### T3 — Unsized buffer growth in the winlink byte plumbing  *(cheap)*
**R3** (`read_block`/`frame_block`/lzhuf output grow unsized though sizes are
known/derivable). Pre-size from `compressed_size`/computed frame length. The
lzhuf *algorithm* itself is sound (Okumura BST — confirmed).

### T4 — Mailbox list read-amplification + serve-from-the-right-source  *(user-facing)*
**R10-1 (CRITICAL):** `native_mailbox::list` reads + parses every full message
body (attachments included) to render header-only rows; cost scales with folder
*bytes*. **M4** already maintains a SQLite index with exactly those header
columns. **R9** renders the result. **Triangulated fix:** serve the list from the
index (or a header-prefix read) — relieves all three. (R10-2: moves should
`fs::rename`, not copy — also closes a data-loss window.)

### T5 — Wide 4 Hz subscription re-render cascade (frontend, localized)
**R9:** the wide `modem:status` (4 Hz) subscription re-renders the whole
`ArdopRadioPanel`, which re-projects an **unbounded** session log every tick. The
cold sweep **confirmed this is localized** (shell uses the gated
`useModemIsActive`; only `ArdopRadioPanel` takes the wide stream) — fix is a
`memo` boundary + memoize the log projection + cap the log buffer.

### T6 — SQLite write durability (M4)
No `WAL`/`synchronous=NORMAL` PRAGMA, per-row commits on bulk re-index, no
`prepare_cached`, and the rebuild holds the connection `Mutex` across the whole
walk (freezes search). Standard fixes; meaningful for index-write throughput.

### T7 — Latent / dev-only code, correctly down-calibrated
**M2** (FEC — no live caller; the O(d_c²) check-node + CSR rewrite is real but
"fires once FEC is wired in"; do the rewrite BEFORE integration per O1) and
**M5** (dev-only sim — calibrated to sweep wall-time; rayon-parallelize the
sweep). Audited but never inflated to shipped-critical.

## Prioritized cross-cutting fix list (operator-reviewable)

1. **M1 `OfdmContext` spine** — hoist all per-symbol mode-invariant state (T1). Live decode path; biggest win; already a written 12-task plan (`docs/plans/2026-06-04-m1-...`).
2. **R10 mailbox list → serve from index / header-prefix read** (T4, CRITICAL) + `fs::rename` moves. User-facing + closes a data-loss window.
3. **M4 SQLite: WAL + transaction-batch the re-index + `prepare_cached`** (T6) + don't hold the lock across rebuild.
4. **Transport `BufWriter`+turn-flush convention** (T2) — one change pattern, four call sites; cheap, systemic.
5. **R9 frontend: memo boundary on `ArdopRadioPanel` + memoize/cap the session log** (T5).
6. **Pre-size the winlink transfer buffers** (T3) — cheap cleanup alongside #4.
7. **Before FEC integration: the M2 SPA rewrite** (T7/O1) — lossless now, regression-risky later.
8. **M5 rayon-parallelize the sweep** (T7) — dev-throughput; do when sweeps get slow.

## The audited audit (skill-quality observations)
- **Blind lanes rediscovered the targeted-review hot-path map** (M1) and **added** findings — the skill *discovers*, not restates.
- **Anti-padding held under test:** the deliberately low-value tail (R5/R6/R4/R1/R2 + cold sweep) returned mostly "No significant findings" / honest MINORs — the skill did NOT manufacture nits when there was little to find. Diminishing *finding count* confirmed; **quality did not degrade.**
- **Calibration is real:** latent (M2), dev-only (M5), hardware-deferred (R2), and low-rate (M3/R5/R6) were all correctly down-ranked; lanes corrected dispatcher load assumptions from source (R9 1Hz→4Hz; R5 test-only `Mutex`).
- **The cold sweep validated boundaries**, not just absence — it confirmed R9/R10's findings are localized, not systemic.

## Regression baseline
`docs/perf-audits/runs.jsonl` holds one ledger line per unit (fingerprints +
counts) — the substrate for future run-over-run diffs. Re-running any slice will
diff against its `run_id`.
