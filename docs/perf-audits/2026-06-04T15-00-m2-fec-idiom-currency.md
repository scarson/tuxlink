# M2 FEC — Library / Language-Idiom Currency Audit

**Agent:** glade-knoll-shoal
**Scope:** `tuxmodem-fec` crate, `/home/user/tuxlink/tuxmodem/crates/tuxmodem-fec/src/**` (excl. `bin/`)
**Dimension:** library/language-idiom currency ONLY — superseded/deprecated patterns + unused fast-path library APIs.
**Stack:** Rust 2021, MSRV 1.75. bitvec@1, crc@3, rand@0.8, rand_chacha@0.3, thiserror.
**Index consulted:** `/home/user/tuxlink/.claude/skills/performance-audit/version-indexes/rust.md` (covered_through Rust 1.96, built 2026-06-03).
**Reachability:** LATENT — no in-tree callers. Encode/decode are the intended per-FEC-block data plane once integrated; matrix construction is per-codec-construction (cold).

---

### [MAJOR] `Encoder::encode` builds the systematic codeword bit-by-bit instead of bulk-copying the info prefix

**Location:** `src/encode.rs:160-167`
**Problem:** The per-block data-plane encode path does:
```rust
let mut codeword: BitVec<u8> = BitVec::with_capacity(self.n);
for bit in info.iter() {
    codeword.push(*bit);          // k iterations, one bit at a time
}
for eq in &self.parity_eqs { ... codeword.push(parity); }
```
The first loop copies the entire k-bit info word (`info: &BitSlice<u8>`) one bit at a time. bitvec@1 provides `BitVec::extend_from_bitslice(&BitSlice)`, which performs a word-aligned bulk copy of the source slice's backing store rather than k individual `push` calls (each of which recomputes head/tail bit position and may re-check capacity). For the largest WiFi block k can be ~972 bits; for the floor code k=512. The systematic prefix is exactly the kind of contiguous bit run that `extend_from_bitslice` is the fast path for.
**Impact:** Per-encode, latent. Replaces k single-bit `push` calls with one bulk memcpy-class copy (the source and destination share `u8` storage and identical bit-ordering, so the copy can proceed by whole bytes for the aligned middle). The parity loop (`m` pushes of computed bools) is genuinely scalar and stays as-is. Eliminates ~k branch-laden push iterations on the encode hot path. Latency: shaves the info-copy portion of per-block encode; the dominant cost remains the parity XOR fold, which this finding does not touch.
**Confidence:** Strong-static. `extend_from_bitslice` is a stable bitvec@1 method; the source is a `BitSlice<u8>` with the same store type as the destination, so the bulk path applies. Inherits index freshness (the index does not enumerate bitvec methods, but the bulk-vs-elementwise principle is the framework-idiom-currency lane's core charter; confidence is on the API existence, which is verifiable).
**Effort:** Localized / low. Replace the first `for` loop with `codeword.extend_from_bitslice(info);`.
**Verification plan:** Micro-benchmark `encode()` for N1296/R3_4 before/after with `criterion` + `black_box` (index line 64: `black_box` stabilised 1.66, required for correct benching). Correctness guard: the existing `encoded_codeword_is_systematic` and `encoded_codeword_satisfies_parity` tests (`src/encode.rs:178-207`) already assert `codeword[i] == info[i]` for the prefix and full parity — they gate the change.

---

### [MAJOR] `crc::bits_to_bytes` hand-rolls MSB-first bit packing instead of using bitvec's `BitField`/raw-store fast path

**Location:** `src/crc.rs:77-89` (and the symmetric `bits_to_u32_msbfirst`, `src/crc.rs:92-101`)
**Problem:** `bits_to_bytes` packs the info bits into a `Vec<u8>` with a doubly-nested scalar loop — outer over 8-bit chunks, inner over the 8 bits of each chunk, OR-ing `1 << (7 - i)`:
```rust
for chunk in bits.chunks(8) {
    let mut b: u8 = 0;
    for (i, bit) in chunk.iter().enumerate() {
        if *bit { b |= 1 << (7 - i); }
    }
    bytes.push(b);
}
```
This is exactly the elementwise pattern bitvec@1's `BitField` trait (`load_be::<u8>()` / `store`) and the `as_raw_slice()` accessor exist to replace. Two cleaner fast paths exist:
- If the `BitSlice<u8>` is constructed with `Msb0` ordering (the bitvec default `Lsb0` is what's in play via `prelude::*`, so this needs an ordering choice), the byte-packed representation is *already* the backing store and `as_raw_slice()` returns it with zero per-bit work for the aligned prefix.
- Otherwise `chunks(8)` + `BitField::load_be::<u8>()` replaces the inner 8-iteration loop with one word load per byte.

The CRC path runs on every encode (`append_crc32`) and every decode (`verify_crc32`, `src/crc.rs:64`), so this packing is on both data-plane directions. `bits_to_u32_msbfirst` has the identical anti-pattern at 32-bit width and could be a single `BitField::load_be::<u32>()`.
**Impact:** Per-encode AND per-decode, latent. Both directions pay the scalar packing on every FEC block. Magnitude scales with k (the info length being packed). Replacing the inner per-bit loop with a `BitField` word-load (or eliminating it entirely via `as_raw_slice` under `Msb0`) removes the inner-loop branch per bit. Latency: trims the CRC-framing portion of both encode and decode hot paths.
**Confidence:** Strong-static for the `BitField::load_be` rewrite (stable bitvec@1 trait, in `prelude`). LOW-to-Heuristic for the `as_raw_slice` zero-copy variant — it requires switching the slice ordering to `Msb0` end-to-end, which is a wider change and interacts with how callers build the `BitVec` (`codec.rs` collects via `.map(|&b| b != 0).collect()`, ordering-agnostic at the boundary but the storage layout differs). Mark the ordering-switch path LOW until verified against the full bit-layout contract.
**Effort:** Contained / low for the `BitField::load_be` swap (localized to crc.rs). Cross-cutting / higher if pursuing the `Msb0` zero-copy variant (touches the slice ordering used across encode/interleave/codec).
**Verification plan:** Bench `append_crc32` + `verify_crc32` for the largest k. Correctness guard: round-trip CRC must be byte-identical — the existing codec round-trip tests (`src/codec.rs:220-249`) and any crc unit tests exercise the full pack→checksum→compare path; a differential test packing the same bits both ways and asserting equal `Vec<u8>` is a cheap direct guard. Cite index charter (framework-idiom lane: elementwise → bulk/word-aligned library ops).

---

### [MINOR] `interleave` / `deinterleave` initialize the matrix bit-by-bit instead of bulk-copying, and emit via per-bit `push`

**Location:** `src/interleaver.rs:36-47` (interleave), `src/interleaver.rs:70-85` (deinterleave)
**Problem:** Both functions allocate `BitVec::repeat(false, total)` then fill the source region with a scalar `matrix.set(i, *bit)` loop:
```rust
let mut matrix: BitVec<u8> = BitVec::repeat(false, total);
for (i, bit) in input.iter().enumerate() {
    matrix.set(i, *bit);          // n scalar sets
}
```
When `n == total` (the production case — `codec.rs:34` fixes `INTERLEAVER_ROWS = 8`, which divides 648/1296/2048 exactly, so there is never any padding), the entire `repeat(false)` + per-bit `set` initialization is replaceable by a single `let mut matrix = input.to_bitvec();` (one bulk allocation+copy) — the padding branch only matters when `total > n`, which the composition guarantees never happens. Separately, the column-major read loop emits with per-bit `out.push(matrix[...])`; that read is a genuine permutation (no contiguous run), so it is inherently scalar and is NOT a finding — only the *initialization* fill is.

Note the tail of `decode_soft` already bypasses bit-by-bit interleaving on the decode side by computing an index permutation (`deinterleave_index_perm`, `codec.rs:192`) and gathering LLRs directly — so `deinterleave` itself is not on the decode hot path. `interleave` IS on the encode hot path (`codec.rs:121`).
**Impact:** Per-encode, latent, small. For n∈{648,1296} the saved work is one `repeat`-fill pass (n scalar `set`s) replaced by a bulk `to_bitvec`. The permutation read dominates and is untouched. Lower-ranked than the encode/crc findings because the saved portion is the smaller half of the function.
**Confidence:** Strong-static. `to_bitvec()` and `BitSlice` indexing are stable bitvec@1. The `n == total` precondition is asserted in the caller's `debug_assert_eq!` (`codec.rs:122-128`) and documented in the module header (`interleaver.rs:18-20`).
**Effort:** Localized / low. Guard on `n == total` to keep the padded path correct: `let mut matrix = if total == n { input.to_bitvec() } else { let mut m = BitVec::repeat(false, total); for (i,b) in input.iter().enumerate() { m.set(i,*b); } m };`.
**Verification plan:** Bench `interleave` for n=1296. Correctness guard: the codec round-trip tests (`codec.rs:220-249`) cover interleave→deinterleave losslessness; add a direct assertion that the padded branch (n not a multiple of rows) still matches the old behavior.

---

### [MINOR] Workspace `[profile.release]` defines no LTO / codegen-units — the dominant Rust perf lever is unset

**Location:** `/home/user/tuxlink/tuxmodem/Cargo.toml` (no `[profile.release]` section present; `grep profile` returns nothing)
**Problem:** The Rust version index's own framing (index lines 22-24) is explicit: *"the majority of perf wins are build-config (codegen flags, linker, PGO/LTO) rather than stdlib API changes."* The workspace manifest sets none of:
- `lto = "thin"` (index line 28 — ~10–20% runtime gain over the default thin-local LTO, crosses crate boundaries; relevant here because the data plane spans `tuxmodem-fec` ↔ `tuxmodem-phy` via the `FecCodec` trait, exactly the cross-crate inlining LTO unlocks),
- `codegen-units = 1` (index line 30 — lets LLVM see the full crate for inlining the tight SPA boxplus/XOR loops),
- `-C target-cpu=native` for the eventual deployment binary (index line 32 — the SPA decoder's `boxplus` is a `tanh`/`atanh` inner loop and the parity XOR folds are SIMD-amenable; auto-vectorization needs the CPU feature unlock).

The SPA decoder (`decode.rs:158-227`) is the throughput-critical inner loop of the whole crate (up to `MAX_ITERS_OFDM = 50` iterations per block, `codec.rs:35`); it is precisely the numeric hot loop these flags target. This is the single highest-leverage idiom-currency item per the index's own weighting, but ranked MINOR here because it is a config addition the operator must validate (and `target-cpu=native` must NOT be set for distributed binaries — index line 32).
**Impact:** Whole-crate, latent, build-config. Affects every data-plane path once integrated. Index cites ~10–20% (thin LTO) plus further codegen-units/target-cpu gains; the SPA `tanh` loop and XOR folds are the auto-vectorization candidates.
**Confidence:** Strong-static (index-cited, durable build-config, version-independent per index lines 28/30/32).
**Effort:** Localized / low to add `[profile.release]` keys; the `target-cpu=native` decision is Contained (must be scoped to non-distributed binaries, set via `RUSTFLAGS`/`.cargo/config.toml`, not the manifest).
**Verification plan:** Build the (forthcoming Phase-8) `benches/decode.rs` with and without `lto="thin"` + `codegen-units=1`; compare criterion throughput. Guard: behavior is unchanged by codegen flags; the existing test suite passing under release is the correctness guard. Cite index §Build & Codegen, lines 28/30/32.

---

## Non-findings (calibration — deliberately NOT raised)

- **crc@3 `const CRC32`** (`src/crc.rs:13`): `const CRC32: Crc<u32> = Crc::<u32>::new(&CRC_32_ISO_HDLC);` — the slice-by-N lookup table is built ONCE as a `const`, NOT reconstructed per `checksum` call. This is the CORRECT crc@3 pattern; the known per-call-rebuild anti-pattern is absent. (`OnceLock`/`LazyLock`, index lines 60/62, are not even needed here since `Crc::new` is `const`-evaluable.) No finding.
- **rand@0.8 / rand_chacha**: `gen_range` in loops (`ofdm_wifi_family.rs:79,91,94`) and `SliceRandom::shuffle` (`floor_rate14.rs:59,78`) are confined to matrix *construction*, which runs once per codec build (cold path), not per block. RNG is encode-side/construction-only; no per-block RNG. `ChaCha8Rng::seed_from_u64` is the current idiomatic constructor. No data-plane RNG finding.
- **decode.rs `incoming: Vec<f32>` per-edge allocations** (`decode.rs:165,193`): real per-iteration heap traffic, but that is the **memory/allocation** lane's finding, not idiom-currency — no superseded API or unused library fast-path is involved (it's plain `Vec`). Out of dimension.
- **`#[allow(clippy::needless_range_loop)]`** in encode/decode/ofdm: index-driven Gaussian-elimination and Tanner-graph loops where iterator rewrites obscure the math without perf benefit. Style, no perf consequence. Not a finding (and the index does not flag range-loops as a perf idiom).
- **`chunks_exact`**: not applicable to the SPA adjacency loops (jagged `Vec<Vec<usize>>` row lengths, not fixed-stride slices). The only fixed-8 chunking is `bits.chunks(8)` in crc, already covered under the BitField finding.
- **`codec::bytes_to_bitvec` / `bitvec_to_bytes`** (`codec.rs:97-103`): one-bit-per-byte `&[u8]` ↔ `BitVec` conversion is mandated by PR #188's `FecCodec` contract (`codec.rs:18-22`); the `.collect()` is the idiomatic conversion at that contract boundary. Not a superseded pattern.

## Suspected Bugs

None. (One latent correctness *hazard* noted only for context, NOT chased as a perf finding: `decode_soft` constructs `llr_bitvec_signs` at `codec.rs:146` then immediately discards it via `let _ = ...` at `codec.rs:151` — dead computation, an n-length `BitVec` allocation + collect with no effect. This is a correctness/cleanliness defect, not an idiom-currency issue; flagging location only per the audit's "record, don't chase" rule.)
