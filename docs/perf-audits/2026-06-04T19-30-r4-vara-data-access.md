# R4 — VARA modem driver: data access & I/O / concurrency audit

Agent: glade-knoll-shoal
Date: 2026-06-04T19:30Z
Dimension: data access & I/O / concurrency (single lane)
Scope: `src-tauri/src/winlink/modem/vara/{command,commands,listener,transport,wire}.rs` + `modem/process.rs`
Prior: `.claude/skills/performance-audit/profile-packs/rust.md` (data-access + concurrency lanes)
Load: ARQ at HF rates (LOW) + status polling. External VARA TNC owns DSP.

---

### [MINOR] B2F data-socket WRITE half is an unbuffered `TcpStream` (theme repeats)

**Location:** `winlink_backend.rs:2043-2049` (`run_vara_b2f_answer`) — the write half handed to
`session::run_exchange_with_role`; the read half at `2050-2058` IS `BufReader`-wrapped, the write
half is a bare `TcpStream::try_clone()`. The VARA data socket originates at
`transport.rs:58/135` (`data_stream()` returns `&mut TcpStream`, no buffering layer).

**Problem:** This is the M3-3 / R8-1 / R6-1 theme recurring. `TcpStream` is unbuffered; every
`write_all` the B2F engine issues for a small token (proposal line, ACK, offset, FF/FQ control
words) becomes its own `send()` syscall and, absent coalescing, its own TCP segment. The asymmetry
is notable: the reader is buffered, the writer is not. Whether the cost actually materializes
depends on `run_exchange_with_role`'s write granularity (out of this scope's source) — if it emits
many small control tokens per turn, each is a syscall + segment.

**Impact:** LOW. reach = inbound B2F answer path only; freq = per-token within a turn-based session;
cost = extra syscalls + potential Nagle/delayed-ACK interaction on tiny segments. At HF ARQ rates
the channel, not the socket, is the bottleneck — payload bulk is dominated by VARA's own framing.
Real but small; the win is syscall-count + tidy segmentation, not throughput.

**Confidence:** Medium that the writer is unbuffered (verified: bare `try_clone`, no `BufWriter`).
Low that it costs measurably given HF rates and unknown write granularity upstream.

**Effort:** Low — wrap the writer clone in `BufWriter` and ensure `run_exchange_with_role` flushes
at turn boundaries (it must already, or the reader half on the peer would stall). Mirror however
ARDOP's b2f handles this.

**Verification:** `strace -e trace=sendto -f` a mock B2F answer session; count send() calls per
turn. Or read `session::run_exchange_with_role`'s write sites and check token sizes. If it already
buffers internally / writes whole turns, this is a non-issue — confirm before changing.

---

### Notes on theme checks that came back clean

- **cmd-socket writes (`wire.rs:54-58` `write_line`):** `write_all(bytes) + write_all(&[CR]) +
  flush()`. Two tiny writes per command (body, then the single CR byte) plus a flush. Technically
  unbuffered, but cmd-socket traffic is setters + DISCONNECT — a handful of lines per session, not
  a hot path. The split CR write is cosmetically the unbuffered-write smell but at this frequency
  it is genuinely immaterial. Not flagged.
- **Lock held across blocking socket I/O:** `commands.rs:202-214` (`send_listen_on`) and
  `vara_start_session_inner` (`commands.rs:377-435`) hold the `VaraSession` mutex across
  `transport.send(...)` / `VaraTransport::connect(...)`. The send is one cmd-socket write (~ms on
  localhost); the connect is bounded by `connect_timeout` (5s). These are documented as
  intentional serialization of the start path (`commands.rs:374-376`) and contended only by UI
  double-presses — not a throughput concern. `vara_status`/`snapshot` deliberately read the
  denormalized snapshot without touching the transport (`commands.rs:24, 142-148`), so a UI poll
  never blocks behind an in-flight start. Correct design; not flagged.
- **Per-byte reads:** none. `LineReader` uses `BufReader::read_until(CR, ...)` (`wire.rs:33`) — one
  buffered scan per line, not per-byte. Good.
- **sleep-poll loops:** `serve_inbound_one` (`listener.rs:335-401`) and `set_listen` drain
  (`listener.rs:243-249`) loop on `transport.recv()`, but recv blocks on the socket up to
  `read_timeout` (default 2s) — the read timeout IS the poll cadence, no busy-spin / no
  `sleep` in the loop. CPU is idle while blocked. `process.rs` poll loops (`stop` 20ms,
  `confirm_audio_device_released` 50ms) are bounded lifecycle waits, not data-path. Not flagged.
- **`recv()` allocates a fresh `Vec` per line (`wire.rs:32`):** one alloc per cmd-socket line.
  cmd-socket line volume is trivial (status events at human/keep-alive cadence). Not a hot path;
  not flagged.

---

## Suspected Bugs

None. (Out-of-lane observation, not chased: `commands.rs:416/431` swallow `transport.send` errors
for MYCALL/BW via `let _ =` — a correctness/observability question for a different lane, not I/O
performance.)
