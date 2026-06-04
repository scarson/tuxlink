# HF-Channel-Sim — Algorithmic Complexity & Data Structures Audit

Agent: glade-knoll-shoal
Date: 2026-06-04T16:00
Dimension: algorithmic complexity & data structures (ONE lane)
Scope: `hf-channel-sim/src/{analysis,channel,fading,noise,rng,params,report,lib}.rs` (bin/ excluded)
Reachability prior: DEV/TEST-ONLY tool, NOT in shipped client runtime. Impact calibrated to **dev sweep throughput** = per-block inner work × (blocks × SNR-points × Monte-Carlo trials). No latency deadline; wall-time of a sweep is the metric.

The dominant sweep multiplier matters: a BER/SNR sweep reconstructs a `WattersonChannel` and runs `run_characterization` once per (condition × SNR-point × trial). Anything invariant across that outer loop but recomputed inside it multiplies by the full sweep cardinality.

---

### [MAJOR] Gaussian Doppler frequency mask recomputed per fading block (transcendental per bin, invariant within a sweep point)

Location: `fading.rs:59-83` (inside `generate_fading_block`, called from `channel.rs:96` / `channel.rs:106`)

Problem: The shaping mask is rebuilt every time a fading buffer drains (every `FADING_BLOCK_LEN`=4096 samples), computing per bin `(-(f*f)/two_sigma_sq).exp().sqrt()` — a `block_len`-long sweep of `exp` + `sqrt`. The mask depends only on `(doppler_spread_hz, sample_rate_hz, block_len)`, all of which are **fixed for the entire lifetime of a channel and identical across every SNR-point and trial of a given condition**. It is recomputed:
- once per 4096-sample refill (signal_len/4096 times per channel),
- × 2 (tap1 + tap2 each call `generate_fading_block` independently — `channel.rs:96` and `channel.rs:106`),
- × every channel reconstruction in the sweep (one per SNR-point × trial).

`exp`/`sqrt` are the expensive ops in this file; over a sweep of K points × T trials × (signal_len/4096) refills × 2 taps × 4096 bins, the transcendental count is K·T·signal_len·2 — entirely redundant beyond the first computation per condition.

Impact: dev-sweep throughput. Per-occurrence is one `exp`+`sqrt` per bin; frequency is the full sweep multiplier. The mask is the same f32 vector every time. Hoisting it to a precomputed `Vec<f32>` (computed once per condition, or memoized keyed on `(doppler, sample_rate, block_len)`) removes ~all of it. The FFT itself (`O(N log N)` per block, unavoidable and correctly FFT-based) then dominates, as it should.

Confidence: Strong-static (the inputs are provably invariant across the inner loop; the recomputation is in the hot per-block path).

Effort: Contained (low). Options: (a) precompute the mask once in `WattersonChannel::new`, store `Vec<f32>`, pass `&[f32]` into `generate_fading_block`; or (b) a module-level memo keyed on the three params. (a) is cleaner given the struct already owns per-channel state.

Verification plan: criterion bench of `generate_fading_block` repeated N times vs a variant taking a precomputed mask; expect the delta to equal the per-bin `exp`/`sqrt` cost. Correctness guard: assert bitwise-equal output between old and new for a fixed seed (existing `same_seed_same_block` test already pins this) — the mask values are identical, so output must be byte-identical.

---

### [MAJOR] `FftPlanner` reconstructed per `estimate_subcarrier_snr` call (twiddle-factor rebuild per sweep point)

Location: `analysis.rs:50` (`let mut planner = FftPlanner::<f32>::new();`), reached from `report.rs:91` once per `run_characterization`

Problem: `estimate_subcarrier_snr` allocates a **fresh** `FftPlanner` and calls `plan_fft_forward(fft_size)` on it every invocation. rustfft caches plans *within a planner instance*, but a brand-new planner each call rebuilds the algorithm selection + twiddle-factor tables for `fft_size` from scratch. Since `run_characterization` is the per-sweep-point entry (`report.rs:63`), the planner+plan construction repeats once per SNR-point × trial despite `fft_size` being constant across the sweep. The actual `fft.process` calls (`analysis.rs:66-67`, window_count × 2 per call) are correct and FFT-based; only the planner construction is the waste.

Impact: dev-sweep throughput, scaling with sweep cardinality. Per-occurrence is one twiddle-table build for `fft_size` (typically 1024); negligible against a single long signal but multiplied by every sweep point it becomes a fixed tax per trial. Lower magnitude than the mask finding because it is O(1) per sweep-point rather than O(signal_len), but it is pure redundancy.

Confidence: Strong-static (planner is local to the fn; `fft_size` is invariant across the sweep).

Effort: Contained. Either (a) accept a `&mut FftPlanner` (or the planned `Arc<dyn Fft>`) as a parameter so the sweep driver builds it once and reuses, mirroring how `channel.rs` already threads a single `fft_planner` into `generate_fading_block`; or (b) a thread-local/once-cell planner cache. (a) matches the existing project idiom and stays deterministic.

Verification plan: criterion bench `estimate_subcarrier_snr` in a tight loop (fixed fft_size) vs a variant taking a shared planner; delta ≈ planner-construction cost × iterations. Correctness guard: output `SubcarrierSnrEstimate` must be byte-identical (FFT plan for a given size is deterministic); existing shape/value tests cover it.

---

### [MINOR] Double allocation + element-wise copy of every fading block (Vec → VecDeque extend)

Location: `channel.rs:103` / `channel.rs:113` (`self.tap1_buf.extend(block)`), with `generate_fading_block` returning an owned `Vec` (`fading.rs:35`)

Problem: Each refill allocates a `Vec<Complex<f32>>` of `FADING_BLOCK_LEN` inside `generate_fading_block`, then `extend`s it into a `VecDeque`, copying all 4096 elements and then dropping the Vec. The `VecDeque` is used purely as a FIFO that is filled fully then drained fully (it never holds a partial mix needing ring semantics — it empties before each refill, `channel.rs:95/105`). A plain `Vec` consumed back-to-front with an index cursor, or draining the returned Vec directly, avoids the per-block copy entirely.

Impact: dev-sweep throughput, low. One extra 4096-element memcpy per refill per tap, × sweep cardinality. Bounded and linear; the FFT in the same refill dominates, so this is a secondary cleanup, not a headline.

Confidence: Strong-static.

Effort: Localized (low). Replace the two `VecDeque` fading buffers with `Vec<Complex<f32>>` + a read cursor, or have `generate_fading_block` write into a caller-owned reused buffer (workhorse-buffer pattern). Reusing one buffer across refills also drops the per-refill Vec allocation.

Verification plan: dhat/heaptrack allocation count before/after over a fixed-length `process_block`; expect ~(refills×2) fewer 4096-elem allocations. Correctness guard: existing `streaming_equals_one_shot` + `same_seed_bit_identical_output` tests pin byte-identical output.

---

### [MINOR] `complex_gaussian_block` returns `Vec<(f32,f32)>` that every caller re-maps into a second allocation

Location: `rng.rs:37-42`; callers `fading.rs:42-46`, `analysis.rs` tests, `report.rs` tests, and `noise.rs:60` (noise.rs consumes it lazily via `zip`, so noise.rs is fine).

Problem: `complex_gaussian_block` builds a `Vec<(f32,f32)>`; `fading.rs:43-46` then iterates it into a *second* `Vec<Complex<f32>>`. Two allocations + a full copy where one would do. `noise.rs:61` consumes the pairs in a `zip` without re-collecting, which is the right pattern; `fading.rs` does not.

Impact: dev-sweep throughput, low. One extra `block_len` allocation+copy per fading block, × refills × taps × sweep cardinality. Same order as the VecDeque copy above; bundle the two.

Confidence: Strong-static.

Effort: Localized (low). Either have `complex_gaussian_block` return `impl Iterator<Item=(f32,f32)>` (lazy; callers `.map(Complex::...)` then collect once or feed directly), or add a `complex_gaussian_into(rng, &mut [Complex<f32>])` that fills a reused buffer. The iterator form composes with the workhorse-buffer fix in `channel.rs`.

Verification plan: dhat allocation count for one `generate_fading_block`; expect one fewer block-sized allocation. Correctness guard: `same_seed_same_block` (fading) + `same_seed_same_sequence` (rng) pin determinism — the draw order is unchanged, so output is byte-identical.

---

### Not flagged (examined, no significant algorithmic finding)

- `channel.rs:95/105` per-sample `is_empty()` check: O(1), branch-predictable, not a quadratic. Could be restructured to a per-block refill but the cost is a predicted branch per sample, not algorithmic. Cold.
- `channel.rs:119-124` delay-line `VecDeque` pop_front/push_back per sample: O(1) amortized, correct container for a fixed-length ring delay. No finding.
- `analysis.rs:59-86` window loop: O(window_count · fft_size) with correct FFT-based spectral analysis; no accidental quadratic, accumulators are flat `Vec<f64>` indexed by bin (correct container).
- `noise.rs:46-69`: single linear pass, power computed once, lazy `zip` over the gaussian draw. Clean.
- `params.rs`: pure match, no loops.
- No `Vec::contains`/`.iter().any()`-in-loop, no `Vec::remove`-in-loop, no per-iteration sort/dedup, no HashMap-hasher concern (no hot maps) anywhere in scope.
- FFT-vs-naive-convolution: the channel is a 2-tap FIR (`channel.rs:127`), correctly applied as direct multiply-add per sample (NOT FFT) — right choice for 2 taps; FFT is used only for fading-spectrum shaping where it belongs. No O(N²)-where-FFT-exists nor FFT-where-direct-is-better inversion.

---

## Suspected Bugs

None observed within this lane's reading. (Correctness not chased per scope; the renormalization in `fading.rs:99-105` and the `noise.rs` SNR scaling were read only for complexity, not validated for numerical correctness.)
