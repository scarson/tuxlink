# Performance-Audit Whole-Repo Scope Partition

> Purpose: slice the tuxlink repo into bounded scope slices, each suitable for
> ONE `performance-audit-cycle` run, collectively covering the whole repo.
> Subject to a mandatory **≥5-round adversarial review** before any audit run
> executes. See `dev/perf-audit-review-log.md` for the round-by-round record.

## Revision history

- **v1** (43b7fc4) — initial 22-slice partition.
- **v2** (b21cfbe) — Round 1 (Opus): sized on production LOC, killed the
  nonexistent frontend "render hot path," sliced by perf-relevance, batched cold
  glue, added the omitted real-time audio path + live-RX overlay. 22 → 16 units.
- **v3** (1f… ) — Round 2 (Opus): fixed `wizard.rs` coverage gap; added two
  missed PHY hot paths (H1 per-subcarrier alphabet rebuild, H7 narrow-FSK
  per-call FFT planner); demoted M3 (ARDOP) full→reduced (verified external-TNC
  I/O); promoted the storage backend out of the cold sweep (native_mailbox
  read-amplification); corrected two soft claims.
- **v4** (cf… ) — Round 3 (Opus): verified all of Round 2's claims against
  source (all confirmed); **corrected a factual error** — `MessageList.tsx` IS
  virtualized (`react-virtuoso`), so R9's mailbox half is weak and the real
  large-mailbox cost is the backend H8 (R10), not a frontend render; down-ranked
  H7 to Secondary (per-call, planner already hoisted); refined H9; named the
  concrete M3 target (`data.rs` per-byte `VecDeque` drain). Coverage verified
  airtight (all 18 `src-tauri/src/*.rs` + every subdir + crate).
- **v5** (this) — Round 4 (Opus, partition-DESIGN lens): found a substantive
  **cross-slice calibration defect** — lzhuf's frequency-establishing caller
  (`winlink_backend.rs::build_outbound_proposals`, loops the Outbox compressing
  every message) is in the cold sweep, so R3 can't see the call frequency. Fix:
  a **winlink call-frequency-map pre-artifact (W0)** handed to R3/R8/R10 as
  adjacent context + run **R8 before R3**. Also: demote **R7** (listener gate)
  reduced→cold (no hot loop); new finding **H11** (`transfer.rs::read_block`
  per-byte `read_exact` loop); add a **verification-mode** flag for
  hardware-deferred slices; fix the lzhuf-driver attribution; narrow R2 to
  timing-only. 17→16 units. **Round 5 pending.**

## Slicing principles (v3)

1. **Language-homogeneous** slices — Rust and TS never mix.
2. **Size on PRODUCTION LOC** (exclude inline `#[cfg(test)]`, `.test.*`, `.css`).
   The prod:test ratio is **not uniform** — measured per-file range ~0.10×–6.89×
   (`lzhuf.rs` ~90% production; `ax25/datalink.rs` ~87% test). Measure each
   slice; do not assume a flat 2×.
3. **Slice by perf-relevance, not raw size.**
4. **No frontend render hot loop exists** (verified: zero `canvas`/`getContext`/
   `WebGL`; one one-shot `requestAnimationFrame` at `help/ReadingPane.tsx:41`).
   Tier E is warm/cold UI.
5. **Complete coverage**; test/bin/probe harnesses explicitly out-of-scope.
6. **Live-RX/TX pipeline** = analysis overlay, not a slice.

## Corrected hot-path map (Rounds 1–2 + verified)

| # | Where | Evidence | Rank | Slice |
|---|-------|----------|------|-------|
| H0 | OFDM per-symbol FFT **replanning** + per-symbol allocs | `tuxmodem-phy/.../receiver.rs::demodulate_one_symbol` (fresh `FftPlanner`/symbol, per-symbol `Vec`/`HashSet`/`Mapper`); `transmitter.rs::modulate_one_symbol` | Critical | M1 |
| **H1** | **Per-subcarrier alloc in LLR/demod inner loop** | `constellations.rs::compute_llr:142` rebuilds `self.alphabet()` (up to 64 nested `map()` allocs) **per data subcarrier** at `receiver.rs:83`; `receiver.rs:78` ALSO allocates a fresh `Mapper::new` per subcarrier — nested alloc in the RX demod inner loop, worse than the per-symbol planner | Critical | M1 |
| H2 | LDPC sum-product decode, per-iteration `Vec` allocs | `tuxmodem-fec/src/decode.rs::Decoder::decode` (`incoming` vecs inside the `max_iters` loop) | Critical | M2 |
| H3 | Frame-sync sliding cross-correlation | `tuxmodem-phy/src/sync/preamble.rs::scan` O(sig×template) | Major | M1 |
| H4 | Real-time audio callback / ring buffer | `tuxmodem-phy/src/audio_device.rs` (CPAL stream; real-time deadline) | Major (deadline) | M1 |
| H5 | Equalizer per-symbol alloc + interpolation | `ofdm_main/equalizer.rs::equalize` | Secondary | M1 |
| **H7** | Narrow-FSK per-call FFT planner | `robustness_floor/narrow_fsk.rs:83` builds a fresh `FftPlanner` per *call* (already hoisted out of the symbol loop, `:83-95`) — real but lower frequency than the OFDM per-symbol path | Secondary | M1 |
| H6 | Search index/extract loops | `src-tauri/src/search/extractor.rs:281,371` + SQLite FTS | Warm | M4 |
| H8 | **Mailbox list read-amplification** | `native_mailbox.rs::list:99-104` does `read_dir` + `fs::read(body)` **per message** to list a folder — N+1 / read-amplification; backend root of the non-virtualized `MessageList` symptom | Warm | R10 |
| H9 | hf-sim per-call FFT planner | `analysis.rs:50` builds a fresh planner per call (genuinely uncached). `channel.rs` caches its planner; `fading.rs:49,86` re-plans hit rustfft's internal plan cache so they're cheaper than they look | Warm | M5 |
| H10 | LZHUF compression | `winlink/lzhuf.rs::compress`/`insert_node` (real algo, fixed arrays). **Frequency NOT visible in R3** — driven by `winlink_backend.rs::build_outbound_proposals:233,250` looping the whole Outbox (see W0) | Modest→re-rank w/ W0 | R3 |
| H11 | Per-byte block read on receive | `winlink/transfer.rs::read_block:100-102` per-byte `read_exact` loop — receive-side mirror of the ARDOP `data.rs` drain; consumed at R8's per-received-message frequency | Modest | R3 |

**Refuted as hot (verified):** frontend render; `winlink/modem` ARQ (DSP runs in
external `ardopcf`/VARA child — `process.rs:74`; this code is TCP plumbing +
framing + state machine); `src-tauri/src/grib` (composes a request string);
`tuxmodem-tx`/`rx` crates (CLI drivers around the M1/M2 hot loops).

### W0 — winlink call-frequency map (pre-artifact, not an audit unit)

Round 4 found that the winlink data-path's frequency-establishing callers live
in DIFFERENT slices from the impls they drive — `winlink_backend.rs::build_outbound_proposals`
(cold sweep) loops the Outbox calling `lzhuf::compress` (R3); `session.rs::receive_turn`
(R8) drives `decompress`/`transfer::read_block` (R3). A per-slice lane that
can't see the caller under-ranks the finding. **Before the winlink-family runs
(R8, R3, R10), produce `docs/perf-audits/<date>-winlink-call-frequency-map.md`**:
which backend/session loops invoke compression / decompression / framing, and at
what structural frequency (per-message, per-proposal, per-block). Hand it to
R3/R8/R10 (and the cold sweep's `winlink_backend.rs` portion) as **adjacent
context** so Impact is calibrated correctly. Cheap insurance; closes the gap
without merging slices.

### Verification modes (which slices can be measured here)

The container builds+tests the pure-Rust crates (verified: `tuxmodem-fec` +
`tuxmodem-phy` build clean; fec tests pass). So **M1/M2/M4/M5** and the
pure-logic winlink slices support a real complexity/allocation argument and, for
fec/phy, dynamic measurement. **Hardware-deferred** (no rig/TNC/audio device →
dynamic lane cannot run; rely on complexity/allocation argument): the audio
real-time path (M1 H4), **M3** (external TNC), **R1** run paths, **R2** (and
note: R2's allocation-argument fallback is weak — it has no allocation concern,
so its timing findings are largely unfalsifiable without a rig; state this in
the R2 report rather than over-claiming).

---

## Slices

### MUST-DO — full 8-phase cycle (4 + 1 overlay)

| ID | Slice | Paths | Why full |
|----|-------|-------|----------|
| **M1** | OFDM PHY + real-time audio | entire `tuxmodem/crates/tuxmodem-phy/src` (ofdm_main/, sync/, equalizer, **constellations**, coded_modulation, robustness_floor/, subcarrier_snr, **audio_device.rs**, audio_io.rs, phy_api, modes) | Marquee FFT-replanning (H0), per-subcarrier alphabet rebuild (H1), narrow-FSK planner (H7), real-time deadline (H4) |
| **M2** | FEC (LDPC) | `tuxmodem-fec/src` | Densest compute; per-iteration allocs (H2) |
| **M4** | Search backend | `src-tauri/src/search/` | SQLite FTS + `extractor.rs` substring/line loops (H6) |
| **M5** | HF channel simulator | `hf-channel-sim/src` | Offline per-sample DSP; per-call FFT planners in `fading.rs`/`analysis.rs` (H9). **Down-ranked** below M4 (offline, no deadline) |
| **O1** | **Live RX/TX pipeline overlay** (analysis, not a code slice) | reconcile M1+M2 per-symbol alloc budget (`audio_device`→`demodulate_one_symbol`→`compute_llr`→`Decoder::decode`) vs audio frame deadline; TX mirror | compounding end-to-end cost; run after M1 & M2 |

### REDUCED-DEPTH cycle (lanes: algorithmic, memory/allocation, data-access, concurrency where threads exist; skip idiom-currency/payload/startup unless flagged)

| ID | Slice | Paths | Focus |
|----|-------|-------|-------|
| **M3** | ARDOP modem transport (**demoted from full**) | `winlink/modem/ardop/*` (transport.rs, session.rs, listener.rs, data.rs) | concrete target: per-byte `VecDeque<u8>` drain + `leftover.extend(payload)` moving whole message body byte-by-byte (`data.rs:104,145`); concurrency split-borrow hazard (`transport.rs:666`); socket I/O. DSP is external-TNC |
| **R1** | Modem TX/RX CLI orchestration | `tuxmodem-tx/src` + `tuxmodem-rx/src` (2317 LOC; orchestration around the M1/M2 hot loops — WAV I/O, `run_transmission`, `compute_ber`) | I/O + buffer copies; **hardware-deferred** for run paths |
| **R2** | Rig / PTT control (timing-only) | `tux-rig-rts/src` + `tux-rig-cm108/src` | `watchdog.rs:78` sleep-poll loop (no alloc concern); timing/latency only; **hardware-deferred** — measurement needs a rig |
| **R3** | Compression + B2F assembly | `winlink/lzhuf.rs`, `message.rs`, `compose.rs`, `proposal.rs`, `transfer.rs`, `wire.rs` | lzhuf (H10), per-byte `read_block` (H11); **driven by `winlink_backend.rs::build_outbound_proposals` (W0 context), NOT session.rs**; run AFTER R8 |
| **R4** | VARA + shared modem | `winlink/modem/vara/*`, `modem/mod.rs`, `process.rs` (child-process mgmt; **shared seam with M3** — primary home here) | |
| **R5** | AX.25 datalink | `winlink/ax25/` | framing/CRC |
| **R6** | Telnet / P2P transport | `winlink/telnet.rs`, `telnet_listen.rs`, `telnet_p2p*.rs`, `relay_banner.rs` | |
| **R8** | B2F session driver | `winlink/session.rs`, `handshake.rs`, `credentials.rs`, `secure.rs`, `mod.rs` | establishes per-message frequency for R3 (`receive_turn:463-471`); run BEFORE R3 |
| **R9** | Warm frontend UI | `src/radio/` (1 Hz sparkline churn, `useSampleHistory`) + `src/mailbox/` (re-render behavior, context/selector cost). **NB:** `MessageList.tsx` IS virtualized (`react-virtuoso`, `:18,338`) and `messageSort` is `useMemo`'d — the large-mailbox cost is the *backend* H8 (R10), not a frontend render; R9's mailbox half is a light check | |
| **R10** | Storage / config backend (**promoted from cold sweep**) | `native_mailbox.rs`, `config.rs`, `user_folders.rs`, `session_log.rs` | mailbox list read-amplification (H8) |

### DEFER — single batched COLD SWEEP (3 lanes only: complexity + allocation + data-access)

- **Rust cold:** `ui_commands.rs`; `winlink_backend.rs` (hand it the W0
  frequency map — its `build_outbound_proposals` Outbox loop is lzhuf's real
  driver) + `modem_commands.rs` + `modem_status.rs` (note: `modem_status.rs:392`
  is a 4 Hz background broadcaster — borderline-warm but stays cold; sweep should
  eyeball its per-tick work); `winlink/listener/` (**demoted from R7** — per-
  connection consent/auth state, no hot loop; `fs::read` once per arm);
  `forms/` + `grib/` + `position/` + `catalog/`;
  `bootstrap.rs` + **`wizard.rs`** (Tauri `WizardMutex` command module — *Rust*,
  was orphaned in v2) + `lib.rs` + `main.rs` + `app_backend.rs` +
  `compose_window.rs` + `help_window.rs` + `tray.rs` + `consent_gate.rs` +
  `theme_state.rs`.
- **TS cold/warm:** `src/shell/` (warm exception: `markdownRender.ts` +
  `sanitizeHtml.ts` on large bodies); `src/search`, `compose`, `packet`,
  `connections`, `session`, `modem`; `src/wizard`, `forms`, `help`, `grib`,
  `catalog`; root `App.tsx` + `main.tsx` + `routing.ts`.

### OUT-OF-SCOPE (documented, not audited)

`test_helpers.rs`, `src-tauri/src/bin/`, `tuxmodem/crates/*/src/bin/`, all
`*/tests/`, `*/examples/`, every inline `#[cfg(test)]` module, `*.test.tsx`.
**Measurement gate:** no Criterion benches exist — recommend adding them to
`tuxmodem-fec` + `tuxmodem-phy` to *measure* M1/M2 claims (Phase 4 of those).

---

## Execution order

M1 → M2 → **O1** → M4 → M5 → M3 → **W0** (winlink frequency map) → R8 → R3 → R10
→ R5 → R6 → R4 → R1 → R2 → R9 → cold sweep.

(R8 before R3 so the session driver's frequency map is in hand; W0 before the
winlink family; R7 removed — folded into cold sweep.)

**Totals:** 4 full + 1 overlay + 10 reduced + 1 cold sweep = **16 units**
(+ W0 pre-artifact, not a full unit).

## Coverage ledger (every code dir/file lands once)

- Rust modem: phy→M1, fec→M2, tx+rx→R1, rig-rts+rig-cm108→R2. ✅
- hf-channel-sim→M5. ✅
- winlink: modem/ardop→M3, modem/vara+mod+process→R4, lzhuf+message+compose+proposal+transfer+wire→R3, ax25→R5, telnet*+relay_banner→R6, session+handshake+credentials+secure+mod→R8, **listener→cold sweep** (demoted from R7). ✅
- src-tauri other: search→M4; native_mailbox+config+user_folders+session_log→R10; ui_commands/winlink_backend/modem_commands/modem_status/forms/grib/position/catalog/bootstrap/**wizard.rs**/lib/main/app_backend/windows/tray/consent_gate/theme_state→cold sweep. ✅
- src frontend: radio+mailbox→R9; shell/search/compose/packet/connections/session/modem/wizard/forms/help/grib/catalog + root→cold sweep. ✅
