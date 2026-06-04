# R3 Perf Audit — Data Access & I/O / Byte-Stream Handling

Dimension: byte-stream handling on the Winlink B2F block transfer path.
Scope: `src-tauri/src/winlink/{transfer.rs,wire.rs,message.rs,compose.rs,proposal.rs,lzhuf.rs}`.
Frequency (W0): `read_block` once per received message; `frame_block`+write once per sent message; modest counts; HF link high-latency/low-rate.
Read of actual source only. Problems only.

---

### [MINOR] `read_block` requires only `Read`, making per-byte buffering an unenforced caller contract

Location: `transfer.rs:78` (signature `read_block<R: Read>`), per-byte sites `transfer.rs:79,83,93,96,100-104,107` via `read_u8` (`transfer.rs:124-130`) and `read_until_nul` (`transfer.rs:132-141`).

Problem: `read_block` consumes the stream strictly one byte at a time — `read_u8` calls `read_exact` on a `[0u8;1]` buffer for the SOH, header-len, every title/offset byte, every STX, every length byte, **every single data byte** (the `for _ in 0..len` loop at `100-104`), and the EOT + checksum. For a typical compressed body this is one `read_exact` call per body byte plus framing overhead. The bound is `R: Read`, NOT `R: BufRead`. On a raw `TcpStream`/socket each `read_exact(1)` is a syscall, so an unbuffered reader would produce a syscall-per-body-byte storm (thousands of `read()` syscalls per message).

Mitigating fact (verified): every production call site passes a `BufReader`. `session.rs:467` calls `read_block(reader)` where `reader` is bound `R: BufRead` in `run_exchange`/`run_exchange_with_role`/`send_turn` (`session.rs:227,246,326,402`), and the concrete readers are `BufReader::new(...)` at `winlink_backend.rs:1297,2050`, `telnet.rs:202`, `b2f.rs:105`, `telnet_p2p.rs:155,171`. So in practice each `read_exact(1)` is a memcpy from the `BufReader`'s 8 KiB internal buffer, not a syscall — cheap-ish, ~1 refill syscall per 8 KiB.

Impact: syscalls/message on the as-written path = O(1) refills (good, because callers buffer). But the type signature does not encode this requirement: a future caller passing a bare `TcpStream`/`Read` would silently regress to O(body-length) syscalls per message with zero compiler signal. Given the public `pub fn`, this is a latent correctness-of-performance hazard, not a current hot-path defect. Confidence: HIGH (signature + call sites both read directly). Effort: trivial — tighten the bound to `R: BufRead`, or wrap internally (`let mut r = BufReader::new(reader)` would double-buffer; bound-tightening is the clean fix). Verification: change bound to `BufRead`; confirm it still compiles against all call sites (it will — they already pass `BufRead`); the bound now documents and enforces the buffering contract. Per-byte reads on a `BufRead` need no further change.

---

### [MINOR] CRC/checksum computed in a fused pass — no redundant scan (non-finding, recorded for completeness)

Location: `transfer.rs:91-104` (read) and `transfer.rs:60-68` (frame), `lzhuf.rs:43-53` (`fbb_crc`).

Problem: none. The receive-path checksum is accumulated inside the same byte-consuming loop that fills `data` (`transfer.rs:101-103`: push + `sum.wrapping_add` in one pass) — there is NO separate second pass over the block bytes for checksum. The frame path likewise sums each chunk as it is emitted (`transfer.rs:65-67`). `lzhuf::fbb_crc` is a single linear pass with a compile-time table (`CRC16_TABLE`, `lzhuf.rs:22`). No redundant passes over the block bytes exist on the framing path. Recorded so the dimension is explicitly cleared, not silently skipped.

---

### [MINOR] `frame_block` builds the full block in an unsized `Vec` before a single bulk write (acceptable; noted)

Location: `transfer.rs:47-74` (`Vec::new()` at `52`), written via `session.rs:384` → `write_bytes` (`session.rs:495-499`, single `write_all`).

Problem: `frame_block` allocates a fresh `Vec` with no `with_capacity` and grows it by repeated `push`/`extend_from_slice` (framing bytes + every data chunk), returning the whole block. The caller then issues one `write_all` (`session.rs:497`) — so the write path is already bulk (one `write_all` per message, NOT a per-byte/per-chunk write storm; write amplification is NOT present). The only data-access cost is the buffer's reallocation doubling during construction. Final size is exactly computable up front (`header_len` is already computed at `transfer.rs:50`; data length and chunk count are known), so `Vec::with_capacity(header + data.len() + 2*num_chunks + 2)` would eliminate the growth reallocations. Impact: a handful of reallocations per sent message at modest send frequency — negligible against the HF link RTT/rate, but it is the one missed bulk-IO/allocation lever on this path. Confidence: HIGH. Effort: trivial (one `with_capacity`). Verification: pre-size the Vec; existing `frames_a_block_*` tests assert exact byte output unchanged.

---

### wire.rs — no findings

`read_line` (`wire.rs:12-22`) requires `R: BufRead` and uses `read_until(b'\r', ...)` — a single bulk buffered read to the delimiter, the idiomatic form. No byte-at-a-time loop. Clean.

---

## Suspected Bugs

None. (Note for other lanes, not this dimension: `read_block` accepts a chunk `len==0` as 256 at `transfer.rs:97-98` while `frame_block` caps chunks at `MAX_CHUNK=125`; this is an asymmetry between writer and reader but is a deliberate protocol-compat read tolerance, not an I/O defect — out of scope here.)
