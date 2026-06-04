---
run_schema_version: 1
run_id: 2026-06-04T16-00-m5-hfsim
date: 2026-06-04T16:00:00Z
scope: "M5 — hf-channel-sim (offline HF multipath+AWGN channel simulator), entire crate src excl. bin/"
methodology: { skill: performance-audit (within performance-audit-cycle), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents, model=opus)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack:
  - { ecosystem: crates, framework: rustfft, version: "6" }
  - { ecosystem: crates, framework: num-complex, version: "0.4" }
  - { ecosystem: crates, framework: rand_distr/rand_xoshiro, version: "0.4/x" }
  - { ecosystem: crates, framework: serde, version: "1" }
currency_briefs:
  - { framework: rustfft, researched_on: null, status: "version-index has NO rustfft/num-complex entries — findings rest on documented 6.x/0.4 API contracts" }
lanes_run: [algorithmic, memory, data-access, concurrency, idiom-currency, cost-map]
lanes_skipped: { payload-startup: "library/CLI sim — no payload/bundle surface", dynamic: "deferred — sweep harness exists (bin/) but no measurement run made this pass" }
finding_counts: { by_impact: { critical: 0, major: 5, minor: 4 }, by_lane: { algorithmic: 4, memory: 6, data-access: 5, concurrency: 1, idiom-currency: 4, cost-map: 0 }, suspected_bugs: 1 }
regression: { prev_run_id: null, new: 9, persisting: 0, resolved: 0 }
---
# Performance Audit — M5: hf-channel-sim (offline HF channel simulator)

**Date:** 2026-06-04 16:00   **Scope:** `hf-channel-sim/src` (excl. `bin/`)
**Stack:** Rust 2021 · rustfft@6 · num-complex@0.4 · rand_distr/rand_xoshiro · serde
**Currency brief:** version-index carries no rustfft/num-complex entries (flagged); findings rest on rustfft 6.x / num-complex 0.4 API contracts
**Lanes run:** all 6 core (payload-startup + dynamic skipped — see frontmatter)
**Regression vs none (first run):** 9 new

> **REACHABILITY: DEV/TEST-ONLY.** This crate is a Watterson-channel simulator
> used for offline BER/SNR sweep testing — it is **not in the shipped tuxlink
> client runtime path**. The metric is **sweep wall-time** (per-block work ×
> blocks × SNR-points × Monte-Carlo trials × conditions), with no latency
> deadline. All findings calibrated to dev-sweep throughput; none affect shipped
> performance. All six lanes calibrated to this correctly.
>
> **Scope correction (idiom-currency lane):** the dispatch brief mis-stated
> `rng.rs` as a fully custom RNG — it actually uses `rand`/`rand_distr`/
> `rand_xoshiro` with only `split_mix64` hand-rolled (at construction, not
> per-sample). The "custom RNG in the hot loop" angle did not apply. (A reminder
> to verify dispatch-time stack assumptions in the lane reports.)

## Executive summary

Two clusters. **(1) The fading shaper** (`fading.rs` + `channel.rs`) rebuilds
mode-invariant state every 4096-sample refill — the Gaussian Doppler mask (N1),
allocating FFTs (N3), and triple per-block buffering through a per-sample
`VecDeque` (N4) — all hoistable into a `FadingShaper` that owns a planner +
reused scratch + the precomputed mask + a reusable block buffer. **(2) The sweep
is single-threaded but embarrassingly parallel** (N5) — each `run_characterization`
is fully independent with explicit per-run seeds, so a rayon `par_iter` is the
single biggest dev-throughput win. `analysis.rs` separately rebuilds an
`FftPlanner` per call (N2). No criticals (dev-only), 5 majors, 4 minors.

## Major findings

### N1. Gaussian Doppler mask recomputed every fading block (invariant across the run)
**Lanes:** algorithmic (MAJOR), memory, idiom-currency, cost-map (High)   **Location:** `fading.rs:59-83`
**Fingerprint:** `algorithmic:fading.rs:generate_fading_block:per-block-mask-recompute`
**Problem:** per-bin `exp().sqrt()` over `block_len`(4096) rebuilt on every refill, ×2 taps (`channel.rs:96,106`), ×every channel reconstruction in the sweep. Mask depends only on `(doppler, sample_rate, block_len)` — all fixed per channel. **Confidence:** Strong-static   **Effort:** Contained (+low) — precompute in `WattersonChannel::new`. (Also `exp().sqrt()` = `exp(x/2)`.) **Verification:** criterion sweep-point bench; correctness guard = identical fading output (the mask values are unchanged, just hoisted).

### N2. Fresh `FftPlanner::new()` per call in subcarrier-SNR analysis
**Lanes:** algorithmic (MAJOR), memory, idiom-currency (MAJOR), cost-map   **Location:** `analysis.rs:50`
**Fingerprint:** `idiom-currency:analysis.rs:estimate_subcarrier_snr:per-call-fftplanner`
**Problem:** rebuilds planner + twiddle tables for an invariant `fft_size` each sweep-grid cell; `channel.rs:39,68` already shows the team's own correct planner-as-field pattern. **Confidence:** Strong-static   **Effort:** Contained — thread a shared planner. **Verification:** bench; bit-identical SNR output.

### N3. Allocating `.process()` everywhere; never `process_with_scratch`
**Lanes:** idiom-currency (MAJOR), memory, cost-map   **Location:** `fading.rs:50,87`; `analysis.rs:66,67`
**Fingerprint:** `idiom-currency:fading.rs:process:no-scratch-reuse`
**Problem:** rustfft `process` heap-allocates scratch each call; fixed FFT sizes ⇒ a reused scratch buffer removes all of it (2×/refill in fading, 2×/window in analysis). **Confidence:** Strong-static   **Effort:** Contained. **Verification:** dhat alloc-count; bit-identical output.

### N4. Per-sample `VecDeque` drain + triple full-block buffering in fading delivery
**Lanes:** memory (MAJOR), data-access (MAJOR), algorithmic (MINOR)   **Location:** `channel.rs:93-129`, `fading.rs:42-46,107`
**Fingerprint:** `memory:fading.rs:generate_fading_block:triple-block-alloc`
**Problem:** `generate_fading_block` allocates a `(f32,f32)` Vec (`rng.rs:37-42`), re-maps into a `Vec<Complex>` (layout-identical re-copy), then a 3rd copy when `extend`ed into the channel `VecDeque` (`channel.rs:103,113`); the consumer drains one element per sample with a per-sample `is_empty()`/refill branch. **Confidence:** Strong-static   **Effort:** Contained — caller-provided reusable buffer, sample directly into `Complex`, `Vec`+cursor instead of `VecDeque`. **Verification:** alloc-count + bench; identical output.

### N5. Sweep over independent runs is single-threaded — rayon `par_iter` is the win
**Lanes:** concurrency (MAJOR)   **Location:** driver loop over `run_characterization` (`report.rs:63`)
**Fingerprint:** `concurrency:report.rs:run_characterization:unparallelized-sweep`
**Problem:** each run is fully self-contained (explicit `channel_seed`/`noise_seed` in `CharacterizationInputs`; fresh channel+generator per call; no `static`/global RNG/shared planner — verified). N_snr × N_trials calls are embarrassingly parallel → near-linear core-count throughput on the FFT-bound hot path. **The single biggest dev-sweep wall-time win.** **Confidence:** Strong-static (independence) / Heuristic (speedup, unbenchmarked)   **Effort:** Localized-but-Cross-cutting (one dep `rayon` + one loop). **Verification:** criterion release-build serial-vs-parallel; **determinism guard (load-bearing):** derive each run's seeds deterministically from `(snr_index, trial_index)` BEFORE the `par_iter` (never a shared counter in the closure), order-preserving `collect`, assert serial == parallel.

## Minor findings
- **N6** `complex_gaussian_block` returns `Vec<(f32,f32)>` re-mapped into `Vec<Complex>` — redundant block alloc; return `impl Iterator` / fill in place. `rng.rs:37-42` → `fading.rs:43-46`. (subsumed by N4's reusable-buffer fix). Fingerprint `memory:rng.rs:complex_gaussian_block:double-alloc`.
- **N7** `snapshots: Vec<Vec<f32>>` (window×bin matrix) is the peak-memory driver on long signals and is JSON-serialized in full though downstream may use only `mean_snr_db` — flatten or `#[serde(skip)]`. `analysis.rs:85`, report. Fingerprint `data-access:analysis.rs:snapshots:matrix-overfetch`.
- **N8** fixed-length `delay_line` shift register as `VecDeque` → flat `Vec`/`Box<[_]>` + wrapping cursor. `channel.rs:44,119-125`. Fingerprint `data-access:channel.rs:delay_line:vecdeque-shift`.
- **N9** `channel_out.clone()` (`report.rs:77`) — semantically required (analyzer needs pre/post-noise streams); noted, no change.

## Cross-cutting theme
A **`FadingShaper`** owning {cached planner + reused FFT scratch + precomputed Doppler mask + reusable block buffer} collapses N1+N2(pattern)+N3+N4+N6 into one refactor. **N5 (parallelize the sweep)** is independent and likely the largest single wall-time win for a dev sweep tool — do both.

## Measurability
Crate builds clean (verified). A `bin/` sweep harness exists, so dynamic measurement is feasible (criterion on a fixed sweep) but was not run this pass — findings are Strong-static. Dynamic measurement is the natural next step here precisely because it's a batch tool with an existing entry point and no transmit/hardware path.

## Execution Cost Map (architectural awareness)
Per the cost-map lane: **fading tap generation** (`generate_fading_block`, `fading.rs:29`) is the hot region — per refill: 8192 `Normal` draws + forward FFT + 4096-bin `exp().sqrt()` mask + inverse FFT + 2 normalization passes, ×2 taps. **SNR estimation** (`estimate_subcarrier_snr`, `analysis.rs:59-86`) is the second FFT-heavy stage (2 FFTs/window + `log10` bin loop + retained snapshot matrix). The planner persists on the channel (cached lookup, 2 `Arc` clones/refill — the two O(M log M) FFTs are the real cost). Single-threaded by design; the parallel axis is the outer sweep loop.

## Suspected Bugs (for follow-up — NOT addressed here)
### SB-M5-1. f32/f64 precision asymmetry in SNR aggregation
**Location:** `analysis.rs:78-83` (snapshot SNR in f32) vs `:92-96` (mean SNR in f64)   **What looks wrong:** per-window snapshots computed in f32 but the mean in f64 — a precision asymmetry that could bias reported SNR.   **Why suspected:** noticed during the audit; a correctness reviewer should confirm intent.
