# Performance Audit — `tuxmodem-fec`, dimension: algorithmic complexity & data structures

Agent: glade-knoll-shoal
Date: 2026-06-04T15:00
Scope: `tuxmodem/crates/tuxmodem-fec/src/**` (excl. `bin/`)
Dimension: algorithmic complexity & data structures ONLY.

## Reachability calibration

The crate is **LATENT**: no in-tree caller (PHY dep commented out; live RX uses
hard-decision slicing bypassing soft FEC). Findings are ranked at the
**intrinsic cost they WILL have once FEC is wired into the per-received-FEC-block
path** — once per received codeword/frame, NOT per OFDM symbol. `decode` runs up
to `max_iters` (= `MAX_ITERS_OFDM` = 50 for the OFDM family) SPA iterations per
call. All findings noted "fires once FEC is wired in."

## Block sizing (derived from `codes/`)

| Code | n | m=n−k | edges E (≈) | avg row wt d_c | avg col wt d_v |
|---|---|---|---|---|---|
| OFDM n648 r1/2 | 648 | 324 | ~1944 | ~6 | 3 |
| OFDM n1296 r1/2 | 1296 | 648 | ~3888 | ~6 | 3 |
| OFDM n648 r5/6 | 648 | 108 | ~1944 | ~18 | 3 |
| floor r1/4 | 2048 | 1536 | 6144 | 4 | 3 |

Edge count E ≈ 3·n (column weight ~3). Per-iteration SPA work is Θ(E) at best.
Decode total ≈ 50 · (check-update + var-update + posterior + syndrome) over E edges.

---

## Findings

### [MAJOR] Check-node update is O(d_c²) per check instead of O(d_c) — quadratic in row weight, on the densest transcendental path

**Location:** `decode.rs:171-187` (the `for i in 0..vars.len()` loop with the nested `for (i2, &m) in incoming.iter()`).

**Problem:** For each check node `c` with degree `d_c`, the code computes each
outgoing message `msg_c_to_v[c][i]` by folding `boxplus` over all `i2 != i`. That
is a fresh leave-one-out reduction per output edge: `d_c` outputs × `(d_c − 1)`
boxplus calls = **O(d_c²) boxplus per check**. The standard forward-backward
(prefix/suffix) technique computes all `d_c` leave-one-out boxplus results in
**O(d_c)** total: one forward pass accumulating prefix box-plus, one backward pass
accumulating suffix, then `out[i] = boxplus(prefix[i], suffix[i])`.

`boxplus` (`llr.rs:23-28`) is the single most expensive scalar op in the decoder:
two `tanh` + one `atanh` (three transcendentals) plus a clamp per call. The
quadratic factor multiplies exactly this op.

**Impact:** Per iteration the check-update does `Σ_c d_c·(d_c−1)` boxplus calls
vs. the achievable `Σ_c (2·d_c − 3)`-ish. For the OFDM r1/2 family (d_c≈6) the
ratio is ~30 vs ~9 boxplus/check → ~3.3× more transcendentals on this step. For
the **r5/6 family d_c≈18** it is ~306 vs ~33 → **~9× more transcendentals**, and
worse as rate climbs. Multiply by m checks × up to 50 iterations. The check-node
update is the dominant compute block of the decoder; this is the largest
single-step algorithmic win available. Latency: directly inflates per-codeword
decode latency (once per received FEC block once wired in), worst at high code
rates where d_c is largest. Confidence: **Strong-static** — quadratic structure
is plain in the loop nest; boxplus cost dominance is a complexity argument, not
measured. **Effort:** Contained (low) — rewrite one loop body in `decode.rs`
with two scratch arrays; correctness guarded by existing round-trip tests.

**Verification plan:** Count boxplus invocations (instrument a counter) before/after
on n648 r1/2 and r5/6; assert the forward-backward count matches `Σ(2d_c−2)`.
Re-run `decode_zero_noise_returns_input`, `decode_one_bit_flip_recovers`, and the
codec round-trip tests for bit-exact-equivalent convergence. Optional criterion
bench on a release build over the r5/6 code to quantify.

---

### [MAJOR] Per-node `incoming` Vec heap-allocated inside the iteration loop — (m + n) allocations per iteration × max_iters

**Location:** `decode.rs:165-169` (check-update `incoming: Vec<f32>`), `decode.rs:193-197`
(var-update `incoming: Vec<f32>`), and the posterior `.map(...).sum()` at
`decode.rs:207-214` (no Vec but re-gather).

**Problem:** Both update loops `.collect()` a fresh `Vec<f32>` for every node on
every iteration. Across one `decode` call that is `(m + n) · max_iters` short-Vec
heap allocations + frees: e.g. floor code (m=1536, n=2048) × 50 = **~179,000
alloc/free pairs per decode**; OFDM n1296 r1/2 (m+n=1944) × 50 = ~97,000. Each
`incoming` is tiny (d_c or d_v elements, ~3–18) — the allocation overhead dwarfs
the useful work per Vec. These are classic loop-body allocations that should be
hoisted into a reused scratch buffer (`Vec` declared outside the iteration loop,
`clear()` + `extend` inside, preserving capacity), or eliminated entirely by
indexing `msg_v_to_c`/`msg_c_to_v` directly in the forward-backward rewrite.

**Impact:** Allocator traffic proportional to `(m+n)·max_iters` per decoded block,
once per received FEC block when wired in. On a Pi-class target the allocator and
the cache churn from these transient buffers add measurable per-codeword latency
on top of the arithmetic. Confidence: **Strong-static** — the `.collect()` inside
the per-iteration, per-node loop is explicit. **Effort:** Contained (low) — two
scratch `Vec<f32>` (sized to max degree) declared before the `for iter` loop,
`clear()`/`extend` per node; folds naturally into the Finding-1 rewrite.

**Verification plan:** `dhat` or a custom global-allocator counter wrapping one
`decode` call before/after; assert allocation count drops from `O((m+n)·iters)` to
`O(1)` per decode (the two scratch buffers + outputs). Round-trip tests guard
correctness.

---

### [MINOR] Posterior step re-gathers check-to-variable messages already summed in the variable-update step

**Location:** `decode.rs:207-216` (posterior loop) vs. `decode.rs:191-204` (var-update loop).

**Problem:** The variable-update loop already computes, for each `v`, the full
`total_sum = Σ_j msg_c_to_v[c][...]` over all adjacent checks (`decode.rs:199`).
The posterior loop immediately afterward re-walks the same `var_to_checks[v]`
adjacency, re-indexes `msg_c_to_v` through `c_edge_pos`, and re-sums the identical
quantity to form `post = channel[v] + Σ ...`. That sum is exactly
`channel[v] + total_sum` from the var-update — recomputed a second time per
variable per iteration. The two loops could be fused, carrying `total_sum` forward
(e.g. store `channel[v] + total_sum` while iterating variables, or compute the
hard decision inside the var-update loop), eliminating a full `Σ_v d_v = E` gather
+ add per iteration.

**Impact:** One redundant Θ(E) pass per iteration (E ≈ 3n adds + the
`c_edge_pos` double-indirection loads). Small next to the boxplus cost but it is
pure recomputation of an already-available value, every iteration × max_iters.
Latency: minor constant addition to per-block decode. Confidence: **Strong-static**
— the recomputed sum is textually identical. **Effort:** Localized (low) — fuse
or carry one scalar; guard with round-trip tests (hard-decision values must be
bit-identical).

---

### [MINOR] `total_sum - incoming[j]` leave-one-out is fine; noting the contrast as the *correct* pattern (no action)

**Location:** `decode.rs:199-203`.

**Problem / note:** The variable-update step correctly uses the
sum-minus-self trick (`channel[v] + total_sum − incoming[j]`) to get all
leave-one-out sums in O(d_v) — the additive analog of the prefix/suffix technique
Finding 1 wants for the *check* node. This is included only to document that the
O(d²)→O(d) fix is already applied on the additive (variable) side and is merely
*missing* on the multiplicative/boxplus (check) side; boxplus has no exact
subtraction inverse, hence the forward-backward array approach rather than a
running "divide-out." **No change required here**; do not "optimize" this into a
running product (numerically unstable for boxplus).

---

### [MINOR] `Decoder::new` edge-inverse construction uses `.position()` linear scans — O(E · d) one-time, cold

**Location:** `decode.rs:73-99` (`v_edge_pos` and `c_edge_pos` built via
`.iter().position(...)`).

**Problem:** For each edge, `v_edge_pos`/`c_edge_pos` find the inverse index with a
linear `.position()` scan over the adjacency list (`var_to_checks[v]` / `check_to_vars[c]`).
That is O(E · d̄) where d̄ is average degree — e.g. floor code E=6144 × ~3 ≈ 18K
comparisons. This is **construction-time, once per codec lifetime** (codec built
up-front in `OfdmAdaptiveCodec::new`), not on the per-decode path.

**Impact:** Negligible — runs once at codec construction, off the hot decode path.
Listed for completeness only; the small `d̄` (≤18) makes the inner scan cheap and
the construction is amortized over every block decoded with that codec.
Confidence: **Strong-static.** **Effort:** Localized — would replace `.position()`
with a counter map keyed during the forward adjacency build, but **not worth it**
given the cold, one-time reachability. Recommend no action unless construction
latency ever matters (it does not on this path).

---

### [MINOR] `channel` Vec copied from `llrs` on every decode

**Location:** `decode.rs:137` (`let channel: Vec<f32> = llrs.to_vec();`).

**Problem:** `decode` copies the input LLR slice into an owned `Vec`. `channel` is
read-only throughout decoding; the function could index `llrs` directly and drop
the copy. One Θ(n) allocation + memcpy per decode (n ≤ 2048 f32 = 8 KB).

**Impact:** One n-element allocation + copy per decoded block — trivial next to the
50-iteration message passing, but pure avoidable work on the per-block path once
wired in. Latency: negligible. Confidence: **Strong-static.** **Effort:** Localized
(low) — replace `channel[v]` reads with `llrs[v]`; the `msg_v_to_c` init at
`decode.rs:146` also reads `channel[v]` and would read `llrs[v]`. Guarded by
round-trip tests. Low priority; mention chiefly because it pairs naturally with the
Finding-2 scratch-buffer cleanup.

---

## Summary of ranked algorithmic findings

1. **MAJOR** `decode.rs:171-187` — O(d_c²) check-node boxplus; use forward-backward O(d_c). ~3.3× (r1/2) to ~9× (r5/6) fewer transcendentals on the dominant step.
2. **MAJOR** `decode.rs:165,193` — per-node `incoming` Vec allocated inside the iteration loop; ~(m+n)·max_iters allocs/decode. Hoist to scratch buffers.
3. **MINOR** `decode.rs:207-216` — posterior re-sums the `total_sum` already computed in var-update; fuse/carry forward.
4. **MINOR** `decode.rs:199-203` — (no action) documents that the additive leave-one-out trick is correctly applied variable-side and merely missing check-side.
5. **MINOR** `decode.rs:73-99` — `.position()` linear scans in edge-inverse build; cold one-time, no action.
6. **MINOR** `decode.rs:137` — needless `llrs.to_vec()` copy per decode; index input directly.

Findings 1 + 2 are the only material wins; both live in the same two loops in
`decode.rs` and are most naturally fixed together in a single forward-backward
rewrite that also removes the loop-body allocations. Everything else is cold or
constant-small. The adjacency representation itself (CSR-style `var_to_checks` /
`check_to_vars` + O(1) edge-inverse maps `v_edge_pos`/`c_edge_pos`) is already the
right data structure — no wrong-container findings; the inverse maps correctly
avoid per-iteration `.position()` scans on the hot path.

## Suspected Bugs (for follow-up)

None within this dimension. (One observation, not a perf finding and not chased:
`codec.rs:146-151` computes `llr_bitvec_signs` and immediately discards it via
`let _ =`; harmless dead computation, flagged here only so a correctness reviewer
can decide whether it was meant to gate something. It is Θ(n) wasted work per
decode but trivial vs. the SPA loop, hence not ranked above.)
