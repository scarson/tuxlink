---
run_schema_version: 1
run_id: 2026-06-04T14-24-m1-ofdm-phy
date: 2026-06-04T14:24:00Z
scope: "M1 — tuxmodem-phy OFDM PHY + real-time audio (entire crate src, excl. bin/)"
methodology:
  skill: performance-audit (within performance-audit-cycle)
  plugin_version: superpowers-plus@0.2.0
dispatch:
  model_requested: "latest-opus (Claude Code Agent subagents, model=opus)"
  reasoning_effort: "default (harness exposes no reasoning-effort knob)"
  overridden_by_user: false
stack:
  - { ecosystem: crates, framework: rustfft, version: "6" }
  - { ecosystem: crates, framework: num-complex, version: "0.4" }
  - { ecosystem: crates, framework: hound, version: "3" }
  - { ecosystem: crates, framework: cpal, version: "(audio I/O)" }
  - { ecosystem: crates, framework: rust, version: "edition 2021 / MSRV 1.75" }
currency_briefs:
  - { framework: rustfft, researched_on: null, status: "version-index (shipped rust.md) — planner-reuse idiom is stable, not version-churn" }
lanes_run: [algorithmic, memory, data-access, concurrency, idiom-currency, cost-map]
lanes_skipped:
  payload-startup: "tuxmodem-phy is a DSP library, not a payload/startup/bundle surface"
  dynamic: "deferred to Phase 4 / the remediation plan's verification gate — crate builds + tests pass, so P1 is directly benchmarkable"
finding_counts:
  by_impact: { critical: 3, major: 6, minor: 4 }
  by_lane: { algorithmic: 7, memory: 8, data-access: 6, concurrency: 1, idiom-currency: 3, cost-map: 6 }
  suspected_bugs: 3
regression:
  prev_run_id: null
  new: 13
  persisting: 0
  resolved: 0
---
# Performance Audit — M1: tuxmodem-phy (OFDM PHY + real-time audio)

**Date:** 2026-06-04 14:24   **Scope:** `tuxmodem/crates/tuxmodem-phy/src` (all `.rs` excl. `bin/`)
**Stack:** Rust 2021 / MSRV 1.75 · rustfft@6 · num-complex@0.4 · hound@3 · cpal
**Currency brief:** shipped rust version-index; rustfft planner-reuse + num-complex `norm_sqr` idioms are stable (Strong-static, not version-churn)
**Lanes run:** algorithmic, memory, data-access, concurrency, idiom-currency, cost-map (payload-startup skipped — DSP library; dynamic deferred to verification gate)
**Regression vs none (first run for this scope):** 13 new, 0 persisting, 0 resolved
**Method note:** lanes were dispatched **blind** (load context only, no prior hot-path map) as a faithful test of the skill. They independently reproduced the entire hot-path map from this repo's 5-round scope review and added several findings — see `dev/perf-audit-skill-feedback.md`.

## Executive summary

One architectural root cause dominates: **frame-invariant DSP state is reconstructed at per-symbol (and per-subcarrier) cadence inside the hot loops** instead of being built once per transmission. It produces the single most-agreed finding in the run — the per-symbol `FftPlanner` rebuild, flagged CRITICAL by **5 of 6 lanes** — plus a cluster of per-symbol object/allocation rebuilds. Two independent issues sit alongside it: an O(N·M) preamble correlation that recomputes window energy per offset (CRITICAL), and a real-time audio-callback `Mutex` held across a copy loop (MAJOR, with a latent RT-thread panic).

## Critical Findings

### P1. Per-symbol `FftPlanner` construction + FFT re-plan (RX and TX)
**Lanes:** algorithmic, memory, data-access, idiom-currency, cost-map (5/6)   **Location:** `ofdm_main/receiver.rs:46-47` (RX), `ofdm_main/transmitter.rs:80-81` (TX)
**Fingerprint:** `idiom-currency:ofdm_main/receiver.rs:demodulate_one_symbol:per-symbol-fft-planner` (+ `…/transmitter.rs:modulate_one_symbol:per-symbol-ifft-planner`)   **Status:** new
**Problem:** `FftPlanner::<f32>::new()` + `plan_fft_*()` run inside `demodulate_one_symbol`/`modulate_one_symbol`, driven once per OFDM symbol by `receive_multi`/`transmit_multi` (`robustness_floor/wideband_lowdensity.rs:334-338, 233-236`). The plan is a pure function of the frame-invariant `fft_size`; rebuilding it allocates twiddle tables + a radix sub-plan `Arc` graph (KB-scale) and re-runs algorithm selection every symbol, defeating rustfft's internal plan cache.
**Impact:** reachability = the live RX/TX path; frequency ≈ 19–38 symbols/s real-time (the crate's own `multi_roundtrip_1000_byte_payload` test exercises ~111–114 planner builds for one 1 KB payload); per-occurrence = O(fft_size) alloc + planning. Dominant avoidable cost on the DSP path.
**Confidence:** Strong-static (directly benchmarkable — see Measurability)   **On cost map:** yes (region #1)
**Effort:** Contained — build the `Arc<dyn Fft>` once (struct field set in `new()`, or a size-keyed `OnceLock` cache) and hoist `OfdmReceiver::new`/`OfdmTransmitter::new` out of the symbol loop.
**Verification plan:** micro-benchmark `demodulate_one_symbol` cached-vs-rebuilt planner (criterion or timed loop); correctness guard = existing `multi_roundtrip_*` roundtrip tests must still pass bit-identical.

### P2. Preamble `scan` is O(N·M) with a redundant per-offset window-energy recompute
**Lanes:** algorithmic (CRITICAL), cost-map (High)   **Location:** `sync/preamble.rs:88-101`
**Fingerprint:** `algorithmic:sync/preamble.rs:scan:on-m-energy-recompute`   **Status:** new
**Problem:** the sliding cross-correlation recomputes the full windowed signal energy at every slide offset instead of maintaining a running two-term (add-leading/drop-trailing) sum. Correlation itself is O(N·M); the energy half is needlessly another O(N·M).
**Impact:** RX-entry path over whole captures (~10⁶ samples) × template length (~192) ⇒ ~10⁸ wasted multiply-adds per acquisition. Reachability high (every receive), frequency once per frame acquisition, per-occurrence large.
**Confidence:** Strong-static   **On cost map:** yes (region #6)
**Effort:** Localized — running-sum energy accumulator.
**Verification plan:** benchmark `scan` on a representative capture; correctness guard = `preamble_roundtrip_*` tests + assert detected offset unchanged (note SB1 below — fix the off-by-one in the same task).

### P3. Per-symbol rebuild of frame-invariant DSP objects (receiver/transmitter, equalizer, pilot `HashSet`, `chan_est`/index Vecs)
**Lanes:** memory (CRITICAL), algorithmic (MAJOR), data-access (MAJOR), cost-map   **Location:** `robustness_floor/wideband_lowdensity.rs:173-176,233-235`; `ofdm_main/receiver.rs:55-61`; `transmitter.rs:55-56`; `ofdm_main/equalizer.rs:37,64-71`
**Fingerprint:** `memory:robustness_floor/wideband_lowdensity.rs:receive_multi:per-symbol-object-rebuild`   **Status:** new
**Problem:** `OfdmReceiver`/`OfdmTransmitter` are recreated per symbol; `OfdmEqualizer::new(pilot_indices().to_vec(), …)` clones the pilot vec and allocates a fresh 1024/2048-complex `chan_est` (8–16 KB) per symbol; the pilot-membership `HashSet` (~25 inserts) is rebuilt per symbol both sides; `bits_per_subcarrier` `vec![1;n]` per symbol. All invariant for an immutable `OfdmParams`.
**Impact:** symbol-cadence allocation + hashing on the live path; multiplies by symbol count. Aggregate is large (memory lane ranked it Critical).
**Confidence:** Strong-static   **On cost map:** yes (regions #4, #5)
**Effort:** Contained — same frame-scoped-context fix as P1 (build once per frame; replace pilot `HashSet` with a precomputed mask / binary-search, allocation-free).
**Verification plan:** alloc-count `receive_multi`/`transmit_multi` before/after; correctness guard = roundtrip tests bit-identical.

## Major Findings

### P4. Constellation `alphabet()` + `Mapper` rebuilt per subcarrier, per symbol (in LLR)
**Lanes:** memory, algorithmic, data-access, cost-map   **Location:** `constellations.rs:142,164-177` via `ofdm_main/receiver.rs:78,83`; TX mirror `transmitter.rs:72`
**Fingerprint:** `memory:constellations.rs:compute_llr:per-subcarrier-alphabet-rebuild`   **Status:** new
**Problem:** `compute_llr` calls `alphabet()`, which allocates a `Vec` of `2^bps` points and calls `map()` (itself `.collect()`ing a throwaway `Vec`) per point — rebuilding a constant pure table for every data subcarrier of every symbol (~18 small allocs/subcarrier at 16-QAM, ×hundreds of thousands of subcarriers). Cheap at BPSK, grows sharply with QAM order.
**Confidence:** Strong-static   **On cost map:** yes (region #3)   **Effort:** Contained — precompute the alphabet/`Mapper` once per (mode, bits-per-subcarrier) and pass by reference into `compute_llr`.
**Verification plan:** alloc-count + bench `compute_llr`; correctness guard = constellation LLR unit tests (`constellations_llr`) unchanged.

### P5. Real-time audio capture callback holds `std::sync::Mutex` across the de-interleave copy; consumer busy-polls the same lock
**Lanes:** concurrency (MAJOR), data-access (MAJOR)   **Location:** `audio_device.rs:538` (lock), `:542-548` (copy under guard), `:572-588` (consumer poll)
**Fingerprint:** `concurrency:audio_device.rs:input-callback:rt-mutex-across-copy`   **Status:** new
**Problem:** the cpal RT input callback (~100+×/s, hard deadline) takes a blocking `Mutex` held across the whole per-callback sample copy; the consumer contends the same lock by polling `guard.len()`. Expected case is a cheap uncontended CAS, but the tail event — a park / priority-inversion stall on the RT thread — is a missed audio deadline → capture overrun → corrupted demod input → frame loss → retransmission. The output callback (`:279-299`) is already lock-free/alloc-free, so the input path doesn't meet the project's own bar.
**Confidence:** Strong-static   **On cost map:** RT risk surface (cost-map called this out explicitly)   **Effort:** Contained — SPSC ring (`rtrb`/`ringbuf`, weigh the dep) or no-dep `try_lock` + callback-owned staging + an `AtomicUsize` progress counter so the consumer never takes the callback's lock.
**Verification plan:** stress the capture path under load and assert no overruns; correctness guard = strict single-producer/single-consumer invariant + existing capture tests. **Fix removes SB3 (RT-thread panic).**

### P6. Per-symbol intermediate `Vec` churn (RX body + un-presized `all_llr`; TX ~4 full-length allocations)
**Lanes:** memory   **Location:** `ofdm_main/receiver.rs:42-45,62,84`; `transmitter.rs:82,85,88-93`
**Fingerprint:** `memory:ofdm_main/transmitter.rs:modulate_one_symbol:per-symbol-vec-churn`   **Status:** new
**Problem:** RX collects a full complex-body `Vec` + an un-presized `all_llr` (doubling reallocs despite a known final length); TX does ~4 full-length `Vec` allocations (clone + scale-collect + CP `to_vec` + real-cast collect) where one in-place IFFT + a presized output buffer would do.
**Confidence:** Strong-static   **Effort:** Contained — presize + reuse scratch buffers (pairs with the frame-context fix).   **Verification plan:** alloc-count; roundtrip tests unchanged.

### P7. narrow-FSK: planner per `receive` call + per-symbol next-pow2 buffer realloc + `.norm()` where `norm_sqr()` suffices
**Lanes:** memory, idiom-currency   **Location:** `robustness_floor/narrow_fsk.rs:83-94` (planner/buffer), `:102` (`.norm()`)
**Fingerprint:** `idiom-currency:robustness_floor/narrow_fsk.rs:receive:per-call-planner-and-norm`   **Status:** new
**Problem:** a fresh `FftPlanner` per `receive` (planner correctly hoisted above the inner symbol loop, so per-call not per-symbol — lower frequency than P1); each symbol collects a `buf` then `resize`s to next-pow2, reallocating/copying a ~64 KB complex `Vec` per symbol; `:102` uses `.norm()` (sqrt) for a pure magnitude argmax where `norm_sqr()` is the sqrt-free, argmax-preserving fast path used elsewhere in the crate.
**Confidence:** Strong-static   **Effort:** Localized (norm_sqr) + Contained (planner/buffer hoist).   **Verification plan:** bench narrow-FSK receive; correctness guard = FSK roundtrip tests + assert argmax unchanged.

### P8. `read_wav` loads the entire transmission into one `Vec<f32>` (no streaming bound on peak memory)
**Lanes:** data-access   **Location:** `audio_io.rs:71`
**Fingerprint:** `data-access:audio_io.rs:read_wav:whole-file-buffer`   **Status:** new
**Problem:** decode is inherently symbol-windowable, but the API is `&[f32]`-over-full-buffer, so peak memory scales with transmission length. Architectural ceiling rather than a hot-loop cost.
**Confidence:** Strong-static   **Effort:** Cross-cutting (streaming API change) — flagged as a design item; schedule but sequence after the localized wins.   **Verification plan:** peak-RSS over a long capture; correctness guard = decode output identical.

## Minor Findings

### P9. `OfdmParams::data_indices()` rebuilds a `HashSet`+`Vec` every call (per-symbol on TX)
**Lanes:** data-access, memory, algorithmic   **Location:** `ofdm_main/ofdm_params.rs:100-108` (callers `wideband_lowdensity.rs:63,165`)   **Fingerprint:** `algorithmic:ofdm_main/ofdm_params.rs:data_indices:per-call-rebuild`   **Status:** new
**Effort:** Localized — memoize on the struct in `for_mode`.

### P10. Playback pre-expands mono→N-channel into a second full `Vec`
**Lanes:** data-access   **Location:** `audio_device.rs:261-266`   **Fingerprint:** `data-access:audio_device.rs:play:mono-expand-copy`   **Status:** new   **Effort:** Localized — expand inside the callback (zero-copy for mono).

### P11. FSK tone-bin indices recomputed per symbol
**Lanes:** algorithmic   **Location:** `robustness_floor/narrow_fsk.rs:99-102`   **Fingerprint:** `algorithmic:robustness_floor/narrow_fsk.rs:receive:per-symbol-bin-indices`   **Status:** new   **Effort:** Localized.

### P12. Preamble template / Zadoff-Chu sequence regenerated per frame
**Lanes:** memory   **Location:** `sync/preamble.rs`, `robustness_floor/wideband_lowdensity.rs:121,274`   **Fingerprint:** `memory:sync/preamble.rs:template:per-frame-regen`   **Status:** new   **Effort:** Localized — cache (frame-scoped or `OnceLock`).

## Cross-Cutting Themes

**Root cause (P1, P3, P4, P6, P9, P11, P12): frame-invariant state rebuilt at per-symbol/per-subcarrier cadence.** A single architectural fix collapses most of them: introduce a **frame-scoped `OfdmContext`** built once per transmission in `transmit_multi`/`receive_multi` (and/or cached on `OfdmParams`) holding the `Arc<dyn Fft>` plan, the equalizer, the pilot mask, the per-subcarrier `Mapper`s/alphabets, and the index vectors; the symbol loops borrow it. This is the highest-leverage change in M1. P2 (preamble running-sum), P5 (RT-callback SPSC ring), P8 (streaming `read_wav`) are independent.

## Measurability

The crate **builds clean and its tests pass** (verified: `cargo test -p tuxmodem-fec` green; `tuxmodem-phy` builds), so the headline findings are directly measurable here — no production metrics/traces exist (it's a library), but P1/P3/P4/P6 are benchmarkable via criterion or a timed loop over `demodulate_one_symbol`/`modulate_one_symbol`, and alloc-countable. The remediation plan's verification gate should capture a before baseline on P1 first (it's the cheapest, highest-signal measurement). No finding here is unfalsifiable in this environment (unlike the hardware-deferred slices M3/R1/R2).

## Execution Cost Map (architectural awareness, not a to-do list)

Per the cost-map lane: `tuxmodem-phy` is **batch/offline DSP feeding a thin real-time callback** — the heavy per-symbol work runs in `transmit_multi`/`receive_multi` ahead-of/after streaming, NOT inside the ~ms cpal callback, so the per-symbol allocations do **not** compete with the audio deadline (this refines the pre-audit assumption that the DSP was on the RT path). Time concentrates in: (1) per-symbol FFT planner construction [P1]; (2) the FFT itself, O(N log N), inherent [map-only]; (3) per-subcarrier alphabet/Mapper rebuild [P4]; (4) per-symbol pilot HashSet [P3]; (5) per-symbol equalizer ZF across all bins incl. ~95% unoccupied [P3]; (6) preamble O(N·M) correlation [P2]. The RT risk surface is just the input-callback mutex [P5] and the poll loops.

## Suspected Bugs (for follow-up — NOT addressed here)
> Correctness leads noticed during the audit. Run `bug-hunt-cycle`; kickoff at
> `docs/perf-audits/2026-06-04-m1-ofdm-phy-bug-hunt-kickoff.md`.

### SB1. Preamble scan loop bound skips the last valid alignment offset
**Location:** `sync/preamble.rs:88`   **What looks wrong:** `0..(signal.len()-n)` is exclusive of the final valid offset (should be inclusive, `..=`).   **Why suspected:** masked by the ±2-sample tolerance in tests; could cost a fraction of a dB in sync at the edge. Fix alongside P2 (same function).

### SB2. Gardner symbol-timing normaliser includes leading silence in `mean_energy`
**Location:** `sync/symbol_timing.rs:43-44`   **What looks wrong:** `mean_energy` averages over a window including leading silence, biasing the normaliser low.   **Why suspected:** behavioral (timing-accuracy), not perf; could affect acquisition robustness.

### SB3. RT audio thread can panic on mutex poisoning
**Location:** `audio_device.rs:538`   **What looks wrong:** `acc_cb.lock().unwrap()` on the RT callback panics if a consumer panicked while holding the lock → next callback unwrap → stream abort.   **Why suspected:** reliability on the RT path; the P5 SPSC fix removes it.
