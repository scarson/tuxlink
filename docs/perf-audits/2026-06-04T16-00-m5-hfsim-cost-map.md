# Execution Cost Map — hf-channel-sim (m5)

> Architectural awareness, NOT a to-do list. Dev/test-only offline sweep tool;
> the metric is sweep wall-time, no real-time deadline. Agent: glade-knoll-shoal.

## Workload shape

A sweep multiplies one signal's end-to-end cost across:
`blocks-per-signal × SNR-points × Monte-Carlo-trials × conditions`.
`run_characterization` (`report.rs:63`) is the per-trial unit. One trial =
channel (`process_block`) → AWGN (`add_noise`) → metrics
(`estimate_subcarrier_snr`) → packaging. Trace below follows one signal of
length `N` samples; fading block `FADING_BLOCK_LEN = 4096` (`channel.rs:20`).

## Likely time-concentration regions

- **Per-refill FFT planner re-planning inside `generate_fading_block`**
  (`fading.rs:49` & `:86`, called from `channel.rs:96`/`:106`) — basis: each
  4096-sample fading refill calls `plan_fft_forward` AND `plan_fft_inverse`.
  rustfft's `FftPlanner` caches plans, and the planner is owned by the channel
  (`channel.rs:39`, persists across refills), so steady-state cost is a cache
  lookup + `Arc` clone, not a fresh plan. Still, two planner lookups + two
  `Arc<dyn Fft>` allocations per refill, repeated `N/4096 × trials × SNR ×
  conditions` times. The FFT `process` itself (two O(M log M) transforms over
  M=4096) is the real arithmetic core here. — confidence: High — map-only.

- **Fading tap generation as a whole** (`generate_fading_block`, `fading.rs:29`)
  — basis: per refill = 4096 complex-Gaussian draws (8192 `Normal::sample`,
  `rng.rs:37`) + forward FFT + a 4096-iter mask loop with a per-bin
  `.exp().sqrt()` transcendental (`fading.rs:65-83`) + inverse FFT + two
  full-buffer normalization passes (`:92`, `:99-104`). Two independent taps
  (tap1+tap2) means this entire cost is paid TWICE per refill window. This is
  the dominant per-sample contributor by structure: every other stage is one
  pass over N; fading is ~5 passes over M plus two FFTs, doubled. — confidence:
  High — map-only (this is the architectural hot region).

- **`Normal::new` rebuilt per call + per-element `Vec` allocation in
  `complex_gaussian_block`** (`rng.rs:37-42`) — basis: constructs a fresh
  `Normal` distribution and collects a new `Vec<(f32,f32)>` on every fading
  refill and every `add_noise`. Allocation churn scales with total samples
  across the sweep. — confidence: Med — overlaps the fading hot-spot (called
  inside it) and the AWGN stage.

- **`process_block` scalar per-sample loop with `is_empty()` checks**
  (`channel.rs:93-129`) — basis: one iteration per input sample doing two
  `VecDeque::is_empty` branch checks, two `pop_front`, a `delay_line`
  rotate, two complex mults. The branch is almost always false (buffer only
  empties every 4096 samples) but is evaluated every sample. `VecDeque`
  front-pops are O(1) but cache-unfriendly vs. a slice. Linear in N × trials.
  — confidence: Med — map-only.

- **`estimate_subcarrier_snr` per-window double FFT + nested bin loop**
  (`analysis.rs:59-86`) — basis: `window_count = N/fft_size` windows, each
  doing TWO `fft.process` (clean + observed) over `fft_size`, plus a
  `fft_size`-wide bin loop with a `log10` per bin (`:79`), and pushing a
  `Vec<f32>` snapshot per window (`:85`). `snapshots` retains every per-window
  per-bin value → O(window_count × fft_size) memory held in the report. Note
  this analyzer allocates its OWN `FftPlanner` per call (`analysis.rs:50`),
  separate from the channel's. Linear-ish in N but with a fat constant (2 FFTs
  + log10/bin) and large retained allocation. — confidence: High — overlaps a
  likely hot-spot (second FFT-heavy stage after fading).

- **`run_characterization` full-signal clones + reduction passes**
  (`report.rs:72-96`) — basis: `channel_out.clone()` (`:77`) copies all N
  samples; three separate O(N) power-sum reductions (`:74`, `:82`, plus the
  analyzer). One extra N-length allocation + redundant passes per trial. —
  confidence: Med — map-only.

## Notes for architecture

- Two FFT-bearing stages dominate: fading synthesis (2 taps × {fwd+inv FFT}
  per 4096-window) and SNR estimation (2 FFTs per window). The sweep multiplier
  hits both. If sweep wall-time ever matters, the fading stage is the lever:
  it is the only stage paying multiple buffer passes + transcendentals +
  doubled work, and its FFTs recur most often (4096-window granularity).
- The Gaussian frequency mask (`fading.rs:59-83`) is identical for every refill
  of a given (sample_rate, doppler, block_len) — it could be precomputed once
  per channel rather than rebuilt per 4096-window. Structurally a per-trial
  invariant recomputed per-window. Map-only observation.
- `estimate_subcarrier_snr` allocates a fresh `FftPlanner` each call
  (`analysis.rs:50`); across a sweep this is one planner construction per trial.
  Minor relative to the FFTs themselves.
- Everything is single-threaded by design (determinism, `rng.rs` docstring).
  The sweep's outer trial/SNR/condition loops are embarrassingly parallel and
  independent per trial — the natural scaling axis is outside this src/ scope
  (bin/ excluded), not inside the per-block kernels.
- All observations are structural. No measurements taken; confidence labels
  reflect reasoning from loop nesting, FFT counts, and allocation sites only.
