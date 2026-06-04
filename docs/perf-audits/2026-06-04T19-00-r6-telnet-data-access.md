# R6 Perf Audit — Telnet transport, data-access & I/O lane

Agent: glade-knoll-shoal. READ-ONLY. Dimension: data access & I/O only.
Scope: `src-tauri/src/winlink/{telnet.rs, telnet_listen.rs, telnet_p2p.rs, telnet_p2p_login.rs, relay_banner.rs}`.
Load calibration: LOW (one session per connect; turn-based B2F; high-ish RTT link).
Prior: profile-packs/rust.md §"Data access & I/O".

---

### [MINOR] Write half is unbuffered — every protocol line is its own syscall/TLS-record

Location:
- `telnet.rs:91-98` (`WriteHalf`), `203` (`writer = WireTap::new(WriteHalf(shared), …)`), `211` (handed straight to `run_exchange`).
- `telnet_p2p.rs:81-88` (`WriteHalf`), `153,156` (`writer = write_half`, handed to `run_exchange_with_role`).
- `telnet_listen.rs:305` (`let mut writer = writer_stream;` — bare `TcpStream`), `433` (handed to `run_b2f_answerer`).

Problem: the write half presented to the exchange driver is the raw stream
(`TcpStream`, or TLS, or `WireTap`→`WriteHalf`) with **no `BufWriter`**. The read
half is buffered (`BufReader`, `telnet.rs:202`, `telnet_p2p.rs:155,171`,
`telnet_listen.rs:306`) but the write half is not. This is the same theme M3/R8
flagged in ardop/session: if `session::run_exchange` emits the B2F dialogue and
the bulk message body line-by-line (or proposal-by-proposal) via `write_all`,
each call is one `write()` syscall — and under `Transport::Tls` (the CMS default,
`telnet.rs:67,241-248`) one TLS record with its own framing/MAC/encrypt cost. On
a high-RTT link, many tiny segments also interact poorly with Nagle. The fix is a
`BufWriter` around the write half with an explicit `flush()` at each turn boundary
(the protocol is turn-based, so flush points are well-defined and correctness is
unaffected).

Impact: LOW. One session per connect; the message volume per session is small
(B2F handshake + a handful of messages). Syscall/TLS-record count scales with line
count, not bytes, so the win is proportional to how chatty `run_exchange` is —
material only if a large message body is written in many small chunks. RTT-aware:
extra small segments add round-trips on a slow link.

Confidence: MEDIUM — the missing-`BufWriter` fact is in-scope and certain; the
per-line write *volume* lives in `session.rs` (out of scope, asserted by M3/R8).
Effort: LOW (wrap write half; add turn-boundary flush).
Verification: `strace -e write -f` a loopback CMS exchange, count `write()`s
before/after; or instrument `WireTap::write` call count. Confirm against a
release build with a realistic multi-message Outbox.

---

### [MINOR] `telnet_login` lock-per-byte: the BufReader read half holds the connection Mutex across each blocking refill

Location: `telnet.rs:85-89` (`ReadHalf::read` takes `self.0.lock()`), `202`
(`BufReader::new(WireTap::new(ReadHalf(shared.clone()), …))`), `207`
(`telnet_login` drives it via `wire::read_line` → `read_until`).

Problem: every `BufReader` refill calls `ReadHalf::read`, which acquires the
`Arc<Mutex<Box<dyn ReadWrite>>>` and performs a **blocking socket read while
holding the lock** (rust.md §concurrency: "lock held across blocking socket I/O").
The doc comment at `telnet.rs:76-80` argues this is contention-free because the
exchange is strictly turn-based — which is true for *correctness*, but it means
the lock is, by design, held for the full read/write blocking window (up to
`TIMEOUT` = 60s, `telnet.rs:29`). No second thread contends today, so there is no
measured stall; this is a latent cost, not a live one. Listed for completeness
since it is the literal "lock across blocking I/O" pattern, and an abort path
(`register_socket`, `telnet.rs:235`) shuts the fd down out-of-band specifically
because the lock can't be grabbed mid-read.

Impact: LOW (no contender thread; turn-based). Not a throughput issue at current
load.
Confidence: HIGH (code is explicit).
Effort: N/A — by-design; flagged, not recommended for change at this load.
Verification: none warranted unless a concurrent reader is added.

---

### Non-findings (examined, no I/O issue)

- **Per-byte read loops** in `read_cr_terminated_line` (`telnet_listen.rs:543-573`)
  and `read_line_with_eol` (`telnet_p2p_login.rs:44-61`) call `reader.read(&mut
  byte)` one byte at a time — but the reader is a `BufReader`
  (`telnet_listen.rs:306`, `telnet_p2p.rs:155`), so each call hits the in-memory
  buffer, NOT a syscall. The 4 KiB cap (`telnet_listen.rs:564`) bounds worst case.
  No per-byte *syscall*; not a finding. (The bare-`read` over a BufReader is a
  deliberate choice to avoid `fill_buf` peek-deadlock on a live socket —
  `telnet_p2p_login.rs:38-43`, `telnet_p2p.rs:192-201`.)
- **Prompt writes** in `telnet_listen.rs` (`write_all` + `flush`, e.g. 309-313,
  371-375) are correctly explicit-flushed and are 1–2 per session — negligible.
- **`PushbackReader`** (`telnet_p2p.rs:91-105`) uses `Vec::drain(..n)` per read;
  pushback is one short B2F line consumed once, then `inner` takes over. Not hot.
- **`connect_with_deadline` / resolve** (`telnet.rs:259-339`) — connect-sweep
  logic, not steady-state I/O; bounded and correct.
- **`relay_banner.rs`** — pure in-memory `starts_with`/`contains` string
  classification, no I/O. Nothing in this lane.
- **`WireTap::observe`** (`telnet.rs:120-150`) — per-byte loop over an in-memory
  `&[u8]` already returned from a read; `flush` allocates a `format!` per logged
  line, but that's the wire-log lane (payload/alloc), not data-access I/O.

## Suspected Bugs

None.
