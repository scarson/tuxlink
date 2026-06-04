# Performance Audit — rustfft / num-complex idiom currency (M5)

**Agent:** glade-knoll-shoal
**Date:** 2026-06-04T16:00Z
**Scope:** `/home/user/tuxlink/hf-channel-sim/src` (channel.rs, fading.rs, analysis.rs, noise.rs, rng.rs); `bin/` excluded.
**Dimension:** rustfft 6.x / num-complex 0.4 fast-path idioms only.
**Load model:** dev/test-only batch sweeps (per-block FFT convolution × blocks × SNR × trials). Not shipped runtime.
**Index basis:** `version-indexes/rust.md` (covered_through Rust 1.96). The Rust index is build-config-centric and carries no rustfft/num-complex API entries; findings below rest on rustfft 6.x / num-complex 0.4 documented API contracts, marked accordingly.

**Stack note / scope correction:** the task brief described a "CUSTOM `rng.rs` (not the `rand` crate)." The actual source uses `rand`/`rand_distr`/`rand_xoshiro` (`rng.rs:9-11`, `Normal::sample` at `rng.rs:38-41`). `split_mix64` is the only hand-rolled primitive, and it is used at construction (seed derivation), not per-sample. No custom per-sample RNG exists; the RNG-in-hot-loop angle is re-scoped to the `rand_distr::Normal` sampler, addressed in MINOR-2.

---

### [MAJOR] `estimate_subcarrier_snr` builds a fresh `FftPlanner` on every call

**Location:** `analysis.rs:50-51`
```rust
let mut planner = FftPlanner::<f32>::new();
let fft: Arc<dyn rustfft::Fft<f32>> = planner.plan_fft_forward(fft_size);
```

**Problem:** A new `FftPlanner` is constructed per invocation. rustfft 6.x's planner exists specifically to *cache* computed plans (twiddle factors, algorithm selection, recursive sub-plans) and amortize that cost across `plan_fft_*` calls. Each fresh `FftPlanner::new()` followed by `plan_fft_forward(n)` redoes the algorithm-selection + twiddle-factor computation from scratch — the exact work the planner is designed to memoize. In a dev sweep that calls `estimate_subcarrier_snr` once per (condition × SNR × trial), the planner-build + plan cost is paid on every cell of the sweep grid. `channel.rs:39,68` shows the correct pattern: the planner is a struct field constructed once. `analysis.rs` does not carry that pattern over.

**Impact (dev-sweep calibrated):** Per call: one planner construction + one plan build that should be a cache hit but is always a cache miss. Multiplied across the sweep grid (conditions × SNR points × trials), this is a fixed per-cell overhead that grows linearly with grid size. The internal `fft.process()` work dominates per-window, but the planner rebuild is pure avoidable setup repeated O(grid) times. For a many-cell sweep this is meaningful wall-independent CPU.

**Confidence:** HIGH. Direct anti-idiom; rustfft's planner-reuse contract is explicit and the in-repo counter-example (channel.rs) proves the team knows the right pattern.

**Effort:** LOW–MEDIUM. Either (a) accept a `&mut FftPlanner<f32>` / `Arc<dyn Fft<f32>>` parameter so the sweep driver constructs once and reuses (mirrors `generate_fading_block`'s signature), or (b) cache a planner in a caller-owned struct. (a) is the smaller diff and matches the existing fading-block convention.

**Verification:** grep call sites of `estimate_subcarrier_snr` in the sweep driver (`bin/`, out of audit scope but the reachability target); confirm it is invoked per grid cell. Then benchmark planner-build cost via `FftPlanner::new()` + `plan_fft_forward(1024)` in isolation vs reused-planner cache hit.

**Basis:** rustfft 6.x `FftPlanner` API contract (plans are cached on the planner instance; reuse is the documented fast path). No conflicting index entry.

---

### [MAJOR] All `fft.process(&mut buf)` call sites use the allocating path, never `process_with_scratch`

**Location:** `fading.rs:50` (forward), `fading.rs:87` (inverse); `analysis.rs:66-67` (forward, two per window).

```rust
fft.process(&mut buf);            // fading.rs:50, :87
fft.process(&mut s_buf);          // analysis.rs:66
fft.process(&mut y_buf);          // analysis.rs:67
```

**Problem:** rustfft 6.x's `Fft::process(&self, buffer)` allocates a scratch buffer internally on **every call** (it calls `get_inplace_scratch_len()` and allocates that much). The idiomatic high-throughput path is `process_with_scratch(&mut buffer, &mut scratch)` (or `process_outofplace_with_scratch`), where the caller allocates the scratch once and reuses it across all transforms of the same size. Every call site here uses the convenience `.process()`, so:
- `analysis.rs:66-67` allocates fresh scratch **twice per window**, `window_count` times per call.
- `fading.rs:50,87` allocates fresh scratch twice per fading block; `channel.rs:95-114` calls `generate_fading_block` once per `FADING_BLOCK_LEN`-sample refill, i.e. repeatedly across a long sweep.

Because the FFT size is fixed per context (`fft_size` in analysis, `FADING_BLOCK_LEN=4096` in fading), the scratch length is invariant and a single reusable buffer would eliminate all of these allocations.

**Impact (dev-sweep calibrated):** One scratch heap allocation per `process` call. analysis: `2 × window_count × grid_cells`. fading: `2 × (total_samples / 4096) × grid_cells`. These are allocations on the FFT hot path of a batch sweep — the per-allocation cost is small but the count scales with both block-count and grid size. Eliminating them via a reused scratch buffer is the canonical rustfft throughput idiom.

**Confidence:** HIGH for the API contract (process allocates; process_with_scratch does not). MEDIUM that the allocator overhead is a top-tier cost vs the transform arithmetic itself — for large N the butterfly work dominates, but the allocations are pure avoidable overhead and idiomatically should not be there.

**Effort:** MEDIUM. Allocate `scratch = vec![Complex::default(); fft.get_inplace_scratch_len()]` once per planned FFT, thread it to each `process_with_scratch` call. In `fading.rs` the forward and inverse plans differ, so two scratch buffers (or one sized to the max) are needed. In `analysis.rs` one scratch suffices (single plan reused for both `s_buf` and `y_buf`).

**Verification:** Check `fft.get_inplace_scratch_len()` for the sizes in use; confirm it is non-zero (it is, for non-trivial composite/prime sizes). Bench `process` vs `process_with_scratch` for N=1024 and N=4096 in a tight loop.

**Basis:** rustfft 6.x `Fft` trait: `process` allocates scratch internally each call; `process_with_scratch` is the zero-per-call-allocation path. No conflicting index entry.

---

### [MINOR] Gaussian Doppler mask recomputed with per-bin `.exp().sqrt()` every block

**Location:** `fading.rs:64-83` (esp. `:73`)
```rust
let mag = if two_sigma_sq > 0.0 {
    (-(f * f) / two_sigma_sq).exp().sqrt() as f32
} ...
buf[i] *= mag;
```

**Problem:** The frequency mask is a pure function of `(block_len, sample_rate_hz, doppler_spread_hz)` — none of which change across calls within a single channel realization (`channel.rs` passes a fixed `FADING_BLOCK_LEN`, `sample_rate_hz`, and `params.doppler_spread_hz` on every refill). Yet `generate_fading_block` recomputes the entire `block_len`-length mask, including a transcendental `.exp().sqrt()` per bin, on every invocation. Over a sweep the same 4096-element mask is rebuilt for every refill block of every trial. This is the "transcendental in a loop where a precomputed table fits" idiom the dimension brief calls out (analogous to fading-tap rotation): the mask is the precomputable table.

Secondary num-complex micro-note: `.exp().sqrt()` computes `sqrt(exp(x))`; this equals `exp(x/2)` — a single transcendental instead of two — but that is a cold micro-opt and NOT the finding. The finding is hoisting the whole mask out of the per-block path.

**Impact (dev-sweep calibrated):** `block_len` transcendental evaluations (4096) per fading-block refill, repeated for every refill across the sweep, all producing the identical mask. Precompute the `Vec<f32>` mask once per channel (or per distinct `(len, rate, doppler)` tuple) and apply by elementwise multiply. The multiply itself stays; only the `exp/sqrt` table-build is hoisted.

**Confidence:** MEDIUM. Clearly redundant recomputation; magnitude depends on refill frequency relative to FFT cost. The FFT (steps 2 & 4) likely dominates a single block, so this is a secondary win, hence MINOR.

**Effort:** MEDIUM. Mask must live where it can be cached (e.g. a field on `WattersonChannel` or a small per-channel `FadingShaper` struct holding planner + mask + scratch). This dovetails with the MAJOR scratch/planner findings — one refactor (a `FadingShaper` owning planner + scratch + mask) addresses all three for the fading path.

**Verification:** Confirm `doppler_spread_hz`, `sample_rate_hz`, `block_len` are constant across `generate_fading_block` calls for a given channel (they are — `channel.rs:96-113`). Bench mask-build (4096 × exp+sqrt) against one in-place 4096 FFT.

**Basis:** num-complex 0.4 / general numeric idiom (precompute invariant transcendental tables out of the hot loop). No conflicting index entry.

---

### [MINOR] Per-sample `Normal::sample` in `complex_gaussian_block` vs bulk/SIMD-friendlier draw

**Location:** `rng.rs:37-42`
```rust
(0..n).map(|_| (normal.sample(rng), normal.sample(rng))).collect()
```

**Problem:** Each complex sample invokes `rand_distr::Normal::sample` twice (re, im). `Normal` in `rand_distr` 0.4 uses the ziggurat method per call; for large bulk draws (the AWGN path `noise.rs:60` requests `signal.len()` samples; the fading path requests `block_len`), this is the standard idiom and is not itself wrong. The idiom note is narrow: this is *not* a custom RNG (contrary to the brief), so the "custom RNG slow per-sample" angle does not apply. The only currency observation is that `rand_distr` offers no batched/SIMD path in 0.4, so there is no missed library fast-path here — flagged LOW-confidence purely to record that the sampler was examined and found idiomatic.

**Impact (dev-sweep calibrated):** None actionable within rustfft/num-complex idiom currency. The sampler is the conventional `rand_distr` path; replacing it is out of dimension scope (and replacing `rand` with a hand-rolled generator would be scope-creep per the brief).

**Confidence:** LOW. Recorded for completeness; not an idiom defect.

**Effort:** N/A (no change recommended on currency grounds).

**Verification:** n/a.

**Basis:** `rand_distr` 0.4 `Normal` API (no batched draw in this version). No conflicting index entry.

---

## Suspected Bugs

- **`analysis.rs:78-83` (`snr_db` typing):** `snr_db` is assigned `f32::INFINITY` in the `else` branch but `10.0 * (sig_pow / noise_pow).log10()` in the `if` branch, where `sig_pow`/`noise_pow` are `f32`. The literal `10.0` infers `f32`, so the result is `f32` — consistent. No type bug; noting because the parallel block at `:92-96` casts an `f64` computation `(10.0 * (s / n).log10()) as f32`, an asymmetry in precision between snapshot and mean SNR. Not a perf finding; record for the correctness lane to confirm intended.

(No correctness bugs were chased; the above is a single observation flagged for the appropriate lane.)
