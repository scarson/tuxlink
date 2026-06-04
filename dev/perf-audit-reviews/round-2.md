# Adversarial Review (Round 2) — Performance-Audit Scope Partition v2

- **Reviewer:** Opus subagent (independent adversarial), agent glade-knoll-shoal
- **Target:** scope partition v2 (`dev/perf-audit-scope-plan.md`)
- **Date:** 2026-06-04
- **Mandate:** independent re-derivation; skeptical of BOTH round 1 and v2; over-correction is a defect.

## Verdict

**Minor-edits.** v2 is fundamentally sound: the hot-path map is honest, the
"no frontend render hot path" claim is verified true, the must-do tiering is
defensible, and the cold batching is the right economy. But it is **not
ready-to-execute** as written, for three concrete reasons, in priority order:

1. **A real coverage gap:** `src-tauri/src/wizard.rs` (614 prod LOC) lands in
   **no slice** — the ledger conflates it with the TS `src/wizard/`.
2. **A hot path BOTH rounds missed:** per-subcarrier alphabet rebuild inside
   `compute_llr` — a nested allocation in the RX demod inner loop, arguably the
   single highest-leverage allocation finding in the PHY, and it is invisible at
   the granularity v2 describes M1.
3. **One mis-tier worth challenging:** M3 (ARDOP) as a *full 8-phase* cycle is
   over-provisioned for what is verified to be external-TNC socket I/O + a state
   machine with zero DSP; and `native_mailbox.rs` carries a genuine N+1 yet sits
   in the lowest-depth cold sweep.

None of these is a rework trigger. They are surgical edits.

---

## Independent hot-path map (re-derived from source) + diff vs v2

Re-derived without anchoring on v2's table. Cited `file:fn:line`.

| # | Where | Evidence | Rank | v2 status |
|---|-------|----------|------|-----------|
| H1 | **`constellations.rs::compute_llr` → `alphabet()` rebuilt per call** | `receiver.rs:83` calls `compute_llr` **once per data subcarrier**; `compute_llr:142` calls `self.alphabet()`; `alphabet:164-176` allocates a `Vec` and calls `self.map()` (another alloc) `n=2^bps` times (up to 64 for QAM64). So the full constellation alphabet is rebuilt — with nested Vec allocs — for **every subcarrier of every symbol**. | **Critical** | **MISSED by both rounds** |
| H2 | OFDM RX per-symbol fresh `FftPlanner` + replan + per-symbol `Vec`/`HashSet`/`Mapper` | `receiver.rs:46-47` (planner+replan), `:42` (body Vec), `:56` (equalizer alloc), `:60` (pilot HashSet), `:78` (Mapper per sc) | Critical | ✅ in v2 |
| H3 | OFDM TX mirror | `transmitter.rs:80-81` planner, `:55` HashSet, `:72` Mapper, `:44/82/85/89` Vecs | Critical | ✅ in v2 |
| H4 | LDPC SPA per-iteration `incoming` Vec allocs | `decode.rs:165` + `:193`, **inside** the `for iter in 0..max_iters` loop (`:158`); adjacency cached in `new` (good) | Critical | ✅ in v2 |
| H5 | Preamble sliding cross-correlation O(signal×template) | `preamble.rs:scan:88-94` nested loop, template re-energized each scan but template itself cached | Major | ✅ in v2 |
| H6 | Real-time CPAL callback / deadline | `audio_device.rs:277-279` `build_output_stream(move |out: &mut [f32]|…)`; mirror input stream | Major (deadline) | ✅ in v2 (was v1 omission) |
| H7 | **`narrow_fsk.rs` fresh `FftPlanner` per call** | `robustness_floor/narrow_fsk.rs:83-85` — same anti-pattern as H2 but in the FSK floor mode | Secondary | **understated** (v2 names only OFDM RX/TX; floor planner-rebuild not called out) |
| H8 | `codec.rs::decode_soft` rebuilds `deinterleave_index_perm` per decode | `codec.rs:149` calls `deinterleave_index_perm(n, rows)` (`:192` allocs `vec![0;n]` + double loop) on **every** decode; trivially hoistable to `OfdmAdaptiveCodec::new` | Modest | not separately noted (subsumed in M2) |
| H9 | Equalizer per-symbol `chan_est` + output Vec + interpolation | `equalizer.rs:37`, `:64-71`, inner interp `:56-59` | Secondary | ✅ in v2 |
| H10 | **`native_mailbox.rs::list` reads every message body to list a folder** | `native_mailbox.rs:99-104`: `fs::read_dir` then `fs::read(&path)` per file → O(messages × body_size) per folder open; read-amplification / N+1 | Warm | **mis-tiered** (in cold sweep) |
| H11 | Search markdown ingest byte-loop | `extractor.rs:strip_inline_md:314-364` with `find_seq_md:369-374` O(n·m) substring scan per line; latin1 `bytes[i] as char` push | Warm | ✅ in v2 (M4) |

### Diff summary

- **Agreements (v2 right):** H2, H3, H4, H5, H6, H9, H11. The marquee
  FFT-replanning + LDPC-allocation framing is accurate; the audio-deadline
  addition was the correct round-1 fix.
- **v2 overstates:** **nothing materially.** v2 does NOT overclaim — it is, if
  anything, conservative. (Round 1's instinct to over-correct toward "small hot
  paths" did not bleed into v2 overstatement.)
- **v2 understates:** **H1** (the big one — a *nested* alloc the per-symbol
  framing hides), **H7** (floor-mode planner rebuild), **H8** (per-decode perm
  rebuild), **H10** (mailbox N+1 buried in cold sweep).
- **Both rounds missed:** **H1** and **H7**. H1 is the headline new finding: it
  is strictly worse than the per-symbol planner because it is per-*subcarrier*
  (inner loop), not per-symbol, and each rebuild does `2^bps` sub-allocations.

All of H1–H9 live inside M1/M2 paths, so **coverage is intact** — these are
"the audit must look here" notes, not new slices. They sharpen M1/M2's Phase-2
targets and strengthen the O1 overlay rationale (H1 compounds with H4 across the
audio-frame deadline — exactly the overlay's thesis).

---

## Tiering challenges (evidence)

### M3 (ARDOP) as a FULL cycle — challenge, recommend reduced-depth

Read `ardop/transport.rs` (823 prod / 1425 test) and `ardop/data.rs` (339 prod /
451 test). Findings:

- `data.rs:48,89` `TcpStream`; `:104-129` leftover `VecDeque<u8>` + frame
  decoder pump; `:317-329` length-prefixed framing write. **Pure socket I/O +
  framing.** No DSP, no per-sample loop.
- `transport.rs:179` connect-retry loop; `:607-676` recv/drain event loops;
  `:337-396` throughput-meter deque prune (`record_buffer` / `current_throughput_bps`).
  The only "compute" is a rolling-window deque trim — O(window), trivial.
- The DSP genuinely runs in the external `ardopcf` process (confirmed:
  `process.rs:74-80` spawns it as a child binary).

**So M3's perf surface is concurrency (reader-thread ↔ cmd/data socket borrow
coordination — see the documented split-borrow hazard at `transport.rs:666`) +
allocation in the frame path + I/O latency.** A *full* 8-phase cycle buys
algorithmic-complexity / payload / framework-currency / startup lanes that have
almost nothing to bite on here. **Recommendation:** demote M3 from full to a
**reduced-depth cycle weighted to concurrency + allocation + data-access**
(the lanes that match its actual risk). It is still warm/important (live data
throughput, real-time-adjacent buffering) — just not full-cycle-shaped. This is
a real disagreement with v2, grounded in the code.

### M5 (hf-channel-sim) as MUST-DO over the sync slice — partial challenge

- v2's claim *"hf-channel-sim caches its FftPlanner unlike PHY"* is **only half
  true.** `channel.rs:39,68` caches the planner as a struct field ✅, but
  `fading.rs:34,49,86` plans **forward AND inverse FFTs on every `process` call**
  (planner passed by `&mut`, re-`plan_fft_*` each block), and `analysis.rs:50-51`
  plans per call. So the sim has the SAME planner-rebuild anti-pattern in its
  fading path, just not in `channel.rs`.
- That said, hf-sim is **offline** (BER/SNR sweep harness, not on any live
  deadline). Keeping it MUST-DO is defensible *because* it shares the
  planner-rebuild pathology with PHY and the fix transfers — but its urgency is
  below M1/M2/M3. **Recommendation:** keep M5 must-do but explicitly **down-rank
  its execution priority below M3/M4** and **correct the "caches its planner"
  claim** (it's `channel.rs` only; `fading.rs`/`analysis.rs` re-plan). The sync
  slice does not need its own cycle — `sync/` is already inside M1 (verified:
  `frame_sync.rs` is a trivial FSM, `carrier_offset.rs`/`symbol_timing.rs` are
  single linear passes, `preamble.rs::scan` H5 is the only hot one and M1 owns
  it). v2 is right not to carve a sync slice.

### Cold sweep too aggressive? — mostly NO, one exception (mailbox N+1)

Verified the four warm-suspects:
- `winlink_backend.rs` (3262), `modem_commands.rs` (1802), `ui_commands.rs`
  (6093): all do `std::fs`, but on grep they are IPC marshalling + config/session
  glue, not query loops over large N. Cold sweep is correct.
- **`native_mailbox.rs` (537 prod) is the exception.** `list:99-104` reads every
  `.b2f` body off disk to build folder metadata (**H10**, N+1 / read-amplification).
  This scales with mailbox size and is the *backend root* of the same
  large-mailbox-scaling symptom R9 flags on the TS side (`MessageList.tsx`
  non-virtualized — verified still non-virtualized, only highlight-substring
  `slice` calls at `:105-109`, no react-window). **Recommendation:** pull
  `native_mailbox.rs` OUT of the cold sweep and either (a) fold it into R9 as the
  backend half of the mailbox-scaling finding, or (b) give it a one-finding note
  in M4 (it's adjacent to search/index — `native_mailbox` already calls the search
  index at `:72`). The data-access cold-sweep lane *would* technically catch it,
  but burying a real N+1 in the lowest-depth tier under-serves it.

### R9 = radio + mailbox merge — sound

Verified both halves are warm-not-hot: `radio/useSampleHistory.ts:45`,
`sections/useListenerState.ts:122`, `modes/ArdopRadioPanel.tsx:139` are all
1 Hz `setInterval` tick churn (not render loops); `mailbox/messageSort.ts:168`
+ `MessageList.tsx:294` is sort-on-render of a non-virtualized list. Both are
TS, both warm, both "scaling under large N" stories. Merge is coherent.

---

## Claim verifications (my own greps)

1. **"No frontend render hot path" — TRUE (verified).** `grep -rn
   'canvas|getContext|requestAnimationFrame|WebGL|OffscreenCanvas|createImageData'
   src/` → only hits are two `canvas` mentions in `wizard/wizard.css` **comments**
   (CSS prose, no `<canvas>`), and a single `requestAnimationFrame` at
   `help/ReadingPane.tsx:41` (one-shot scroll-into-view). No counterexamples.
   v2 principle #4 stands.

2. **"Production ≈ 2× test" — FALSE as a uniform rule; true only as a loose
   average.** Per-file `prod`/`test` line split:
   - `wideband_lowdensity.rs`: 352 / 450 (test 1.28×)
   - `ax25/datalink.rs`: 228 / 1571 (test **6.89×**)
   - `modem/process.rs`: 300 / 177 (test 0.59×)
   - `search/extractor.rs`: 183 / 242 (1.32×)
   - `lzhuf.rs`: 658 / 63 (test **0.10×** — almost all production)
   - `ardop/transport.rs`: 823 / 1425 (1.73×)

   The ratio ranges from 0.10× to 6.89×. **The "≈2×" heuristic is unreliable for
   per-slice sizing** — v2 leans on it ("most v1 oversized flags dissolve") but
   that justification only holds on average, not per slice. **Recommendation:**
   replace the global "2×" assertion with the actual per-slice prod counts where
   sizing is load-bearing (e.g., M3 transport, R5 ax25), or soften the claim to
   "test bulk inflates raw LOC; size on measured prod LOC per slice" without the
   misleading 2× constant.

---

## Coverage / seam corrections

1. **GAP — `src-tauri/src/wizard.rs` (614 prod LOC) is in NO slice.** The ledger
   (line 121) lists only the TS `src/wizard/`; the cold-sweep Rust enumeration
   (line 90-91) lists `bootstrap/lib/main/app_backend/compose_window/help_window/
   tray/consent_gate/theme_state` but **omits `wizard.rs`**. It's a real Tauri
   command module (`WizardMutex` state at `:54`). **Fix:** add `wizard.rs` to the
   Rust cold sweep.

2. **SEAM — `modem/process.rs` is shared infra bucketed in R4 (VARA), but M3
   (ARDOP) depends on it.** `mod.rs:21` declares `process` as a sibling of
   `ardop`/`vara`; `transport.rs:161` (`ManagedModem::spawn`) drives it. v2 puts
   `process.rs` in R4. The ARDOP cycle (M3) won't formally cover the
   process-spawn/kill path it relies on. **Fix:** note `process.rs` as a shared
   dependency both M3 and R4 touch (analogous to v2's own lzhuf-in-R3-driven-by-R8
   note), or move it to a shared-infra line. Low severity (it's small + I/O), but
   the ledger should not imply M3 covers spawn when it doesn't.

3. The `winlink/modem/` split (ardop→M3, vara+mod+process→R4) **is along real
   module boundaries** — verified `mod.rs:20-22` and the `ardop/`, `vara/` dir
   contents. No double-count beyond the `process.rs` seam above.

4. No other dir double-counts found. `sync/` correctly inside M1; `robustness_floor/`
   inside M1; search inside M4; ax25 in R5.

---

## Pipeline-overlay call (O1)

**Keep O1 as an overlay, not a slice — and I can now argue it more strongly than
v2 did.** The overlay's thesis (compounding cost across M1→M2 against the audio
frame deadline) is *concretely* validated by **H1**: the RX path's per-subcarrier
`compute_llr`→`alphabet()` rebuild (`receiver.rs:83`→`constellations.rs:142`)
feeds LLRs straight into M2's per-iteration-allocating LDPC decoder
(`decode.rs:165,193`). A per-slice audit sees H1 as "an M1 allocation finding" and
H4 as "an M2 allocation finding"; only the overlay sees that **both fire inside
the same real-time symbol budget**, so their alloc pressure adds. Folding this
into a code slice would force an artificial M1∪M2 mega-slice (mixes two crates,
violates language-homogeneity is fine but violates bounded-subsystem sizing).
The overlay is the right instrument. **No change** except: explicitly list H1 as
the overlay's worked example.

---

## Final ranked minimal edits to make v2 executable

1. **[coverage, blocking] Add `wizard.rs` (614 LOC) to the Rust cold sweep.** It
   is currently un-audited. One-line ledger fix.
2. **[hot-path, high-value] Add H1 to the corrected hot-path map** —
   `constellations.rs::compute_llr` rebuilds `alphabet()` per subcarrier (nested
   alloc, inner loop). Flag as an explicit M1 Phase-2 target and the O1 overlay's
   worked example. Highest-leverage allocation finding both rounds missed.
3. **[tiering] Demote M3 (ARDOP) from full cycle to reduced-depth** weighted to
   concurrency + allocation + data-access. It is verified external-TNC socket
   I/O + state machine; full-cycle complexity/payload/startup lanes have nothing
   to bite. Keeps M-tier honest.
4. **[tiering] Pull `native_mailbox.rs` out of the cold sweep** (H10 N+1 in
   `list:99-104`); attach to R9 as the backend half of the mailbox-scaling
   finding, or note in M4.
5. **[claim] Replace the global "production ≈ 2×" assertion** with per-slice prod
   counts where sizing is load-bearing; the real ratio spans 0.10×–6.89×.
6. **[claim] Correct "hf-sim caches its FftPlanner"** — true for `channel.rs`
   only; `fading.rs:49,86` + `analysis.rs:50` re-plan per call (H7 sibling). Add
   H7 (`narrow_fsk.rs:83` planner rebuild) to M1's notes.
7. **[seam, low] Note `modem/process.rs` as shared M3↔R4 infra** so the ledger
   doesn't imply M3 covers the spawn path.

**Right-sizing call:** 16 units is proportionate *after* edit 1 (gap) and edit 3
(M3 demote → 4 full + 1 overlay + 10 reduced + 1 cold sweep). No further merges
or cuts needed — the slice count is not the problem; the two tier mismatches
(M3, native_mailbox) and the one gap (wizard.rs) are. Do NOT over-correct into
fewer slices; v2's granularity is sound.
