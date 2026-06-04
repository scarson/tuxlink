# Plan Review — M1 OFDM-PHY remediation plan

**Reviewer:** Opus subagent (agent glade-knoll-shoal)
**Date:** 2026-06-04
**Target:** `docs/plans/2026-06-04-m1-ofdm-phy-perf-audit-remediation-plan.md`
**Method:** plan-review-cycle dimensions (ambiguity, context gaps, interpretation latitude, cross-task deps, pitfall coverage), single consolidated adversarial pass, verified against `tuxmodem/crates/tuxmodem-phy/src` + `tests/` and the source audit.

---

## Verdict: **needs-minor-edits**

The plan is structurally strong and unusually well-grounded: every line/file reference I spot-checked is accurate, the coverage map is complete and honest, the file-overlap analysis driving the execution model is *mostly* correct, and the correctness-guard strategy (ADD goldens/equivalence tests because the existing tests are too weak to catch numerical drift) is exactly right. It is **not** subagent-ready as written because of three substantive gaps — one blocking (Task 9 unsound no-dep design), two near-blocking (Task 1 misleading line refs; Task 6 undeclared public-API change) — plus a handful of small readiness nits. None require reworking the plan's architecture.

---

## Per-task readiness issues

### Task 1 — BLOCKS-ish (misleading "where exactly")
- **Line-ref mismatch.** Task 1 says `OfdmTransmitter::new`/`OfdmReceiver::new` are called "inside the per-symbol loop" at `wideband_lowdensity.rs:233-235` (transmit_multi) and `:334-338` (receive_multi). **Verified:** those line ranges are the *loops* (`transmit_multi` loop is `:233-237`, `receive_multi` loop is `:334-338`), but the constructors are NOT there. `OfdmTransmitter::new` is at **`:71` inside `transmit()`** (called per-chunk from the loop at `:234`), and `OfdmReceiver::new` is at **`:175` inside `decode_symbol_bytes()`** (called per-symbol from `:313` and `:336`). A fresh subagent navigating to line 233 will not find the constructor and may misjudge the refactor surface. **Fix:** state the indirection explicitly — "the loops call `self.transmit(chunk)` (`:234`) → `OfdmTransmitter::new` (`:71`) and `self.decode_symbol_bytes(...)` (`:313,:336`) → `OfdmReceiver::new` (`:175`); the constructor is one call-frame down from the loop."
- **Module path ambiguity.** Plan uses both bare `receiver.rs`/`transmitter.rs` and `ofdm_main/receiver.rs`. New module is `ofdm_main/context.rs` (correct — `ofdm_main/mod.rs` is where `pub mod context;` must be added; the plan never says to register the module in `mod.rs`). **Fix:** add "register `pub mod context;` in `ofdm_main/mod.rs`."

### Task 2 — ready. Refs `receiver.rs:46-47` / `transmitter.rs:80-81` verified exact.

### Task 3 — ready, minor. Refs verified: `equalizer.rs:37` (chan_est alloc), pilot HashSet `receiver.rs:60-61` + `transmitter.rs:55-56`, `bits_per_subcarrier` at `wideband_lowdensity.rs:48`. The `mask[sc]` design is sound (freq bins indexed by absolute bin). Note Task 3 also edits `wideband_lowdensity.rs` (bits_per_subcarrier) — shared with Tasks 1/6/7 but all Phase A, so fine. The `1e-9` floor lives at `equalizer.rs:68` (`norm_sqr().max(1e-9)`) — plan's "do not change" correctly pins it.

### Task 4 — ready. `compute_llr` at `constellations.rs:137`, `alphabet()` at `:164`, callers `receiver.rs:78,83` + `transmitter.rs:72` verified. Equivalence-test guard is the right call — see Coverage note on existing test weakness.

### Task 5 — ready. RX/TX alloc sites verified (`receiver.rs:42-45,62,84`; `transmitter.rs:82,85,88-93`). "In-place IFFT on `freq_bins`" is sound. Watch: TX currently `td = freq_bins.clone()` (`:82`) then reuses nothing — in-place is safe since `freq_bins` is local.

### Task 6 — NEAR-BLOCKING (undeclared public-API change + missing external caller)
- `data_indices()` is **`pub fn`** (`ofdm_params.rs:100`). Task 6 changes its signature to return `&[usize]`. The plan's **universal "do NOT touch" boundary #2 says "Do NOT change the public PHY API except where Task 12 explicitly requires it."** Task 6 silently violates this. Reconcile: either (a) carve out an explicit exception in Task 6 ("this is an approved public-signature change; rationale: pure memoization"), or (b) keep the `pub fn data_indices(&self) -> Vec<usize>` signature returning `self.cached.clone()` and add a private `data_indices_ref()` for the hot callers — preserving the public API. **(b) is the lower-risk default and aligns with the stated boundary.**
- **Missing caller.** Plan says "check `data_indices().len()` call sites." **Verified all 3 callers:** `wideband_lowdensity.rs:63`, `:166`, AND **`tests/ofdm_tx.rs:11`** — the test is not mentioned. All three use `.len()`, so a slice return works, but the plan must name the test caller so the subagent updates/compiles it.

### Task 7 — ready. `OnceLock<Vec<f32>>` design sound; `generate()` `:31-37`, detector `:55`, `zadoff_chu` `:120-128` verified. Correctly relies on existing bit-exact preamble pins (`transmit_with_preamble_starts_with_preamble_samples` etc.) — those DO assert `< 1e-6` equality (`wideband_lowdensity.rs:493`), so "no new golden needed" is justified.

### Task 8 — ready, one guard gap (see Verification-gate). Refs `preamble.rs:88-101` verified; SB1 bound at `:88` verified (`0..(signal.len()-n)`; max index accessed is `i+n-1` = `len-1` at `i=len-n`, so `..=signal.len()-n` is in-bounds and correct). **Running-sum underflow:** `sig_energy += signal[i+n-1]^2 - signal[i-1]^2` underflows at `i=0`. Plan says "first window once, then each subsequent offset `i`" implying i≥1 — acceptable but SHOULD say explicitly "compute the i=0 window directly; the recurrence starts at i=1" to stop a junior from indexing `signal[i-1]` at i=0.

### Task 9 — **BLOCKS EXECUTION** (unsound no-dep design as written)
The "preferred no-dep" design is internally contradictory and not implementable as described by a junior:
- It says the staging `Vec<f32>` is "owned solely by the callback via `move`" AND that the consumer reads progress via `AtomicUsize` AND "takes ownership of the staging buffer ... via an `Arc<Mutex<>>` swapped only at teardown." If the buffer is moved solely into the callback closure, the consumer holds no handle to read from it. If instead it is behind `Arc<Mutex<Vec>>`, you are back to a mutex (the thing being removed). A plain `Arc<Vec<f32>>` shared between callback and consumer does **not** permit the callback to mutate elements (no interior mutability) — this will not compile.
- The genuinely-sound no-dep pattern needs either (i) `Arc<[UnsafeCell<f32>]>` (or a `Box<[MaybeUninit<f32>]>` raw-ptr handed to the callback) + an `AtomicUsize` published length with Release/Acquire — i.e. **unsafe**, with the SPSC invariant making it sound; or (ii) a real SPSC ring (the "alternative" `rtrb`/`ringbuf`). The current text gestures at (i) without the `UnsafeCell`/raw-ptr mechanism and without acknowledging it requires `unsafe`.
- Teardown reads also unaddressed: today the consumer reads length on the timeout path (`audio_device.rs:583`) and truncates after `drop(stream)` (`:599`). The atomic-progress design must specify that the consumer reads `count.load(Acquire)` for length AND how it obtains the samples after stream drop (the `Arc::try_unwrap` at `:591` assumes sole ownership — the lock-free design must preserve a clean ownership handoff).
- **Fix (unblock):** either (a) specify the unsafe `UnsafeCell`/raw-ptr-staging + `AtomicUsize` design concretely (with the Release/Acquire pairing, the SPSC-soundness comment the plan already mandates, and the post-drop ownership recovery), OR (b) make the SPSC-ring (`ringbuf`, no `unsafe` in user code) the **primary** recommendation and get operator sign-off on the dep up front. As written, a subagent cannot derive a compiling, sound implementation. (SB3 disposition is fine: a lock-free path has no poisoning surface.)

### Task 10 — ready. Refs `narrow_fsk.rs:83-94,99-102,102` verified. `norm`→`norm_sqr` argmax is decision-preserving (monotonic). `Complex::ZERO` (plan's suggested tail-fill) IS available — num-complex resolves to **0.4.6** in `Cargo.lock` (`ZERO` const since 0.4.1); compiles. Minor: crate elsewhere uses `Complex::new(0.0,0.0)`; either is fine.

### Task 11 — ready. `play_blocking_with_abort` pre-expand `:261-266`, callback `:279-299` verified. Mono-cursor design sound; `channels==1` becomes a direct copy.

### Task 12 — ready. `read_wav` `:61-74`, whole-file collect `:71` verified. Additive API; existing `tests/audio_io.rs` roundtrip preserved.

---

## Coverage check (P1–P12 + SB1/SB3)

**All 12 findings scheduled; mapping verified against audit + source. No silent drops.**

| Finding | Task | Genuine fix? | Verified |
|---|---|---|---|
| P1 per-symbol FftPlanner | 2 | Yes — cached `Arc<dyn Fft>` on context | `receiver.rs:46-47`, `transmitter.rs:80-81` |
| P2 O(N·M) energy recompute | 8 | Yes — running two-term sum | `preamble.rs:90-94` |
| P3 per-symbol equalizer/HashSet/chan_est | 3 | Yes — built-once + bool mask + scratch | `equalizer.rs:37`, `receiver.rs:60-61`, `transmitter.rs:55-56` |
| P4 per-subcarrier alphabet/Mapper | 4 | Yes — precomputed alphabet by bpc | `constellations.rs:142,164` |
| P5 RT mutex across copy | 9 | Yes (design unsound as written — see Task 9) | `audio_device.rs:538,542-548` |
| P6 per-symbol Vec churn | 5 | Yes — presize + reuse | `receiver.rs:42-45,62,84`; `transmitter.rs:82-93` |
| P7 FSK planner/buffer/.norm | 10 | Yes | `narrow_fsk.rs:83-94,102` |
| P8 read_wav whole-file | 12 | Yes — additive streaming API | `audio_io.rs:71` |
| P9 data_indices per-call | 6 | Yes — memoize in `for_mode` | `ofdm_params.rs:100-108` |
| P10 playback mono-expand | 11 | Yes — expand in callback | `audio_device.rs:261-266` |
| P11 FSK tone-bin per symbol | 10 | Yes — precompute `[usize;8]` | `narrow_fsk.rs:99-102` |
| P12 preamble per-frame regen | 7 | Yes — OnceLock | `preamble.rs:31-37,55` |
| **SB1** last-offset off-by-one | **8** ✓ | Yes — `..=` bound + red/green test | `preamble.rs:88` |
| **SB3** mutex-poison RT panic | **9** ✓ | Yes — removed by lock-free path | `audio_device.rs:538` |

SB1 confirmed handled in the P2 task (Task 8). **NOTE: the review brief said "SB3 by the P5 task" and "SB3 handled by Task 5" — actually SB3 is handled by Task 9 (the P5 task), and Task 5 is the P6 buffer task. The plan's mapping is correct; the brief's task-number phrasing is the thing to ignore.** SB2 is correctly declared out-of-scope (behavioral, not perf) and tracked in the bug-hunt kickoff (which exists at `docs/perf-audits/2026-06-04-m1-ofdm-phy-bug-hunt-kickoff.md`).

**Existing-test weakness — validates the plan's ADD-a-guard strategy.** The crate has a real `tests/` dir (~22 integration files, NOT "~6 tests"), but the named guards are *weak*: `constellations_llr.rs` asserts only LLR **sign + length**, not values; `floor_narrow_fsk.rs` is clean-channel roundtrip only; `sync_preamble.rs` uses ±32 tolerance and never plants the last offset; `audio_io.rs` uses `1e-4` tolerance. So none would catch the numerical drift these optimizations risk. The plan's per-task "ADD a bit-exact golden/equivalence test" is therefore *necessary*, not belt-and-suspenders — good. (One inflated baseline claim: the plan says the suite "has roundtrips but no waveform-golden" in Task 1 — accurate.)

---

## Cross-task conflict findings (verified against source)

**Phase A shared-file claim — VERIFIED CORRECT.** Tasks 1,2,3,4,5,7 touch `receiver.rs`/`transmitter.rs`/`context.rs`; Tasks 1,3,6,7 touch `wideband_lowdensity.rs` (confirmed: `bits_per_subcarrier:48`, `data_indices` callers `:63,:166`, constructor sites `:71,:175`, preamble gen `:95,:252`). All are Phase A, sequential on one branch — correct. No undeclared intra-Phase-A conflict.

**Phase B claims — VERIFIED.**
- Task 7 (Phase A) + Task 8 (Phase B) both edit `preamble.rs` — **declared** by the plan (sequence 8 after 7 / same branch). Correct.
- Tasks 9 + 11 both edit `audio_device.rs` — **declared**. Verified they touch *different* functions (`record_blocking_with_abort:513` vs `play_blocking_with_abort:253`), no line overlap, but same-file merge risk on parallel branches is real; plan's "keep on same branch or sequence" is the right mitigation.
- Task 10 (`narrow_fsk.rs`) is fully disjoint — confirmed narrow_fsk does NOT import `constellations`/`Mapper`, so no hidden Task 4 ↔ Task 10 coupling.

**No UNDECLARED cross-task file conflict found.** The one cross-boundary item the plan under-states is not a *file* conflict but a *correctness-guard ordering* coupling: Tasks 3/5 lean on "the golden TX waveform from Task 1 and golden LLR from Task 2." That makes Tasks 1–2's goldens a hard prerequisite for 3+; the plan implies this but should state "Task 5's correctness guard REQUIRES Task 1+2 goldens to already exist on the branch" so a subagent running Task 5 doesn't skip them.

---

## Verification-gate gaps

1. **Task 9 SPSC/ordering guard is hand-wavy because the design is** (see Task 9). The "all produced samples received in order, none lost/duplicated" test cannot be written until the abstraction is concrete. Blocking.
2. **Task 8 numerical-equivalence guard is the only one that pins the exact running-sum equivalence** — good and concrete ("identical `start_sample`, not just ±2"). The last-offset red/green test is properly specified as a true TDD red (fails before SB1 fix). Keep.
3. **Tasks 1–2 goldens are the load-bearing guards for all of Phase A** but the plan never says HOW to capture a golden (hash? first-N samples? a committed fixture file?). For reproducibility, specify the mechanism once in the shared preamble: e.g. "assert against a hard-coded `[f32; N]` literal of the first 32 samples, tolerance `0.0` (exact bits)" — "byte-identical" + "hash" + "first-N" are used interchangeably and a subagent will pick three different mechanisms across tasks.
4. **"Revert if no improvement" is present on every measurable task** (good) but Tasks using a pure complexity/allocation *argument* (1, 9, 11, 12) have no falsifiable revert trigger — an argument can't "fail to improve." That's acceptable for genuinely un-measurable changes, but Task 1 (constructor-count) and Task 11 (alloc-count) ARE countable; prefer the count over the argument so the revert gate has teeth.

---

## Over-optimization / risk flags

- **Task 6 public-API change** (above) is the one real boundary breach — flagged.
- **Task 9 `unsafe`** — if design (a) is chosen, the plan must explicitly authorize `unsafe` (the universal boundaries don't currently mention it) and require the SPSC-soundness comment. Risk: a subagent writing `unsafe` without the Acquire/Release discipline introduces a data race the thin tests won't catch.
- **Task 5 in-place IFFT** — risk of corrupting a buffer still needed later; the plan's "do not change CP region selection `fft_size-cp_len..`" boundary is good, but should add "verify `freq_bins` is not read after the in-place IFFT" as an explicit check (the post-remediation bug-hunt advisory already names this class).
- No task changes DSP math; the bit-exact boundary is well-stated and repeated per task.

---

## Ranked required edits

| # | Edit | Task | Blocks execution? |
|---|---|---|---|
| 1 | Make Task 9's no-dep design concrete & sound (unsafe `UnsafeCell`/raw-ptr + `AtomicUsize` Release/Acquire + post-drop ownership recovery) OR promote the SPSC-ring as primary with up-front operator dep-approval | 9 | **YES** |
| 2 | Fix Task 1 constructor line-refs (state the `transmit`→`:71` / `decode_symbol_bytes`→`:175` indirection); add "register `pub mod context;` in `ofdm_main/mod.rs`" | 1 | YES (subagent will mis-navigate) |
| 3 | Reconcile Task 6 with the public-API boundary: keep `pub fn data_indices() -> Vec` (clone cached) + private ref accessor, OR carve an explicit approved exception; name the `tests/ofdm_tx.rs:11` caller | 6 | YES (boundary violation + missing caller breaks compile) |
| 4 | Specify the golden-capture mechanism once (exact `[f32; N]` literal, tolerance 0.0) and stop alternating "hash"/"byte-identical"/"first-N" | preamble / 1,2,3,5 | No (but high drift risk) |
| 5 | Task 8: state the running-sum recurrence starts at i=1 (i=0 window computed directly) to avoid `signal[i-1]` underflow | 8 | No |
| 6 | State that Task 5's correctness guard REQUIRES Task 1+2 goldens already on-branch | 5 | No |
| 7 | Prefer countable baselines (constructor-count, alloc-count) over prose arguments where countable, so the revert gate is falsifiable | 1, 11 | No |
| 8 | Clarify in the doc that SB3 is fixed by Task 9 (the P5 task), not "Task 5" — the coverage table is right; just pre-empt the brief's mislabel | 9 | No |

---

## Closing

Architecture, sequencing, coverage, and the file-overlap analysis are sound and verified — no rework needed. Edit #1 (Task 9) is the single true blocker: its no-dep design is not implementable as written and a subagent will either fail to compile or ship a racy `unsafe` path. Edits #2–#3 will otherwise cause a fresh subagent to mis-navigate (Task 1) or breach the plan's own API boundary and miss a test caller (Task 6). With those three fixed and the four polish edits applied, this plan is subagent-ready.
