# Performance Audit — `tuxmodem-fec` — Dimension: Library / Language-Idiom Currency

**Agent:** glade-knoll-shoal
**Date:** 2026-06-04T15:00
**Scope:** `/home/user/tuxlink/tuxmodem/crates/tuxmodem-fec/src/**/*.rs` (excl. `bin/`)
**Stack (verified against `tuxmodem/Cargo.toml`):** Rust 2021, MSRV 1.75; `bitvec = "1"`, `crc = "3"`, `rand = "0.8"`, `rand_chacha = "0.3"`, `thiserror`.
**Index consulted:** `/home/user/tuxlink/.claude/skills/performance-audit/version-indexes/rust.md` (`covered_through: Rust 1.96`, built 2026-06-03).
**Reachability:** LATENT — no in-tree callers. Intended hot path is `OfdmAdaptiveCodec::decode_soft` / `::encode` per FEC block once subsystem #3 integrates. Findings ranked by per-occurrence cost × intended frequency (per-block on the data plane) vs. construction-time (once per codec).

This report flags ONLY superseded/deprecated patterns and unused fast-path APIs in my dimension. No praise, grade, or summary. Correctness observations are parked in "Suspected Bugs."

---

### [MAJOR] `crc::Crc<u32>` declared `const`, not `static` — slice-by-1 lookup table re-materialized at every checksum call site

**Location:** `crc.rs:13` (`const CRC32: Crc<u32> = Crc::<u32>::new(&CRC_32_ISO_HDLC);`); consumed at `crc.rs:46` (`append_crc32`) and `crc.rs:65` (`verify_crc32`).

**Problem:** `Crc::<u32>::new(&CRC_32_ISO_HDLC)` returns the default `Crc<u32, Table<1>>` in crc 3.x, which embeds a precomputed 256-entry `[u32; 256]` slice-by-1 lookup table (1 KiB) inside the returned struct. Declaring this as a **`const`** rather than a **`static`** is the crc@3 currency anti-pattern in its Rust-const-semantics form: a `const` item has no single address — it is *substituted inline at each use site*. Each `CRC32.checksum(&bytes)` reference (`append_crc32`, `verify_crc32`) gets its own materialized copy of the 1 KiB table rather than borrowing one shared instance. The compiler *may* dedup identical `const` values into rodata under optimization, but that is not guaranteed and is not the idiom; the crc crate's own docs and the index's `OnceLock`/`static` guidance both point at "build the table once, reference one instance." The anti-pattern the dimension brief names — "rebuilding the lookup table per call" — is *almost* avoided here (it is not rebuilt at runtime per call, because the table is const-evaluated at compile time), but `const` still defeats the "one shared instance" intent: the table is duplicated per use site / per monomorphization rather than living once in static memory.

**Impact:** Per-FEC-block, both `append_crc32` (encode) and `verify_crc32` (decode) run; each touches its own copy of the table. On the data plane this is a cold-ish constant relative to SPA cost, so latency impact is **small per block** but the fix is free and removes table duplication from the binary. Note: the *severe* form of this anti-pattern (constructing a fresh `Crc` and rebuilding the table inside the per-call function body) is NOT present — the code correctly hoists construction out of the function. The residual defect is purely `const` vs `static`.

**Confidence:** Strong-static. crc@3 table-materialization behavior is documented; the `const`→inline-substitution vs `static`→single-instance distinction is core Rust semantics. Inherits index freshness (index covers `OnceLock`/`static` one-time-init guidance, lines 60–62).

**Effort:** Localized / low. Change `const CRC32` → `static CRC32: Crc<u32> = Crc::<u32>::new(&CRC_32_ISO_HDLC);`. `Crc::new` is a `const fn` in crc 3.x, so a `static` initializer compiles with no `OnceLock`/`LazyLock` needed. (If a future crc version makes `new` non-const, fall back to `static CRC32: OnceLock<Crc<u32>>` per index line 60. Note `LazyLock` is 1.80+; at MSRV 1.75 use `OnceLock::get_or_init`, not `LazyLock`.)

**Verification plan:** (1) `cargo bloat` / `nm --size-sort` on a release build before/after to confirm the 1 KiB `[u32; 256]` table appears once (static) vs. potentially duplicated (const). (2) Correctness guard: `static` and `const` produce bit-identical checksums; the WiFi-family round-trip tests in `codec.rs` (`round_trip_n648_r12_zero_noise`, `round_trip_n1296_r34_zero_noise`) already cover the CRC path end-to-end. Cite index lines 60–62 (`OnceLock`/`static` for one-time global init supersedes per-site duplication).

---

### [MAJOR] `bits_to_bytes` / `bits_to_u32_msbfirst` hand-roll bit-by-bit packing instead of bitvec `BitField` load / `chunks_exact`

**Location:** `crc.rs:77–89` (`bits_to_bytes`), `crc.rs:92–101` (`bits_to_u32_msbfirst`), and the 32-iteration push loop at `crc.rs:49–51` (`append_crc32`).

**Problem:** `bits_to_bytes` iterates every bit and sets it into a `u8` with `b |= 1 << (7 - i)` per bit; `bits_to_u32_msbfirst` does the same for 32 bits; `append_crc32` pushes the CRC out one bit at a time across 32 `push` calls. bitvec@1 ships exactly the fast-path APIs for this: the `BitField` trait (`load_be::<u32>()` / `store_be`) does word-aligned bulk load/store, and `chunks_exact(8)` + `BitField::load_be::<u8>()` bulk-pack to bytes without per-bit branching. The code uses `bits.chunks(8)` (the bitvec chunk iterator — good) but then falls back to a manual per-bit inner loop, discarding the bulk path. This is the canonical bitvec-1 "bit-by-bit where word-aligned ops are the fast path" pattern the dimension targets.

**Impact:** Per-FEC-block on both encode (`append_crc32` → `bits_to_bytes` over k≈292–972 bits, plus 32-bit CRC push) and decode (`verify_crc32` → `bits_to_bytes` over the same prefix + `bits_to_u32_msbfirst` over 32 bits). Per-occurrence cost is O(bits) scalar branches where `BitField::load_be` is O(bits/8) word loads — a constant-factor win, **not** an order change. Latency: bounded by prefix length (hundreds–~1000 bits/block); modest next to SPA's per-iteration edge sweep, but runs on the data plane every block on both sides.

**Confidence:** Strong-static. `BitField`/`load_be`/`chunks_exact` are stable bitvec-1 / std APIs. The Rust index is intentionally thin on library entries (its own preamble, lines 19–24), and has no bitvec entry, so this rests on bitvec-1 API knowledge — marked Strong-static, not Measured.

**Effort:** Contained / low. `bits_to_u32_msbfirst(bits)` → `bits.load_be::<u32>()` (with `use bitvec::field::BitField`). `append_crc32`'s 32-bit tail → reserve 32 then build the CRC bits via `BitField`/bulk extend rather than 32 pushes. `bits_to_bytes` → `chunks_exact(8).map(|c| c.load_be::<u8>())` plus a remainder branch preserving the existing MSB-first zero-pad of the final byte (`crc.rs:84`). Bit-ordering caveat: the code is MSB-first; `BitSlice<u8>` here defaults to `Lsb0`, so the **big-endian** `BitField` methods (`load_be`/`store_be`) must be used and cross-checked against the current manual `1 << (7 - i)` convention.

**Verification plan:** (1) Microbench `bits_to_bytes` on a 972-bit input (criterion + `black_box`) before/after. (2) Load-bearing correctness guard: assert bit-identical output of the new `BitField` path vs the current manual loop across all lengths 0..=1024 including non-byte-aligned tails (the final-byte zero-pad at `crc.rs:84` must survive). Existing `codec.rs` round-trips exercise the composed path but not byte-packing in isolation — add a focused equivalence test before swapping. Cite bitvec-1 `BitField` fast-path rationale (no index line — bitvec uncovered; freshness inherited as Strong-static).

---

### [MINOR] `interleave` / `deinterleave` build the matrix and emit output bit-by-bit via `set` + `push` instead of bulk bit moves on the prefix edges

**Location:** `interleaver.rs:36–47` (`interleave`: `BitVec::repeat` + per-bit `matrix.set` + per-bit `out.push`), `interleaver.rs:70–85` (`deinterleave`: same shape).

**Problem:** Both functions (a) materialize the full R×C matrix one `set` per input bit, then (b) read it out one `push` per output bit. The column-major read is a genuine permutation (the transpose legitimately touches each bit once — irreducible without a block-transpose algorithm). But two sub-steps are needless-per-bit: the initial copy of `input` into `matrix` (`interleaver.rs:37–39`) is a straight prefix copy that `extend_from_bitslice` would do in bulk, and `deinterleave`'s final `for i in 0..original_len { out.push(matrix[i]) }` (`interleaver.rs:81–85`) is a prefix slice that should be `matrix[..original_len].to_bitvec()`. These are the bitvec-1 "bulk where bit-by-bit is used" pattern, on the cheap edges of an otherwise-necessary permutation.

**Impact:** Per-FEC-block, encode-side only for `interleave` (n bits). Decode-side: `deinterleave` is **not on the hot path** — `decode_soft` (`codec.rs:149–150`) reconstructs the permutation via `deinterleave_index_perm` and gathers LLRs directly, so `deinterleave` itself is currently latent/test-only. Realized data-plane cost is `interleave`'s prefix-copy + transpose over n∈{648,1296} bits per block. Order-unchanged constant-factor; latency small next to SPA.

**Confidence:** Strong-static. Inherits index freshness (bitvec uncovered; Strong-static on bitvec-1 API knowledge).

**Effort:** Localized / low. Replace input→matrix copy with a bulk extend; replace the final prefix readout with `to_bitvec()` of a slice. Leave the column-major transpose loop as-is — that's the necessary permutation; a block-transpose rewrite is an algorithmic finding, out of an idiom pass's scope.

**Verification plan:** Argument + guard: prefix-copy and prefix-readout are provably equivalent to bulk slice ops (identity permutation on the prefix). Guard with the existing interleave/deinterleave round-trip property — add a proptest if absent (the crate already dev-depends on `proptest`). No benchmark strictly required given the constant-factor argument; a criterion run on n=1296 confirms the win isn't lost to bounds-check noise.

---

### [MINOR] `Encoder::encode` copies info bits into the codeword via per-bit `push` instead of a bulk prefix extend

**Location:** `encode.rs:160–167` (`encode`): `BitVec::with_capacity(self.n)` then `for bit in info.iter() { codeword.push(*bit); }` for the systematic prefix.

**Problem:** The systematic-form copy `c[0..k] == u` is a verbatim prefix copy of the input bitslice. The per-bit `for bit in info.iter() { codeword.push(*bit) }` loop is the bitvec-1 bit-by-bit anti-pattern where `codeword.extend_from_bitslice(info)` (single bulk copy) is the fast path. The parity tail (`encode.rs:164–167`) genuinely computes one bit per equation and must push per-bit — irreducible. Only the k-bit prefix copy is improvable.

**Impact:** Per-FEC-block encode, k∈{324,972,…} bit pushes replaced by one bulk extend. Constant-factor; encode-side only; small next to the parity-equation fold (`encode.rs:165`, itself O(edges) — an algorithmic concern, not currency). Latency minor.

**Confidence:** Strong-static. `extend_from_bitslice` is stable bitvec-1. Inherits index freshness (Strong-static, bitvec uncovered).

**Effort:** Localized / low. `let mut codeword = BitVec::with_capacity(self.n); codeword.extend_from_bitslice(info);` then keep the parity-push loop. Capacity reserve is already present (good).

**Verification plan:** Argument + guard. `extend_from_bitslice` is definitionally the prefix copy. Existing `encoded_codeword_is_systematic` asserts `codeword[i] == info[i]` for the prefix and already guards the semantic. Microbench optional.

---

### [MINOR] `codec.rs` bit↔byte adapters are one-bit-per-byte by the bus contract — NOT actionable; recorded so it isn't re-flagged

**Location:** `codec.rs:97–103` (`bytes_to_bitvec`, `bitvec_to_bytes`).

**Problem:** These iterate one `u8` per bit (`b != 0` → bit; `u8::from(*b)` → byte). They *look* like the bitvec bit-by-bit anti-pattern but are **not** — the `FecCodec` bus contract (`codec.rs` module docs, lines 18–22) mandates one-bit-per-byte payload layout (LSB carries the bit, value 0/1). So `as_raw_slice`/`BitField` bulk packing is **inapplicable**: source/destination is a `[u8]` of 0/1 values, not a packed bitfield. No faster idiom respects the contract. (The `.collect()` from a known-length `ExactSizeIterator` already reserves capacity, so even the `with_capacity` micro-tweak is moot.)

**Impact:** None actionable. Per-block on both sides but irreducible under the contract.

**Confidence:** Strong-static (contract is explicit in-file).

**Effort:** N/A — no change recommended.

**Verification plan:** N/A. Cross-reference the `FecCodec` one-bit-per-byte contract before any "optimize this" temptation.

---

## RNG / construction-path notes (rand@0.8 / rand_chacha@0.3) — examined, no data-plane finding

`rand` + `rand_chacha` appear ONLY in matrix construction (`codes/floor_rate14.rs:58–59`; `codes/ofdm_wifi_family.rs:65,78,79,93,94`) and a hand-rolled LCG in tests (`codec.rs:210`). Construction runs **once per codec at `OfdmAdaptiveCodec::new`** (WiFi family iterates up to 64 seeds, `ofdm_wifi_family.rs:37`, each re-seeding a fresh `ChaCha8Rng`). Off the per-block data plane → cold / latent-once.

- `ChaCha8Rng::seed_from_u64` per seed-iteration reconstructs RNG state up to 64× per codec build; `gen_range(0..z)` / `gen_range(0..m_blocks)` in loops (`ofdm_wifi_family.rs:79,93,94`) are fine for one-time construction. No idiom-currency defect: rand 0.8's `gen_range`/`gen::<f32>` are current for the 0.8 line, and construction cost amortizes to zero on the data plane. **No finding** — would be a cold micro-opt (explicitly out of scope per calibration).
- For a *different* dimension (dependency-version-bump backlog, not data-plane currency, not ranked here): `rand 0.8` is one minor behind `rand 0.9` (2025), which renamed `gen`→`random`, `gen_range`→`random_range`, `thread_rng`→`rng`, and reworked `SeedableRng`/distribution traits. Construction-only here; recorded for the upgrade backlog.

## std / MSRV-1.75 idiom notes — examined

- `Vec::with_capacity` is used at the right places (`crc.rs:78`, `encode.rs:114,160`, `codec.rs:195`; decode message vecs come from `.map().collect()` over `ExactSizeIterator`). No growth-from-empty hot loop lacking a reserve.
- `chunks(8)` (`crc.rs:79`) → the fixed-size `chunks_exact(8)` is the std fast path (drops per-chunk length checks in the body, isolates the remainder); pairs with the `BitField::load_be::<u8>()` rewrite in the MAJOR bitvec finding. Folded there, not ranked separately.
- `div_ceil` (`crc.rs:78`, `encode.rs:78`, `interleaver.rs:33,60`) is the current std idiom (stable 1.73) — no superseded `(x + 7) / 8` remains. Good.
- SPA inner loops (`decode.rs:165,193`) allocate a fresh `incoming: Vec<f32>` per check and per variable per iteration. That's an allocation/data-access concern (different dimension); the `.map().collect()` usage itself is current Rust. Out of my dimension; noting the boundary so the memory/allocation lane covers it.
- `boxplus` (`llr.rs:23–28`) uses `tanh`/`atanh` per edge-combine; the file flags a min-sum approximation for Phase 8. Algorithmic/numeric choice, not stale-API currency. Out of dimension.

---

## Suspected Bugs

(Recorded, not chased — correctness, not my dimension.)

- **`codec.rs:146,151` — dead `llr_bitvec_signs`.** Computed (`let llr_bitvec_signs: BitVec<u8> = llr.iter().map(|x| *x < 0.0).collect();`) then discarded via `let _ = llr_bitvec_signs;` (comment: "sanity-check witness; could remove"). It allocates an n-bit `BitVec` per decode on the data plane for nothing. Not a currency finding (bitvec usage is fine) — a dead per-block heap allocation that should likely be deleted.
- **Floor code known rank-deficient, `Encoder::new` panics on it** (`floor_rate14.rs:29–34` docstring + ignored test `encode.rs:209–227`). Pre-existing, tracked by `tuxlink-bbin`. Not mine.
- **`decode.rs:148–152` — `msg_c_to_v` init to `0.0`** is overwritten by the check-update before first read; benign. Noted for completeness, not a bug.
