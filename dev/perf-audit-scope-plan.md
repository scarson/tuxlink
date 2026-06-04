# Performance-Audit Whole-Repo Scope Partition — DRAFT v1

> Purpose: slice the tuxlink repo into bounded scope slices, each suitable for
> ONE `performance-audit-cycle` run, collectively covering the whole repo.
> This draft is the subject of a mandatory **two-round adversarial review**
> before any audit run executes. Hot-path claims marked **[HYP]** are
> hypotheses for the review to confirm or refute against the actual code.

## Slicing principles (v1)

1. **Language-homogeneous slices.** Never mix Rust and TS/React in one slice —
   the audit lanes and profile packs differ (rust/* vs javascript-typescript/*).
2. **Coherent subsystem + shared data flow** per slice.
3. **Tractable size band** ~1k–4k LOC. Split anything materially larger so lanes
   stay precise.
4. **Hot-path-aware grouping.** Keep genuine compute/render hot paths together;
   isolate cold CRUD/glue so Impact (reachability × frequency × per-occurrence
   cost) calibrates honestly.
5. **Complete coverage.** Every code directory lands in exactly one slice.

## Repo size baseline (code LOC, approx)

- Rust modem `tuxmodem/crates/`: phy 3.1k, tx 1.3k, rx 1.0k, fec 1.6k, rig-rts 0.85k, rig-cm108 0.71k
- `hf-channel-sim/`: 1.6k
- Rust backend `src-tauri/src/`: ~50k (winlink 21.9k, ui_commands 6.1k, winlink_backend 3.3k, modem_commands 1.8k, native_mailbox 1.1k, config 1.2k, search 2.6k, forms 3.0k, + lifecycle/misc)
- TS/React `src/`: ~33k (radio 8.4k, shell 7.6k, mailbox 5.6k, wizard 2.5k, help 1.9k, search 1.4k, compose 1.3k, forms 1.2k, + smaller)

Total ≈ 96k LOC of code.

---

## Proposed slices

### Tier A — Rust real-time DSP / modem signal path (highest perf value) **[HYP]**

| ID | Slice | Paths | ~LOC |
|----|-------|-------|------|
| A1 | OFDM PHY core | `tuxmodem/crates/tuxmodem-phy/src` (ofdm_main, sync, equalizer, constellations, coded_modulation, robustness_floor, subcarrier_snr) | 3.1k |
| A2 | Modem TX/RX pipelines | `tuxmodem-tx/src` + `tuxmodem-rx/src` | 2.3k |
| A3 | FEC encode/decode | `tuxmodem-fec/src` | 1.6k |
| A4 | HF channel simulator | `hf-channel-sim/src` | 1.6k |

### Tier B — Rust rig / PTT control (real-time but I/O-bound)

| ID | Slice | Paths | ~LOC |
|----|-------|-------|------|
| B1 | PTT / rig control + watchdog | `tux-rig-rts/src` + `tux-rig-cm108/src` | 1.6k |

### Tier C — Rust Winlink protocol / transport (data-transfer hot-ish) **[HYP]**

| ID | Slice | Paths | ~LOC |
|----|-------|-------|------|
| C1 | Compression + B2F message assembly | `winlink/lzhuf.rs`, `message.rs`, `compose.rs`, `proposal.rs`, `transfer.rs`, `wire.rs` | 2.4k |
| C2 | Modem-mode ARQ session | `winlink/modem/` | 9.1k → **SPLIT?** |
| C3 | AX.25 packet | `winlink/ax25/` | 3.3k |
| C4 | Telnet / P2P transport | `winlink/telnet.rs`, `telnet_listen.rs`, `telnet_p2p*.rs`, `listener/`, `relay_banner.rs` | 6.0k → **SPLIT?** |
| C5 | Session / handshake / credentials | `winlink/session.rs`, `handshake.rs`, `credentials.rs`, `secure.rs`, `mod.rs` | 2.5k |

### Tier D — Rust app backend / IPC / storage (mostly cold glue) **[HYP: low perf value]**

| ID | Slice | Paths | ~LOC |
|----|-------|-------|------|
| D1 | IPC command surface | `ui_commands.rs` | 6.1k → **SPLIT?** |
| D2 | Backend orchestration + modem control | `winlink_backend.rs`, `modem_commands.rs`, `modem_status.rs` | 5.7k |
| D3 | Storage / config | `native_mailbox.rs`, `config.rs`, `user_folders.rs`, `session_log.rs` | 2.8k |
| D4 | Feature backends | `search/`, `catalog/`, `forms/`, `grib/`, `position/` | 7.5k → **SPLIT?** |
| D5 | App lifecycle / windows | `bootstrap.rs`, `wizard.rs`, `lib.rs`, `app_backend.rs`, `compose_window.rs`, `help_window.rs`, `tray.rs`, `consent_gate.rs`, `theme_state.rs`, `bin/` | 2.9k |

### Tier E — TS/React frontend (render hot paths + cold UI) **[HYP]**

| ID | Slice | Paths | ~LOC |
|----|-------|-------|------|
| E1 | Radio UI / waterfall-spectrum | `src/radio/` | 8.4k → **SPLIT?** |
| E2 | Mailbox list / reader | `src/mailbox/` | 5.6k |
| E3 | App shell / layout / state | `src/shell/` | 7.6k → **SPLIT?** |
| E4 | Smaller feature UIs (active) | `src/search`, `compose`, `packet`, `connections`, `session`, `modem` | 3.4k |
| E5 | Setup / forms / help UIs (cold) | `src/wizard`, `forms`, `help`, `grib`, `catalog` | 6.8k |

**Slice count: 22.** Open issues flagged inline: 5 slices likely exceed the size
band (C2, C4, D1, D4, E1, E3) and several Tier-D/E slices are suspected cold
glue where a full 8-phase cycle may be low-yield.

---

## Proposed execution order

A1 → A2 → A3 → A4 (DSP first; highest signal) → C1 → C2 (Winlink data path) →
E1 → E2 (render hot paths) → B1 → C3 → C4 → C5 → D2 → D1 → D3 → D4 → E3 → E4 →
D5 → E5 (cold glue last).

## Known tensions for the adversarial review to resolve

1. **Volume.** 22 full cycles ≈ 150+ subagent dispatches. Is whole-repo
   coverage at full-cycle depth the right call, or should cold tiers get a
   single lighter pass / explicit deferral?
2. **Oversized slices** (C2, C4, D1, D4, E1, E3) — split how, along what seam?
3. **Cold-glue value** — D3/D4/D5/E5 may yield ~nothing; include anyway for
   completeness, or document-and-defer?
4. **Cross-slice hot paths** — e.g. the live RX audio→PHY→FEC→ARQ→UI pipeline
   spans A1/A2/A3/C2/D2/E1. Does per-slice auditing miss end-to-end pipeline
   cost? Should there be one cross-cutting "live-receive pipeline" slice?
