# Performance Audit — `tuxmodem-fec` — Dimension: Concurrency & Parallelization

**Auditor:** glade-knoll-shoal
**Date:** 2026-06-04T15:00
**Scope:** `tuxmodem/crates/tuxmodem-fec/src/**.rs` (excl. `bin/`)
**Dimension:** concurrency & parallelization ONLY
**Stack:** Rust 2021, MSRV 1.75. Single-threaded, no async runtime, no rayon, no I/O.
**Reachability posture:** LATENT — no in-tree callers (PHY dep present in Cargo.toml but
the live path bypasses FEC; `OfdmAdaptiveCodec` is constructed nowhere on a hot path yet).

---

## Dimension inventory (what was examined)

- `decode.rs` — the SPA belief-propagation decoder. This is the ONLY compute-heavy loop
  in the crate and the only plausible parallelization target. Read in full.
- `parity_matrix.rs` — `edge_count()`, sparse row-list `H` representation, sizes.
- `codes/mod.rs`, `codes/ofdm_wifi_family.rs`, `codes/floor_rate14.rs` — to establish
  the actual codeword sizes `n`, parity-row counts `m`, and edge counts that determine
  whether parallelization pays.
- `llr.rs` — `boxplus` (the per-edge transcendental kernel: 2×`tanh` + 1×`atanh`).
- `encode.rs`, `codec.rs`, `interleaver.rs`, `crc.rs`, `stats.rs`, `puncture.rs`,
  `lib.rs` — confirmed no threading, no shared state, no parallel structure.
- Grep across the whole `src/` tree for `rayon|thread|spawn|Mutex|RwLock|Arc|par_iter|atomic`:
  **zero matches.** No existing concurrency to defend; no shared mutable state.

### Sizes that gate the EXPLOIT decision

| Code | n | m = n−k | col-wt | edges ≈ | max_iters |
|---|---|---|---|---|---|
| WiFi N648 R1/2  | 648  | 324 | ~3 | ~1944 | 50 (`MAX_ITERS_OFDM`, codec.rs:36) |
| WiFi N1296 R1/2 | 1296 | 648 | ~3 | ~3888 | 50 |
| WiFi N1296 R5/6 | 1296 | 216 | ~3 | ~3888 | 50 |
| Floor R1/4      | 2048 | 1536| 3  | ~6144 | n/a — encoder panics, **not live** (floor_rate14.rs:28–34) |

So the realistic live codeword is n ≤ 1296, with ~2k–4k Tanner-graph edges. Per iteration
the decoder makes one `boxplus` pass per edge in the check-update inner loops plus a few
cheap sum passes; an upper bound across 50 iters is on the order of low-hundreds-of-thousands
of `boxplus` calls. `boxplus` (llr.rs:23) is the dominant per-edge cost (two `tanh`, one
`atanh`, transcendental).

---

## Independence verification (prerequisite for any EXPLOIT finding)

Belief propagation is the textbook data-parallel structure, but the report must be earned
against the *actual* code, not the textbook. Verified against `decode.rs`:

**Check-to-variable update (decode.rs:162–188).** The outer loop is `for c in 0..check_to_vars.len()`.
Each iteration:
- READS `msg_v_to_c[v][...]` (from the *previous* iteration / init) into a local `incoming` Vec.
- WRITES only `msg_c_to_v[c][i]` — i.e. row `c` of `msg_c_to_v`, which no other check `c' != c`
  touches.
There is no read of `msg_c_to_v` inside this loop and no write to `msg_v_to_c`. → **Checks are
mutually independent within this phase.** The write target is partitioned by `c`, so a
`par_iter_mut()` over `msg_c_to_v` rows (zipped with `check_to_vars`/`v_edge_pos`) is a
clean disjoint-write parallelization with no race.

**Variable-to-check update (decode.rs:191–204).** Outer loop `for v in 0..n`. Each iteration
READS `msg_c_to_v[c][...]` (just written, now read-only) and WRITES only `msg_v_to_c[v][...]`
(row `v`, disjoint across `v`). → **Variables are mutually independent within this phase.**
Same disjoint-write partition by `v`.

**Posterior + hard-decision (decode.rs:207–216).** Per-`v` write to `decoded[v]`, reads
read-only `msg_c_to_v`. Trivially independent (a `map`), but small.

**Convergence check (decode.rs:219–226).** Read-only fold over `decoded`; a parallel reduce
is possible but it is a cheap XOR pass — not worth it.

**Phase ordering is a hard serial dependency.** Check-update must fully complete before
variable-update reads `msg_c_to_v`; variable-update before posterior; all four before the
next `iter`. So parallelism is *within* each of the 3 main phases, with a join (barrier)
between phases and between iterations. With 50 iterations × 3 joinable phases, that is up to
**~150 fork/join barriers per decode** — each barrier amortized over only ~1–4k edges of work.

**Conclusion on independence:** the independence claim is TRUE and the disjoint-write partition
is clean (no shared-state mutation mid-phase, no atomics needed). The blocker is not
correctness — it is *granularity*.

---

## Findings

### [MINOR] Per-phase data parallelism in the SPA decoder does not pay at the live codeword sizes — do NOT add rayon now

**Location:** `decode.rs:162–188` (check-update), `decode.rs:191–204` (variable-update);
sizes from `codes/ofdm_wifi_family.rs:54` (n = z·24) and `codec.rs:36` (`MAX_ITERS_OFDM = 50`).

**Opportunity (and why it is being declined):** The check-node and variable-node updates are
genuinely independent within a phase (verified above), so `rayon`'s `par_iter_mut()` over
`msg_c_to_v` / `msg_v_to_c` rows would be a correct parallelization. But the arithmetic does
not support it at this scale:

- Live codewords are n ≤ 1296 with ~2k–4k edges. A single phase is ~2k–4k `boxplus` evaluations.
- That is on the order of a few tens of microseconds of serial work per phase (transcendental,
  but only thousands of them). Rayon's per-`join`/per-parallel-iterator dispatch + work-stealing
  + barrier overhead is itself on the order of microseconds to low-tens-of-microseconds *per
  fork/join*.
- The decoder forks/joins ~3 times per iteration × up to 50 iterations = **up to ~150 barriers
  per single-block decode**, each wrapping only ~1–4k edges. Thread-dispatch and barrier overhead
  would plausibly **dominate or exceed** the compute it parallelizes — a likely net *regression*,
  especially given early-termination (many decodes converge in well under 50 iters, making each
  decode even smaller).
- This is exactly the "speculative parallelization that wouldn't pay for the codeword size"
  case the audit charter says to NOT flag as actionable. Per the lens
  (`profile-packs/rust/data-parallelism.md` reasoning, and the core pack's
  "confirm no shared mutable state before parallelizing / benchmark before adopting"):
  rayon shines on large, coarse-grained data-parallel batches, not on thousands-of-elements
  inner loops behind a tight barrier.

**Where it COULD pay — batch-level, not node-level:** The realistic deployment unit is
*per-FEC-block decode*, and an HF frame carries **many independent blocks**. If/when the
integration drives N independent `decode()` calls per frame, the right parallel grain is
**one rayon task per block** (`blocks.par_iter().map(|llrs| decoder.decode(llrs, iters))`),
NOT per-node-within-a-block. `Decoder` is `&self` / immutable during decode (all per-decode
state is locally allocated in `decode()` — `decode.rs:137–156`), so it is trivially `Sync`
and shareable across block-tasks with zero locking. That coarse grain has a work-per-task of
a whole 50-iteration decode (hundreds of thousands of boxplus calls) — far above the
dispatch-overhead floor. **This is the parallelization to revisit at integration time**, and
it requires no change to `decode.rs` internals.

**Impact:** latent (no live caller). Per-occurrence: node-level parallelism would likely be a
latency *regression* at n ≤ 1296 due to barrier overhead; block-level parallelism is a real
throughput opportunity but only materializes once a multi-block caller exists, so it is not
yet reachable. Not on any hot path today.

**Confidence:** Strong-static for the independence analysis and the disjoint-write safety;
Heuristic for the overhead-dominates conclusion (no `criterion` numbers were taken — the
crate has no benches yet; benches are deferred to "Phase 8" per `Cargo.toml`).

**Effort:** Cross-cutting (+high) — node-level rayon would add a workspace dependency (a dep
change is cross-cutting by the audit's own rule) AND restructure the two inner loops; it is
NOT recommended. Block-level rayon at the (future) caller is Contained (+low) and is the one
to actually do, later.

**Verification plan (before EVER adding rayon):**
1. Land the deferred `benches/decode.rs` (`criterion`, release build, realistic n=648 and
   n=1296 LLRs at a representative SNR so iteration counts are realistic, not the zero-noise
   1-iteration case).
2. Measure serial single-block decode as the baseline.
3. ONLY if a profile shows decode is a real throughput bottleneck under the integrated load,
   prototype **block-level** `par_iter` at the caller (many blocks per frame) and compare
   serial-vs-parallel throughput with the *same* `criterion` harness. Require a clear win
   beyond noise before adopting.
4. Do NOT prototype node-level (within-block) rayon unless block-level is somehow unavailable
   (it won't be) AND a profile proves a single very-large-n decode dominates — and even then
   expect the barrier overhead to bite. Correctness guard if ever attempted: assert the
   parallel decode produces bit-identical `DecodeOutcome` to the serial path across a property
   test corpus (`proptest` is already a dev-dep) — `boxplus` is non-associative in f32, so a
   parallel *reduction* over an edge set could perturb results; the row-partitioned
   `par_iter_mut` form above does NOT reorder any reduction (each `msg_c_to_v[c][i]` is still
   computed by the same serial inner fold), so it is bit-exact, but this must be asserted, not
   assumed.

---

## Notes / non-findings (calibration — deliberately NOT flagged)

- **No shared mutable state, no locks, no atomics anywhere** (grep-confirmed). The DEFEND
  direction of this dimension is N/A: there is nothing to contend on, no critical section to
  shrink, no lock-across-await (no async at all), no false sharing (single thread).
- `Decoder` holds only immutable precomputed adjacency (`decode.rs:37–54`) and `decode()`
  takes `&self`; it is already `Send + Sync`-clean for future block-level fan-out. This is a
  *good* property, not a finding — noting it only because it makes the future block-level
  parallelization cheap.
- The serial per-iteration allocations (`incoming: Vec<f32>` rebuilt per check / per variable,
  `channel = llrs.to_vec()`) are an **allocation/memory** concern, not concurrency — out of
  scope here, deferred to the memory-dimension auditor.
- The `boxplus` exact-`tanh` kernel vs. the min-sum approximation (llr.rs:21–22, "Phase 8
  profiling may swap") is an **algorithmic/compute** concern (cheaper per-edge kernel), not
  concurrency — out of scope.

---

## Suspected Bugs

None. (No concurrency-introduced races — the crate is single-threaded. The independence
analysis surfaced no correctness issue in the existing serial code; the `boxplus`
f32 non-associativity note above is a *constraint on any future parallel reduction*, not a
bug in the current code.)
