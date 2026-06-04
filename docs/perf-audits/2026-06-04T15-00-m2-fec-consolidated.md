---
run_schema_version: 1
run_id: 2026-06-04T15-00-m2-fec
date: 2026-06-04T15:00:00Z
scope: "M2 — tuxmodem-fec LDPC FEC (encode + sum-product decode), entire crate src excl. bin/"
methodology:
  skill: performance-audit (within performance-audit-cycle)
  plugin_version: superpowers-plus@0.2.0
dispatch:
  model_requested: "latest-opus (Claude Code Agent subagents, model=opus)"
  reasoning_effort: "default (harness exposes no reasoning-effort knob)"
  overridden_by_user: false
stack:
  - { ecosystem: crates, framework: bitvec, version: "1" }
  - { ecosystem: crates, framework: crc, version: "3" }
  - { ecosystem: crates, framework: rand, version: "0.8" }
  - { ecosystem: crates, framework: rust, version: "edition 2021 / MSRV 1.75" }
currency_briefs:
  - { framework: crc, researched_on: null, status: "version-index (shipped rust.md) — crc@3 const-vs-static table idiom" }
lanes_run: [algorithmic, memory, data-access, concurrency, idiom-currency, cost-map]
lanes_skipped:
  payload-startup: "pure compute library — no payload/startup/bundle surface"
  dynamic: "deferred — and the crate is LATENT (no live caller), so a representative live workload does not yet exist; node-level micro-benchmarks would need a constructed harness"
finding_counts:
  by_impact: { critical: 1, major: 4, minor: 4 }
  by_lane: { algorithmic: 4, memory: 3, data-access: 3, concurrency: 0, idiom-currency: 5, cost-map: 0 }
  suspected_bugs: 1
regression:
  prev_run_id: null
  new: 9
  persisting: 0
  resolved: 0
---
# Performance Audit — M2: tuxmodem-fec (LDPC FEC)

**Date:** 2026-06-04 15:00   **Scope:** `tuxmodem/crates/tuxmodem-fec/src` (all `.rs` excl. `bin/`)
**Stack:** Rust 2021 / MSRV 1.75 · bitvec@1 · crc@3 · rand@0.8 · rand_chacha@0.3
**Currency brief:** shipped rust version-index (crc@3 const-vs-static table idiom; bitvec uncovered → Strong-static from library knowledge)
**Lanes run:** algorithmic, memory, data-access, concurrency, idiom-currency, cost-map (payload-startup + dynamic skipped — see frontmatter)
**Regression vs none (first run):** 9 new, 0 persisting, 0 resolved

> **⚠ LATENCY / REACHABILITY CAVEAT (governs every finding's Impact).** This
> crate currently has **NO in-tree callers**: the PHY dependency is commented out
> (`tuxmodem-phy/Cargo.toml:23`) and the live RX path uses hard-decision LLR
> slicing (`decode_symbol_bytes`) that bypasses soft FEC entirely ("real FEC
> plugs in once #4 lands"). So **reachability ≈ 0 today**; every finding is an
> *intrinsic-cost* item that fires **once FEC is integrated** (per received
> codeword, up to `MAX_ITERS_OFDM = 50` SPA iterations per decode — `codec.rs:35`).
> The value of acting now is that the SPA-loop rewrite below is a lossless
> refactor that is far cheaper and safer to do **before** FEC is wired into a
> live, regression-sensitive path than after. All six lanes calibrated to this
> correctly — a clean test of the skill's reachability handling.

## Executive summary

The decode hot center (`decode.rs` SPA loop, ~99% of per-block decode cost per
the cost map) carries one tight cluster of improvements that share a single
rewrite: an **O(d_c²) check-node update** that textbook forward-backward makes
O(d_c) (Q1), a **`Vec<Vec>` Tanner-graph layout** that should be flat CSR (Q2),
and **per-iteration per-node `Vec` allocations** (Q3). Two independent
library-idiom wins sit in the encode/CRC path (crc `const`→`static` table; bitvec
bulk packing). Concurrency's verdict is a deliberate **negative**: node-level
parallelism would *regress* at these codeword sizes; block-level fan-out at the
(future) caller is the real lever and needs no change here.

## Critical / headline findings

### Q1. Check-node update is O(d_c²) per check (forward-backward makes it O(d_c))
**Lanes:** algorithmic (MAJOR), cost-map (High)   **Location:** `decode.rs:171-187` (kernel `llr.rs:23` `boxplus`)
**Fingerprint:** `algorithmic:decode.rs:check_node_update:on2-boxplus`   **Status:** new (latent)
**Problem:** each outgoing check→var message re-folds `boxplus` over all *other* incident edges, costing O(d_c²) `boxplus` per check; the standard forward/backward (prefix/suffix) sweep is O(d_c). `boxplus` is the decoder's most expensive scalar (2×`tanh` + `atanh`), so the quadratic multiplies exactly the dominant transcendental: ~3.3× fewer at rate-1/2 (d_c≈6), **~9× at rate-5/6** (d_c≈18).
**Impact (latent):** once integrated, the dominant per-codeword decode cost; worst at high code rates. **Confidence:** Strong-static   **On cost map:** yes   **Effort:** Contained (rewrite the two SPA loops; do NOT turn `boxplus` into a running product — numerically unstable).
**Verification plan:** criterion micro-bench decode at rate-1/2 and rate-5/6, before/after; correctness guard = decode output bit-identical on existing roundtrip tests + an added fixed-LLR golden (the leave-one-out math must be exactly preserved).

### Q2. Tanner-graph adjacency + messages are `Vec<Vec<…>>` (pointer-chasing); flat CSR fixes locality
**Lanes:** data-access (CRITICAL), memory (cross-cutting), algorithmic (implied)   **Location:** `decode.rs:41-53` (cached adjacency), `:142-152` (per-call message arrays), root `parity_matrix.rs:22`
**Fingerprint:** `data-access:decode.rs:adjacency:nested-vec-no-csr`   **Status:** new (latent)
**Problem:** all six working structures (`var_to_checks`, `check_to_vars`, `v_edge_pos`, `c_edge_pos`, plus per-decode `msg_v_to_c`/`msg_c_to_v`) are nested-`Vec` forests — each inner `Vec` a separate heap allocation, so consecutive node indices land on arbitrary cache lines and the prefetcher can't follow the data-dependent base loads. Every iteration chases 3–5 unrelated heap regions per node across ~all edges (~2k–6k edges), pulling a full line to use tens of useful bytes.
**Impact (latent):** the dominant *per-iteration* memory cost once integrated. **Confidence:** Strong-static   **On cost map:** yes   **Effort:** Cross-cutting-but-localized to `decode.rs` (canonical LDPC CSR: flat `data` + prefix-sum `offsets`); message arrays flatten to one edge-indexed `Vec<f32>` in the same ordinal space.
**Verification plan:** criterion decode bench (cache-miss-bound, so wall-time + `perf stat` cache-miss delta) before/after; correctness guard = bit-identical decode + the added golden.

## Major findings

### Q3. Per-iteration per-node `incoming: Vec<f32>` heap allocation (×(m+n)×iters per decode)
**Lanes:** memory (MAJOR), algorithmic (MAJOR), data-access (MINOR), cost-map   **Location:** `decode.rs:165-169` (per-check), `:193-197` (per-variable)
**Fingerprint:** `memory:decode.rs:decode:per-iteration-incoming-alloc`   **Status:** new (latent)
**Problem:** both half-iterations `collect()` a fresh short-lived `Vec` per node inside the `for iter` loop — ~97k–179k alloc/free pairs per worst-case (50-iter) decode. Hoist to reused scratch buffers (`clear()`+reuse, or a `SmallVec`/`ArrayVec` given bounded degree); largely **subsumed by the Q1+Q2 rewrite** (do them together).
**Confidence:** Strong-static   **Effort:** Contained (+low)   **Verification plan:** `dhat` alloc-count before/after; correctness guard = bit-identical.

### Q4. crc@3 `Crc::<u32>` table is a `const` (inlined per use site) — should be `static`
**Lanes:** idiom-currency (MAJOR)   **Location:** `crc.rs:13` (def), used `:46,65`
**Fingerprint:** `idiom-currency:crc.rs:CRC32:const-table-duplication`   **Status:** new (latent)
**Problem:** `Crc::<u32>::new` builds a 256-entry / 1 KiB slice-by-1 table; `const` is inline-substituted per use site (per monomorphization), duplicating the table rather than sharing one instance. (The *severe* form — rebuilding per call — is correctly avoided.) `Crc::new` is `const fn`, so `static CRC32` needs no `OnceLock` at MSRV 1.75.
**Confidence:** Strong-static (index lines 60–62)   **Effort:** Localized (`const`→`static`)   **Verification plan:** confirm single table instance (codegen/size); correctness guard = CRC test vectors unchanged.

### Q5. Hand-rolled bit-by-bit packing where bitvec@1 bulk ops are the fast path
**Lanes:** idiom-currency (MAJOR)   **Location:** `crc.rs:49-51,77-101` (`bits_to_bytes`/`bits_to_u32_msbfirst`/`append_crc32`)
**Fingerprint:** `idiom-currency:crc.rs:bits_to_bytes:per-bit-pack`   **Status:** new (latent)
**Problem:** per-bit `b |= 1<<(7-i)` and 32 individual `push`es where `BitField::load_be`/`store_be` + `chunks_exact(8)` do it word-at-a-time. MSB-first ordering → must use big-endian `BitField` methods (correctness caveat).
**Confidence:** Strong-static   **Effort:** Contained   **Verification plan:** bench + byte-identical CRC/packing output.

## Minor findings

- **Q6** [algorithmic] Posterior re-gathers the `total_sum` the variable-update already computed — redundant Θ(E)/iter pass. `decode.rs:207-216` (vs `:199`). Fuse/carry-forward. Fingerprint `algorithmic:decode.rs:posterior:redundant-resum`.
- **Q7** [memory/data-access] `deinterleave_index_perm(n, ROWS)` rebuilt per `decode_soft` though invariant — cache in `new`. `codec.rs:149,192-204`. Fingerprint `memory:codec.rs:decode_soft:per-call-perm-rebuild`.
- **Q8** [algorithmic/memory] `channel = llrs.to_vec()` unnecessary per-decode copy (never mutated — borrow). `decode.rs:137`. Fingerprint `memory:decode.rs:decode:needless-llr-copy`.
- **Q9** [idiom-currency] interleaver/encoder systematic-prefix per-bit `set`/`push` → `extend_from_bitslice`/`to_bitvec`. `interleaver.rs:37-39,81-85`; `encode.rs:160-163`. Fingerprint `idiom-currency:interleaver.rs:prefix:per-bit-copy`.

## Concurrency — deliberate negative (no finding, design note)
The SPA check/variable updates are genuinely independent within a phase (writes partitioned by node index, verified `decode.rs:162-204`), but at live codeword sizes (n ≤ 1296, ~2k–4k edges; floor n=2048 is not live — encoder panics `floor_rate14.rs:28-34`) the ~150 fork/join barriers per decode (3 phases × ≤50 iters) would let rayon overhead **dominate** — node-level parallelism is a likely *regression*. The lever is **block-level** fan-out at the future caller: `Decoder::decode` is `&self` with all state locally allocated, already `Sync`, zero locking — revisit at integration, no change to `decode.rs`.

## Cross-cutting theme
**Q1 + Q2 + Q3 are one rewrite** of the SPA inner loops: forward-backward check update + flat CSR adjacency/message layout + reused scratch. Doing them together (before FEC is wired into a live path) is the highest-leverage M2 change. Q4/Q5/Q9 are independent encode/CRC-path idiom wins.

## Measurability
The crate **builds and its (thin, ~22-file but weak-assertion) test suite passes** (verified `cargo test -p tuxmodem-fec` green, 6 test cases). But it is **latent**, so no live workload exists — dynamic measurement requires a constructed decode harness (defensible: feed known codewords + AWGN LLRs at the two code rates). Recommend a criterion bench at integration time; today the findings are Strong-static.

## Execution Cost Map (architectural awareness)
Per the cost-map lane (all currently dead from any runtime path): **`Decoder::decode` SPA loop** dominates per-block (~99%); within it the **check-to-variable update** is the densest knot (O(row_len²) `boxplus`); `boxplus` (`llr.rs:23`) is the hot leaf (transcendentals). `Encoder::try_new` Gaussian elimination (`encode.rs:74-126`, ~O(m²n/64), "~5s for the floor code" per source) is **construction-time only**, never per-block — high unit cost, cold path. Interleave/CRC/matrix-construction are O(n)/O(E), cold or cheap-per-block. Early-termination `break` (`decode.rs:225`) keeps typical iterations well under 50; the 50× is the worst-case latency budget.

## Suspected Bugs (for follow-up — NOT addressed here)
### SB-M2-1. `decode_soft` builds and discards `llr_bitvec_signs`
**Location:** `codec.rs:146,151`   **What looks wrong:** computed then thrown away via `let _ = …` — Θ(n) dead work per decode.   **Why suspected:** likely a vestigial/incomplete code path; a correctness reviewer should confirm it isn't a dropped step. (Also: the floor code is currently `RankDeficient` so its encode path panics — pre-existing, tracked elsewhere.)
