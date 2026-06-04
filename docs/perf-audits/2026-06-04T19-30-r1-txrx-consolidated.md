---
run_schema_version: 1
run_id: 2026-06-04T19-30-r1-txrx
date: 2026-06-04T19:30:00Z
scope: "R1 — tuxmodem-tx + tuxmodem-rx (thin CLI driver crates around the M1 PHY)"
methodology: { skill: performance-audit (REDUCED, combined algorithmic+memory lane), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust, version: "hound WAV + tuxmodem-phy" }]
lanes_run: [algorithmic+memory (combined — thin slice)]
finding_counts: { by_impact: { critical: 0, major: 0, minor: 3 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 3, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R1: tuxmodem-tx + tuxmodem-rx

**Thin CLI orchestration — all-minor, offline-CLI calibrated.** The dense DSP is
M1 (already audited); these just call it. `compute_ber` is a clean single O(n)
popcount pass; arg parsing linear.

## Minor findings (all LOW at one-shot CLI load)
- **R1-1** (memory) `tuxmodem-rx/src/lib.rs:174-206,238` `--decode-wav` Raw path reads the WHOLE WAV into a `Vec<f32>` then uses only the first ~2560 samples — streaming would bound it (Raw-only; Sync/MultiSync need the full buffer for preamble scan). Fingerprint `memory:tuxmodem-rx/lib.rs:decode-wav:full-read`.
- **R1-2** (memory) `record_to_wav` (`rx:301-313`) holds the whole N-second capture in one `Vec<f32>` (alloc is upstream in phy's `record_blocking_with_abort`). Fingerprint `memory:tuxmodem-rx/lib.rs:record:whole-capture`.
- **R1-3** (memory/algorithmic) `WidebandLowDensityFloor::new()` constructed per-call (`rx:189,194,200,222,228`, `tx:206`) — re-pays FFT-plan/Zadoff-Chu setup each entry; harmless at one-shot CLI, **would bite a future batch/BER-sweep driver** (cross-ref M5's sweep concern). Hoistable. Fingerprint `memory:tuxmodem-rx/lib.rs:floor-new-per-call`.

## Cleared
`compute_ber` single O(n) popcount; `run_transmission` lead-in bounded ≤5 iters; encode/decode dispatch one move no clone. No suspected bugs (Ber empty-input NaN is intentional per tests).
