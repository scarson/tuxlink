# Adversarial Review (Round 1) — Performance-Audit Scope Partition v1

- **Reviewer:** Claude subagent (general-purpose), agent glade-knoll-shoal
- **Target:** scope partition v1 (commit 43b7fc4)
- **Date:** 2026-06-04

## Verdict

Salvageable with substantial changes. v1 rests on two systematic errors: (1)
slices sized by raw LOC including inline `#[cfg(test)]` modules and `.test.tsx`
files — roughly **2× the real production surface**, so most "oversized → SPLIT?"
flags evaporate once measured on production LOC; (2) the Tier E "render hot path"
framing is **factually wrong** — no canvas, no `requestAnimationFrame` render
loop, no waterfall/spectrum anywhere in `src/`. Genuine hot paths are
concentrated and small (Tier A DSP + FEC). With re-sizing, a corrected hot-path
map, and aggressive batching of cold glue, 22 full cycles → ~10–12 must-do
slices + 1 batched cold pass.

## Confirmed hot paths (evidence)

- `tuxmodem-phy/src/ofdm_main/receiver.rs::demodulate_one_symbol` — per-symbol
  fresh `FftPlanner` + replan (46–47), per-symbol `Vec<Complex>` (42–45), pilot
  `HashSet` per symbol (60–61), `Mapper` per data subcarrier (78).
- `.../ofdm_main/transmitter.rs::modulate_one_symbol` — same anti-patterns
  (80–81 planner, 55–56 HashSet, 72 Mapper, 44/82/85/88–90 vecs).
- `.../ofdm_main/equalizer.rs::equalize` — per-symbol chan_est + output vec
  (37, 64–71); inner interpolation loop. Secondary to FFT replanning.
- `tuxmodem-fec/src/decode.rs::Decoder::decode` — **densest compute in repo.**
  LDPC sum-product BP: `max_iters` × edges, per-check/per-variable `incoming`
  `Vec` allocations *inside* the iteration loop (165–169, 193–197). Adjacency
  cached in `Decoder::new` (good); inner-loop allocs are not. Highest-leverage.
- `.../sync/preamble.rs::scan` — sliding cross-correlation O(signal×template)
  per acquisition (88).
- `.../audio_device.rs` (672) — CPAL real-time stream/callback + ring buffer;
  the true real-time-deadline path. **Plan omits it.**
- `.../robustness_floor/wideband_lowdensity.rs` (802) — largest PHY file, real
  DSP floor mode; buried under bare "robustness_floor" in A1.
- `winlink/lzhuf.rs::compress`/`insert_node` — real LZSS BST match + adaptive
  Huffman, but fixed arrays (no per-byte heap alloc), KB-scale bodies once per
  send/receive → low frequency, modest Impact.

## Refuted / downgraded hypotheses

- **E1 "Radio UI / waterfall-spectrum render hot path" — FALSE.** grep for
  `canvas|getContext|requestAnimationFrame|WebGL|OffscreenCanvas` across
  `src/radio` = nothing. Only "charts" are `radio/charts/Sparkline.tsx` (60 DOM
  `<div>` bars) + `FrameRibbon.tsx`, fed by `useSampleHistory.ts` ticking once
  per second (`setInterval(...,1000)`). No real-time render anywhere in frontend.
- Whole repo has no render hot loop. Only `requestAnimationFrame` in `src/` is a
  one-shot scroll-into-view (`src/help/ReadingPane.tsx:41`).
- **C2 "modem ARQ compute-heavy" — FALSE; I/O + state machine.** ARDOP/VARA DSP
  runs in an external TNC process (`ardopcf`/VARA); `winlink/modem/ardop/transport.rs`
  is TCP plumbing + status accumulation. ~62% of modem dir is inline tests
  (prod 4548 / test 4609 of 9157).
- **D4 "grib parsing" — FALSE.** `src-tauri/src/grib/composer.rs` composes a
  request string for a remote GRIB service; does not decode GRIB binary. Cold.
- **E2 mailbox virtualization** — `src/mailbox/MessageList.tsx` (373) uses no
  virtualization; plain list + `messageSort.ts`. Worth a *finding*
  (large-mailbox scaling), not a render hot path.
- **A2 (tx+rx) downgraded** — `tuxmodem-tx/src/lib.rs`, `tuxmodem-rx/src/lib.rs`
  are CLI driver crates (arg parse, WAV I/O, `run_transmission`, `compute_ber`).
  Dense loops live in A1/A3; A2 merely calls them.

## Split proposals (production LOC)

| Slice | Raw | Prod | Verdict |
|---|---|---|---|
| C2 modem | 9157 | 4548 | Split by protocol: **C2a ARDOP** (`modem/ardop/*` ≈2.5k) + **C2b VARA+shared** (`modem/vara/*`+mod.rs+process.rs ≈1.4k) |
| C3 ax25 | 3284 | 1535 | Do not split |
| C4 telnet/listener | ~6000 | telnet ≈1.9k + listener 1381 | Split: **C4a telnet/P2P** + **C4b listener gate** |
| D1 ui_commands | 6094 | 4458 | One slice ok; pure IPC marshalling (cold) → prefer defer |
| D4 feature backends | 7510 | ~4730 | Split by perf-relevance: **D4a search** (real) + **D4b cold** (forms/grib/position/catalog) |
| E1 radio | ~9.5k | 4730 | No split; re-tier warm/cold |
| E3 shell | ~9.1k | 3981 | No split; cold except markdown render of large bodies |

## Cold-glue recommendation

Batch into ONE lightweight pass (3 lanes: complexity + allocation + data-access):
D3, D5, cold D4 (forms/grib/position/catalog), and most of E. D1/E3 keep as
single reduced-depth slices; the one E3 thing worth a look is
`shell/markdownRender.ts` + `sanitizeHtml.ts` on large bodies.

## Cross-slice pipeline recommendation

Add a live-RX pipeline reasoning pass as an **analysis overlay, not a 23rd
slice**: `audio_device.rs` → `rx::decode_one_symbol` → `phy::demodulate_one_symbol`
(FFT replanning) → `fec::Decoder::decode` (LDPC). Per-slice misses the
compounding cost across A1→A3 + the audio frame deadline. TX direction mirrors.
Do NOT fold ARQ/UI in (I/O-bound, external-TNC).

## Coverage corrections

- Gap: `src-tauri/src/main.rs` (D5 lists only `lib.rs`).
- Gap: `src/App.tsx`, `src/main.tsx`, `src/routing.ts` (root render tree/router).
- Mark out-of-scope: `test_helpers.rs`, `src-tauri/src/bin/`, `tuxmodem/.../bin/`.
- A1 omits `audio_device.rs`, `audio_io.rs`, `phy_api.rs`, `modes.rs` — say
  "entire tuxmodem-phy/src" or enumerate all 21 files.
- hf-channel-sim is a sim/test harness (offline BER/SNR sweeps); it *caches* its
  FftPlanner (unlike PHY). Keep but tier below A1–A3.
- No `benches/` exist — recommend Criterion benches in fec/phy as the
  measurement gate for A-tier claims.
- C1/C5 seam: `winlink/session.rs::run_exchange` (B2F driver) lives in C5 but
  drives lzhuf compression in C1. Acceptable (C1 = algorithmic unit); note it.

## Recommended tiering: 22 → ~11 full + 1 batched cold pass

- **MUST-DO (full):** A1 phy, A3 fec, C2a ardop, D4a search, A4 hf-sim, pipeline overlay.
- **NICE (reduced depth):** A2 tx/rx, B1 rig, C1 compression, C2b VARA + C3 ax25, C4a/C4b, E1 radio + E2 mailbox.
- **DEFER (cold sweep, 3 lanes):** D1, D2, D3, D5, cold D4, E3, E4-cold, E5.

## Top 5 changes to v1

1. Re-size every slice on production LOC (strip `#[cfg(test)]` + `.test.*` + `.css`).
2. Delete "render hot path" framing for Tier E; re-tier warm/cold.
3. Split D4 by perf-relevance: carve out `search/`; batch forms/grib/position/catalog cold.
4. Add `audio_device.rs` + live-RX pipeline overlay to Tier A.
5. Close coverage gaps (`main.rs`, `App.tsx`/`main.tsx`/`routing.ts`); exclude test/bin; recommend Criterion benches in fec/phy.
