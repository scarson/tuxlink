# R4 — VARA modem driver: algorithmic-complexity audit

Agent: glade-knoll-shoal
Date: 2026-06-04T19-30
Scope: `src-tauri/src/winlink/modem/vara/*.rs` + `modem/process.rs`
Dimension: algorithmic complexity (O(n²) reassembly, per-byte drains, repeated
state scans, status-loop recompute). Load prior: HF rates (LOW).

## No significant findings

No O(n²) buffer reassembly, per-byte hot-path drains, or repeated-scan growth
in the VARA driver under the algorithmic-complexity lens.

What was examined and why each is benign at HF rates:

- **`wire.rs` `read_line`** (`wire.rs:31-50`): `BufReader::read_until(CR)` does
  not re-scan a growing buffer — it is linear in the single line consumed, with
  a fresh `Vec` per call. Classic O(n²) "re-scan accumulated buffer every read"
  is absent; there is no persistent reassembly buffer.
- **`command.rs::parse`** (`command.rs:188-262`): `splitn(2,' ')` +
  `split_whitespace` are O(line length), bounded by one short cmd line. No
  scan-of-state.
- **`listener.rs` drain/serve loops** (`set_listen` `:243-249`,
  `serve_inbound_one` `:335-401`): each iteration consumes one line via `recv()`;
  no accumulation re-scanned per pass. Bounded by wall-clock budgets, not buffer
  size.
- **`process.rs`** poll loops (`stop` `:153-170`, `confirm_audio_device_released`
  `:204-231`, `Drop` `:276-289`): fixed-interval `try_wait`/`lsof` polls; cost is
  O(elapsed/interval), independent of any data structure. `lsof` stdout
  whitespace check (`:220`) is single linear pass per poll.

Status loop (`vara_status`/`snapshot`, `commands.rs:143-148`) clones a tiny
fixed-size `VaraStatus` — no recompute over collections.

## Suspected Bugs

None (algorithmic-complexity lens).
