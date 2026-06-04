---
run_schema_version: 1
run_id: 2026-06-04T19-00-r6-telnet
date: 2026-06-04T19:00:00Z
scope: "R6 — winlink telnet transport (telnet.rs, telnet_listen.rs, telnet_p2p.rs, telnet_p2p_login.rs, relay_banner.rs)"
methodology: { skill: performance-audit (REDUCED depth), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "TcpStream + threads" }]
lanes_run: [algorithmic, memory, data-access]
lanes_skipped: { concurrency: "covered within data-access", idiom-currency: "std-only", cost-map: "reduced", payload-startup: "n/a", dynamic: "needs a CMS/peer" }
finding_counts: { by_impact: { critical: 0, major: 0, minor: 4 }, by_lane: { algorithmic: 0, memory: 2, data-access: 2 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 4, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R6: winlink telnet transport

**Mostly-clean cold glue — all-minor.** algorithmic: **No significant findings**
(no O(n²) reassembly; per-byte reads are `BufReader`-backed → memory not
syscalls). The one item worth carrying forward is the recurring transport theme:

## Minor findings
- **R6-1** **Unbuffered write half** (the M3-3/R8-1 theme on a 3rd transport): the write half handed to `session::run_exchange` is the raw stream with no `BufWriter`, while the read half IS buffered (`telnet.rs:91-98,203,211`; `telnet_p2p.rs:81-88,153`; `telnet_listen.rs:305,433`). If the session writes line-by-line (it does — R8-1), each line is one `write()` syscall + one TLS record on the CMS TLS path. **Fix:** `BufWriter` + flush at turn boundaries (same fix as R8-1/M3-3). Confidence MEDIUM (missing-BufWriter certain in-scope; per-line write volume is in R8). Fingerprint `data-access:telnet.rs:write-half:unbuffered`.
- **R6-2** `ReadHalf::read` holds the connection `Mutex` across a blocking socket read up to 60 s (`telnet.rs:85-89`). By design / turn-based, no contender today → latent, not live. Fingerprint `data-access:telnet.rs:read:lock-across-blocking`.
- **R6-3** `read_line_with_eol` uses `Vec::new()` (no pre-size) vs sibling's `with_capacity(64)` (`telnet_p2p_login.rs:45` vs `telnet_listen.rs:546`); trivial. Fingerprint `memory:telnet_p2p_login.rs:read_line:no-capacity`.
- **R6-4** single-byte `reader.read()` loops (`telnet_listen.rs:548`, `telnet_p2p_login.rs:47`) — per-byte call overhead but BufReader-backed + justified (`\n`/NUL-skip semantics preclude `read_until`); not actionable.

## Cleared (anti-padding)
`relay_banner.rs` zero-alloc (`&str` matching); per-session `format!`/`clone` fire once/session; connect resolves a small address list once. No suspected bugs.

## Note
**R6-1 makes the transport-write-buffering theme systemic** across ardop (M3-3),
session (R8-1), and telnet (R6-1) — the winlink+modem transports all write tiny
protocol tokens without coalescing. One shared `BufWriter`+turn-flush convention
(applied at the `run_exchange` writer boundary) would address all three.
