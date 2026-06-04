# Perf Audit — Data Access & I/O / Data-Layout (m5, hf-channel-sim)

Agent: glade-knoll-shoal. Scope: `hf-channel-sim/src/{channel,fading,analysis,noise,report,params,rng,lib}.rs` (bin/ excluded). Load profile: dev/test batch sweeps. Lens: rust.md data-access lane.

---

### [MAJOR] Per-sample `VecDeque` element-wise drain is the hot-loop data-access pattern

**Location:** `channel.rs:93-129` (`process_block`), buffers declared `channel.rs:41-44`.

**Problem:** The hot loop runs once per input sample and, per sample, does: two `tap_buf.is_empty()` branch checks, two `pop_front()` from `VecDeque`, plus a `pop_front()` + `push_back()` on `delay_line`. The fading samples are produced in contiguous 4096-element `Vec`s (`generate_fading_block`) but are then poured into a `VecDeque` via `extend` (`channel.rs:103,113`) and consumed one element at a time. `VecDeque` is a ring buffer: each `pop_front` touches head/tail indices and is not contiguous-iteration-friendly the way slice iteration is — the access is strided/index-driven rather than the linear streaming the data layout would permit. The `is_empty()` test executes every sample even though the buffer is only exhausted once per 4096 samples (a loop-invariant-ish check that belongs at block granularity).

**Impact (dev-sweep):** For an N-sample signal swept across conditions × seeds × SNRs, this is the dominant inner loop. Restructuring to (a) generate a full fading block as a `Vec`/slice and index it linearly, and (b) hoist the refill to a chunk boundary, converts the per-sample `VecDeque` bookkeeping into contiguous slice reads — better cache behaviour and fewer per-sample branches across every trial in the sweep.

**Confidence:** Medium-High (the AoS-into-ring-buffer-then-drain pattern is visible in source; magnitude needs a release-build profile).
**Effort:** M — restructure the fading buffer from `VecDeque` to a `Vec` + cursor, refill at chunk boundary; preserve the streaming-equals-one-shot invariant (`channel.rs:184`).
**Verification:** `criterion` bench of `process_block` over a representative N (e.g. 65536) before/after; confirm `streaming_equals_one_shot` and `same_seed_bit_identical_output` still pass.

---

### [MINOR] `delay_line` as `VecDeque` for a fixed-length shift register

**Location:** `channel.rs:44,57-60,119-125`.

**Problem:** `delay_line` is a fixed-capacity (`delay_samples`, e.g. 16 at 8 kHz / 2 ms) FIFO used as a shift register: pop_front + push_back per sample. For a small fixed length, a flat `Vec` (or `Box<[Complex<f32>]>`) with a wrapping write cursor is the cache-friendly layout — a single indexed read+write into a contiguous buffer instead of ring-buffer index juggling, and no per-sample `unwrap()` on `Option`.

**Impact (dev-sweep):** Small per-sample constant-factor win that compounds over every sample of every trial; the buffer is tiny so it stays L1-resident either way — the gain is from removing `VecDeque`'s two-index arithmetic per access, not from cache misses.
**Confidence:** Medium. **Effort:** S.
**Verification:** Same benches/tests as above; `delay_line_introduces_expected_lag` (`channel.rs:218`) must still pass.

---

### [MINOR] `complex_gaussian_block` materializes an intermediate `Vec<(f32,f32)>` that is always re-mapped

**Location:** `rng.rs:37-42`; consumers `fading.rs:42-46`, `report.rs` test paths, `noise.rs:60-68`.

**Problem:** The function returns `Vec<(f32,f32)>`. Two of three call sites immediately `.into_iter().map(|(re,im)| Complex{re,im}).collect()` into a fresh `Vec<Complex<f32>>` (`fading.rs:43-46`), allocating and copying a second full-length buffer per fading block (4096 elements × every block × every trial). `noise.rs:61` zips the pairs directly so it is fine, but the fading path pays a redundant allocate-and-copy. Returning `Vec<Complex<f32>>` directly (or `impl Iterator`) eliminates one full-block allocation+copy per fading block.

**Impact (dev-sweep):** One extra 4096-element allocation + memcpy per fading block. With blocks regenerated continuously across a sweep, this is steady allocator churn on the channel hot path, though bounded.
**Confidence:** High (the double-collect is explicit in source). **Effort:** S — change return type or add a `Complex`-returning variant; `(f32,f32)` layout is bit-identical to `Complex<f32>` so determinism is preserved.
**Verification:** `rng` and `fading` test modules unchanged-pass; bench `generate_fading_block`.

---

### [MINOR] `snapshots` retains full per-window × per-bin matrix; serialized whole

**Location:** `analysis.rs:33,54,69-85`; serialized via `report.rs` (`CharacterizationReport` → `serde_json::to_string`).

**Problem:** `snapshots: Vec<Vec<f32>>` is `window_count × fft_size`. For a 65536-sample signal at `fft_size=1024` that is 64 × 1024 `f32` = 256 KB held in memory and emitted into the JSON report in full — the per-window data is over-fetched into the canonical artifact even when only `mean_snr_db` (the bit-loading input the doc cites at `analysis.rs:6-9`) is consumed downstream. The outer `Vec<Vec<f32>>` is also a pointer-chasing layout (each inner `Vec` a separate allocation) rather than a single flat `window_count*fft_size` buffer. Serialization cost (serde walking N inner Vecs + JSON float formatting) scales with the full matrix per report, per trial in a sweep.

**Impact (dev-sweep):** Per-trial JSON serialization of a quadratic-in-parameters matrix; allocation of `window_count` separate inner `Vec`s. If downstream consumes only `mean_snr_db`, gate `snapshots` behind a flag / `#[serde(skip)]`, or flatten to one `Vec<f32>` + dims to cut per-report serialize cost and pointer-chase.
**Confidence:** Medium (over-fetch depends on actual downstream usage — flagged, not asserted). **Effort:** M.
**Verification:** Confirm consumers; bench `serde_json::to_string(&report)` with/without snapshots; `serde_roundtrip` (`analysis.rs:172`) must pass.

---

### [MINOR] Per-window `to_vec()` copies before FFT

**Location:** `analysis.rs:63-64`.

**Problem:** Each window copies `clean[s..e]` and `observed[s..e]` into fresh `Vec`s every iteration (`window_count` × 2 allocations of `fft_size`). A reusable workhorse pair of scratch buffers declared outside the loop and `copy_from_slice`d each iteration (rustfft needs a mutable in-place buffer, so the copy itself is unavoidable, but the per-iteration allocation is) removes `2 × window_count` allocations per report.

**Impact (dev-sweep):** Loop-body allocation churn proportional to window count per report, per trial. Bounded but avoidable.
**Confidence:** High. **Effort:** S.
**Verification:** `analysis` tests unchanged-pass; bench `estimate_subcarrier_snr`.

---

## Examined, no in-dimension finding
- `noise.rs` `add_noise`: streams via `zip`, in-place; no redundant copy. Fine.
- `report.rs:77` `channel_out.clone()`: a memory-lane clone, not data-access/I-O; out of this dimension.
- No file/stdout I/O inside any sweep inner loop in scope (bin/ excluded); JSON serialization is the only I/O-adjacent surface, covered above. No unbuffered-writer or `println!`-in-loop pattern in scope.
- `params.rs`/`lib.rs`: no data-access surface.

## Suspected Bugs
None.
