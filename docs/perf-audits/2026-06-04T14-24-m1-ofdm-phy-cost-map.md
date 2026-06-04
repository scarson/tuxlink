# tuxmodem-phy — Execution Cost Map (M1 lane)

Agent: glade-knoll-shoal
Date: 2026-06-04T14:24Z
Scope: `tuxmodem/crates/tuxmodem-phy/src` (all `.rs` except `src/bin/`)
Method: structural reasoning over the actual RX/TX DSP source. Descriptive map, not a defect list.

## Execution Cost Map

> Architectural awareness, NOT an optimization to-do list. Not every region here is a problem.

### Path topology (what runs how often)

- **TX frame** (`wideband_lowdensity.rs:214 transmit_multi`): payload → N symbols, where `N = ceil((2 + payload.len()) / 9)` for Wide mode (9 data bytes/symbol). A 1000-byte payload = ~112 symbols. Each symbol calls `transmit()` → `OfdmTransmitter::modulate_one_symbol`.
- **RX frame** (`wideband_lowdensity.rs:303 receive_multi`): one preamble scan, then `decode_symbol_bytes` per symbol → `OfdmReceiver::demodulate_one_symbol`. Note the **first symbol is decoded twice** (`receive_multi:313` reads the length header, then the `1..symbols_needed` loop at `:334` decodes symbols 1..N; symbol 0 is only decoded once — correct, no double-decode here, but the header decode IS a full `decode_symbol_bytes`).
- **Real-time audio callback** (`audio_device.rs:279` output, `:536` input): runs 100+×/sec on the audio thread. Critically, **no DSP runs inside the callback** — modulate/demodulate happen ahead of time on the caller thread, and the callback only does a `copy_from_slice` (TX) or a mutex-guarded `push` loop (RX). See "Real-time callback budget" below.

So PHY here is **batch/offline DSP feeding a thin real-time callback**, not per-callback DSP. That reshapes where cost concentrates: the heavy work is amortized across a `play_blocking`/`record_blocking` call, not metered against the ~10 ms callback deadline.

### Likely time-concentration regions

- **Per-symbol FFT planner construction (`receiver.rs:46-47`, `transmitter.rs:80-81`)** — basis: `FftPlanner::<f32>::new()` + `plan_fft_forward/inverse(fft_size)` are called **inside** `demodulate_one_symbol` / `modulate_one_symbol`, i.e. once per OFDM symbol, not once per mode. rustfft's planner computes/caches twiddle factors and selects an algorithm on `plan_*`; doing it per symbol means ~19–112 planner builds per frame instead of 1. The FFT *execution* (`fft.process`, O(N log N), N=1024/2048) is inherent and fine; the **planning** repeated per symbol is the concentration. — confidence: High — overlaps a likely hot-spot.

- **The forward/inverse FFT itself (`receiver.rs:49 fft.process`, `transmitter.rs:83 ifft.process`)** — basis: O(N log N) butterflies over N=1024 or 2048 complex points, once per symbol, on every symbol of every frame. This is the single heaviest *unit-cost* operation on both paths and is inherent to OFDM. — confidence: High — map-only (inherent; not a problem).

- **Per-subcarrier `Mapper::new()` + brute-force LLR alphabet rebuild (`receiver.rs:78,83` → `constellations.rs:137 compute_llr` → `:164 alphabet`)** — basis: inside the per-subcarrier loop (`receiver.rs:63`), every data subcarrier constructs a fresh `Mapper`, then `compute_llr` calls `self.alphabet()` which **re-maps the entire constellation from scratch** (loop `0..2^bps`, each calling `self.map()`). For 64-QAM that's a 64-entry alphabet rebuilt for every subcarrier, every symbol. `compute_llr`'s inner triple-loop is `syms × bits × alphabet` = `1 × bps × 2^bps` per subcarrier. With dozens of data subcarriers × tens of symbols, the alphabet rebuild + max-log scan is a real frequency×unit-cost multiplier. For BPSK (the Wide floor's actual loading, `bits_per_subcarrier` all 1s) the alphabet is only 2 entries, so the floor mode is cheap; the cost grows sharply with bit-loading density. — confidence: High — overlaps a likely hot-spot.

- **Per-subcarrier pilot-set HashSet rebuild (`receiver.rs:60`, `transmitter.rs:55`, `ofdm_params.rs:101`)** — basis: both `modulate_one_symbol` and `demodulate_one_symbol` build a `HashSet<usize>` from `pilot_indices()` **once per symbol** just to test membership inside the subcarrier loop. `OfdmParams::data_indices()` (`:100`) builds the same HashSet again on each call, and `data_indices()` is itself called per-`transmit()`/per-symbol in the floor (`wideband_lowdensity.rs:63,166,221,305`). Hashing + allocation per symbol where the pilot set is fixed for the lifetime of the mode. Cheap per element but multiplied by symbol count. — confidence: High — overlaps a likely hot-spot.

- **`OfdmEqualizer::new(...pilot_indices().to_vec()...)` per symbol (`receiver.rs:56`)** — basis: clones the pilot-index vector and constructs a fresh equalizer every symbol; `equalize()` (`equalizer.rs:35`) then allocates a full `n_bins`-length `chan_est` vec (1024/2048 complex), does pilot interpolation over the occupied band, and a full-spectrum zero-forcing `map().collect()` over **all** n_bins (not just occupied bins). The all-bins ZF division (`:64-71`) touches every bin including the ~95% that are zero/unused. — confidence: Medium — overlaps a likely hot-spot (the full-spectrum pass is the multiplier; interpolation is small).

- **Preamble correlation scan (`preamble.rs:78 scan`)** — basis: classic O(signal_len × template_len) sliding correlation — outer loop `0..(signal.len()-192)`, inner loop `0..192` recomputing `corr` AND `sig_energy` from scratch at every offset (no running/sliding energy accumulator). For a multi-second capture at 48 kHz, signal_len is hundreds of thousands of samples × 192-tap inner loop. This is the dominant **unit-cost** operation on the RX entry path and runs once per `receive_*_with_sync` call. The redundant per-offset `sig_energy` recomputation is a sliding-window that could be incremental. — confidence: High — overlaps a likely hot-spot.

- **Sample-format / channel expansion + interleave (`audio_device.rs:261-266 play; :542-547 record`)** — basis: TX pre-expands the entire buffer mono→device-channels into a new `Vec` before streaming (one pass over all samples × channels); RX callback does a `chunks_exact(channels)` de-interleave under a `Mutex` lock taken **on every callback invocation** (`:538`). The lock + length check per callback is the only per-callback cost of note, and it's light, but it is on the real-time thread. — confidence: Medium — map-only (light; flagged because it's the one thing touching the RT thread).

- **Byte↔bit expansion loops (`wideband_lowdensity.rs:56-61, 173-192`)** — basis: per-frame MSB-first bit unpacking on TX and bit→byte packing on RX, plus the `rposition` trailing-zero trim (`:153`). Linear in payload bits, runs once per frame. Cheap relative to FFT/LLR but on the hot path. — confidence: Low — map-only.

### Real-time callback budget vs per-symbol work

The CPAL callback fires ~100+×/sec; at 48 kHz with typical buffer sizes the per-callback deadline is on the order of a few ms to ~20 ms. **The design keeps DSP out of the callback:** `play_blocking_with_abort` renders nothing in the closure — it only `copy_from_slice`s pre-rendered frames (`audio_device.rs:283`) and zero-fills the tail. The capture callback only appends channel-0 samples under a mutex. So the per-symbol FFT/LLR work (the expensive regions above) does **not** compete with the callback deadline; it runs in the `transmit_multi`/`receive_multi` batch ahead of (TX) or after (RX) streaming. The real-time risk surface is therefore small and confined to: (a) the mutex lock in the input callback (`:538`), (b) the `recv_timeout`/`sleep`-based polling loops that gate playback/capture completion. No glitch-budget pressure from the DSP itself in the current architecture.

### Notes for architecture

- The dominant theme is **per-symbol reconstruction of mode-invariant state**: FFT plans, pilot HashSets, equalizer instances, and constellation alphabets are all rebuilt every symbol though they depend only on the resolved `OfdmParams` + bit-loading, which are fixed for a frame (and largely for a mode). A `Mapper`/FFT-plan/pilot-set cache hung off `OfdmParams` (or a frame-scoped context struct constructed once in `transmit_multi`/`receive_multi`) would move all of this out of the symbol loop. This is the single highest-leverage structural observation; the FFT execution and the correlation inner product are the only truly inherent costs.
- `compute_llr`'s alphabet is derived purely from the constellation enum — it could be a `const`/`once`-computed table per constellation rather than rebuilt per call. The brute-force max-log scan is fine for ≤64-QAM and is the standard approach; only the alphabet rebuild is incidental.
- `PreambleDetector::scan` recomputes signal energy per offset; a running sum (add leading sample², subtract trailing) would drop the inner loop's energy half. The correlation half is the inherent cost (a matched filter); an FFT-based correlation would change the complexity class for long captures but is a bigger redesign than the running-energy tweak.
- `OfdmEqualizer::equalize` zero-forces across **all** FFT bins; restricting the ZF pass to `subcarrier_indices()` would skip the ~95% unoccupied bins. Whether that matters depends on N and occupied-band fraction (Wide: ~98 occupied of 2048).
- These are HYPOTHESES from structure, not measurements. The Wide robustness floor runs BPSK (alphabet size 2), so its LLR cost is modest; the LLR/alphabet concentration becomes significant only once the bit-adaptive OFDM main family drives 16-/64-QAM loadings. A profiler run on a representative multi-symbol 64-QAM frame would confirm the FFT-plan and alphabet-rebuild hypotheses first.
