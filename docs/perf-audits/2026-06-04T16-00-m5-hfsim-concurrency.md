# Performance Audit — Concurrency & Parallelization (hf-channel-sim)

Agent: glade-knoll-shoal
Date: 2026-06-04T16:00
Scope: `hf-channel-sim/src/{channel,fading,analysis,noise,lib,params,report,rng}.rs` (bin/ excluded)
Dimension: concurrency & parallelization ONLY. Dev/test-only tool; offline BER/SNR sweep workload; no real-time deadline. Stack currently single-threaded (no rayon, no threads).

---

### [MAJOR] Sweep over independent (SNR point, trial) runs is unparallelized; rayon `par_iter` is the obvious win

**Location:** `report.rs:63` (`run_characterization`) — the per-run unit. The sweep loop itself is in `bin/` (out of scope) but `run_characterization` is the leaf the sweep calls. `lib.rs:34` re-exports it as the public API surface a sweep driver iterates over.

**Problem / opportunity:** Each `run_characterization` call is fully self-contained and independent of every other:
- All randomness is seeded from explicit `u64` fields in `CharacterizationInputs` (`channel_seed`, `noise_seed`) — `report.rs:26-27`.
- A fresh `WattersonChannel` (`report.rs:67`) and fresh `AwgnGenerator` (`report.rs:78`) are constructed per call; both own their RNG (`channel.rs:37-38`, `noise.rs:19`). No `static`, no global RNG, no `Arc<Mutex<…>>`, no shared mutable buffer exists anywhere in scope.
- `WattersonChannel` even owns its own `FftPlanner` (`channel.rs:39`), and `analysis.rs:50` / `fading.rs` build planners locally — so there is no shared planner to contend on.

This means a sweep of `N_snr × N_trials` `run_characterization` calls is embarrassingly parallel. Converting the driver's `for`/`map` over the input set to `inputs.into_par_iter().map(|inp| run_characterization(&clean, inp)).collect()` (rayon) parallelizes across all available cores with no algorithmic change. For a dev sweep — long-running, many SNR points × many Monte-Carlo trials, each doing FFT-heavy fading-block generation (`channel.rs:96`, refilled every 4096 samples) plus two FFTs per analysis window (`analysis.rs:66-67`) — this is a near-linear core-count throughput multiplier on the dominant cost.

**Impact:** Dev-sweep throughput scales ~linearly with core count (e.g. ~4–8× on a typical multicore dev box) for the primary workload. The single largest concurrency lever available; everything else in this codebase is already CPU-bound serial arithmetic that rayon-at-the-run-boundary subsumes.

**Confidence:** HIGH that the runs are independent and parallelizable (verified: no shared mutable state, per-call seed determinism). MEDIUM-HIGH that it pays (dev sweeps are long-running and FFT-bound; not benchmarked here — see Verification).

**Effort:** LOW. Add `rayon` dep; change one loop in the sweep driver to `par_iter`/`into_par_iter`. Cross-cutting (new dependency) — weigh: rayon is the idiomatic, well-maintained choice for exactly this data-parallel shape; for a dev-only tool the dep cost is negligible and confined to the binary (could be `cfg`/feature-gated if library purity matters).

**Determinism / correctness guard (load-bearing — a sim tool that loses reproducibility is a regression):**
- Reproducibility is per-run, NOT per-iteration-order. Determinism here comes from each run carrying its own `channel_seed` + `noise_seed` (`report.rs:26-27`), so results are independent of the order runs execute. Parallel == serial results PROVIDED each run's seeds are assigned deterministically from the sweep index, not from iteration/spawn order or a shared mutable counter.
- Therefore: derive each run's seeds deterministically from the (snr_index, trial_index) tuple — e.g. via `split_mix64` (`rng.rs:19`) over a base seed + index — and assign them BEFORE the `par_iter`. Do not increment a shared seed counter inside the parallel closure (that reintroduces order-dependence and a data race).
- Collect into an index-keyed structure or `Vec` via rayon's order-preserving `collect` so output ordering is stable regardless of completion order.
- Verification of equivalence: assert `serial_results == parallel_results` for a fixed small sweep. The existing determinism tests (`report.rs:129` `same_inputs_same_report_modulo_version`, `channel.rs:146`, `noise.rs:85`, `rng.rs:49`) confirm per-run determinism is already solid — the guard is purely about seed-assignment discipline at the sweep layer.

**Verification:** `criterion` benchmark on a `--release` build comparing serial vs `par_iter` sweep at a realistic `(N_snr, N_trials, signal_length)`; confirm near-linear scaling and bit-identical `Vec<CharacterizationReport>` between the two. Banned: wall-clock ad-hoc timing.

---

## Defend (shared mutable state)

n/a. No shared mutable state exists in scope. Every RNG is per-instance and seed-derived (`channel.rs:51-67`, `noise.rs:25-30`); no `static mut`, no global RNG, no `Mutex`/`RwLock`/`Arc<…mut…>`, no thread spawning. There is nothing to defend against because nothing is shared today; the only concurrency concern is the seed-assignment discipline noted in the guard above, which applies WHEN parallelism is introduced.

## Sub-block / within-run parallelism — NOT a finding

Within a single `run_characterization`, candidates (parallel FFT windows in `analysis.rs:59`, parallel fading-block generation, par over the sample loop in `channel.rs:93`) are NOT recommended: the channel's `process_block` is inherently sequential (delay-line + fading-buffer state carried sample-to-sample, `channel.rs:115-125`), and per-window analysis FFTs are cheap relative to coordination overhead. Run-level parallelism subsumes these and avoids contention on the per-channel `FftPlanner`. Speculative finer-grained parallelism that doesn't pay is excluded per scope.

## Suspected Bugs

None.
