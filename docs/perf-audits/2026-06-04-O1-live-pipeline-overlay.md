# O1 — Live RX/TX pipeline overlay (analysis, not a code slice)

**Purpose (from the scope plan):** per-slice audits can miss cost that *compounds
across* the M1 (tuxmodem-phy) ↔ M2 (tuxmodem-fec) boundary on the live
receive/transmit path. This overlay reconciles their findings against the
end-to-end pipeline and the real-time audio budget. It introduces no new
findings of its own — it relates M1's and M2's.

## The actual live RX path (verified during M1)
```
cpal input callback (audio_device.rs)              ── real-time thread
   → record full buffer, DROP the stream            ── BATCH boundary
   → wideband_lowdensity::receive_multi             ── off-RT, per-symbol loop
       → demodulate_one_symbol (per OFDM symbol)
           · FftPlanner rebuilt per symbol            [M1 H0, Critical]
           · per-subcarrier Mapper::new + alphabet()  [M1 H1, Critical]
           · pilot HashSet + equalizer rebuilt/symbol [M1 findings]
       → decode_symbol_bytes  ── HARD-DECISION slicing; **FEC BYPASSED today**
           · (Decoder::decode / LDPC SPA would insert here once FEC lands) [M2, LATENT]
```
TX mirrors it: bits → per-subcarrier `Mapper` → `modulate_one_symbol` (FftPlanner
rebuilt per symbol) → cpal output callback.

## Reconciliation findings (cross-slice)

1. **The real-time deadline and the per-symbol allocations are on DIFFERENT
   threads — this is the key calibration.** M1's cost-map established the DSP is
   *batch* (record-then-process), so the per-symbol planner/alphabet/equalizer
   allocations do **not** threaten the cpal audio deadline. They manifest as
   **longer decode wall-time + allocator pressure on a received transmission**,
   not as audio underrun. The ONLY real-time-deadline item on the pipeline is the
   **audio_device.rs input-callback mutex-held-across-copy** (M1 concurrency,
   Major) — independent of the per-symbol DSP. *Fixing the per-symbol allocs does
   not reduce underrun risk; fixing the mutex does. Don't conflate them.*

2. **Per-symbol allocation budget (batch, not RT).** At 48 kHz a symbol is
   1280/2560 samples → ~19–38 symbols/sec of decoded audio. Each symbol currently
   does 1 `FftPlanner` rebuild + (dozens of subcarriers × (`Mapper::new` + full
   `alphabet()` rebuild, up to 64 nested allocs)) + a pilot `HashSet` + an
   equalizer alloc. Over a multi-minute Winlink transfer that is a large
   *cumulative* alloc load — exactly what the M1 fix-plan's **frame-scoped
   `OfdmContext`** (hoist planner/mask/mapper/equalizer/pilot out of the symbol
   loop) collapses. **This is the single highest-leverage change for the live
   pipeline, and it is already the spine of the M1 plan.**

3. **The FEC stage is latent — sequence the M2 rewrite BEFORE integration.** Today
   `decode_symbol_bytes` uses hard-decision slicing and `tuxmodem-fec` has no live
   caller (M2 §reachability). Once FEC is wired in (`#4`), `Decoder::decode`
   inserts between LLR and bytes, adding the O(d_c²) check-node cost + per-iteration
   allocs **per FEC block** (not per symbol). The integrated pipeline's per-block
   cost would then be dominated by LDPC SPA iterations. **Recommendation:** land
   M2's forward-backward + flat-CSR rewrite (M2 Q1/Q2/Q3) *before* FEC is
   integrated — it's a lossless refactor with no live-path regression risk now,
   and far cheaper to do before it's on a hot path.

4. **No DSP analog of the winlink cross-slice frequency gap.** Round 5 verified
   M1's per-symbol frequency is established *within* M1 (`receive_multi` drives
   `demodulate_one_symbol`); the rx/tx CLI crates (R1) are not multi-symbol
   drivers. So M1/M2 each see their own frequency — this overlay suffices; no
   adjacent-context note (the W0 analog) is needed for the DSP tier.

## Net guidance for the operator
- **Ship order on the live path:** (a) M1 `OfdmContext` spine (biggest decode-time
  win, no behavior change); (b) audio_device input-callback mutex fix (the only
  underrun-risk item); (c) M2 SPA rewrite *before* FEC integration; (d) FEC
  integration itself (separate feature, `#4`).
- **Do not** spend real-time-safety effort on the per-symbol DSP allocations —
  they're off the RT thread. **Do** treat the input-callback mutex as the RT item.
