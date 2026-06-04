# Performance Audit — tuxmodem-phy — Data Access & I/O

**Agent:** glade-knoll-shoal
**Date:** 2026-06-04T14-24
**Scope:** `tuxmodem/crates/tuxmodem-phy/src/**` (excluding `src/bin/`)
**Dimension:** data access & I/O — audio device I/O (cpal), WAV file read/write (hound), and buffering between the audio stream and the symbol processor. No DB/network in this crate.
**Lens prior:** `performance-audit/profile-packs/rust.md` data-access lane + Runtime & build notes. My reading of the actual source is primary.

Impact model: Impact = reachability × frequency × per-occurrence cost. Effort is magnitude-only.

---

### [CRITICAL] FFT planner is reconstructed and the plan re-derived on every single OFDM symbol on the demod path

**Location:** `ofdm_main/receiver.rs:46-47` (`OfdmReceiver::demodulate_one_symbol`); mirror on TX at `ofdm_main/transmitter.rs:80-81`.
**Problem:** Each `demodulate_one_symbol` call does `let mut planner = FftPlanner::<f32>::new(); let fft = planner.plan_fft_forward(p.fft_size());`. `FftPlanner::new()` builds a fresh planner state and `plan_fft_*` derives the radix decomposition + twiddle-factor tables for the FFT size *from scratch* every call — none of it is cached across calls because the planner is a stack local that dies at the end of the function. The plan for a fixed FFT size (1024 or 2048, pinned per mode) is invariant across the entire session, yet it is recomputed per symbol.
**Reachability/frequency:** This is THE receive hot path. `WidebandLowDensityFloor::decode_symbol_bytes` (wideband_lowdensity.rs:173-193) calls `OfdmReceiver::new(...)` + `demodulate_one_symbol` once per symbol, and `receive_multi` (wideband_lowdensity.rs:334-338) loops it over *every symbol in the transmission* — a 1000-byte payload is ~111 symbols (per the crate's own `multi_roundtrip_1000_byte_payload` test), so 111 planner builds + 111 twiddle-table derivations for one received frame. A whole-WAV capture (tens of seconds → hundreds of symbols) multiplies this. The plan-derivation cost for a 2048-point FFT (twiddle tables, prime-factor planning) dominates the per-symbol arithmetic and recurs at symbol cadence.
**Impact:** High. Per-occurrence cost is large (plan derivation ≫ a single transform), frequency is per-symbol across an entire transmission, reachability is the primary decode path. Same defect on TX (`transmitter.rs:80`), reached per symbol from `transmit`/`transmit_multi`.
**Confidence:** Strong-static. rustfft@6's documented design is that planners cache plans and `Arc<dyn Fft>` instances are meant to be reused; constructing the planner inside the per-symbol function defeats that cache by construction.
**Effort:** Contained (+low). Hoist a planner (or the resolved `Arc<dyn Fft>`) to a field on `OfdmReceiver`/`OfdmTransmitter`, or better onto the long-lived `WidebandLowDensityFloor`/`OfdmParams` so it is built once per mode. The `Arc<dyn Fft>` is `Send + Sync` and reusable across calls; threading note below.
**Verification plan:** criterion bench of `receive_multi` on a 1000-byte payload (release build) before/after hoisting the plan to a reused `Arc<dyn Fft>`. Expect the planner-construction frames to vanish from a `cargo-flamegraph`/`samply` profile. Correctness guard: existing roundtrip tests (`multi_roundtrip_*`) must stay green — the transform output is identical, only the plan source changes.

---

### [MAJOR] `decode_symbol_bytes` rebuilds an `OfdmReceiver`, a pilot `HashSet`, an `OfdmEqualizer`, and a per-bit `Mapper` on every symbol — all derivable once per mode

**Location:** `robustness_floor/wideband_lowdensity.rs:173-193` → `ofdm_main/receiver.rs:55-83`.
**Problem:** Per symbol the receive path allocates and rebuilds invariant-per-mode state:
- `receiver.rs:56` `OfdmEqualizer::new(p.pilot_indices().to_vec(), ...)` — clones the pilot index vector into a fresh equalizer every symbol.
- `receiver.rs:60-61` builds a `HashSet<usize>` of pilot indices from scratch every symbol (default SipHash, small-n, but re-hashed every symbol). The transmitter has the identical per-symbol `HashSet` rebuild at `transmitter.rs:55-56`.
- `receiver.rs:78` `Mapper::new(constellation)` constructed inside the per-sub-carrier loop — once per data sub-carrier per symbol (74 data SCs in Wide mode), then fed a single-element slice `&[equalized[sc]]` (a 1-element allocation/borrow per call).
- `wideband_lowdensity.rs:175` `OfdmReceiver::new` and `:174` `bits_per_subcarrier()` (`vec![1; n]`, wideband_lowdensity.rs:48-50) rebuilt per symbol.
**Reachability/frequency:** Same per-symbol-across-transmission cadence as the CRITICAL finding — hundreds of times for a whole-WAV decode. The pilot set and bit-loading vector are identical for every symbol of a given mode; recomputing them per symbol is pure waste on the decode hot path.
**Impact:** Medium-high. Individually each is small-n, but they recur at symbol cadence alongside the FFT defect and collectively allocate several `Vec`/`HashSet`/`Mapper` per symbol. The `to_vec()` of pilot indices (`receiver.rs:56`) and the `vec![1; ...]` bit-loading (`wideband_lowdensity.rs:49`) are heap allocations per symbol.
**Confidence:** Strong-static (allocation/rebuild is visible in source; aggregate cost is per-symbol × transmission length).
**Effort:** Contained. Precompute the pilot `HashSet`, the data-bit-loading vector, and the equalizer's channel-estimate scaffolding once per `OfdmParams`/floor; pass borrows into the per-symbol demod. `Mapper::new` can be hoisted out of the sub-carrier loop when bit-loading is uniform (it is, for the BPSK floor).
**Verification plan:** `dhat` allocation profile of `receive_multi` over a multi-hundred-symbol input before/after — expect per-symbol allocation count to drop from O(symbols × (3 + data_sc)) toward O(1). Guard with the `multi_roundtrip_*` tests.

---

### [MAJOR] `read_wav` collects the entire transmission into a `Vec<f32>` before any decoding; no streaming bound on peak memory

**Location:** `audio_io.rs:61-74` (`AudioBuffer::read_wav`), specifically the `r.samples::<f32>().collect()` at `:71`.
**Problem:** The whole WAV is materialized into a single `Vec<f32>` up front (`Result<Vec<f32>, _> = r.samples::<f32>().collect()`), then handed to `receive_multi_with_sync`/`receive_with_sync`, which themselves take `&[f32]` over the entire buffer (wideband_lowdensity.rs:120, 270). For "whole transmissions (tens of seconds → ~MBs of i16/f32 samples)" per the stated load, peak resident memory is the full file regardless of how much of it the decoder actually consumes. The decode model is fundamentally streamable: the preamble scan finds a start offset, then symbols are consumed left-to-right (`receive_multi` indexes `samples[start..start+symbol_size]` per symbol at wideband_lowdensity.rs:335-336). A bounded reader feeding a symbol-sized window would cap peak usage at ~one-symbol + preamble-search-window rather than the whole file.
**Impact:** Medium. Reachability is every file-based receive (the documented operator workflow — "operator captured a WAV"). Frequency is once-per-file (not per-symbol), so this is a peak-memory / large-allocation concern, not a per-symbol CPU one. At tens of seconds × 48 kHz × 4 bytes that is low-single-digit MB — material on a Pi-class dev target but not catastrophic; the win is bounding peak, not shaving constant cost.
**Confidence:** Strong-static for the load-all behavior; Heuristic on the magnitude of the benefit (depends on whether operators ever feed multi-minute captures, which would scale this linearly).
**Effort:** Cross-cutting (+high) — the decode API is `&[f32]`-over-whole-buffer (`receive_with_sync`, `receive_multi_with_sync`, `PreambleDetector::scan`), so streaming requires a windowed-reader abstraction and a scan that operates on a sliding buffer. Not a localized change; flagging the architectural ceiling, not prescribing an immediate rewrite.
**Verification plan:** Argument-based: peak RSS under a synthetic multi-minute WAV scales linearly with file length today; a symbol-windowed reader caps it at O(symbol_size + scan_window). If pursued, guard with roundtrip tests on long inputs and a `valgrind --tool=massif`/`dhat` peak comparison. Correctness guard: the preamble-scan-then-decode semantics must be preserved across the buffer boundary (the scan window must cover the worst-case leading-silence offset the tests exercise, e.g. the 2000-sample lead in `multi_with_preamble_handles_leading_silence`).

---

### [MAJOR] Capture callback takes the accumulator `Mutex` and re-checks `len()` on every audio callback; main thread busy-polls the same lock under a 20 ms sleep

**Location:** `audio_device.rs:536-548` (capture callback) + `:564-588` (poll loop), shared `Arc<Mutex<Vec<f32>>>` at `:528`.
**Problem:** Two interacting issues on the audio-stream↔processor boundary:
1. The CPAL capture callback (`:536`) — which runs under a hard real-time deadline, ~100+×/sec — acquires `acc_cb.lock().unwrap()` (`:538`) and pushes channel-0 samples one at a time inside `for frame in samples.chunks_exact(channels)` (`:542-547`). Holding a `std::sync::Mutex` in a real-time audio callback is a priority-inversion / unbounded-blocking hazard: if the polling main thread holds the lock (it does, see below), the callback blocks inside the RT deadline. Per-sample `guard.push(frame[0])` also re-borrows the guarded Vec each iteration (the bounds/capacity check is cheap, but the lock is held for the whole chunk-copy).
2. The main thread (`:572-577`) acquires the *same* lock every 20 ms just to read `guard.len()`, then again at `:584` in the timeout path. This is a lock-hop purely to observe a count. A lock-free length signal (an `AtomicUsize` the callback bumps after appending) would let the main thread observe progress without contending the callback's lock at all.
**Reachability/frequency:** Every live capture (`record_blocking_with_abort`), callback fires ~100+×/sec for the capture duration; the poll loop contends every 20 ms. The lock contention window is small in samples but lands inside the RT callback where any blocking risks a buffer underrun/xrun.
**Impact:** Medium. The per-occurrence copy cost is small; the real exposure is RT-deadline jitter from a `std::sync::Mutex` shared with a polling thread. For a DSP RT path the idiomatic structure is a lock-free SPSC ring (e.g. the producer writes, consumer drains) or at minimum an `AtomicUsize` progress counter so the consumer never takes the producer's lock.
**Confidence:** Strong-static (the lock-in-RT-callback + same-lock poll is plainly in source). The xrun risk is Heuristic — depends on device buffer size and host scheduler; on a loaded Pi it is realistic.
**Effort:** Contained (+low for the `AtomicUsize`-progress half; +high if moving to a true lock-free ring). The cheap win: replace the main thread's `guard.len()` polls (`:574`, `:584`) with an `AtomicUsize` the callback updates after each chunk, eliminating consumer-side contention on the callback's lock.
**Verification plan:** Argument + guard. Argument: removing the consumer's lock acquisitions removes all main-thread contention on the callback's mutex; the callback then only ever blocks on its own lock (uncontended), eliminating the priority-inversion path. Verify with a contention/xrun count under a stress capture (e.g. `cpal` xrun-error counter via the existing `err_tx` channel). Correctness guard: the final `Arc::try_unwrap` + `truncate(target_samples)` (`:591-600`) semantics must be preserved; the atomic is advisory, the Vec remains the source of truth.

---

### [MINOR] Playback expands mono→device-channels into a full second `Vec` up front instead of expanding inside the callback

**Location:** `audio_device.rs:261-266` (`play_blocking_with_abort`).
**Problem:** Before starting the stream, the entire buffer is expanded mono→N-channel into `frames` (`Vec::with_capacity(len * channels)`), duplicating every sample `channels` times (`:262-266`). For a stereo device this doubles the resident copy of a whole transmission; the source `AudioBuffer` is also still alive (borrowed), so peak memory is ~`(1 + channels) ×` the sample count during playback setup. The callback (`:279-298`) just `copy_from_slice`s from this pre-expanded buffer. The duplication could instead happen inside the callback (read cursor / channels, write each output frame), keeping only the mono buffer resident.
**Reachability/frequency:** Once per `play_blocking` call (not per-callback), so this is a peak-memory concern, not a per-callback CPU one. Mono devices (`channels == 1`) make the expansion a straight copy with no fan-out — still one redundant full-buffer copy.
**Impact:** Low. Bounded by transmission length × channel count; the copy is a one-time setup cost, not in the RT callback. Listed for completeness because it interacts with the `read_wav` load-all to set the playback-path peak.
**Confidence:** Strong-static.
**Effort:** Contained. Move channel fan-out into the callback closure (it already owns a `cursor`); for `channels == 1` borrow the buffer's slice directly with zero copy.
**Verification plan:** Argument: callback-side expansion drops peak from `(1+channels)×N` to `~1×N` plus the device buffer. Guard: the existing `channel_expansion_*` unit tests (`:631-655`) pin the fan-out arithmetic; the callback version must reproduce identical interleaving.

---

### [MINOR] `OfdmParams::data_indices()` returns a freshly-allocated `Vec` (and rebuilds a `HashSet`) on every call; called per `transmit`

**Location:** `ofdm_main/ofdm_params.rs:100-108`; callers at `wideband_lowdensity.rs:63` (`transmit`), `:165` (`data_bytes_per_symbol`).
**Problem:** `data_indices()` builds a `HashSet<usize>` from the pilot indices and `collect()`s a filtered `Vec<usize>` on each call. `transmit` (wideband_lowdensity.rs:63) calls `self.params.data_indices().len()` per symbol, and `transmit_multi` loops `transmit` per chunk (wideband_lowdensity.rs:233-236), so the set+vec are rebuilt per symbol on the TX side just to read `.len()`. `data_bytes_per_symbol()` (wideband_lowdensity.rs:165) does the same and is called in `transmit_multi`/`receive_multi` loops.
**Reachability/frequency:** Per-symbol on the transmit path. The result is invariant per mode.
**Impact:** Low (small-n, TX side, but per-symbol allocation that is trivially cacheable). Grouped under the same "recompute-per-symbol invariant" theme as the MAJOR receiver finding.
**Confidence:** Strong-static.
**Effort:** Localized (+low). Compute `data_indices` once in `OfdmParams::for_mode` and store it as a field (return `&[usize]`); or memoize the count. Note the existing `transmit` only ever needs `.len()` — a stored `data_index_count: usize` suffices for the hot reads.
**Verification plan:** Argument: replaces O(occupied) set-build + vec-collect per symbol with a field read. Guard: `data_bytes_per_symbol_is_positive` test (`:533-537`) and the `transmit_multi_length_*` tests pin the value.

---

## Suspected Bugs (for follow-up)

None within the data-access/I/O dimension. (The per-symbol `n0 = 0.1` noise-variance proxy in `receiver.rs:82` and the real-part-only audio simplification are documented design choices, not I/O defects. The `record` callback's `chunks_exact(channels)` drops a trailing partial frame if a callback delivers a non-channel-multiple length, but that is a correctness/edge concern outside this dimension and CPAL frames are channel-aligned by contract.)
