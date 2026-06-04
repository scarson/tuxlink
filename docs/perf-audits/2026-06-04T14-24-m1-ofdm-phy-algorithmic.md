# Performance audit — `tuxmodem-phy`: algorithmic complexity & data structures

**Agent:** glade-knoll-shoal
**Date:** 2026-06-04
**Scope:** `tuxmodem/crates/tuxmodem-phy/src` (all `.rs` except `src/bin/`)
**Dimension:** algorithmic complexity & data structures only — accidental quadratics, repeated/recomputed-in-loop work that could be hoisted/memoized, wrong container for access pattern, recomputation of pure results.

## Load model used for impact calibration

- 48 kHz, OFDM symbol = `fft_size` (1024 narrow/mid, 2048 wide) + CP (`fft_size/4`) → ~19–38 symbols/sec in real time; a transmission is tens of seconds → hundreds–thousands of symbols.
- Wide mode occupies ~98 subcarriers (`start_bin..=end_bin` from `ofdm_params.rs`), ~74 data + ~25 pilot; the per-subcarrier LLR/map inner loop runs dozens of times per symbol → hundreds–low-thousands of times/sec.
- `transmit_multi` / `receive_multi` iterate one symbol per `data_bytes_per_symbol` (9 bytes) chunk, so a 1 KB payload is ~114 symbols in a single call (confirmed by the `multi_roundtrip_1000_byte_payload` test).

The per-symbol and per-subcarrier scopes are the ones that matter; module-construction / mode-table work that runs once per `for_mode` is bounded and excluded.

---

### [CRITICAL] FFT planner + plan rebuilt from scratch on every OFDM symbol (TX and RX)

**Location:**
- `ofdm_main/transmitter.rs:80-83` (`let mut planner = FftPlanner::new(); let ifft = planner.plan_fft_inverse(p.fft_size());`)
- `ofdm_main/receiver.rs:46-49` (`let mut planner = FftPlanner::new(); let fft = planner.plan_fft_forward(p.fft_size());`)

**Problem:** `modulate_one_symbol` and `demodulate_one_symbol` each construct a fresh `FftPlanner` and call `plan_fft_*` every invocation — i.e. once per OFDM symbol. rustfft's planner builds (or at minimum looks up + clones an `Arc` to) the radix decomposition and the twiddle-factor tables for the transform size on each `plan_*` call against a fresh planner; the twiddle tables for a 2048-point FFT are O(fft_size) complex floats that get recomputed/reallocated. The FFT size is invariant for the lifetime of a `WidebandLowDensityFloor` / a given mode — the plan is a pure function of `fft_size` and should be built once and reused. A `FftPlanner` also caches plans internally across calls, but only if the *same planner* is reused; constructing a new one each symbol defeats that cache entirely.

**Impact:** Reachability: every TX symbol (`transmit` → `modulate_one_symbol`) and every RX symbol (`decode_symbol_bytes` → `demodulate_one_symbol`). Frequency: once per symbol → ~19–38×/sec real-time, and `receive_multi`/`transmit_multi` call it once per symbol in a tight loop (`wideband_lowdensity.rs:233-236`, `:334-338`) → ~114× in one `receive_multi` call for a 1 KB payload. Per-occurrence: planner construction + plan build is O(fft_size) twiddle setup + allocations, on the same order of magnitude as the FFT butterflies themselves, so this is roughly a constant-factor doubling of per-symbol DSP cost plus heap churn the steady state shouldn't have. On the `transmit`/`receive` real-time path this directly eats into the per-symbol budget that gates the CPAL deadline downstream.

**Confidence:** Strong-static. **Effort:** Contained (store an `Arc<dyn Fft<f32>>` on `OfdmReceiver`/`OfdmTransmitter`, or hoist a shared planner into `WidebandLowDensityFloor`; receiver/transmitter are currently constructed per-symbol too — see next finding — so the natural fix is to build the plan once where `OfdmParams` lives and thread it through).

**Verification plan:** Criterion bench of `WidebandLowDensityFloor::receive_multi` on a 1 KB payload, before/after caching a single `Arc<dyn Fft>` per FFT size; expect a measurable drop in both wall-time and allocation count (confirm with `dhat`). Correctness guard: the existing `multi_roundtrip_*` round-trip tests pin bit-exact output — the cached plan must reproduce them unchanged.

---

### [CRITICAL] Preamble correlation `scan` is O(signal_len × template_len) with a redundant energy recomputation

**Location:** `sync/preamble.rs:88-101` (the `for i in 0..(signal.len()-n) { for j in 0..n {...} }` double loop).

**Problem:** `scan` slides the 192-sample template across the whole signal and, at every offset `i`, recomputes both the cross-correlation **and** the signal's windowed energy (`sig_energy += signal[i+j]^2`) from scratch over all `n` samples. The energy of window `[i+1 .. i+1+n]` differs from window `[i .. i+n]` by exactly two terms (drop `signal[i]^2`, add `signal[i+n]^2`); recomputing the full sum is the textbook accidental-quadratic-via-recompute pattern. The cross-correlation itself is inherently O(N·M) in this naive form, but the energy normaliser is gratuitously so.

**Impact:** Reachability: every `receive_with_sync` / `receive_multi_with_sync` call (`wideband_lowdensity.rs:121`, `:274`) — the real-capture decode entry points. Frequency: once per received frame. Per-occurrence: `signal.len()` for a real capture includes leading silence + preamble + the whole multi-symbol body (the tests feed 10 k–20 k-sample buffers; a real tens-of-seconds capture is ~10⁶ samples). At N≈10⁶, M=192 the inner work is ~2×10⁸ multiply-adds, of which the energy half (~10⁸) is pure waste that a running-sum eliminates. This is the single most expensive operation on the receive path for long captures.

**Impact note (correctness-adjacent, not chased here):** the loop bound `0..(signal.len()-n)` stops one short of the last valid offset `signal.len()-n` — logged under Suspected Bugs.

**Confidence:** Strong-static (complexity argument is exact). **Effort:** Localized (maintain a running `sig_energy` across `i`; subtract the leaving sample, add the entering one). The cross-correlation can additionally be moved to an FFT-based correlation (O(N log N)) if profiling shows the correlation half dominates after the energy fix, but that's a larger change.

**Verification plan:** Criterion bench of `scan` on a ~10⁶-sample buffer before/after the running-energy fix; complexity argument says the energy half drops from O(N·M) to O(N). Correctness guard: `preamble_roundtrip_*` and `receive_with_sync_returns_frame_detect_*` tests pin detected `start_sample` and SNR-threshold behavior; the incremental energy must reproduce them. Pin a test on the boundary offset before touching the loop bound (see Suspected Bugs).

---

### [MAJOR] Constellation alphabet (and `Mapper`) recomputed per subcarrier, per symbol, in the LLR loop

**Location:**
- `ofdm_main/receiver.rs:78,83` — `Mapper::new(constellation)` then `mapper.compute_llr(&[equalized[sc]], n0)` inside the per-subcarrier loop (`:63`).
- `constellations.rs:137-177` — `compute_llr` calls `self.alphabet()` (`:142`), and `alphabet()` (`:164-177`) rebuilds the full constellation point set by allocating a `Vec`, looping `0..2^bps`, and calling `self.map(&bits)` (which allocates a `Vec<Complex>` per point) for **every** code point.

**Problem:** The constellation alphabet is a pure function of the `Constellation` enum (4 possible values, fully `const`-computable). For each data subcarrier of each symbol, the receiver constructs a `Mapper`, then `compute_llr` rebuilds the entire alphabet from scratch — for 64-QAM that's 64 calls to `map()`, each allocating a 1-element `Vec`, plus the outer `alphabet` `Vec`. Then the LLR brute-force loops over that freshly-built alphabet. The alphabet build is repeated for every subcarrier even though every subcarrier in a BPSK floor symbol shares the identical alphabet.

**Impact:** Reachability: every RX data subcarrier (`demodulate_one_symbol` loop). Frequency: ~74 data subcarriers/symbol × ~19–38 symbols/sec → ~1.4 k–2.8 k alphabet rebuilds/sec in real-time RX, and ~74×114 ≈ 8.4 k rebuilds in a single 1 KB `receive_multi` call. Per-occurrence: BPSK is cheap (2 points), but the wideband floor is BPSK so the dominant cost is the per-subcarrier `Vec` allocations (alphabet Vec + per-point map Vec), not the arithmetic — thousands of tiny heap allocs/sec that a hoisted/`const` alphabet eliminates entirely. For bit-loaded OFDM-main modes carrying 16-/64-QAM the arithmetic cost compounds. The transmitter side mirrors this: `Mapper::new` per data subcarrier at `transmitter.rs:72`, and `map()` allocates a `Vec` per subcarrier symbol (`:74`).

**Confidence:** Strong-static. **Effort:** Contained. Make the alphabet a `const`/`static` per-constellation table (or memoize behind a `OnceLock` keyed by the 4 enum variants), and have `compute_llr` borrow it. Hoist `Mapper` construction (it is zero-sized state, so the win is purely the alphabet/allocation, not the mapper struct). Consider a scalar `map_one`/`llr_one` API that avoids the 1-element `Vec` round-trip the per-subcarrier call sites use.

**Verification plan:** Criterion bench of `demodulate_one_symbol` on a wide-mode symbol before/after; `dhat` to confirm the per-subcarrier alloc count drops to ~0. Correctness guard: the `multi_roundtrip_*` round-trips and any constellation LLR-sign tests pin behavior; the const alphabet must be bit-identical to the computed one (add a test asserting `alphabet()` equals the const table for all four constellations during migration).

---

### [MAJOR] Equalizer rebuilt and pilot `HashSet` reconstructed on every received symbol

**Location:**
- `ofdm_main/receiver.rs:56` — `OfdmEqualizer::new(p.pilot_indices().to_vec(), p.fft_size())` allocates a fresh `Vec<usize>` copy of the pilot indices per symbol.
- `ofdm_main/receiver.rs:60-61` — `let pilot_set: HashSet<usize> = p.pilot_indices().iter().copied().collect();` rebuilds the pilot membership set per symbol.
- `ofdm_main/transmitter.rs:55-56` — same pilot `HashSet` rebuild per symbol on the TX side.
- `ofdm_main/equalizer.rs:37` — `equalize` allocates a fresh `chan_est` `Vec<Complex>` of length `n_bins` (1024/2048) per call.

**Problem:** The pilot index set and the equalizer's structure are invariant for a mode. Each symbol: (a) clones the pilot Vec into a new `OfdmEqualizer`, (b) builds a `HashSet<usize>` from the pilot indices purely to answer "is this subcarrier a pilot?" inside the subcarrier loop. The pilot set is small (~25 entries, ascending, every-4th-subcarrier) and the lookup could be a precomputed `Vec<bool>` mask indexed by bin, or a `data_indices()` iteration that skips pilots structurally — `ofdm_params.rs:100` already computes `data_indices()` (itself rebuilding a HashSet each call, see below). The `HashSet` build is O(pilots) allocations + hashing per symbol to replace an O(1)-per-lookup mask.

**Impact:** Reachability: every TX and RX symbol. Frequency: per symbol (~19–38/sec real-time; ~114 per 1 KB multi call). Per-occurrence: one `HashSet` alloc + ~25 inserts (TX and RX), one pilot-Vec clone + one `chan_est` Vec alloc of 1024–2048 complex (RX). The `chan_est` allocation is the largest single item here (8–16 KB/symbol) and is a clear workhorse-buffer candidate. Aggregate: thousands of small-to-medium allocations/sec on the real-time path.

**Confidence:** Strong-static. **Effort:** Contained. Precompute a `Vec<bool>` pilot mask (or a sorted-slice membership check, since pilots are ascending — `binary_search` is O(log n) with zero allocation) once per `OfdmParams`; make `OfdmEqualizer` borrow `&[usize]` instead of owning a cloned Vec; give the equalizer a reusable `chan_est` buffer (`clear()` + resize, or pass a scratch buffer in).

**Verification plan:** `dhat` alloc-count diff on `receive_multi` before/after; expect per-symbol HashSet + pilot-Vec + chan_est allocs to drop to amortized zero. Correctness guard: round-trip tests; the mask/binary-search membership must select the exact same data subcarriers as the current `HashSet`.

---

### [MAJOR] `OfdmReceiver`/`OfdmTransmitter` and `bits_per_subcarrier` rebuilt per symbol inside the multi-symbol loops

**Location:**
- `robustness_floor/wideband_lowdensity.rs:173-176` (`decode_symbol_bytes`) — `self.bits_per_subcarrier()` and `OfdmReceiver::new(&self.params)` constructed on every call.
- `decode_symbol_bytes` is called once per symbol from `receive_multi` (`:313`, `:336`) and `receive` (`:152`).
- `transmit` (`:62,:71`) builds `bits_per_subcarrier()` and an `OfdmTransmitter` per call; `transmit_multi` (`:233-235`) calls `transmit` per chunk.
- `bits_per_subcarrier` (`:48-50`) allocates `vec![1; subcarrier_indices().len()]` (~98 entries) every call.

**Problem:** Inside `receive_multi`/`transmit_multi`, every symbol re-allocates the `bits_per_subcarrier` Vec and reconstructs the receiver/transmitter wrapper. `bits_per_subcarrier` for the wideband floor is a constant `vec![1; N]` — it never changes across symbols of a transmission. The receiver/transmitter structs are thin (hold `&OfdmParams`), so the struct construction is cheap, but they are the entry points that then do the per-symbol FFT-planner and pilot-HashSet rebuilds above, so this loop is where all the per-symbol waste is multiplied by symbol count.

**Impact:** Reachability: every multi-symbol TX/RX (the real arbitrary-length path). Frequency: per symbol within a call → ~114 redundant `bits_per_subcarrier` allocs for a 1 KB payload. Per-occurrence: one ~98-byte Vec alloc/symbol — modest individually, but it is the cheapest of the per-symbol rebuilds and sits in the same hot loop, so fixing it is free alongside the planner/equalizer fixes.

**Confidence:** Strong-static. **Effort:** Localized-to-Contained. Hoist `bits_per_subcarrier` out of the per-symbol loop in `receive_multi`/`transmit_multi` (compute once, pass `&[u8]` down); cache it on the floor struct since it is constant.

**Verification plan:** `dhat` alloc diff on `receive_multi`/`transmit_multi`; round-trip tests as the correctness guard.

---

### [MINOR] `data_indices()` rebuilds a `HashSet` and a `Vec` on every call; called repeatedly per transmit

**Location:** `ofdm_main/ofdm_params.rs:100-108` — `data_indices` builds a `HashSet<usize>` from pilots then filters `subcarrier_indices` into a new `Vec` on each call.

**Problem:** `data_indices()` is pure (depends only on the immutable `OfdmParams`) but recomputes a HashSet + Vec every call. It is called from `transmit` (`wideband_lowdensity.rs:63` `data_per_symbol`), `data_bytes_per_symbol` (`:166`), and `receive`/`transmit_multi` flows — i.e. at least once per `transmit` and thus once per symbol in `transmit_multi`, and several times per `receive_multi` (via `data_bytes_per_symbol` at `:221`, `:305`).

**Impact:** Reachability: per `transmit` (so per symbol in multi) + per `data_bytes_per_symbol`. Frequency: a few × per symbol. Per-occurrence: one HashSet alloc + ~25 inserts + one ~74-entry Vec alloc. Aggregate is real (hundreds–thousands/sec) but smaller per-item than the FFT/alphabet findings. Memoizing on `OfdmParams` removes it entirely.

**Confidence:** Strong-static. **Effort:** Localized. Compute `data_indices` once in `for_mode` and store it (or store `data_indices.len()` + a cached Vec) — `OfdmParams` is already an owned struct, adding a field is cheap and the value is immutable post-construction.

**Verification plan:** Trivial — store-once and assert the cached value equals the recomputed one in a test; round-trip tests guard end-to-end.

---

### [MINOR] FSK receive recomputes tone-bin indices per symbol; planner ok but bins are invariant

**Location:** `robustness_floor/narrow_fsk.rs:99-102` — inside the per-symbol loop, for each of the 8 tones, `tone_freq_hz(tone_idx)` and the bin index `(f * fft_size / SAMPLE_RATE).round()` are recomputed every symbol.

**Problem:** The 8 tone frequencies and their FFT bin indices are invariant for the mode (fixed `fft_size = sps.next_power_of_two()`, fixed tone grid). They are recomputed for every symbol of every receive. Unlike the OFDM path, `narrow_fsk.rs:83-85` *does* build the planner + plan once outside the symbol loop (good), so the only remaining recompute is the 8 trig/round bin computations per symbol.

**Impact:** Reachability: `NarrowFskFloor::receive` — the situational crowded-band floor, not the default path, so lower reachability than the OFDM findings. Frequency: 8 bin computations/symbol; FSK symbols are 0.16 s so ~6 symbols/sec → ~48 redundant computations/sec. Per-occurrence: trivial (8 multiplies + rounds). This is a MINOR borderline-CALIBRATION item — listed because it is a clean hoist, not because the aggregate is large.

**Confidence:** Strong-static. **Effort:** Localized. Precompute the 8 bin indices into an `[usize; 8]` before the symbol loop.

**Verification plan:** Hoist + assert identical demod output on the FSK round-trip path. Low priority.

---

## Items examined and explicitly NOT flagged (calibration)

- `modes.rs:86-94` `distinct_families` uses `Vec::contains` in a loop (O(n²)) and `by_name` (`:120`) linear-scans — but the mode table is 5 entries, provably bounded, and resolved off the hot DSP path. Not a finding.
- `constellations.rs` `compute_llr` brute-force max-log over the alphabet is O(2^bps · bps) per symbol — inherent to max-log demod and bounded at 64 points (64-QAM); the *recompute of the alphabet* is the finding, not the brute force itself.
- `subcarrier_snr.rs:38-48`, `equalizer.rs:64-71` zip-map passes are single-pass O(n_bins), correct container, no recompute — fine.
- `audio_device.rs` callbacks: the output callback (`:279-299`) is a bounded `copy_from_slice` + zero-fill, no per-sample scan growth; the input callback (`:536-548`) holds a `Mutex` and pushes per frame — a concurrency/lock-scope concern, out of this dimension's scope (noted for the concurrency auditor). Channel-expansion (`:261-266`) pre-sizes with `with_capacity`. No algorithmic finding.
- `narrow_fsk.rs` / `wideband_lowdensity.rs` bit-packing loops are single-pass O(bits), pre-sized. Fine.
- `audio_io.rs` WAV read/write iterate samples once; `hound` buffering is the I/O auditor's lane.

---

## Suspected Bugs (for follow-up)

- **`sync/preamble.rs:88`** — `for i in 0..(signal.len() - n)` excludes the final valid alignment offset `i = signal.len() - n`. The window `signal[i..i+n]` is in-bounds for `i == signal.len() - n` (it reads indices `signal.len()-n .. signal.len()`), so the last possible preamble position is never tested. Should be `0..=(signal.len() - n)` or `0..(signal.len() - n + 1)`. Effect: a preamble that begins exactly at the last fitting offset is missed; also shifts the detected peak by up to 1 sample near the tail. The existing tests tolerate ±2 sample error (`wideband_lowdensity.rs:398`, `:753`) so this is masked. Not chased — flagging file:line + reasoning only.
- **`sync/symbol_timing.rs:43-44`** (not a perf issue, noted in passing) — `mean_energy` is computed over the *entire* signal including any leading silence, which can bias the Gardner normaliser; behavioral, not algorithmic. Flagged for the correctness pass, not chased here.
