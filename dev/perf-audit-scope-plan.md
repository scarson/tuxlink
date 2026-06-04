# Performance-Audit Whole-Repo Scope Partition

> Purpose: slice the tuxlink repo into bounded scope slices, each suitable for
> ONE `performance-audit-cycle` run, collectively covering the whole repo.
> Subject to a mandatory **two-round adversarial review** before any audit run
> executes.

## Revision history

- **v1** (commit 43b7fc4) — initial 22-slice partition. Flaws found by Round 1
  adversarial review (Claude subagent, agent glade-knoll-shoal): sized on raw
  LOC (incl. inline `#[cfg(test)]`, `.test.tsx`, `.css`) ≈ 2× production;
  hallucinated a frontend "waterfall/spectrum render hot path" that does not
  exist; mis-ranked modem-ARQ and grib as hot.
- **v2** (this) — re-sized on production LOC, corrected hot-path map, slices by
  perf-relevance, batches cold glue, adds the omitted real-time audio path +
  live-RX pipeline overlay, closes coverage gaps. 22 → 5 full + 1 overlay +
  9 reduced-depth + 1 cold sweep. **Awaiting Round 2 (Codex / independent
  engine).**

## Slicing principles (v2)

1. **Language-homogeneous** slices — Rust and TS never mix (different lanes /
   profile packs).
2. **Size on PRODUCTION LOC** — exclude inline `#[cfg(test)]` modules, `.test.*`,
   `.css`. Verified: raw ≈ 2× production (e.g. `tuxmodem-phy` 1760 prod / 1358
   test; `winlink/modem` ~4.5k prod / ~4.6k test). Most v1 "oversized" flags
   dissolve once measured correctly.
3. **Slice by perf-relevance, not raw size.**
4. **No frontend render hot loop exists** — verified: zero `canvas` / `getContext`
   / `WebGL` and only one one-shot `requestAnimationFrame` (`help/ReadingPane.tsx:41`
   scroll-into-view) in all of `src/`. Tier E is warm/cold UI, never a render
   hot path.
5. **Complete coverage**; test/bin/probe harnesses explicitly out-of-scope.
6. **Live-RX/TX pipeline** is an analysis overlay, not a slice (the dominant cost
   compounds across the A-tier slice boundary).

## Corrected hot-path map (Round 1 + verified)

| Where | Evidence | Rank |
|-------|----------|------|
| OFDM per-symbol FFT **replanning** + per-symbol allocs | `tuxmodem-phy/src/ofdm_main/receiver.rs::demodulate_one_symbol` (fresh `FftPlanner` + replan every symbol, per-symbol `Vec`/`HashSet`/`Mapper`); `transmitter.rs::modulate_one_symbol` (same) | **Critical** |
| LDPC sum-product decode, per-iteration `Vec` allocs | `tuxmodem-fec/src/decode.rs::Decoder::decode` (msg-passing `incoming` vecs allocated inside the `max_iters` loop) | **Critical** |
| Frame-sync sliding cross-correlation | `tuxmodem-phy/src/sync/preamble.rs::scan` O(sig×template) | Major |
| Real-time audio callback / ring buffer | `tuxmodem-phy/src/audio_device.rs` (CPAL stream; sets the real-time deadline) — **omitted from v1** | Major (deadline) |
| Equalizer per-symbol alloc + interpolation | `tuxmodem-phy/src/ofdm_main/equalizer.rs::equalize` | Secondary |
| Search index/extract loops | `src-tauri/src/search/extractor.rs:281,371` + SQLite FTS | Warm |
| LZHUF compression | `winlink/lzhuf.rs::compress`/`insert_node` (real algo, fixed arrays, ~once per message → low frequency) | Modest |

**Refuted as hot (verified):** frontend render (no canvas/RAF); `winlink/modem`
ARQ (DSP runs in external `ardopcf`/VARA TNC process — this code is TCP plumbing
+ state machine); `src-tauri/src/grib` (composes a request *string*, no binary
decode); `tuxmodem-tx`/`tuxmodem-rx` crates (thin CLI drivers that call A1/A3);
`hf-channel-sim` per-sample loop *caches* its FftPlanner (unlike PHY).

---

## Slices

### MUST-DO — full 8-phase cycle (5 + 1 overlay)

| ID | Slice | Paths | Why full |
|----|-------|-------|----------|
| **M1** | OFDM PHY + real-time audio | entire `tuxmodem/crates/tuxmodem-phy/src` (ofdm_main/, sync/, equalizer, constellations, coded_modulation, robustness_floor/, subcarrier_snr, **audio_device.rs**, audio_io.rs, phy_api, modes) | Marquee FFT-replanning + per-symbol allocs; real-time deadline |
| **M2** | FEC (LDPC) | `tuxmodem-fec/src` | Densest compute in repo; per-iteration allocs |
| **M3** | ARDOP modem transport | `winlink/modem/ardop/*` (transport.rs 823, session.rs, listener.rs, data.rs) | Real-time-adjacent buffering + socket I/O throughput |
| **M4** | Search backend | `src-tauri/src/search/` | SQLite FTS + `extractor.rs` substring/line loops — the one warm backend |
| **M5** | HF channel simulator | `hf-channel-sim/src` | Offline per-sample DSP (`channel.rs::process_block`); lower urgency but genuine |
| **O1** | **Live RX/TX pipeline overlay** (analysis, NOT a code slice) | reconcile M1+M2: `audio_device`→`demodulate_one_symbol`→`Decoder::decode` per-symbol alloc budget vs audio frame deadline; TX mirror | Per-slice audit misses the compounding end-to-end cost; run after M1 & M2 |

### NICE-TO-HAVE — reduced-depth cycle (lanes: algorithmic-complexity, allocation, data-access, + concurrency where threads exist; skip framework-currency/payload/startup)

| ID | Slice | Paths |
|----|-------|-------|
| **R1** | Modem TX/RX CLI orchestration | `tuxmodem-tx/src` + `tuxmodem-rx/src` (thin) |
| **R2** | Rig / PTT control | `tux-rig-rts/src` + `tux-rig-cm108/src` (PTT, watchdog timing) |
| **R3** | Compression + B2F assembly | `winlink/lzhuf.rs`, `message.rs`, `compose.rs`, `proposal.rs`, `transfer.rs`, `wire.rs` |
| **R4** | VARA + shared modem | `winlink/modem/vara/*`, `modem/mod.rs`, `process.rs` |
| **R5** | AX.25 datalink | `winlink/ax25/` |
| **R6** | Telnet / P2P transport | `winlink/telnet.rs`, `telnet_listen.rs`, `telnet_p2p*.rs`, `relay_banner.rs` |
| **R7** | P2P listener gate | `winlink/listener/` (decide, packet_gate, allowed_stations, station_password, arms_record) |
| **R8** | B2F session driver | `winlink/session.rs`, `handshake.rs`, `credentials.rs`, `secure.rs`, `mod.rs` |
| **R9** | Warm frontend UI | `src/radio/` (1 Hz sparkline tick churn, `useSampleHistory`) + `src/mailbox/` (`MessageList.tsx` non-virtualized large-mailbox scaling, `messageSort.ts`) |

### DEFER — single batched COLD SWEEP (3 lanes only: complexity + allocation + data-access)

- **Rust cold:** `ui_commands.rs`; `winlink_backend.rs` + `modem_commands.rs` +
  `modem_status.rs`; `config.rs` + `native_mailbox.rs` + `user_folders.rs` +
  `session_log.rs`; `forms/` + `grib/` + `position/` + `catalog/`;
  `bootstrap.rs` + `lib.rs` + `main.rs` + `app_backend.rs` + `compose_window.rs`
  + `help_window.rs` + `tray.rs` + `consent_gate.rs` + `theme_state.rs`.
- **TS cold/warm:** `src/shell/` (warm exception: `markdownRender.ts` +
  `sanitizeHtml.ts` on large message/help bodies — flag in sweep); `src/search`,
  `compose`, `packet`, `connections`, `session`, `modem`; `src/wizard`, `forms`,
  `help`, `grib`, `catalog`; root `App.tsx` + `main.tsx` + `routing.ts`.

### OUT-OF-SCOPE (documented, not audited)

`test_helpers.rs`, `src-tauri/src/bin/`, `tuxmodem/crates/*/src/bin/`, all
`*/tests/`, `*/examples/`, every inline `#[cfg(test)]` module, `*.test.tsx`.
**Measurement gate:** no Criterion benches exist — recommend adding them to
`tuxmodem-fec` + `tuxmodem-phy` to *measure* the M1/M2 hot-path claims (Phase 4
of those cycles).

---

## Execution order

M1 → M2 → **O1** (pipeline overlay) → M3 → M4 → M5 → R1 → R2 → R3 → R4 → R5 →
R6 → R7 → R8 → R9 → cold sweep.

**Totals:** 5 full + 1 overlay + 9 reduced + 1 cold sweep = **16 audit units**
(was 22). Est. dispatch count ~80 (was ~150).

## Coverage ledger (every code dir lands once)

- Rust modem: phy→M1, fec→M2, tx+rx→R1, rig-rts+rig-cm108→R2. ✅
- hf-channel-sim→M5. ✅
- winlink: modem/ardop→M3, modem/vara+mod+process→R4, lzhuf+message+compose+proposal+transfer+wire→R3, ax25→R5, telnet*+relay_banner→R6, listener→R7, session+handshake+credentials+secure+mod→R8. ✅
- src-tauri other: search→M4; ui_commands/winlink_backend/modem_commands/modem_status/config/native_mailbox/user_folders/session_log/forms/grib/position/catalog/bootstrap/lib/main/app_backend/windows/tray/consent_gate/theme_state→cold sweep. ✅
- src frontend: radio+mailbox→R9; shell/search/compose/packet/connections/session/modem/wizard/forms/help/grib/catalog + root→cold sweep. ✅
