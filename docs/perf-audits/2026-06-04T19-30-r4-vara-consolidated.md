---
run_schema_version: 1
run_id: 2026-06-04T19-30-r4-vara
date: 2026-06-04T19:30:00Z
scope: "R4 — winlink/modem/vara + shared (VARA modem driver, external-TNC)"
methodology: { skill: performance-audit (REDUCED), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "TcpStream to external VARA TNC" }]
lanes_run: [algorithmic, memory, data-access]
finding_counts: { by_impact: { critical: 0, major: 0, minor: 2 }, by_lane: { algorithmic: 0, memory: 1, data-access: 1 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 2, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R4: winlink/modem/vara

**All-minor — external-TNC class, like ardop.** algorithmic: **No significant
findings** (line-oriented framing, no O(n²) reassembly). Locks across socket I/O
are intentional ms-scale start-path serialization (`commands.rs:202-214`),
status reads a denormalized snapshot to avoid blocking — correct.

## Minor findings
- **R4-1** (data-access) **B2F data-socket write half is unbuffered** (`winlink_backend.rs:2043-2049`; read half gets a `BufReader`) — **the 4th instance of the M3-3/R8-1/R6-1 transport-write theme** (ardop/session/telnet/vara). Small tokens → per-write syscalls/segments. LOW (HF channel is the bottleneck). Fix: `BufWriter` + turn-flush. Fingerprint `data-access:winlink_backend.rs:vara-write-half:unbuffered`.
- **R4-2** (memory) `wire.rs:32 LineReader::read_line` allocates a fresh `Vec<u8>` per call (per event + idle-poll tick) instead of a reused `buf.clear()` scratch field. Negligible at HF rates. Fingerprint `memory:vara/wire.rs:read_line:per-call-alloc`.

## Cleared
cmd-socket setter writes (handful/session), no per-byte reads (`read_until`), no busy-spin (recv `read_timeout` is the cadence), `process.rs` `lsof` buffer is teardown-path. No suspected bugs.
