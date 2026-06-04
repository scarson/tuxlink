# Performance Audit — `tuxmodem-fec` — Dimension: Data Access / Data Layout / Memory-Access Patterns

**Auditor:** glade-knoll-shoal
**Date:** 2026-06-04T15:00
**Scope:** `tuxmodem/crates/tuxmodem-fec/src/**/*.rs` (excl. `bin/`)
**Dimension:** data-access analog for a no-I/O compute crate — DATA LAYOUT & MEMORY-ACCESS PATTERNS on the hot SPA decode path.
**Reachability:** LATENT — no in-tree callers (PHY dep commented, live path bypasses FEC). All impact framed as per-FEC-block decode cost once integrated. Decode runs up to 50 iterations/block (`MAX_ITERS_OFDM`), and is the only loop that executes O(iters × edges) times.

This report covers ONLY data-layout / memory-indirection. Algorithmic complexity, allocation churn, and concurrency belong to sibling lanes (cross-referenced where a finding straddles, but not double-claimed).

---

### [CRITICAL] Tanner-graph adjacency is nested `Vec<Vec<usize>>` — pointer-chasing defeats prefetch on every SPA edge traversal

**Location:** `decode.rs:41-53` (the five `Vec<Vec<usize>>` / `Vec<Vec<f32>>` fields), exercised at `decode.rs:162-227` (per-iteration hot loops); root struct `parity_matrix.rs:22` (`rows: Vec<Vec<usize>>`).

**Problem:** The decoder's entire working set is six independent nested-vector forests:
`var_to_checks`, `check_to_vars`, `v_edge_pos`, `c_edge_pos` (built once in `new`), plus the per-decode `msg_v_to_c` and `msg_c_to_v`. Each is `Vec<Vec<_>>` — an outer `Vec` of `(ptr,len,cap)` triples, each pointing to a *separately heap-allocated* inner `Vec`. Walking check `c`'s neighbors (`decode.rs:163` `&self.check_to_vars[c]`) loads the outer slot, dereferences to a distinct allocation, and only then streams the inner data. Per iteration the check-update touches `check_to_vars[c]`, `v_edge_pos[c]`, and writes `msg_c_to_v[c]` — three *unrelated* heap regions per check node; the variable-update touches `var_to_checks[v]`, `c_edge_pos[v]`, `msg_c_to_v[c]` (random `c`), and `msg_v_to_c[v]` — four to five. None are contiguous; the allocator placed each inner `Vec` wherever it found space at build time, so adjacent check indices `c`, `c+1` land on arbitrary, non-prefetchable cache lines.

For the floor code (n=2048, ~3 ones/col ⇒ ~6144 edges) and even the N648/N1296 WiFi codes, each of the ~50 iterations chases these pointers across every edge. The inner vectors are tiny (degree ~3-7 `usize` = 24-56 bytes), so each pointer-chase pulls a fresh cache line to consume only a few useful bytes — the classic indirection-defeats-prefetch pattern called out in the rust profile-pack's data-access/runtime-cache notes. The hardware prefetcher cannot follow `Vec<Vec<_>>` because the inner base addresses are data-dependent loads, not a stride.

**The structure-of-flat-arrays (CSR) alternative:** collapse each `Vec<Vec<T>>` to two flat vectors — `data: Vec<T>` (all inner elements concatenated in node order) + `offsets: Vec<u32>` (n+1 prefix sums). `check_to_vars[c]` becomes `&data[offsets[c]..offsets[c+1]]`. This is the canonical LDPC decoder layout (every production SPA/min-sum implementation uses CSR or an edge-indexed flat array). Benefits stack: (a) one allocation per array instead of n+1, (b) sequential check indices stream contiguously so the prefetcher engages, (c) the message arrays `msg_c_to_v`/`msg_v_to_c` become flat `Vec<f32>` indexed by a precomputed edge ordinal, which is then the *same index space* as `data` — the `v_edge_pos`/`c_edge_pos` indirection maps directly to flat offsets.

**Impact:** This is the dominant per-decode memory cost. Latency-critical: an HF OFDM modem decodes one FEC block per received frame on the live RX path; the 50-iteration SPA loop is the per-block bottleneck and every iteration pays the pointer-chase tax across all edges. Converting to CSR is the single highest-leverage data-layout change in the crate. Confidence that the *pattern* is cache-hostile is high; the *magnitude* depends on code size and cache state, hence not Measured.

**Confidence:** Strong-static (layout is unambiguously nested-Vec; magnitude unmeasured).

**Effort:** Cross-cutting (+low). Touches `ParityCheckMatrix` (or adds a derived CSR view in `Decoder::new`), all four adjacency fields, and the four hot loops. Mechanical, not algorithmic — the math is unchanged; only indexing changes. Can be staged: build a CSR mirror inside `Decoder::new` without altering `ParityCheckMatrix`'s public shape, so the blast radius stays inside `decode.rs`.

**Verification plan:** `criterion` benchmark of `Decoder::decode` on N1296/R3_4 and the floor code at a fixed iteration count (disable early-exit to hold work constant), release build, comparing nested-Vec vs CSR. Expect the win to grow with n. Correctness guard: the existing `decode_zero_noise_returns_input` / `decode_one_bit_flip_recovers` tests plus a bit-exact equivalence check (same LLR input ⇒ identical `decoded` vector) between the two layouts across a seed sweep. Optionally `perf stat -e cache-misses,L1-dcache-load-misses` on a decode microbench to confirm miss-rate drop.

---

### [MAJOR] Per-edge message arrays `msg_v_to_c` / `msg_c_to_v` are AoS-nested and reallocated every `decode()` call

**Location:** `decode.rs:142-152` (allocation), `decode.rs:168,186,196,202,213` (access).

**Problem:** Two issues compound on the message buffers, both data-layout:

1. **Nested layout (SoA opportunity).** `msg_v_to_c: Vec<Vec<f32>>` and `msg_c_to_v: Vec<Vec<f32>>` mirror the nested adjacency, so the LLR messages — the values actually mutated 50× — live in n separate tiny allocations. The check-update reads `msg_v_to_c[v][v_edge_pos[c][i]]` (`decode.rs:168`): `v` is a *neighbor* index, so consecutive `i` reads jump to different `v` rows ⇒ different allocations. This is a scatter-gather over the message forest every edge, every iteration. A flat edge-indexed `Vec<f32>` (one slot per Tanner edge, same ordinal space as the CSR `data` from the CRITICAL finding) makes the message store a single contiguous array; the `v_edge_pos`/`c_edge_pos` maps become precomputed flat edge ordinals so `msg_v_to_c[edge_id]` is a direct indexed load. SoA over the edge set is the idiomatic LDPC message layout.

2. **Per-decode reallocation of an invariant-shape buffer.** Both message forests are rebuilt from scratch on *every* `decode()` call (`decode.rs:142-152`) — n+1 allocations each, even though the shape (degree per node) is fixed at `Decoder::new` time. On the integrated RX path this is one decode per frame, so the allocation storm repeats per frame. The buffers are pure scratch; they should be workhorse buffers owned by a per-decoder scratch struct (or `&mut` scratch passed in) and `clear()`/overwritten in place. (The allocation *count* is the allocation lane's to quantify; the data-layout point here is that a flat reusable `Vec<f32>` makes both the reuse and the contiguity trivial — the same refactor fixes both.)

**Impact:** Same 50×-per-block multiplier as the CRITICAL finding; the message arrays are written once and read (degree−1) times per node per iteration, so they are touched even more than the adjacency. Latency: directly on the per-frame decode path. The scatter on `msg_v_to_c[v][...]` is the second-worst indirection after the adjacency itself.

**Confidence:** Strong-static.

**Effort:** Contained → Cross-cutting (+low) — naturally folds into the CRITICAL CSR conversion (shared edge-ordinal index space). If done alone (flatten messages, keep adjacency nested) it's Contained but loses the index-space unification, so doing both together is preferred.

**Verification plan:** Fold into the CRITICAL benchmark; additionally `dhat`/heaptrack on a multi-decode loop to confirm the per-call allocation count drops from O(n) to O(1) after introducing reusable scratch. Correctness: bit-exact decode equivalence across a seed sweep.

---

### [MINOR] `incoming` LLR vectors are heap-allocated and gathered per check / per variable, every iteration

**Location:** `decode.rs:165-169` (check-update `incoming`), `decode.rs:193-197` (variable-update `incoming`).

**Problem:** Inside both hot loops a fresh `Vec<f32> incoming` is `.collect()`-ed per node per iteration by gathering through the nested message arrays. Two data-access costs: (a) the gather itself is the scatter described in the MAJOR finding (random-access into `msg_v_to_c[v][...]`); (b) the materialized `Vec` is a transient heap allocation whose only purpose is to be summed/box-plused immediately after. Node degrees are small and bounded (~3-19 for these codes), so the gather target fits trivially in a stack buffer — `arrayvec::ArrayVec<f32, MAX_DEGREE>` or `smallvec` eliminates the heap touch and keeps the gathered values in a contiguous, cache-hot stack region for the inner `boxplus`/sum loop. Once the CRITICAL/MAJOR CSR+flat-message refactor lands, `incoming` can often be elided entirely (iterate the contiguous edge slice directly), making this finding partly subsumed.

**Impact:** Per node, per iteration, per block — frequent, but each occurrence is a small bounded gather + small allocation, so MINOR on its own. The allocation-churn angle is the allocation lane's to size; the data-layout angle is that a contiguous stack gather replaces a scatter-into-heap-Vec. Latency contribution is real but secondary to the two findings above.

**Confidence:** Heuristic (degrees are bounded and small for the in-scope codes; the win is contingent on the inner loop staying gather-bound, which the CSR refactor may change).

**Effort:** Localized (if done independently) — swap `Vec` for `ArrayVec`/`SmallVec` with a degree-bound const. Or zero-effort-subsumed by the CRITICAL refactor.

**Verification plan:** Argument-based: bounded small-n stack gather strictly removes a heap allocation and a pointer indirection vs `Vec::collect`. Guard with the same decode-equivalence test. Only worth a standalone change if the CSR refactor is deferred; otherwise verify it's subsumed.

---

### Examined and found clean (in-dimension)

- **`encode.rs` Gaussian elimination + `encode()`** — the `Vec<Vec<u64>>` dense matrix and bit-packing (`encode.rs:80-122`) are **construction-time / cold** (run once per `Encoder::new`, not per-encode). `encode()` itself (`encode.rs:151-170`) walks `parity_eqs: Vec<Vec<usize>>` once per codeword — same nested-Vec pattern as decode, but encode is O(edges) *once* per block with no 50× iteration multiplier, and is a lighter path than decode. The u64-word-at-a-time XOR (`encode.rs:107-109`) is already the right word-level (not bit-by-bit) access for the cold elimination. Nested-Vec in `parity_eqs` is a candidate CSR target but its impact is an order of magnitude below decode; noting it, not flagging it.
- **`interleaver.rs`** — `interleave`/`deinterleave` use `BitVec` with `.set()`/`.push()` and a `row*cols+col` index. Access is bit-at-a-time rather than word-at-a-time, but this runs once per block (not per SPA iteration) and the index arithmetic is trivial; below the data-layout flag threshold for this latent, decode-dominated crate. Word-at-a-time `bitvec` tricks here would be cold-path micro-opt (calibration: excluded).
- **`codec.rs` `deinterleave_index_perm`** — recomputed on every `decode_soft` call (`codec.rs:149,192-204`); the permutation is invariant in `(n, rows)` and both are fixed per codec, so it could be precomputed once in `OfdmAdaptiveCodec::new`. This is *repeated index computation that could be precomputed* — squarely in-dimension — but it's O(n) per block vs decode's O(iters×edges), and `n` is small; recording as a low-impact note rather than a ranked finding. Worth folding in opportunistically when touching the decode path. (`llr_bitvec_signs` at `codec.rs:146` is a dead `_`-discarded allocation per decode — also low-impact, more a cleanliness item than data-layout.)
- **`llr.rs` `boxplus`** — pure scalar arithmetic, no data layout. (The `tanh`/`atanh` cost is a compute/idiom concern, already self-flagged for the min-sum approximation in the source comment; out of this dimension.)
- **`parity_matrix.rs` `parity_check` / `edge_count`** — `parity_check` walks `rows: Vec<Vec<usize>>` (nested, cache-hostile) but runs once per encode/verify, not in the SPA inner loop; the convergence check at `decode.rs:219-222` re-walks `check_to_vars` per iteration but that's the same adjacency already flagged in the CRITICAL finding.
- **`crc.rs`, `puncture.rs`, `stats.rs`, `codes/`** — `puncture.rs` is a stub; `codes/` is construction-time (cold, build-once); CRC and stats are not on the data-layout-sensitive inner loop. Not in-dimension targets.

---

## Suspected Bugs

None. (Correctness was out of scope; nothing in-dimension surfaced an obvious defect. The `llr_bitvec_signs` discarded witness at `codec.rs:146,151` is dead but harmless.)
