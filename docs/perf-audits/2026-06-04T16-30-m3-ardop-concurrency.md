# ARDOP modem driver — concurrency & parallelization audit (m3)

Agent: glade-knoll-shoal. Dimension: concurrency only. Problems only.
Scope: `src-tauri/src/winlink/modem/ardop/{transport,session,listener,data,arq_state,b2f}.rs`
plus the lock surface in `src-tauri/src/modem_status.rs` and the take/run path in
`src-tauri/src/modem_commands.rs`.

Stack: Rust 2021, blocking `std::net::TcpStream`, `std::thread`, `mpsc`, `Arc`/atomics. No async runtime.

## Architecture note (reviewed, NOT a finding)

The headline DEFEND risk for this dimension — a `Mutex` held across blocking socket
I/O — is **absent by construction**. The single `Mutex<ModemSessionInner>`
(`modem_status.rs:79`) is never held during a blocking exchange:

- `modem_ardop_b2f_exchange` (`modem_commands.rs:692`) calls `take_transport()` to
  pull the transport OUT of the Mutex, runs the minutes-long B2F exchange and
  `disconnect()` with no lock held, then `reset_to_stopped()` re-acquires for a
  single atomic state flip (`modem_commands.rs:700-711`). Documented at
  `modem_commands.rs:650-654`.
- The broadcaster's `tick_and_snapshot` (`modem_status.rs:165-176`) holds the lock
  only across `drain_status_events`, which is hard-bounded non-blocking
  (`recv_event(Duration::ZERO)`, capped at `MAX_DRAIN_EVENTS_PER_TICK = 64`,
  `transport.rs:675-696`).
- The split-borrow at `transport.rs:666-679` (cmd `&mut` + `apply_event_to_accumulators_inline`
  on disjoint fields, SAFETY-commented) was reviewed and is sound — explicitly NOT
  flagged per audit scope.

This is the correct pattern for a no-async, blocking-socket design. Findings below
are second-order.

---

### [MINOR] Broadcaster status path goes dark for the entire B2F exchange

Location: `modem_commands.rs:692-714` (`take_transport`) vs `modem_status.rs:171`
(`tick_and_snapshot`'s `if let Some(transport)`).

Problem: Because `modem_ardop_b2f_exchange` *removes* the transport from the session
for the full duration of the exchange (seconds-to-minutes), every broadcaster tick
during that window observes `inner.transport == None` and skips
`drain_status_events` entirely. The cmd-socket reader thread keeps enqueueing
events (BUFFER depth, NEWSTATE, PTT, Busy) into the `mpsc` channel the whole time,
but nobody drains it: the UI's live meters (throughput_bps, bytes_tx, PTT/Busy
flags) freeze at their pre-exchange values for precisely the period the operator
most wants to watch them. The frozen status is a stale cached snapshot, not a
"transferring" indication. This is the same gap the doc-comment defers as
"Throughput-stats integration with the modem status broadcaster"
(`modem_commands.rs:661`).

Secondary: the channel accumulates unbounded events during the dark window (see
next finding) and they are all discarded by the post-exchange `reset_to_stopped`
drop — so the bytes_tx/throughput accounting the accumulators were built for
(`transport.rs:337-419`) never runs for the bytes sent inside the exchange.

Impact: reachability = every send/receive; frequency = once per exchange but spans
the whole transfer; per-occurrence = no live telemetry + lost accounting. UI-stall
class (no thread stall, but a user-visible freeze of the live-meter contract).

Confidence: High (control flow is explicit; transport is provably absent from the
Mutex for the exchange duration). Effort: Medium — requires either a shared
status-drain handle that survives `take_transport`, or routing the exchange's
own progress into the broadcaster.

Verification: connect, start a multi-message B2F exchange, observe `modem:status`
events emitted on the Tauri channel during the transfer; confirm throughput_bps /
bytes_tx do not advance and arq_flags.tx does not track PTT until the exchange ends.

---

### [MINOR] Unbounded `mpsc::channel` on the cmd-socket reader

Location: `session.rs:74` (`mpsc::channel::<Command>()`), producer at
`session.rs:122` (`tx.send(cmd)`).

Problem: The cmd-socket reader thread is a producer with no back-pressure into an
unbounded channel. Under normal HF cadence this is fine. But whenever the consumer
stalls or is absent — most concretely during the B2F dark window above, where the
transport (and thus the only `recv_event` caller for status) is removed for
minutes while ardopcf may emit a BUFFER event per frame — the queue grows without
bound, capped only by how chatty ardopcf is. ardopcf BUFFER/STATUS emission on a
busy link can be many events/sec; over a multi-minute transfer that is thousands of
small `Command` allocations retained until the post-exchange drop. No starvation of
the protocol path (the B2F exchange reads the *data* socket, not this channel), but
it is an unbounded buffer keyed to consumer absence rather than to producer rate.

Impact: reachability = every exchange + any prolonged consumer absence; frequency =
proportional to ardopcf chattiness × exchange duration; per-occurrence = bounded
memory growth, not a stall. Low at HF rates; called out because "unbounded channel"
is explicitly in this dimension's scope and the consumer-absence pattern makes it
reachable rather than theoretical.

Confidence: High (channel is unbounded; consumer is provably absent during the
exchange). Effort: Low — `mpsc::sync_channel(N)` with a small bound, OR have the
reader drop non-essential status events (BUFFER/STATUS) when the queue is deep.
Note: a bounded channel changes `tx.send` to block the reader thread on a full
queue, which would in turn block the reader's blocking `read_until` cadence — verify
that interaction before adopting; dropping low-value events is the safer lever.

---

### [MINOR] `with_managed_modem` bind-wait is a fixed-interval sleep-poll

Location: `transport.rs:178-195` (loop with `std::thread::sleep(BIND_WAIT_POLL_INTERVAL)`,
100 ms, up to 5 s).

Problem: Spawn-time port-readiness detection polls `TcpListener::bind` every 100 ms
for up to 5 s. This is a sleep-poll, but it is bounded, runs once per modem spawn
(not per operation), is off the hot path, and the alternative (inotify / connect-retry
with exponential backoff) buys nothing perceptible at a one-shot 100 ms granularity.
Recorded for completeness because sleep-poll loops are in scope; it is not worth
changing.

Impact: reachability = once per managed-modem spawn; frequency = once; per-occurrence
= up to one extra 100 ms sleep of latency on a fast bind. Negligible.

Confidence: High. Effort: Low (but not recommended — the current form is appropriate
for a one-shot startup gate).

---

## Suspected Bugs

None. (The B2F dark-window telemetry gap is a perf/UX contract gap, reported above
as a MINOR finding, not a correctness bug — the protocol path is unaffected.)
