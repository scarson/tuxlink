# Performance Audit — ARDOP modem driver, data access & I/O dimension

Agent: glade-knoll-shoal
Date: 2026-06-04T16-30
Scope: `src-tauri/src/winlink/modem/ardop/{arq_state,b2f,command,data,frame,listener,session,transport,wire}.rs`
Dimension: data access & I/O only. Reduced-depth slice.
Load calibration: ARQ session streams at HF modem rates (hundreds of bps to a few kbps); a transfer is KB-to-low-MB over seconds-to-minutes. Impacts are expressed against that LOW absolute rate.

---

### [MINOR] Data-socket write side is fully unbuffered; each B2F write = one TCP frame + one syscall + one fresh `Vec` alloc

Location: `data.rs:317-331` (`DataSocket::write`); `b2f.rs:105-106` wraps only the *read* half in `BufReader`, the write half goes straight through.

Problem: The B2F engine's `WriteHalf` (`b2f.rs:96-103`) calls `DataSocket::write` directly — no `BufWriter` anywhere on the outbound path. Each call allocates a fresh `Vec::with_capacity(2 + n)` (`data.rs:326`), does two `extend_from_slice`, and a `write_all` syscall. The B2F protocol writes proposal lines, `FF\r`/`FQ\r`/`FS Y\r` control tokens, and framed message blocks as separate small `write` calls — every one becomes its own length-prefixed wire frame and its own syscall, plus a per-call heap allocation. The read side is buffered (b2f.rs:105 + the decoder), so the asymmetry is real and unintentional-looking.

Impact: LOW in absolute terms. Outbound HF throughput is a few kbps, so syscall count and small-Vec allocs are never the bottleneck — the radio is. But note: each `write` also becomes a *separate ARDOP data frame on the wire* (`[u16 len][payload]`), so chatty small writes add 2 framing bytes each, slightly inflating what the TNC has to transmit. For control tokens (2-4 bytes) the 2-byte header is ~50-100% overhead per token. A `BufWriter` around the write half in `b2f.rs` (flushed at turn boundaries) would coalesce control tokens + body fragments into fewer, larger frames, cutting both syscalls and on-air framing overhead. Worth doing because it's trivial and the on-air-bytes saving, while small, is the scarcest resource here.

Confidence: HIGH (code is explicit; read/write asymmetry is visible at b2f.rs:105-106).
Effort: LOW (wrap `WriteHalf` in `BufWriter` in `run_b2f_exchange`; ensure flush before each read turn — B2F is strictly turn-based per the module doc, so flush points are well-defined).
Verification: count outbound TCP frames / `write` syscalls (`strace -e write`) for a scripted exchange before/after; assert control tokens coalesce into the following body frame.

---

### [MINOR] `DataDecoder::next_frame` does an O(n) `Vec::drain(..total)` memmove per frame plus a per-frame payload `to_vec()`

Location: `frame.rs:90-91` (`payload = self.buf[5..total].to_vec(); self.buf.drain(..total);`).

Problem: Each decoded frame (a) heap-allocates a fresh `Vec` for the payload, then (b) `drain(..total)` shifts all *remaining* bytes in `buf` left to index 0 (an O(remaining) memmove). When one TCP read delivers several queued frames (`decode_yields_multiple_frames_from_one_push` shows this is a supported case), decoding N frames from a buffer of size B is O(N·B) in the worst case because each drain re-shifts the tail. A cursor/offset that advances and only compacts when the buffer is drained (or `VecDeque<u8>` with `drain` from the front, which is still a shift but amortized) would make it O(B).

Impact: LOW. At HF rates the decoder buffer holds at most a few KB at a time (TCP read chunk is 4096, data.rs:258), and frames are typically read one-or-few per syscall, so the quadratic term is bounded by a tiny B. Real, but never material at these rates. Listed because it's a textbook drain-in-a-loop pattern that would matter at higher throughput and is cheap to note.

Confidence: HIGH (the drain + to_vec are explicit).
Effort: MEDIUM (introduce a read cursor; compact lazily — touches the partial-frame invariant, needs the existing frame tests to stay green).
Verification: `cargo bench`/criterion on `next_frame` decoding a buffer pre-loaded with M back-to-back frames; assert ~linear scaling in M.

---

### [MINOR] Decoded payload makes three hops with two whole-body copies before the caller sees it

Location: `data.rs:145` (`self.leftover.extend(frame.payload)`) + `data.rs:104-110` (`drain_leftover` byte-by-byte zip-copy out).

Problem: A payload's lifecycle: `frame.rs:90` allocates `payload: Vec<u8>` → `data.rs:145` `leftover.extend(frame.payload)` copies every byte into the `VecDeque<u8>` → `drain_leftover` (data.rs:106) copies each byte again out of the deque into the caller's `buf` via a `for` zip loop. That's two full-body byte copies (Vec→VecDeque, VecDeque→buf) on top of the initial `to_vec` alloc in the decoder. The `for (dst, src) in ...zip(drain(..n))` loop at data.rs:106-108 is element-wise; for a contiguous front slice of a `VecDeque` a `copy_from_slice` over `as_slices().0` would be a `memcpy`.

Impact: LOW at HF rates — at a few kbps the per-byte work is invisible against the radio. The element-wise drain loop is the most trivially-fixable piece: `VecDeque::as_slices()` + `copy_from_slice` replaces the byte loop with a `memcpy` for the common case where the requested span lies in the first ring segment. Architecturally the `Vec → VecDeque<u8> → buf` double-copy exists because frames can be larger than the caller's `buf` and must be re-offered across `read` calls; a `Vec<u8> + read-offset` (instead of `VecDeque`) would let `read` slice directly from the held payload and drop one copy + the deque entirely. Not worth a rework at these rates, but the `drain_leftover` byte loop → `copy_from_slice` swap is a clean, free micro-win.

Confidence: HIGH (all three hops are explicit in source).
Effort: LOW for the `drain_leftover` `memcpy` swap; MEDIUM for the `VecDeque`→`Vec`+offset redesign that drops a whole copy.
Verification: read-path microbench feeding a large ARQ frame through small `buf`s; confirm byte-loop replaced by `memcpy` and one fewer allocation per frame.

---

## Notes / examined-but-not-flagged

- `session.rs:77-81` cmd-socket reader correctly uses `BufReader` + `read_until(b'\r')` — properly buffered, no per-byte syscalls. Good.
- `data.rs:184-229` `drain_pending` reads into an 8192 scratch buffer in a loop with a 1ms timeout — bounded, runs once per connect transition (not a hot path). Fine.
- `data.rs:258` read loop uses a 4096 stack buffer pushed into the decoder — no unbuffered per-byte socket reads. Fine.
- `transport.rs` accumulator/throughput math (`record_buffer`, `current_throughput_bps`) is pure in-memory VecDeque bookkeeping ticked a few times/sec — not I/O, negligible.
- `wire.rs:13-18` `encode_cmd_line` pre-sizes its Vec; cmd lines are short and infrequent. Fine.
- No serde/serialization on the data path; framing is hand-rolled byte work. No N+1 / re-read patterns found. The ARQ_STATE poll (250ms, data.rs:38) is a deliberate low-rate wake-up, not a busy-spin.

## Suspected Bugs

None.
