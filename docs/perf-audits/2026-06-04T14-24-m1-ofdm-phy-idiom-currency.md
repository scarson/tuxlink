# Performance Audit — `tuxmodem-phy` — Dimension: Framework/Library-Idiom Currency

- **Agent:** glade-knoll-shoal
- **Date:** 2026-06-04
- **Scope:** `/home/user/tuxlink/tuxmodem/crates/tuxmodem-phy/src` (all `.rs` except `src/bin/`)
- **Stack:** Rust 2021, MSRV 1.75; rustfft@6, num-complex@0.4 (confirmed via `tuxmodem/Cargo.toml` workspace deps)
- **Index consulted:** `/home/user/tuxlink/.claude/skills/performance-audit/version-indexes/rust.md` (covered_through Rust 1.96)
- **Dimension:** ONLY framework/library-idiom currency — superseded/deprecated patterns still in use, and fast-path APIs the library provides but the code skips.

Files read in full: `ofdm_main/receiver.rs`, `ofdm_main/transmitter.rs`, `ofdm_main/equalizer.rs`, `robustness_floor/narrow_fsk.rs`, `robustness_floor/wideband_lowdensity.rs`, `constellations.rs`, `subcarrier_snr.rs`, `sync/preamble.rs`, `sync/frame_sync.rs`, `sync/symbol_timing.rs`, `sync/carrier_offset.rs`. `audio_device.rs` grepped for FFT/transcendental idioms (no FFT in the CPAL callback path).

---

## Findings (ranked)

### [CRITICAL] OFDM receiver rebuilds `FftPlanner` and re-plans the FFT on every demodulated symbol

**Location:** `ofdm_main/receiver.rs:46-47` (inside `demodulate_one_symbol`), reached per-symbol from `robustness_floor/wideband_lowdensity.rs:173-176` (`decode_symbol_bytes`) which is called once per symbol by `receive` / `receive_multi` (`wideband_lowdensity.rs:313, 336`).

**Problem:** `demodulate_one_symbol` constructs a fresh `FftPlanner::<f32>::new()` and calls `planner.plan_fft_forward(p.fft_size())` *inside the per-symbol method*. The rustfft@6 idiomatic fast path is to build the planner and obtain the `Arc<dyn Fft<T>>` exactly ONCE, then reuse the `Arc<dyn Fft>` across all calls. `plan_fft_*` performs algorithm selection (Radix-4 / mixed-radix / Bluestein decision for the given size), precomputes twiddle-factor tables, and allocates — all of which is pure overhead that recurs on every symbol. For multi-symbol receive this is N rebuilds: a 1000-byte payload is ~111 symbols (`wideband_lowdensity.rs:579-583` test asserts this), so 111 planner constructions + 111 twiddle-table computations for a single decode, where one cached plan would suffice.

**Impact:** reachability = every OFDM decode path (`receive`, `receive_multi`, `receive_with_sync`, `receive_multi_with_sync`); frequency = once per symbol (~19–38 symbols/sec sustained, or N-per-call in batch decode); per-occurrence cost = planner alloc + algorithm dispatch + twiddle-table build for size 1024/2048, which is on the order of the FFT compute itself or larger for the first plan of a size. The twiddle tables for a 2048-point FFT are not free to recompute each symbol. On a real-time HF modem this is squarely in the hot path.

**Confidence:** Strong-static (idiom-currency; inherits index freshness — rustfft@6 planner-reuse is a stable, current idiom, not version-churn-sensitive). The anti-pattern is directly visible in source.

**Effort:** Contained (+low). The struct already holds `params: &'a OfdmParams`. Move the planned FFT into the struct: store an `Arc<dyn Fft<f32>>` built in `OfdmReceiver::new` (or lazily via a cached field), reuse it in `demodulate_one_symbol`. Caller already constructs `OfdmReceiver::new(&self.params)` once per symbol in `decode_symbol_bytes` (`wideband_lowdensity.rs:175`) — to capture the full win, also hoist receiver construction out of the per-symbol loop in `receive_multi` (`wideband_lowdensity.rs:334-338`) so one receiver+plan serves all symbols. Even without hoisting the caller, caching inside `demodulate_one_symbol` is impossible (no `&mut self`); the clean fix is the struct-field plan built in `new`, plus lifting `OfdmReceiver::new` above the loop.

**Verification plan:** Criterion bench `receive_multi` on a fixed ≥100-symbol payload, before/after. Expected: large reduction in per-symbol time dominated by removing the repeated `plan_fft_forward`. Correctness guard: existing round-trip tests (`multi_roundtrip_*` in `wideband_lowdensity.rs`) must stay green — the FFT output is identical regardless of how many times the plan was built. Index/version basis: rustfft@6 planner-reuse idiom (audit prompt's explicit fast-path criterion; `FftPlanner` caches algorithm instances internally only across calls *on the same planner instance*, so a per-call `new()` defeats that cache by construction).

---

### [CRITICAL] OFDM transmitter rebuilds `FftPlanner` and re-plans the inverse FFT on every modulated symbol

**Location:** `ofdm_main/transmitter.rs:80-81` (inside `modulate_one_symbol`), reached per-symbol from `wideband_lowdensity.rs:71-72` (`transmit`) and the multi-symbol loop `wideband_lowdensity.rs:233-236` (`transmit_multi` calls `self.transmit(chunk)` per symbol).

**Problem:** Same anti-idiom as the receiver, inverse direction. `modulate_one_symbol` does `FftPlanner::<f32>::new()` + `planner.plan_fft_inverse(p.fft_size())` *per symbol*. `transmit_multi` constructs a new `OfdmTransmitter` (`wideband_lowdensity.rs:71`) and thus a new planner + inverse plan for every chunk in the `stream.chunks(cap)` loop. 1000-byte payload ⇒ 111 inverse-FFT plans built where one would do.

**Impact:** every TX path; once per symbol; per-occurrence cost = planner alloc + inverse-algorithm selection + twiddle-table build for size 1024/2048. Symmetric to the receiver finding. TX may be slightly less latency-critical than the RX real-time decode, but batch `transmit_multi` of a long frame pays the full N× penalty up front.

**Confidence:** Strong-static (idiom-currency, inherits index freshness — same stable rustfft@6 idiom).

**Effort:** Contained (+low). Store `Arc<dyn Fft<f32>>` (inverse) as a struct field built in `OfdmTransmitter::new`; hoist `OfdmTransmitter::new(&self.params)` out of the `transmit_multi` chunk loop (currently re-created inside `transmit` per chunk).

**Verification plan:** Criterion bench `transmit_multi` on a ≥100-symbol payload, before/after; expect per-symbol time drop from eliminating repeated `plan_fft_inverse`. Correctness guard: `bare_transmit_still_works_unchanged`, `multi_roundtrip_*` tests must remain bit-identical (`wideband_lowdensity.rs:501, 521`). Basis: rustfft@6 planner-reuse idiom (same as above).

---

### [MAJOR] narrow-FSK receiver rebuilds `FftPlanner` per `receive` call (and uses `.norm()` for a pure magnitude comparison)

**Location:** `robustness_floor/narrow_fsk.rs:83-85` (planner) and `narrow_fsk.rs:102` (`.norm()`).

**Problem (two sub-issues, same hot loop):**
1. **Planner:** `receive` builds `FftPlanner::<f32>::new()` + `plan_fft_forward(fft_size)` once per `receive` invocation. This is better than the OFDM per-symbol case — the plan is hoisted above the `for sym_idx` loop (line 88), so within one `receive` it is reused across symbols. But `receive` itself re-plans on every call; for a streaming/real-time FSK floor invoked repeatedly, the planner is still rebuilt per call rather than cached on `NarrowFskFloor`. `NarrowFskFloor` is a zero-sized stateless struct (`narrow_fsk.rs:24`), so today there is no field to cache into — but `fft_size = sps.next_power_of_two()` is a compile-time-derivable constant (`sps` is fixed by pinned params), so a cached plan is feasible.
2. **`.norm()` vs `.norm_sqr()`:** line 102 computes `buf[bin].norm()` purely to find the max-magnitude bin across the 8 candidate tones (`if m > best_mag`). `Complex::norm()` is `(re² + im²).sqrt()`; the `sqrt` is wasted when you only compare magnitudes — `norm_sqr()` (no sqrt) preserves the argmax ordering exactly. The rest of the crate already uses `norm_sqr()` for this exact reason (equalizer.rs:68, subcarrier_snr.rs:42,44, constellations.rs:148). narrow_fsk is the lone holdout.

**Impact:** Sub-issue 2 (the `.norm()`) is small in absolute terms — only 8 bins per symbol — but it is a clear, free, codebase-consistent idiom fix with zero correctness risk (argmax over magnitude == argmax over squared-magnitude for nonnegative values). Sub-issue 1 (per-call planner) matters only if the FSK floor is invoked in a per-frame streaming loop; the floor is the crowded-band fallback mode, so frequency is conditional. Combined: MAJOR for the planner if the floor is streamed; the `.norm()` alone is MINOR.

**Confidence:** Strong-static. The `.norm()`→`norm_sqr()` substitution is provably ordering-preserving. The planner reuse is the same rustfft@6 idiom as above.

**Effort:** Localized (+low). `.norm()` → `.norm_sqr()` is a one-token edit (and rename `best_mag` semantics to squared, no other change). Planner caching requires giving `NarrowFskFloor` a field (or a `OnceCell`/lazy plan) — Contained.

**Verification plan:** `.norm()` change: assert FSK round-trip tests still decode identically (argmax unchanged). Planner: bench `receive` called M times in a loop, before/after caching. Basis: num-complex@0.4 `norm_sqr()` avoids the `sqrt` (audit prompt's explicit norm/abs idiom); rustfft@6 planner reuse.

---

### [MINOR] `PreambleDetector` / `CfoEstimator` use direct time-domain correlation rather than an FFT-based fast path — but this is not an idiom-currency defect

**Location:** `sync/preamble.rs:78-101` (`scan`), `sync/carrier_offset.rs:22-29`.

**Problem (recorded for completeness, NOT a strong finding in this dimension):** `PreambleDetector::scan` is an O(signal_len × template_len) sliding dot-product (192-tap template). An FFT-based cross-correlation would be asymptotically faster for long captures. However, this is an *algorithmic-complexity* observation, not a *library-idiom-currency* one — the code is not using a deprecated/superseded rustfft or num-complex API; it simply doesn't use rustfft here at all. The template-energy and per-shift `sig_energy` are recomputed from scratch each shift (a sliding-window energy update would be O(1) per shift), but again that is algorithmic, not idiom-currency. Flagging only so the algorithmic/data-access lanes can pick it up; out of scope for my dimension. `CfoEstimator` is a clean, correct stateless reduction with no idiom issue.

**Confidence:** Heuristic (and explicitly deferred to other lanes).

**Effort:** n/a for this dimension.

---

## Items examined and found CLEAN for this dimension

- **`norm_sqr()` discipline:** `equalizer.rs:68`, `subcarrier_snr.rs:42,44`, `constellations.rs:148` all correctly use `norm_sqr()` for magnitude/distance comparisons and zero-forcing denominators — idiomatically current num-complex@0.4. No `.norm()`-where-`norm_sqr` misuse except narrow_fsk:102.
- **Transcendentals:** `preamble.rs:124-126` (Zadoff-Chu `cos`/`sin`) and `narrow_fsk.rs:71` (tone `sin`) compute trig directly. These are generated once (preamble template built in `PreambleDetector::new`, line 55) or are inherent to FSK tone synthesis; no `.exp()` misuse, no place where a precomputed twiddle table is the obvious idiom over what's written. No finding.
- **Slice/iterator idioms:** the bit/byte packing loops (`narrow_fsk.rs:115`, `wideband_lowdensity.rs:182`, `constellations.rs`) use `chunks(8)` / `chunks(2/4/6)` appropriately. No `chunks_exact` fast-path win of note — the code already length-guards (`if chunk.len() < 8 { break }`) so `chunks` is the correct choice given partial-chunk handling; `chunks_exact` would change semantics. No finding.
- **`audio_device.rs` CPAL callback:** grepped for FFT/planner/transcendental usage — none present. The real-time callback does not run an FFT or per-sample transcendental in this crate, so the "CPAL callback ~100+×/sec" load does not intersect a per-callback planner-rebuild. No finding in this dimension.
- **Build-config (`Cargo.toml`):** out of this dimension's scope (belongs to the payload/startup or build lane). Noted only: the index's top recommendation is build-config (`lto`, `codegen-units=1`, `target-cpu=native`) — not checked here per scope, flag for the build-config lane.

---

## Suspected Bugs (for follow-up)

None. (The `.norm()` at narrow_fsk.rs:102 is a perf idiom, not a correctness bug — argmax is preserved. No correctness defects observed in the read of these files.)
