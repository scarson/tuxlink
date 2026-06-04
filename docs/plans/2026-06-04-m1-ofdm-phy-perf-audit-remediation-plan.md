# M1 OFDM PHY Performance-Audit Remediation Plan

**Plan date:** 2026-06-04
**Author:** agent glade-knoll-shoal
**Source audit:** [`docs/perf-audits/2026-06-04T14-24-m1-ofdm-phy-consolidated.md`](../perf-audits/2026-06-04T14-24-m1-ofdm-phy-consolidated.md) (run_id `2026-06-04T14-24-m1-ofdm-phy`)
**Scope crate:** `tuxmodem/crates/tuxmodem-phy/src` (excl. `bin/`)

## Goal

Remediate all 12 performance findings (P1–P12) from the M1 OFDM-PHY
performance audit, plus the co-located off-by-one (SB1) that shares
P2's function. The dominant root cause — **frame-invariant DSP state
rebuilt at per-symbol / per-subcarrier cadence inside the hot loops** —
is fixed once by introducing a frame-scoped `OfdmContext`, then each
finding's call site migrates onto it. Every change MUST preserve
numerical results exactly (bit-identical roundtrips, unchanged detected
sync offsets, unchanged argmax decisions). This is an optimization
pass, not a behavior change.

## Architecture

`tuxmodem-phy` is **batch/offline DSP feeding a thin real-time audio
callback**. The heavy per-symbol work runs in `transmit_multi` /
`receive_multi` (ahead-of / after streaming), NOT inside the cpal
callback — so the per-symbol allocations do not compete with the audio
deadline. The RT risk surface is narrow: the input-callback mutex (P5)
and the playback mono-expand (P10).

The remediation has two structural pieces:

1. **The `OfdmContext` spine** (Tasks 1–7) — a frame-scoped context
   built once per `transmit_multi` / `receive_multi` invocation,
   holding the `Arc<dyn Fft>` plan(s), the equalizer, a pilot
   *membership mask* (replacing the per-symbol `HashSet`), the
   per-subcarrier `Mapper`s/alphabets, and the precomputed index
   vectors. The symbol loops borrow it. This collapses P1, P3, P4, P6,
   P9, P11(OFDM-side N/A — see P11 task), P12. Introduced first
   (Task 1), then call sites migrate onto it one finding at a time.

2. **Independent localized fixes** (Tasks 8–12) — P2 (+SB1), P5, P7,
   P10, P8. These touch disjoint files from the spine and from each
   other, so they parallelize.

**Tech Stack:** Rust 2021 / MSRV 1.75 · rustfft@6 · num-complex@0.4 ·
hound@3 · cpal (feature-gated `audio-device`). No criterion in
dev-deps; verification uses **timed loops, alloc-counts, and explicit
complexity/allocation arguments** rather than a criterion harness
(adding criterion is out of scope — do NOT add it).

## Living Document Contract

This plan is a living document. Every executing agent MUST update it as
execution progresses, not only at completion.

- **On phase claim:** the executor MUST flip the banner to 🚧 IN PROGRESS
  with a claim timestamp (ISO 8601 UTC) and the active branch name. The
  banner MUST NOT include an expected-completion estimate — agents cannot
  reliably estimate their own wall-clock, and a fabricated duration
  becomes a stale anchor that misleads future readers. Followers
  encountering a 🚧 banner determine liveness by observable signals (PR
  existence, recent branch commits), not by arithmetic on expected times.
  See Step 5's stale-claim reclaim protocol.
- **On phase ship:** the executor MUST update that phase's **Execution
  Status** banner with the shipped commit SHA(s) and date. If a PR is
  open, the PR number and URL MUST appear in the top-of-plan Execution
  Status table.
- **On phase defer:** the executor MUST update the banner with ⏸ status
  AND a prose description of the unblock condition + a link to the
  likely-unblocker artifact (plan page, task, or PR whose own Execution
  Status banner will signal completion). Prose + link is durable across
  paraphrases and scope edits; exact-string coordination between agents
  is not.
- **On PR merge:** the executor MUST record the merge SHA in the banner
  + the top-of-plan Execution Status table.
- **On deviation from the written plan** (scope edits, structural
  refactors, dropped tasks, reordered phases): the executor MUST
  inline-document the deviation in the affected task AND summarize it
  in the top-of-plan Execution Status as a "Deviations" subsection.
  Deviation state MUST NOT live only in PR notes or status reports.
- **On discovery** (pre-existing drift surfaced during execution, new
  bugs found, architectural issues noted): the executor MUST add a
  "Discoveries" subsection at the top of the plan with pointers to the
  files/lines affected. Follow-up dispatches read this subsection to
  avoid duplicate discovery work.

The plan SHOULD reflect reality at the end of every session that touches
it. Anything worth putting in a status report to the user is worth
putting in the plan.

Rationale: `/writing-plans-enhanced` Step 5. Writing at ship time is
cheap; reconstruction by downstream readers is expensive, compounds
across dispatches, and fails silently when state is split across PR
notes and commit messages.

## Execution Status

**Overall:** Not started. 0/12 tasks shipped.

| Task | Finding(s) | Status | Ship SHA(s) | Notes |
|---|---|---|---|---|
| 1 — Introduce frame-scoped `OfdmContext` | (spine) | ⬜ Not started | — | enables 2–7 |
| 2 — Cache FFT/IFFT plan on context | P1 | ⬜ Not started | — | depends on 1 |
| 3 — Build equalizer + pilot mask once | P3 | ⬜ Not started | — | depends on 1 |
| 4 — Precompute Mapper/alphabet per bit-loading | P4 | ⬜ Not started | — | depends on 1 |
| 5 — Presize/reuse RX+TX scratch buffers | P6 | ⬜ Not started | — | depends on 1–4 |
| 6 — Memoize `OfdmParams::data_indices` | P9 | ⬜ Not started | — | depends on 1 (or standalone) |
| 7 — Cache preamble template per frame | P12 | ⬜ Not started | — | depends on 1 |
| 8 — Preamble running-sum energy + SB1 fix | P2, SB1 | ⬜ Not started | — | independent |
| 9 — RT input callback lock-free path | P5 | ⬜ Not started | — | independent; removes SB3 |
| 10 — narrow-FSK planner/buffer hoist + norm_sqr | P7, P11 | ⬜ Not started | — | independent |
| 11 — Playback mono-expand in callback | P10 | ⬜ Not started | — | independent |
| 12 — Streaming `read_wav` API | P8 | ⬜ Not started | — | sequence LAST |

### Deviations
_(none yet)_

### Discoveries
_(none yet)_

---

## Coverage map (all 12 findings scheduled — disposition discipline)

| Finding | Default disposition | Task | Mechanism if deferred |
|---|---|---|---|
| P1 — per-symbol FftPlanner rebuild | FIX | Task 2 | — |
| P2 — preamble O(N·M) energy recompute | FIX | Task 8 | — |
| P3 — per-symbol object/equalizer/HashSet rebuild | FIX | Task 3 | — |
| P4 — per-subcarrier alphabet/Mapper rebuild | FIX | Task 4 | — |
| P5 — RT callback mutex across copy | FIX | Task 9 | — |
| P6 — per-symbol Vec churn (RX/TX) | FIX | Task 5 | — |
| P7 — narrow-FSK planner/buffer + .norm() | FIX | Task 10 | — |
| P8 — read_wav whole-file buffer | FIX (sequenced LAST) | Task 12 | — |
| P9 — data_indices() per-call rebuild | FIX | Task 6 | — |
| P10 — playback mono-expand copy | FIX | Task 11 | — |
| P11 — FSK tone-bin indices per symbol | FIX | Task 10 | — |
| P12 — preamble template per-frame regen | FIX | Task 7 | — |
| SB1 — preamble scan loop off-by-one | FIX (co-located w/ P2) | Task 8 | — |

**No deferrals.** All of P1–P12 are scheduled. The "Deferred"
appendix below is empty. Per disposition discipline: a finding may be
deferred ONLY with a substantive named mechanism (specific refactor
collision / dependency bump / correctness regression) — never on
severity or effort grounds. None of these findings has such a
mechanism.

---

## Per-task TDD + verification preamble (applies to EVERY task)

Every task below MUST begin and end with the following. Do not skip
these for "obvious" optimizations — the entire risk of a perf change is
silent behavior drift.

```
BEFORE starting work:
1. Invoke /superpowers:test-driven-development.
2. Read docs/pitfalls/testing-pitfalls.md and docs/pitfalls/implementation-pitfalls.md.
3. Capture the task's BASELINE (see the task's "Baseline" line): a
   measurement (timed loop over the named entry point, and/or an
   alloc-count) OR, where measurement is impractical, an explicit
   written complexity/allocation argument. Record the number/argument
   in the task's notes in this plan.
4. Per TDD: write the CORRECTNESS-GUARD test FIRST and watch it pass
   against the UNCHANGED code (it pins current behavior — it is a
   characterization test, so it is green before the change; the "red"
   you must see is that the guard FAILS if you deliberately perturb the
   output, which you should sanity-check once).
```

**Golden-capture mechanism — PINNED (one mechanism for every "add a
golden" guard in this plan).** Wherever a task says "pin a golden" /
"golden sample vector" / "golden LLR", use EXACTLY this: capture the
output as an exact `[f32; N]` literal hard-coded in the test (or, if N is
large, a small committed fixture file), and compare at **tolerance 0.0
(bit/float-exact equality)**. These are lossless refactors — the output
must be bit-identical, so an exact comparison is correct and is the
red/green signal. Do NOT use a hash, a "byte-identical" byte compare, a
"first-N-samples" heuristic, or an approximate tolerance for these
guards; those phrasings elsewhere in this plan all mean this one
mechanism. (The pre-existing `< 1e-6` preamble pins referenced in Task 7
are existing tests, not new goldens, and are left as-is.)

**Why ADD these guards (existing tests are too weak).** The crate has
~22 integration test files, NOT a thin suite — but their assertions are
weak: sign/length-only (`constellations_llr`), clean-channel roundtrip
only (`floor_narrow_fsk`), ±tolerance (`sync_preamble` ±32,
`audio_io` 1e-4). None would catch the silent numerical drift these
optimizations risk. So the bit-exact behavior-pinning guards MUST be
ADDED first as the TDD red step — this STRENGTHENS the existing
verification mandate, it does not replace or weaken it.

```
BEFORE marking this task complete:
1. POST-CHANGE demonstration: re-run the baseline measurement (or
   restate the complexity/allocation argument) and confirm it improved.
   IF IT DID NOT IMPROVE, REVERT the change — a perf change that does
   not measurably reduce work is not worth its risk.
2. CORRECTNESS GUARD: run `cargo test -p tuxmodem-phy` — ALL existing
   tests pass, specifically the roundtrip families
   (`multi_roundtrip_*`, `multi_with_preamble_roundtrip_*`,
   `preamble_roundtrip_*`), `constellations_llr`, and the FSK roundtrip
   tests. PLUS the new behavior-pinning guard this task added passes.
3. Review tests against docs/pitfalls/testing-pitfalls.md; verify edge
   cases (empty payload, max QAM order where relevant, multi-symbol
   boundary).
4. Confirm green.
```

**Assertion-rigor rule (Tasks 9 especially — concurrency/timing):** if
any test races, flakes, or fails nondeterministically, the fix is
deterministic synchronization (barrier/atomic fence/condvar), NOT
assertion removal or weakening. If synchronization cannot make it pass
reliably, STOP and raise to the dispatching agent. Do not ship a weaker
test. A commit touching test assertions states what happened to them
("add"/"strengthen"/"preserve") in its subject.

**Universal "do NOT touch" boundaries (all tasks):**
- Do NOT rewrite the DSP math (FFT scaling, ZF equalizer formula, Gray
  mappings, Zadoff-Chu generation, LLR max-log metric). Preserve
  numerical results bit-for-bit.
- Do NOT change the public PHY API except where Task 12 (P8) explicitly
  requires it.
- Do NOT add a benchmarking dependency (criterion). Use timed loops /
  alloc-counts / arguments.
- Do NOT "while I'm here" refactor adjacent code. Minimum change per
  task.

---

## Phase A — The `OfdmContext` spine (Tasks 1–7)

These tasks are **sequentially dependent**: Task 1 creates the context;
Tasks 2–7 migrate findings onto it. They mostly touch the same files
(`receiver.rs`, `transmitter.rs`, `wideband_lowdensity.rs`,
`ofdm_params.rs`), so they MUST be executed in order on the same branch
(or with explicit rebasing) — parallel subagents on these tasks would
collide. Task 6 (P9) is the one exception that could stand alone, but
keep it in this phase for cohesion.

### Task 1 — Introduce a frame-scoped `OfdmContext` built once per transmission, replacing per-symbol object construction [perf spine for P1/P3/P4/P6/P12]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** Today `WidebandLowDensityFloor::transmit_multi`'s
per-symbol loop (`robustness_floor/wideband_lowdensity.rs:233-235`) and
`receive_multi`'s per-symbol loop (`:334-338`) call
`OfdmTransmitter::new` / `OfdmReceiver::new` **one call-frame down from
the loop** — the constructors themselves are NOT at the loop lines:
- TX: the loop at `:234` calls `self.transmit(chunk)`, and
  `OfdmTransmitter::new` is at **`:71` inside `transmit`**.
- RX: the loop at `:336` (and the first-symbol path) calls
  `self.decode_symbol_bytes(...)`, and `OfdmReceiver::new` is at
  **`:175` inside `decode_symbol_bytes`**.

A subagent navigating to `:233`/`:334` finds the loops, not the
constructors — go to `:71`/`:175` for the actual `::new` call sites.
Those constructors and the symbol bodies in turn rebuild
frame-invariant state every symbol (FFT planner, equalizer, pilot
`HashSet`, mappers, index vectors). All of that is a pure function of
the immutable `OfdmParams`. This task introduces the container that
later tasks fill; it does NOT yet move any specific computation — it
establishes the seam.

**Minimum change (this task only):**
- Add a new module `ofdm_main/context.rs` defining
  `pub struct OfdmContext<'a> { params: &'a OfdmParams, /* fields
  added by Tasks 2–4,7 */ }` with `OfdmContext::new(params: &'a
  OfdmParams) -> Self` that does the build-once work (initially just
  stores `params`).
- Register the new module: add `pub mod context;` (or `mod context;` +
  a re-export) in `ofdm_main/mod.rs` so the module compiles and is
  reachable.
- Change `decode_symbol_bytes` and `transmit_multi`'s loop so the
  context is built ONCE before the loop and borrowed inside it. The
  per-symbol `OfdmReceiver::new`/`OfdmTransmitter::new` calls move out
  of the loop (build once, reuse) OR become methods on the context.
  Pick the methods-on-context approach: `ctx.demodulate_one_symbol(...)`
  / `ctx.modulate_one_symbol(...)` delegating to the existing receiver/
  transmitter logic, so Tasks 2–4 have one place to hang cached state.
- `transmit` / `receive` (single-symbol public entry points) build a
  one-shot context internally so their behavior is unchanged.

**Do NOT** in this task: move the FFT planner, equalizer, mappers, or
pilot set yet — those are Tasks 2–4. This task is purely the structural
seam. Keep all numerical output identical (the context just relocates
*where* objects are constructed, not *how many times* yet — that lands
incrementally so each later task has an isolated before/after).

**Baseline:** complexity/allocation argument — count how many times
`OfdmTransmitter::new`/`OfdmReceiver::new` are invoked for a 1 KB
payload today (≈111–114 per the audit's `multi_roundtrip_1000_byte`
note). Record it.
**Post-change demonstration:** the same count is now 1 per
`transmit_multi`/`receive_multi` call (constructor hoisted out of the
loop). State this in notes.
**Correctness guard (add):** a test asserting `transmit_multi` output
is bit-identical before/after by pinning a golden sample vector for a
fixed payload (e.g. `b"GOLDEN-CTX"`) — capture the current
`transmit_multi(b"GOLDEN-CTX")` output as an exact `[f32; N]` literal
(tolerance 0.0; see the pinned golden-capture mechanism in the per-task
preamble) and assert it unchanged. Existing `multi_roundtrip_*` already
guard end-to-end; this
golden pins the TX waveform exactly. (Guard MUST be added — the crate's
suite has roundtrips but no waveform-golden.)

### Task 2 — Cache the OFDM FFT/IFFT plan once per frame instead of rebuilding the planner every symbol in demodulate/modulate [perf P1]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `receiver.rs:46-47` runs
`FftPlanner::<f32>::new()` + `plan_fft_forward(fft_size)` inside
`demodulate_one_symbol`; `transmitter.rs:80-81` mirrors it with
`plan_fft_inverse`. Both run once per OFDM symbol. The plan is a pure
function of the frame-invariant `fft_size`; rebuilding allocates twiddle
tables + a radix sub-plan `Arc` graph (KB-scale) and re-runs algorithm
selection every symbol, defeating rustfft's internal cache. This is the
single most-agreed finding (5/6 lanes, CRITICAL).

**Minimum change:** store `forward: Arc<dyn Fft<f32>>` and `inverse:
Arc<dyn Fft<f32>>` on `OfdmContext` (Task 1), built once in
`OfdmContext::new` via a single `FftPlanner` (the planner itself can be
local to `new`; only the resulting `Arc<dyn Fft>` plans are stored).
`demodulate_one_symbol`/`modulate_one_symbol` use the cached `Arc`
(`ctx.forward.process(&mut freq)`), removing the per-symbol planner
construction. Keep the `1/sqrt(N)` scaling and all surrounding math
identical.

**Do NOT** introduce a global/`OnceLock` size-keyed cache — the
frame-scoped context is the chosen mechanism; a global cache adds
thread-safety surface for no extra win here.

**Baseline:** timed loop — call `demodulate_one_symbol` (or the
context equivalent) N=10_000 times on a fixed symbol and record wall
time; this is dominated by the planner rebuild today.
**Post-change demonstration:** same timed loop with the cached plan;
expect a multiple-x reduction. If not faster, revert.
**Correctness guard (add):** pin `demodulate_one_symbol` output as a
golden LLR vector for a fixed input symbol; assert bit-identical
before/after. Existing `multi_roundtrip_*` cover end-to-end. (Add the
golden.)

### Task 3 — Build the equalizer and a pilot-membership mask once per frame instead of rebuilding the equalizer, pilot HashSet, and chan_est buffer every symbol [perf P3]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** Per symbol the code: (a)
`OfdmEqualizer::new(p.pilot_indices().to_vec(), …)` clones the pilot
vec and (b) `equalize` allocates a fresh `chan_est`
`vec![Complex::new(1,0); n_bins]` (8–16 KB) (`equalizer.rs:37`); (c)
builds a pilot-membership `HashSet` in BOTH `receiver.rs:60-61` and
`transmitter.rs:55-56` (~25 inserts each, per symbol); (d)
`bits_per_subcarrier()` `vec![1; n]` is rebuilt per call
(`wideband_lowdensity.rs:48`, called per symbol at `:174`). All
invariant for an immutable `OfdmParams`.

**Minimum change:**
- Store the `OfdmEqualizer` on `OfdmContext`, constructed once.
- Replace the per-symbol pilot `HashSet` with a precomputed,
  allocation-free **membership mask** on the context — a
  `Vec<bool>` of length `fft_size` (index = bin, true = pilot) is the
  simplest; `freq_bins` indexing is already by absolute bin, so
  `mask[sc]` replaces `pilot_set.contains(&sc)` in both `receiver.rs:64`
  and `transmitter.rs:58`. (Binary search over the sorted pilot vec is
  the audit's alternative; the bool mask is O(1) and trivially correct —
  pick the mask.)
- Cache the `bits_per_subcarrier` vector on the context (or pass the
  floor's once-built copy in) so `vec![1; n]` is not rebuilt per symbol.
- For `chan_est`: give `OfdmEqualizer::equalize` a reusable scratch
  buffer (store `chan_est: Vec<Complex<f32>>` on the equalizer/context
  and `clear()`+refill, or take `&mut` scratch) so the 8–16 KB
  allocation happens once. Equalizer becomes `&mut self` or uses
  interior reuse — keep the ZF math (`r * h.conj() / h2`,
  `h2.max(1e-9)`) byte-identical.

**Do NOT** change the interpolation math or the `1e-9` channel floor;
do NOT alter pilot positions. The mask MUST produce the exact same
"is this bin a pilot" answers as the `HashSet` did.

**Baseline:** alloc-count (instrument a counting global allocator in a
test, or reason explicitly) over one `receive_multi` of a 100-byte
payload: count `chan_est` allocations + HashSet allocations =
2 × symbol_count today.
**Post-change demonstration:** those drop to ~1 (equalizer built once,
mask once). State the count delta.
**Correctness guard (add):** a test feeding a known `freq_bins` vector
through the equalizer and asserting the equalized output is identical
before/after (pin a golden). Plus existing roundtrips. (Add the
equalizer golden.)

### Task 4 — Precompute the constellation alphabet/Mapper once per bit-loading instead of rebuilding it per data subcarrier per symbol in compute_llr and modulate [perf P4]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `compute_llr` (`constellations.rs:142`) calls
`self.alphabet()` (`:164-177`), which allocates a `Vec` of `2^bps`
points and calls `map()` (itself `.collect()`s a throwaway `Vec`) per
point — rebuilding a constant pure table for every data subcarrier of
every symbol. RX hits it at `receiver.rs:78,83` (fresh `Mapper::new` +
`compute_llr` per subcarrier); TX mirrors with `Mapper::new` +
`map` per subcarrier at `transmitter.rs:72`. Cheap at BPSK (current
floor), grows sharply at 16/64-QAM.

**Minimum change:**
- Precompute, once per `(constellation)` actually used by the frame, an
  alphabet table `Vec<(usize, Complex<f32>)>` and store the `Mapper`s on
  `OfdmContext` keyed by bits-per-subcarrier (the frame uses a small
  fixed set — for the current floor, only BPSK). A
  `[Option<Mapper>; 7]` indexed by `bpc`, or a small map, suffices.
- Add `Mapper::compute_llr_with_alphabet(&self, syms, n0, alphabet:
  &[(usize, Complex<f32>)])` (or pass the prebuilt alphabet by
  reference) so `compute_llr` does not rebuild the table. Keep the
  existing `compute_llr` as a thin wrapper that builds-then-delegates so
  the public API and the `constellations_llr` unit tests are unchanged.
- RX/TX symbol loops fetch the cached `Mapper`/alphabet from the
  context by `bpc` instead of `Mapper::new(...)` per subcarrier.

**Do NOT** change the max-log LLR metric, the Gray tables, or the
normalization constants. The alphabet table MUST equal what
`alphabet()` produces today for each constellation.

**Baseline:** alloc-count + timed loop on `compute_llr` for a
16-QAM-sized alphabet (force `bpc=4` in the micro-test even though the
floor is BPSK, to exercise the hot case the finding targets): ~18 small
allocs/subcarrier today.
**Post-change demonstration:** alphabet built once; per-subcarrier
allocs drop to ~0. State the delta.
**Correctness guard:** existing `constellations_llr` (and
`constellations_qam`) tests MUST pass unchanged. ADD a test asserting
`compute_llr` == `compute_llr_with_alphabet(precomputed)` bit-identical
for BPSK/QPSK/16-QAM/64-QAM on a fixed symbol set.

### Task 5 — Presize and reuse RX/TX scratch buffers instead of allocating intermediate Vecs per symbol [perf P6]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** RX (`receiver.rs:42-45,62,84`) collects a
full complex-body `Vec` and an **un-presized** `all_llr` (doubling
reallocs despite a known final length). TX
(`transmitter.rs:82,85,88-93`) does ~4 full-length allocations: `td =
freq_bins.clone()`, a scale-`collect` into `samples_complex`, CP
`to_vec`, and the final real-cast `collect`. One in-place IFFT + a
presized output buffer replaces most of them.

**Minimum change:**
- RX: presize `all_llr` with `Vec::with_capacity(expected_llr_len)`
  (computable from data-subcarrier count × bps). Reuse a context-owned
  scratch `Vec<Complex<f32>>` for the CP-stripped body instead of a
  fresh `collect` per symbol (`clear()` + extend).
- TX: IFFT in place on the `freq_bins` buffer (no `.clone()`); apply
  the `1/sqrt(N)` scale in place; write the CP + body directly into a
  presized `Vec<f32>` output (CP region = last `cp_len` real parts,
  then full body), eliminating the intermediate `samples_complex`,
  `cp` `to_vec`, and real-cast `collect`. Reuse context-owned scratch
  across symbols.

**Do NOT** change sample ordering, the CP region selection (`fft_size
- cp_len..`), or the real-part-only audio simplification. Output MUST
be bit-identical.

**Baseline:** alloc-count per `modulate_one_symbol` /
`demodulate_one_symbol` (4 + churn today). Or explicit argument
enumerating each allocation removed.
**Post-change demonstration:** allocations per symbol reduced to the
presized minimum (state the count). If not reduced, revert.
**Correctness guard:** the golden TX waveform from Task 1 and golden
LLR from Task 2 MUST stay bit-identical. Existing roundtrips pass.

### Task 6 — Memoize OfdmParams::data_indices so it stops rebuilding a HashSet+Vec on every call [perf P9]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `OfdmParams::data_indices()`
(`ofdm_params.rs:100-108`) builds a `HashSet` of pilots and filters the
subcarrier vec into a fresh `Vec` **every call**; it is called per
symbol on the TX/RX paths (`wideband_lowdensity.rs:63,165`). The result
is frame-invariant.

**Minimum change (PRESERVE the public signature — do NOT break the
public API):**
- Add a private memoized field on `OfdmParams`
  (`data_indices: Vec<usize>`) populated once in `OfdmParams::for_mode`
  with today's pilot-filter result.
- KEEP the public `pub fn data_indices(&self) -> Vec<usize>` signature
  intact — it returns a clone of the cached field (or stays as-is). This
  avoids breaking the public PHY API and the external caller
  `tests/ofdm_tx.rs:11`.
- Add a private `data_indices_ref(&self) -> &[usize]` accessor for the
  internal per-symbol callers (`wideband_lowdensity.rs:63`, `:166`),
  which only need `.len()`/iteration — they can read the slice with no
  per-call allocation. (`tests/ofdm_tx.rs:11` keeps calling the public
  `Vec`-returning method; verify it still compiles.)
- Keep the computed set identical to today's filter result.

**Do NOT** change the pilot/data partition logic, and do NOT change the
public `data_indices() -> Vec<usize>` signature. This is a pure
memoization — same values, computed once, public API preserved.

**Baseline:** count `data_indices()` calls per `transmit_multi` /
`receive_multi` (1 per `transmit`/`decode_symbol_bytes` → per symbol);
each is a HashSet build + filter today.
**Post-change demonstration:** the per-call HashSet+filter is gone
(field read). State it.
**Correctness guard:** `ofdm_params` integration test MUST pass; ADD an
assertion that the memoized `data_indices` equals the
previously-computed-on-the-fly value for all three modes (Narrow/Mid/
Wide).

### Task 7 — Cache the Zadoff-Chu preamble template once per frame instead of regenerating it on every transmit/detector construction [perf P12]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `PreambleGenerator::generate()` regenerates the
full Zadoff-Chu sequence (`preamble.rs:31-37,120-128`) and
`PreambleDetector::new()` calls it again (`:55`); the floor regenerates
it per frame at `wideband_lowdensity.rs:121,274` (each `receive_*_with_
sync`) and `:95,252` (each `transmit_*_with_preamble`). The sequence is
a compile-time constant (fixed `PREAMBLE_LEN`/`PREAMBLE_ROOT`).

**Minimum change:** cache the generated template behind a
`OnceLock<Vec<f32>>` (module-level in `preamble.rs`) so
`generate()`/`PreambleDetector::new()` clone or borrow the
once-computed sequence rather than recomputing the cos/sin per call. A
`OnceLock` is the right mechanism here (not the frame-scoped context)
because the template is *globally* constant, not frame-scoped, and is
needed by both the floor and the detector. (Frame-scoped caching would
also work but `OnceLock` matches the global-constant nature and serves
all call sites.)

**Do NOT** change `PREAMBLE_LEN`, `PREAMBLE_ROOT`, or the Zadoff-Chu
formula. The cached samples MUST equal `zadoff_chu(192,25).re` exactly.

**Baseline:** count `zadoff_chu` invocations per
`receive_multi_with_sync` (≥2 today: generator + detector) and the
trig cost. Or explicit argument.
**Post-change demonstration:** `zadoff_chu` computed once process-wide;
subsequent calls are a clone/borrow. State it.
**Correctness guard:** `sync_preamble` tests pass; the existing
`transmit_with_preamble_starts_with_preamble_samples` /
`multi_with_preamble_starts_with_preamble_samples` tests already pin the
template bit-for-bit (`< 1e-6`). Confirm they stay green; no new golden
needed beyond confirming those.

```
After completing Phase A (Tasks 1–7):
Review the batch from multiple perspectives. Minimum 3 review rounds.
Focus: (a) is the OfdmContext genuinely built once per transmit_multi/
receive_multi (grep the loops)? (b) are ALL roundtrip + LLR goldens
still bit-identical? (c) did any task fail to improve and get silently
kept instead of reverted? If round 3 still finds issues, keep going.
```

---

## Phase B — Independent localized fixes (Tasks 8–11)

These touch disjoint files (`sync/preamble.rs`, `audio_device.rs`,
`robustness_floor/narrow_fsk.rs`) from Phase A and from each other, so
they parallelize cleanly. Task 8 also touches `preamble.rs` which Task 7
touches (`generate`/template caching) — sequence Task 8 AFTER Task 7 or
keep them on the same branch to avoid a `preamble.rs` collision. Tasks
9/10/11 are fully independent.

### Task 8 — Replace the preamble scan's per-offset window-energy recompute with a running two-term sum, and fix the off-by-one that skips the last valid alignment offset [perf P2 + SB1]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `PreambleDetector::scan`
(`preamble.rs:88-101`) slides a cross-correlation across the signal and
recomputes the **full windowed signal energy** (`sig_energy += signal[i
+j]^2` over all `n` taps) at every offset — O(N·M) wasted multiply-adds
on top of the inherent O(N·M) correlation (~10⁸ wasted MACs per
acquisition on ~10⁶-sample captures). The energy term is a sliding
window: maintain a running sum that adds the entering sample's square
and drops the leaving sample's square per step. **SB1 (co-located):**
the loop bound `0..(signal.len()-n)` (`:88`) is exclusive of the final
valid offset; it should be inclusive (`..=signal.len()-n`, i.e.
`0..(signal.len()-n+1)`), currently masked by the ±2-sample test
tolerance.

**Minimum change:**
- Compute `sig_energy` for the first window once, then for each
  subsequent offset `i`: `sig_energy += signal[i+n-1]^2 -
  signal[i-1]^2`. Keep `sig_norm = sig_energy.max(0).sqrt().max(1e-9)`
  (guard against tiny negatives from f32 cancellation — clamp at 0
  before sqrt). The **correlation** term stays O(N·M) (this task does
  NOT attempt FFT-correlation — that's a larger rewrite out of scope);
  only the energy half becomes O(N).
- Fix the loop bound to include the last valid offset (`..=`).

**Do NOT** change the 0.5 detection threshold, the SNR-estimate math,
or attempt an FFT-based correlation. The detected `start_sample` for
existing test inputs MUST remain within the tests' tolerance — and
SHOULD become *more* accurate at the tail edge (that's the SB1 fix).

**Baseline:** timed loop on `scan` over a representative capture
(e.g. 100_000 samples of leading silence + a planted preamble);
record wall time (energy recompute dominates).
**Post-change demonstration:** same capture, running-sum version;
expect a clear reduction. If not faster, revert (but keep the SB1
bound fix — it's a correctness fix, not a perf fix; carry it
regardless).
**Correctness guard (add + existing):** existing `sync_preamble` and
`preamble_roundtrip_*` tests MUST pass. ADD a test asserting the
detected `start_sample` is **identical** between old and new energy
computation for a fixed planted-offset capture (pin the exact offset,
not just the ±2 tolerance) — this proves the running-sum is numerically
equivalent. ADD a test exercising the previously-skipped last offset:
plant the preamble at exactly `signal.len()-n` and assert detection
succeeds (this would FAIL before the SB1 fix → true red/green for SB1).

### Task 9 — Make the real-time audio capture callback lock-free so it never holds a mutex across the sample copy and the consumer never contends it [perf P5, removes SB3]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** The cpal RT input callback
(`audio_device.rs:536-548`) takes a blocking `std::sync::Mutex`
(`acc_cb.lock().unwrap()`, `:538`) held across the whole per-callback
de-interleave copy (`:542-548`); the consumer busy-polls the SAME lock
(`:573-574`, `:583`). Expected case is a cheap uncontended CAS, but the
tail event — a park / priority-inversion stall on the RT thread — is a
missed audio deadline → capture overrun → corrupted demod → frame loss.
The output callback (`:279-299`) is already lock-free, so the input
path doesn't meet the project's own bar. **SB3:** `acc_cb.lock().
unwrap()` panics the RT thread if a consumer panicked while holding the
lock (mutex poisoning) → stream abort; the lock-free fix removes this.

**Minimum change — the design has two paths; the executor MUST pick one
and SURFACE the choice before proceeding (the Primary path is a
deliberate new-dependency decision that requires explicit operator
approval):**

- **Primary (real SPSC ring buffer — REQUIRES a new-dependency
  decision):** an `rtrb` or `ringbuf` SPSC ring. The callback is the
  sole producer (`producer.push_slice(...)` / equivalent); the poll-loop
  consumer is the sole consumer (`consumer.pop_slice(...)`). This is the
  cleanest, fully-safe path — no `unsafe` in user code. **It adds a new
  crate dependency**, and the project prefers minimal deps, so this is a
  deliberate decision the executor MUST call out and get the operator to
  approve **before writing any code on this path.** Do not silently pull
  in the dep; surface it as a decision, justify it (RT-safe lock-free
  capture with no hand-rolled `unsafe`), and wait for sign-off.

- **Alternative (no-dep, `unsafe`):** a `UnsafeCell<Box<[f32]>>` staging
  buffer (sized `target_samples`) published via an `AtomicUsize` length.
  The callback (producer) writes captured samples into the cells and
  publishes the new count with `len.store(written, Ordering::Release)`;
  the consumer reads `len.load(Ordering::Acquire)` to learn how many
  samples are valid, then reads only that many. The `Release`/`Acquire`
  pairing is what makes the prior writes visible to the consumer. **This
  path is `unsafe`** (the `UnsafeCell` access bypasses the borrow
  checker): it REQUIRES a safety-invariant comment justifying soundness —
  specifically that the strict single-producer (callback) /
  single-consumer (poll loop) discipline plus the Release/Acquire fence
  means no two threads ever access the same cell concurrently, and the
  consumer only reads indices `< len.load(Acquire)`. Without that comment
  + the ordering discipline, the path is a latent data race and MUST NOT
  ship.

**Post-`drop(stream)` ownership handoff — a HARD constraint on BOTH
paths.** The current teardown reads the captured length on the timeout
path (`audio_device.rs:583`), takes sole ownership of the buffer via
`Arc::try_unwrap` after the stream is dropped (`:591`), and truncates to
`target_samples` for a Completed outcome (`:599`). The chosen design
MUST preserve this exact post-drop handoff: the consumer takes ownership
of the captured samples ONLY after `drop(stream)` (when the callback can
no longer run, so there is no live producer), and the timeout path must
still be able to read the current sample count. For the SPSC-ring path,
drain the ring after drop; for the `unsafe` staging path, recover the
boxed buffer (e.g. via `Arc::try_unwrap` on the `Arc<…>` wrapping the
`UnsafeCell`, mirroring `:591`) and read `len.load(Acquire)` for the
count. Either way the hot path has zero lock acquisition.

**Do NOT** change the abort/timeout/`drop(stream)` teardown semantics,
the `target_samples` truncation, or the de-interleave (channel-0)
selection. Captured samples MUST be identical to the mutex version.

**Baseline:** there is no existing micro-benchmark; capture the current
design's lock-acquisition count per second of capture (1 per callback,
~100+/s) and the SB3 panic surface as the explicit argument. Optionally
a stress test that runs many short captures under load.
**Post-change demonstration:** zero lock acquisitions on the callback
hot path (argument: the callback only does an `Atomic::store` and slice
writes). State it.
**Correctness guard (add — writable only AFTER the design is chosen):**
the SPSC correctness/ordering test cannot be written until the Primary
(SPSC-ring) vs Alternative (`unsafe` staging) decision above is settled,
because its surface (the abstraction under test) differs per path —
so capture the design choice FIRST, then write the guard. The guard is a
single-producer/single-consumer test (behind `audio-device` if
cpal-gated; otherwise a unit test of the chosen ring/atomic-staging
abstraction in isolation) asserting all produced samples are received in
order with none lost/duplicated. Apply the assertion-rigor rule: if it
races, fix with a deterministic fence, do NOT weaken. SB3: ADD a test
that a consumer-side panic does not abort
the capture thread (or document that the lock-free design has no
poisoning surface — the AtomicUsize cannot be "poisoned").

### Task 10 — Hoist the narrow-FSK planner and per-symbol buffer out of the hot loop, precompute the tone-bin indices once, and use norm_sqr instead of norm for the magnitude argmax [perf P7 + P11]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** In `NarrowFskFloor::receive`
(`narrow_fsk.rs:80-111`): the `FftPlanner` is built per `receive` call
(`:83`; already above the inner symbol loop, so lower frequency than P1
but still avoidable) (P7); each symbol collects a `buf` then
`resize`s to `fft_size` (next-pow2), reallocating a ~64 KB complex
`Vec` per symbol (`:89-94`) (P7); the 8 tone-bin indices are recomputed
per symbol from `tone_freq_hz` + `round` (`:99-102`) (P11); and `:102`
uses `.norm()` (a sqrt) for a pure magnitude argmax where `norm_sqr()`
is the sqrt-free, argmax-preserving fast path used elsewhere in the
crate (P7).

**Minimum change:**
- Precompute the 8 tone-bin indices once (the `fft_size` and tone
  frequencies are fixed for a given `receive` call) into a `[usize; 8]`
  before the symbol loop (P11).
- Allocate ONE reusable `buf: Vec<Complex<f32>>` of length `fft_size`
  before the loop; per symbol, overwrite the first `sps` entries from
  the sample slice and zero the tail (`buf[sps..].fill(Complex::ZERO)`),
  then `fft.process(&mut buf)` — no per-symbol allocation/resize (P7).
- Replace `buf[bin].norm()` with `buf[bin].norm_sqr()` for the argmax
  comparison (P7). Argmax over magnitude == argmax over magnitude² since
  sqrt is monotonic — the selected `best_tone` is unchanged.
- The planner is already once-per-call; leave it as-is OR hoist the
  whole `(planner, fft)` construction is fine since `receive` is
  per-call not per-symbol — the audit rates this "Contained"; the
  buffer + norm_sqr + bin-index wins are the substantive ones. (Do not
  over-engineer a cross-call planner cache here.)

**Do NOT** change `M`, tone spacing, symbol duration, the bit-packing,
or the trailing-zero trim. The decoded bytes MUST be identical.

**Baseline:** timed loop on `receive` over a multi-symbol FSK capture;
alloc-count per symbol (1 × 64 KB resize today). Or explicit argument.
**Post-change demonstration:** zero per-symbol buffer allocs; argmax
uses norm_sqr. State the alloc delta + timing.
**Correctness guard:** existing `floor_narrow_fsk` roundtrip test MUST
pass. ADD a test asserting `best_tone`/decoded bytes are identical
between `.norm()` and `.norm_sqr()` argmax on a fixed multi-symbol
capture (proves the sqrt-free path preserves the decision).

### Task 11 — Expand mono→N-channel inside the playback callback instead of pre-building a second full-length interleaved Vec [perf P10]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `play_blocking_with_abort`
(`audio_device.rs:261-266`) pre-expands the mono buffer into a second
full `frames: Vec<f32>` of `len × channels` before the stream starts.
For mono devices (the common PHY case) this is a pointless full copy;
even for multi-channel it doubles peak memory. The expansion can happen
inside the output callback per-frame (zero-copy for mono).

**Minimum change:** move the mono→channels duplication into the output
callback. Track the cursor in **mono-sample** units; for each output
frame slot, write `buffer[mono_cursor]` to all `channels` lanes,
advancing `mono_cursor` once per output frame (not per lane). For
`channels == 1`, this is a direct copy with no expansion. The callback
borrows/owns the mono buffer (move an `Arc<[f32]>` or the owned `Vec`
into the closure); preserve the zero-fill-tail and the done-signal
logic exactly.

**Do NOT** change the abort polling, the done/err channels, the
tail-drain sleep, or the `PlayOutcome` semantics. Output audio MUST be
identical sample-for-sample.

**Baseline:** allocation argument — current code allocates `len ×
channels` f32 up front; the change removes that allocation (callback
reads the existing mono buffer). State the bytes saved for a typical
buffer.
**Post-change demonstration:** the pre-expand `frames` allocation is
gone. State it.
**Correctness guard (add):** a unit test of the in-callback expansion
logic (factor the per-frame fill into a testable helper if needed)
asserting, for channels ∈ {1,2,6}, the produced interleaved output
equals the old pre-expanded `frames` content. (cpal device tests may be
gated; test the expansion helper in isolation.)

```
After completing Phase B (Tasks 8–11):
Review the batch from multiple perspectives. Minimum 3 review rounds.
Focus: (a) SB1's last-offset test genuinely red-before/green-after?
(b) the lock-free callback's SPSC invariant documented and the
ordering test non-flaky via real synchronization (not weakened)?
(c) norm_sqr argmax proven decision-identical? If round 3 still finds
issues, keep going.
```

---

## Phase C — Cross-cutting API change (Task 12, sequenced LAST)

### Task 12 — Add a streaming/symbol-windowed read_wav path so peak memory no longer scales with the whole transmission length [perf P8]

**Execution Status:** ⬜ NOT STARTED

**What / where / why.** `AudioBuffer::read_wav` (`audio_io.rs:61-74`)
collects the entire file into one `Vec<f32>` (`:71`), so peak memory
scales with transmission length. Decode is inherently
symbol-windowable, so a streaming reader caps peak memory at a window.
This is an **architectural ceiling**, not a hot-loop cost, and it
requires a **public API addition** — hence sequenced last, after the
localized wins land and stabilize.

**This is scheduled, NOT deferred.** (Per disposition discipline, P8
gets a task; it is sequenced last because it is the only cross-cutting
API change and benefits from a stable base. It is NOT deferred — no
named refactor-collision / dependency-bump / correctness-regression
mechanism justifies deferral.)

**Minimum change (additive API — do not break `read_wav`):**
- Add `AudioBuffer::read_wav_windowed(path, window_samples, f:
  impl FnMut(&[f32]))` OR a `WavSampleStream` iterator yielding
  fixed-size windows of decoded f32 samples, reusing one scratch window
  buffer. Keep the existing `read_wav` (whole-file) for callers that
  genuinely need the full buffer (tests, short captures).
- Wire the streaming reader into the one decode path that benefits
  (the floor's `receive_*` consume `&[f32]` over a full buffer today —
  this task ADDS the streaming entry point; migrating `receive_multi`
  to consume a stream is a follow-up if the symbol framing allows it,
  and is NOT required by this task — keep scope to the I/O API + reuse
  scratch).

**Do NOT** remove or change the signature of the existing `read_wav`,
the sample-rate validation, or the f32-format check. The same WAV MUST
decode to the same samples through either path.

**Baseline:** peak-RSS argument — current `read_wav` peaks at
`file_len × 4` bytes; the windowed reader peaks at `window × 4`. State
the ratio for a representative long capture.
**Post-change demonstration:** the windowed path's peak working set is
bounded by the window, independent of file length (argument + a test
that reads a large synthetic WAV through the windowed API and asserts
the cumulative samples equal the whole-file `read_wav` output).
**Correctness guard (add):** a test writing a known multi-window WAV,
reading it via the windowed API, concatenating the windows, and
asserting equality with `read_wav(...).into_samples()` bit-for-bit.
Existing `audio_io` test MUST pass unchanged.

```
After completing Task 12:
Review the streaming API for: window-boundary correctness (a sample is
neither dropped nor duplicated at window edges), the reused-scratch
buffer not aliasing across windows, and the whole-file equality guard.
Minimum 3 review rounds.
```

---

## Post-remediation advisory: bug-hunt over the diff

Performance changes are a classic bug source (off-by-one in running
sums, buffer-reuse aliasing, in-place FFT corrupting a buffer still
needed, atomic ordering bugs in the lock-free callback). After the
remediation lands, run the **bug-hunt-cycle** over the full diff. A
kickoff doc already exists for the co-located suspected bugs:
[`docs/perf-audits/2026-06-04-m1-ofdm-phy-bug-hunt-kickoff.md`](../perf-audits/2026-06-04-m1-ofdm-phy-bug-hunt-kickoff.md).
Note SB2 (Gardner symbol-timing normaliser includes leading silence)
and the residual SB3 surface are tracked there — SB2 is a behavioral
(not perf) finding and is OUT of scope for this plan; SB1 is fixed in
Task 8 and SB3 is removed by Task 9.

---

## Deferred appendix

**Empty.** All of P1–P12 are scheduled (Tasks 1–12). No finding was
deferred. Disposition discipline requires a substantive named mechanism
(a specific refactor collision, a specific dependency bump, or a
specific correctness regression) to defer; none of these findings has
one. P8 is sequenced last but explicitly scheduled (Task 12), not
deferred.

---

## Execution-strategy recommendation

**Recommended: subagent-driven development
(`/superpowers:subagent-driven-development`) — a fresh subagent per
task with a review gate between tasks — run on a single shared branch
for Phase A and parallel branches for Phase B.**

Reasoning, grounded in task independence:

- **Phase A (Tasks 1–7) is a sequential dependency chain.** Task 1
  creates `OfdmContext`; Tasks 2–7 hang cached state on it and migrate
  call sites. They touch the same four files (`context.rs`,
  `receiver.rs`, `transmitter.rs`, `wideband_lowdensity.rs`,
  `ofdm_params.rs`). Parallel agents here would collide. Run them
  **in order on one branch**, with the per-task review gate catching
  any silent behavior drift before the next task builds on it. The
  golden-waveform/LLR guards added in Tasks 1–2 protect every
  subsequent Phase-A task.

- **Phase B (Tasks 8–11) is genuinely parallelizable** — `preamble.rs`
  (Task 8), `audio_device.rs` (Tasks 9 + 11 — note these two share the
  file, so keep 9 and 11 on the same branch or sequence them),
  `narrow_fsk.rs` (Task 10) are disjoint from Phase A and mostly from
  each other. These suit `/superpowers:dispatching-parallel-agents`
  across 2–3 branches IF the session wants throughput; otherwise fold
  them into the same subagent-driven sequence. One caveat: Task 8 edits
  `preamble.rs`, which Task 7 also touches — sequence Task 8 after Task
  7 (or same branch).

- **Phase C (Task 12) last**, on its own — it's the only public-API
  change and benefits from a stable base.

The per-task TDD + baseline/post-change/correctness-guard structure is
non-negotiable for a perf pass (silent numerical drift is the headline
risk), which favors the subagent-driven review-gate cadence over a
single batch session. If context budget is tight, dispatch Phase B as
parallel agents while a focused session drives the Phase A chain.

**Why this discipline (Living Document Contract):** see
`/writing-plans-enhanced` Step 5 — update banners at ship/defer time so
the next session reads live state instead of reconstructing it.
