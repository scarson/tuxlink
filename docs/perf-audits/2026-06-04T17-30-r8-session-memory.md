# Perf audit r8 — Winlink B2F session driver: memory & allocation

Agent: glade-knoll-shoal. Dimension: per-message/per-turn allocations. READ-ONLY.
Scope: `src-tauri/src/winlink/{session.rs,handshake.rs,credentials.rs,secure.rs,mod.rs}`
(+ `transfer.rs`, `wire.rs` read as the per-turn call targets).
Frequency prior: per-connect handshake (one-time); `send_turn`/`receive_turn` per
batch; modest message counts. Impact calibrated LOW throughout.

---

### [MINOR] `transfer::read_block` accumulates the body byte-by-byte with no `with_capacity`

Location: `transfer.rs:90-105` (`let mut data = Vec::new();` then `data.push(b)` in a
per-byte inner loop), on the per-message receive path `session.rs:467`.

Problem: every accepted inbound message's compressed body is gathered one byte at a
time via `data.push(b)` into a `Vec` started at zero capacity. For an N-byte body this
is N `push` calls plus log-N reallocations (1→2→4→…). The chunk length is known at each
STX (`transfer.rs:96-99`), so each chunk could be appended with a single sized
`extend`/`read_exact` into a pre-reserved buffer; even reserving on the first chunk
removes the doubling. Per-byte `read_u8` (`transfer.rs:124-129`, a 1-byte array
`read_exact` per byte) also defeats the injected `BufRead`'s buffering on the data path
— though that is runtime-syscall shape, not allocation.

Impact: LOW. Compressed bodies are small (KB-scale) and message counts modest; the
reallocations are a handful per message. Records the only genuine grow-unbounded-style
per-message buffer in scope.

Confidence: HIGH (code is explicit). Effort: LOW (reserve + chunk-extend).

Verification: `dhat`/heaptrack on a `receive_turn` over a multi-chunk block; count
allocations against body size before/after a `Vec::with_capacity` + per-chunk extend.

---

### [MINOR] `frame_block` builds the whole framed body in a fresh unsized `Vec` per sent message

Location: `transfer.rs:47-74` (`let mut out = Vec::new();`), called per accepted
proposal at `session.rs:384`.

Problem: the entire frame (header + all STX chunks + EOT) is assembled in a heap `Vec`
started at zero capacity, then handed to `write_bytes` → `write_all`. Final size is
known up front (`data.len()` + framing overhead ≈ `data.len() + data.len()/125*2 +
header`), so `Vec::with_capacity` would avoid the doubling reallocations. Stronger form:
the frame could be streamed directly to the writer (write SOH/header, then per-chunk
`write_all`) since a `Write` is already in hand at the call site — whole-body buffering
where streaming fits. The `offset_str = offset.to_string()` (`transfer.rs:48`) is a
small per-message String but trivial.

Impact: LOW. One Vec per sent message; modest counts; bodies small.

Confidence: HIGH. Effort: LOW (with_capacity) / MEDIUM (stream to writer, changes
signature).

Verification: allocation count per `send_turn` accept branch before/after sizing.

---

### [MINOR] `send_turn` clones every proposal in the batch when a borrow would do

Location: `session.rs:346` — `let proposals: Vec<Proposal> = batch.iter().map(|m|
m.proposal.clone()).collect();`. Also `session.rs:380` `msg.proposal.mid.clone()`.

Problem: the proposals are cloned out of the borrowed `&[OutboundMessage]` only to call
`proposal.line()` and `batch_checksum_line(&proposals)` (`session.rs:347-355`). Both
uses are read-only; a `Vec<&Proposal>` (or iterating `batch` directly and a
`batch_checksum_line` that accepts `&[&Proposal]` / an iterator) avoids cloning up to
`MAX_BATCH` (5) `Proposal` structs per send turn. `mid.clone()` at line 380 is needed
only on the `Accept`/`Reject`/`Defer` push into the outcome `Vec<String>`, so it is
defensible, but the line-346 batch clone is pure ceremony. `Proposal` is `Clone` and
likely holds several `String`s, so each clone is multiple small allocations.

Impact: LOW. ≤5 proposals × per send turn × modest turns. Allocation, not throughput.

Confidence: MEDIUM (depends on `Proposal`'s field shape; not read, but it derives Clone
and `.line()` builds from its fields). Effort: LOW–MEDIUM (touches
`batch_checksum_line` signature).

---

### [MINOR] one-time handshake builders use a `format!`-per-line append pattern

Location: `handshake.rs:43-53` (`build_handshake`) and `66-73`
(`build_master_handshake`): four/three `out.push_str(&format!(...))` calls, each
allocating a throwaway `String` then copying into `out`, which itself starts unsized.

Problem: `format!` into a temporary then `push_str` allocates-and-discards once per
line; `write!(&mut out, ...)` formats directly into `out`, and `String::with_capacity`
on `out` avoids its own doubling. Noted explicitly as **one-time (per-connect handshake)
cost**, not per-message — calibrated below the per-turn findings deliberately.

Impact: VERY LOW. Once per connection; ~4 small temp Strings.

Confidence: HIGH. Effort: LOW.

---

### Not-a-finding notes (recorded so the next pass doesn't re-chase)

- `answer_line` (`session.rs:476`) builds a tiny `String` per turn (≤MAX_BATCH+4 chars);
  micro, ignored.
- `remote_error`/`read_line`/`clean_line` (`session.rs:491`, `wire.rs:12-22`) allocate a
  `String` per protocol line read. `read_line` does `String::from_utf8_lossy(&buf)`
  then `.to_string()` — one extra copy of every line. Line volume is low (handshake +
  a few control lines per turn); LOW, not pursued. If line volume rose this would be the
  next target (the `to_string()` after a `Cow` that is usually already `Borrowed` is a
  reliable redundant copy).
- `credentials.rs`/`secure.rs`: per-connect (auth) only, not per-message. `secure.rs`
  `format!`/slice-to-`String` is one-time; credentials `format!("{e}")` only on error.
  Out of the hot per-turn band.

---

## Suspected Bugs

None. (`transfer.rs:96-99` treats a 0 length-byte as a 256-byte chunk while
`frame_block` caps chunks at 125 and never emits a 0 length — an asymmetry, but it is
inbound-tolerance for other senders, a correctness/compat concern, not a memory issue,
and not in primary scope.)
