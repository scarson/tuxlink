# Performance Audit — tuxmodem-phy, dimension: Memory & Allocation

**Agent:** glade-knoll-shoal
**Scope:** `tuxmodem/crates/tuxmodem-phy/src` (all `.rs` except `src/bin/`)
**Dimension:** allocation on hot paths, large discarded intermediates, avoidable clones, re-creation of reusable objects, unbounded growth, whole-resource reads.
**Date:** 2026-06-04

Findings ranked by aggregate cost. Frequency model per brief: hundreds–thousands of OFDM symbols per transmission; ~70+ data subcarriers per symbol; per-subcarrier LLR/map work runs hundreds–low-thousands of times/sec; CPAL callback ~100+×/sec under a hard real-time deadline.

---

### [CRITICAL] FFT planner is constructed (and the FFT re-planned) on every single OFDM symbol in both TX and RX

**Location:** `ofdm_main/receiver.rs:46-47`; `ofdm_main/transmitter.rs:80-81`

**Problem:** `demodulate_one_symbol` and `modulate_one_symbol` each do:
```rust
let mut planner = FftPlanner::<f32>::new();
let fft = planner.plan_fft_forward(p.fft_size());   // RX
let ifft = planner.plan_fft_inverse(p.fft_size());  // TX
```
A fresh `FftPlanner` is allocated and a fresh FFT plan is built *per symbol*. rustfft planning allocates the twiddle-factor tables and the algorithm-selection scratch (a 1024/2048-point radix plan with twiddles is on the order of several KB to tens of KB of `Vec<Complex<f32>>` plus the recursive sub-plan `Arc`s), and rustfft explicitly documents that planning is the expensive step and `Arc<dyn Fft>` instances are meant to be created once and reused. Here every symbol throws the plan away. `OfdmReceiver`/`OfdmTransmitter` are reconstructed per symbol too (see below), so there is no surviving home for the plan as written — but the natural fix is to cache the `Arc<dyn Fft>` in a longer-lived struct.

**Impact:** reachability = every TX and every RX symbol (the core DSP loop); frequency = hundreds–thousands of symbols per transmission, and `receive_multi`/`transmit_multi` loop this once per symbol (`wideband_lowdensity.rs:233-236`, `334-337`). Per-occurrence: one planner alloc + full twiddle-table allocation + radix sub-plan `Arc` graph, all freed immediately. This is the single largest avoidable allocation on the DSP path — easily multiple KB allocated-and-freed per symbol, ×thousands of symbols.

**Confidence:** Strong-static (rustfft planning cost + reuse guidance is documented and well-known; the per-symbol re-plan is unambiguous in source).

**Effort:** Contained (+low). Cache the planned `Arc<dyn Fft>` in the receiver/transmitter struct, or in a session object that outlives the symbol loop. Requires lifting `OfdmReceiver`/`OfdmTransmitter` out of the per-symbol decode path (see CRITICAL #2) so the plan has somewhere to live.

**Verification plan:** `dhat` or a `#[global_allocator]` counting shim over a `transmit_multi`/`receive_multi` of a ~100-symbol payload before/after; expect per-symbol allocation count to drop by the planner+plan graph (dozens of allocations/symbol). Correctness guard: round-trip tests in `wideband_lowdensity.rs` already assert bit-exact recovery — they must still pass.

---

### [CRITICAL] `OfdmReceiver`/`OfdmTransmitter`, the equalizer, the pilot `HashSet`, and per-subcarrier `Mapper`s are all rebuilt per symbol and per subcarrier

**Location:** `wideband_lowdensity.rs:174-176` (rx per symbol); `receiver.rs:56`, `60-61`, `78`; `transmitter.rs:55-56`, `72`

**Problem:** A cascade of reconstructed reusable objects in the inner DSP loop:

1. `decode_symbol_bytes` builds `OfdmReceiver::new(&self.params)` **per symbol** (`wideband_lowdensity.rs:175`); `receive_multi` calls `decode_symbol_bytes` once per symbol (`:313`, `:336`). `bits_per_subcarrier()` (`:48-50`, `vec![1; …]` of ~575 entries for Wide) is also rebuilt per symbol at `:174`.
2. Inside `demodulate_one_symbol`, the equalizer is rebuilt every symbol with `OfdmEqualizer::new(p.pilot_indices().to_vec(), …)` (`receiver.rs:56`) — note the `.to_vec()` clones the pilot index vector each time.
3. The pilot-membership `HashSet` is rebuilt every symbol on both sides (`receiver.rs:60-61`, `transmitter.rs:55-56`) — a fresh `HashSet<usize>` allocation + ~140 inserts (Wide pilots) per symbol, with default SipHash.
4. A fresh `Mapper::new(constellation)` is constructed **per data subcarrier** (`receiver.rs:78`, `transmitter.rs:72`) — ~430 data subcarriers/symbol for Wide. `Mapper` is a one-field copy type so the struct itself is cheap, but it gates the per-subcarrier alphabet rebuild (next finding).

**Impact:** reachability = every symbol / every data subcarrier on both TX and RX. Frequency: pilot-`HashSet` and equalizer rebuilds run per symbol (thousands/transmission); `Mapper` construction runs per data subcarrier (hundreds of thousands–millions/transmission). Per-occurrence: the `HashSet` alloc + hash inserts and the `pilot_indices().to_vec()` clone are the material allocations; both are pure overhead since `params` is immutable for the whole session. The pilot set could be computed once and the equalizer/receiver bound once per session.

**Confidence:** Strong-static.

**Effort:** Contained. Hoist `OfdmReceiver`/`OfdmTransmitter`/`OfdmEqualizer`/pilot-`HashSet`/`bits_per_subcarrier` to session-scoped state built once from `OfdmParams`. `OfdmParams` already has the data to precompute a pilot bitset or a `data_indices` list once.

**Verification plan:** allocation-count diff over a multi-symbol round-trip; the `HashSet` + `to_vec` allocations per symbol should disappear. Existing round-trip tests guard correctness.

---

### [MAJOR] Constellation alphabet (`Vec<(usize, Complex<f32>)>`) is rebuilt — with nested `map()` allocations — on every LLR call, i.e. per data subcarrier

**Location:** `constellations.rs:164-177` (`alphabet`), called from `compute_llr` `:142`; `compute_llr` called per-subcarrier from `receiver.rs:83`

**Problem:** `compute_llr` calls `self.alphabet()` which allocates a `Vec<(usize, Complex<f32>)>` of size `2^bps` (up to 64 entries for 64-QAM) and, for **each** of those entries, calls `self.map(&bits)` — and `map` itself `.collect()`s a fresh `Vec<Complex<f32>>` (`:52-106`) just to read `sym[0]`. So one `compute_llr` call for one subcarrier allocates: the alphabet `Vec` + `2^bps` throwaway single-element `Vec`s from `map` + the `bits` scratch `Vec` (`:167`). In the RX path, `compute_llr` is called once **per data subcarrier per symbol** with a single-symbol slice (`receiver.rs:83`, `&[equalized[sc]]` — itself a 1-element borrow that's fine, but the alphabet rebuild behind it is not).

**Impact:** reachability = every data subcarrier on RX (the LLR demod is the per-subcarrier hot path); frequency = hundreds of thousands–millions of calls per transmission for QAM modes. Per-occurrence for 16-QAM: 1 alphabet `Vec` (16×) + 16 single-element `map` `Vec`s + 1 `bits` `Vec` = ~18 allocations, all discarded — to produce an alphabet that is **constant** for the constellation. For BPSK floor (the default Wide floor) bps=1 so 2-entry alphabet, still 2+ allocs/subcarrier × ~430 subcarriers × thousands of symbols.

Note also `hard_demap` for QAM (`:123-129`) routes through `compute_llr`, inheriting the same per-call alphabet rebuild.

**Confidence:** Strong-static.

**Effort:** Contained (+low). Precompute the four constellation alphabets once (const tables or `OnceLock`), keyed by constellation; `compute_llr` borrows the slice. Eliminates all per-subcarrier alphabet allocation.

**Verification plan:** alloc-count over a QAM-mode RX of a multi-symbol frame; expect ~`2^bps`+2 allocations/subcarrier to vanish. LLR-sign correctness guarded by existing demap/round-trip tests.

---

### [MAJOR] RX promotes the symbol body to `Vec<Complex<f32>>` and TX clones `freq_bins` + allocates two more time-domain `Vec`s, per symbol

**Location:** `receiver.rs:42-45`, `:62` (`all_llr` un-presized), `:84`; `transmitter.rs:82`, `:85`, `:88-90`, `:93`

**Problem:** Per-symbol intermediate `Vec`s that could be workhorse buffers or done in-place:

- RX `:42-45`: `body: Vec<Complex<f32>>` collected from the sample slice (fft_size complex elements ≈ 8–16 KB), then `freq = body` is processed in place — one fresh allocation per symbol that could be a reused scratch buffer cleared each call.
- RX `:62`: `let mut all_llr = Vec::new();` — grown by `extend_from_slice` per subcarrier with **no** `with_capacity`, so it reallocates through several doublings every symbol despite the final length (≈ data-subcarrier-bit count) being exactly known from `subcarrier_indices` − pilots × bits.
- TX `:82`: `let mut td = freq_bins.clone();` clones the full fft_size complex vector; TX `:85`: `samples_complex` collected via `.map(|c| c*scale)` (another full `Vec`); TX `:88-90`: `cp` `.to_vec()` + `full.extend` builds a third; TX `:93`: final `.map(|c| c.re).collect()` builds a fourth. That is roughly **4 full-length `Vec` allocations per symbol** for what is fundamentally one in-place IFFT + a real-cast.

**Impact:** reachability = every symbol, TX and RX; frequency = thousands of symbols/transmission. Per-occurrence: RX ≈ 1 complex `Vec` (8–16 KB) + an un-presized `all_llr` doubling chain; TX ≈ 4 full-length `Vec`s (≈ 8–16 KB each at fft_size, ×4) allocated and freed. Aggregate: tens of KB allocated-and-freed per symbol on TX alone.

**Confidence:** Strong-static (allocation sites are explicit; sizes follow fft_size).

**Effort:** Contained. Presize `all_llr` with `Vec::with_capacity(data_bits)`. On TX, IFFT in place on `freq_bins`, scale in place, and emit the real CP+body in a single presized output `Vec` (skip the intermediate complex `Vec`s); reuse scratch buffers across symbols once the transmitter is session-scoped (CRITICAL #2).

**Verification plan:** alloc-count + peak-bytes (`dhat`) over a multi-symbol TX; expect the per-symbol full-length-`Vec` count to drop from ~4 to ~1 and the `all_llr` reallocation chain to vanish. Round-trip tests guard bit-exactness.

---

### [MAJOR] Narrow-FSK RX builds an `FftPlanner` per `receive` call and a per-symbol complex `Vec` with a resize-realloc

**Location:** `robustness_floor/narrow_fsk.rs:83-85`, `:89-94`

**Problem:** `receive` builds `FftPlanner::<f32>::new()` and plans the FFT once per `receive` (acceptable amortization across symbols *within* one call, but still re-planned every call). More notably, the per-symbol loop (`:88-95`) allocates a fresh `buf: Vec<Complex<f32>>` by `.collect()` from the sample slice and then `buf.resize(fft_size, …)` — the collect sizes to `sps` (7680) and the resize to `next_power_of_two` (8192) forces a reallocation+copy on top of the collect, **per symbol**. `bits` is presized (`:87`), good. The two output `Vec`s at `:114` and the trailing trims are minor.

**Impact:** reachability = every FSK symbol on RX; frequency = the FSK floor is the situational crowded-band mode (less hot than the OFDM main path), but per-symbol an 8192-element complex `Vec` (≈ 64 KB) is allocated, then reallocated by the resize, then freed — per symbol. Planner per `receive` call adds the twiddle-table allocation once per frame.

**Confidence:** Strong-static.

**Effort:** Contained (+low). Allocate one `buf` of `fft_size`, `clear()`/zero-fill and refill per symbol (reuses capacity, avoids the resize-realloc). Hoist the planner/plan to a constructed-once estimator struct.

**Verification plan:** alloc-count over an FSK `receive` of a multi-symbol stream; expect per-symbol `buf` allocation to collapse to capacity reuse and the resize realloc to disappear. FSK round-trip behavior guarded by the module's own logic (no dedicated test in-file, so add an alloc-count harness rather than rely on existing tests).

---

### [MINOR] `OfdmParams::data_indices()` rebuilds a `HashSet` + filtered `Vec` on every call; called per TX symbol via `transmit`

**Location:** `ofdm_params.rs:100-108`; called from `wideband_lowdensity.rs:63`, `:221`, `:305`, and indirectly per `transmit` at `:67-68`

**Problem:** `data_indices()` allocates a `HashSet<usize>` (pilot set) + a filtered `Vec<usize>` every call. `transmit` calls it once (`:63`) and `transmit`/`transmit_multi` are invoked per symbol in `transmit_multi`'s loop (`:233-236`). `data_bytes_per_symbol()` (`:165-167`) also calls `data_indices()` and is itself called per-frame in `transmit_multi`/`receive_multi`. The result is invariant for a given `OfdmParams`.

**Impact:** reachability = per TX symbol (via `transmit`) + a few per-frame call sites; frequency = thousands of symbols/transmission. Per-occurrence: 1 `HashSet` alloc + ~140 inserts + 1 `Vec` of ~430 entries — modest next to the FFT planner but pure repeated overhead.

**Confidence:** Strong-static.

**Effort:** Localized (+low). Precompute `data_indices` (and its length) once in `OfdmParams::for_mode`, store as a field, return a borrow. Note `subcarrier_indices()`/`pilot_indices()` already return borrows — `data_indices` is the odd one out returning an owned `Vec`.

**Verification plan:** alloc-count over `transmit_multi`; the per-symbol `data_indices` `HashSet`+`Vec` should vanish.

---

### [MINOR] Preamble template + detector regenerated per receive-with-sync call; Zadoff-Chu recomputed each generate

**Location:** `wideband_lowdensity.rs:121`, `:274`, `:95`, `:252`; `sync/preamble.rs:31-37`, `:53-57`, `:120-128`

**Problem:** `receive_with_sync`/`receive_multi_with_sync` build `PreambleDetector::new()` per call (`:121`, `:274`), which calls `PreambleGenerator::new().generate()` → `zadoff_chu(192, 25)` (192 `cos`/`sin` pairs + a complex `Vec` + a real `Vec`). TX-side `transmit_with_preamble`/`transmit_multi_with_preamble` regenerate the preamble per call too (`:95`, `:252`). The template is a compile-time constant (fixed length + root).

**Impact:** reachability = once per received/transmitted frame (not per symbol) — bounded frequency, hence MINOR. Per-occurrence: 192 transcendental evaluations + 2 small `Vec`s. The `scan` correlation itself (`preamble.rs:88-101`) is O(signal_len × 192) compute but allocation-free in the loop — out of scope for this dimension (no per-iteration allocation), flagged only so it isn't mistaken for a memory issue.

**Confidence:** Strong-static.

**Effort:** Localized (+low). Cache the template in a `OnceLock<Vec<f32>>` (or a session-scoped detector). Per-frame, not per-symbol, so low priority.

**Verification plan:** alloc/CPU count over repeated `receive_with_sync` calls; the zadoff-chu recompute should amortize to zero after first.

---

### [MINOR] `IdentityFec::encode` / `decode_soft` allocate fresh `Vec`s; `NullPhy::send_frame` and `channel_quality` clone payloads/reports

**Location:** `coded_modulation.rs:67-68`, `:70-75`; `phy_api.rs:178` (`payload.to_vec()`), `:191` (`quality.clone()`)

**Problem:** `IdentityFec::encode` does `info_bits.to_vec()` and `decode_soft` collects a fresh `Vec<u8>` — unavoidable given the `FecCodec` owned-return contract, but worth noting these sit on the per-frame coded-modulation path once the real FEC lands; the trait returning `Vec<u8>` precludes a caller-provided output buffer. `NullPhy::send_frame` clones the payload into the queue (`:178`) and `channel_quality` clones the whole report including its `Vec`s (`:191`) — `NullPhy` is a loopback/test harness so frequency is low.

**Impact:** reachability = per-frame (FEC) / test-only (`NullPhy`); low frequency. Per-occurrence: one `Vec` per call. MINOR; flagged mainly as a forward-looking note that the `FecCodec` trait's owned-`Vec` return shape will force per-block allocation on the real FEC's hot path.

**Confidence:** Heuristic (real FEC not yet present; current impl is identity stub).

**Effort:** Cross-cutting (trait-signature change to offer a `&mut Vec<u8>`/slice-out variant) — defer until the real FEC lands and is measured.

**Verification plan:** revisit when `tuxmodem-fec` lands; benchmark the real codec's per-block allocation before changing the trait.

---

## Out-of-dimension observations (not memory findings, noted to prevent misclassification)

- `preamble.rs:88-101` scan is an O(N·192) nested-loop correlation recomputing `sig_energy` from scratch each shift (a sliding-window sum would make it O(N)). That is an **algorithmic-complexity / compute** issue, not allocation — the inner loop allocates nothing. Out of scope for this dimension.
- `subcarrier_snr.rs` `estimate_from_pilots` (`:38-47`) collects one result `Vec` per call — single allocation, sized by input, not on a per-subcarrier inner loop; acceptable.

---

## Suspected Bugs (for follow-up)

None. (No correctness bugs surfaced incidentally while auditing the allocation dimension. The audit did not chase correctness; the differential/holistic bug-hunters own that.)
