# R8 — Winlink B2F session driver: data-access & I/O audit

Agent: glade-knoll-shoal. Dimension: data access & I/O (protocol-over-stream).
Scope: `src-tauri/src/winlink/{session.rs,handshake.rs,credentials.rs,secure.rs,mod.rs}`.
Frequency prior: per-connect handshake + a handful of message turns; modest counts. Impact is calibrated LOW in absolute terms but expressed as **round-trips on a high-latency HF link**, where each separately-segmented write can cost a full link round-trip.

Note on the writer: the injected `writer: Write` is, in production, the raw `WriteHalf` socket (or a `WireTap` that just passes through) — see `telnet.rs:91-98,161-170`. There is **no `BufWriter`** anywhere in the write path, and this driver calls `writer.flush()` **zero times** (confirmed by grep). So each `write_all` is one unbuffered `write()` syscall → potentially one TCP segment.

---

### [MAJOR] Proposal batch is sent as ~12 separate unbuffered writes instead of one coalesced write

Location: `session.rs:347-356` (`send_turn`), via `write_bytes` → `telnet.rs:91-94` (unbuffered `WriteHalf::write`).

Problem: each proposal line is emitted as two distinct `write_all` calls — the line, then a separate `b"\r"` — and the batch checksum line is likewise two more. For a full 5-proposal batch (`MAX_BATCH = 5`) that is `5 × 2 + 2 = 12` separate `write()` syscalls on an unbuffered socket. With Nagle disabled (typical for an interactive protocol) each becomes its own tiny TCP segment; with Nagle on, the un-flushed driver instead depends on the OS to coalesce, which is non-deterministic and can stall on delayed-ACK. Either way the control channel is fragmented far more than the protocol requires — the entire batch (proposals + checksum) is one logical unit the remote does not act on until the checksum line arrives.

Impact: write amplification on the control channel — up to ~12 segments where 1 suffices per send turn. On a high-latency HF/packet link the segmentation wastes link MTU and invites per-segment ACK latency; the round-trip cost is bounded by turns (handful per connect) but multiplies the already-expensive HF RTT. LOW absolute frequency, HF-latency-amplified.

Fix sketch: build the whole batch (all proposal lines + `\r` separators + checksum line) into one `Vec<u8>`/`String` and issue a single `write_all`, then one explicit `flush()` to mark the turn boundary. The `frame_block` body (`session.rs:384`) is already a single `write_all` — only the line-oriented control writes are fragmented.

Confidence: HIGH (code path is explicit). Effort: LOW.
Verification: count `write()` calls per send turn against a capturing mock `Write` (push each `write` len into a Vec); assert one write per turn after the fix. Or `strace -e write` / tcpdump segment count against a loopback CMS mock.

---

### [MINOR] No explicit `flush()` at turn boundaries — correctness-adjacent latency risk if a buffered writer is ever injected

Location: entire `session.rs` write path (`write_bytes`, `session.rs:495-499`); no `flush` call exists.

Problem: the driver works today only because the injected writer happens to be unbuffered (raw socket). The `Write` contract makes no such guarantee — if a future transport injects a `BufWriter` (a natural fix for the MAJOR above, or an ARDOP/VARA framing layer), the absence of any `writer.flush()` means a completed turn's bytes can sit in the buffer indefinitely, deadlocking the strictly-alternating turn protocol (we wait to read the remote's reply that the remote never sees because our request never left the buffer). This is the flip side of the over-segmentation finding: the right shape is "buffer the turn, flush once at the turn boundary," and neither half is present.

Impact: not a live perf defect at the current unbuffered transport, but it forecloses the correct buffered-write fix and is a latatent hang on any buffered injection. On HF the symptom would present as a hung session, not just slowness.

Confidence: HIGH (structural). Effort: LOW.
Verification: inject a `BufWriter`-wrapped sink in a test; assert the remote-visible bytes after a send turn are non-empty before the read. (Record as design risk, not a wall-clock measurement.)

---

### [MINOR] Per-line UTF-8 lossy allocation on the read path (`read_until` → `from_utf8_lossy` → `String`) for opaque ASCII control lines

Location: `session.rs:501-503` (`read_line`) → `wire.rs:12-22`.

Problem: `wire::read_line` allocates a fresh `Vec` per line, runs `String::from_utf8_lossy`, then `clean_line(...).to_string()` — a second allocation. B2F control lines are short opaque ASCII (`FS +`, `FA ...`, `F> 0a`); the UTF-8 validation + double allocation per line is unnecessary. This is the profile pack's "`String` I/O incurs UTF-8 validation overhead; for ASCII/opaque-byte workloads use `BufRead::read_until`" signal — `read_until` is already used, but the result is needlessly funneled through `String`.

Impact: trivial allocation/CPU cost, NOT round-trip-bound (it is read-side, post-arrival). Listed only because it is squarely the data-access lane; far below the write-fragmentation finding in importance. The per-message compressed-block read/decompress is in `transfer.rs`/`lzhuf` (out of scope, R3), correctly not counted here.

Confidence: HIGH. Effort: LOW (but low value — the line count per session is small).
Verification: not worth a benchmark at this frequency; a `dhat` allocation count would show ~2 allocs/line if pursued.

---

## Suspected Bugs

None. (The flush-absence in the MINOR above is a latent-hang risk only under a hypothetical future buffered writer, not a defect against the current injected unbuffered socket; recorded there as a design risk, not a correctness bug in shipped behavior.)
