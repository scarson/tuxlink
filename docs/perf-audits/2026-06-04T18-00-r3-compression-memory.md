# R3 — Memory & Allocation Audit: Winlink Compression + B2F Assembly

Date: 2026-06-04T18:00
Agent: glade-knoll-shoal
Dimension: memory & allocation (single-dimension)
Scope: `src-tauri/src/winlink/{lzhuf.rs,message.rs,compose.rs,proposal.rs,transfer.rs,wire.rs}`
Frequency prior (W0): `compress` per Outbox msg per connect; `decompress`+`read_block` per received msg; bodies KB–tens of KB; modest counts.

Findings only. Impact calibrated to modest frequency. No wall-clock.

---

### [MAJOR] `read_block` builds the body with per-byte push into a zero-capacity Vec (H11)

Location: `transfer.rs:90-105` (chunk loop), `data` declared line 90.

Problem: `let mut data = Vec::new();` starts at capacity 0. Each STX chunk is read one byte at a time via `read_u8` (line 101) and appended with `data.push(b)` (line 102). The chunk length `len` is known at line 96 *before* the copy loop, and the total body size is the compressed size from the accepted proposal — neither is used to pre-size. For a tens-of-KB compressed body that is N individual `push` calls plus the geometric-doubling realloc chain (≈log2(N) reallocs, each a full memcpy of the grown buffer).

Two-level fix:
- Pre-size `data` once from the proposal's `compressed_size` if the caller threads it in, or at minimum `Vec::with_capacity` per chunk before the inner loop.
- Replace the per-byte read+push loop with a single sized read: at line 96, after computing `len`, do `let mut chunk = vec![0u8; len]; reader.read_exact(&mut chunk)...` then `data.extend_from_slice(&chunk)` and fold the checksum over `&chunk`. This collapses N `read_u8` calls (each allocating a `[0u8;1]` stack array + a fallible `read_exact`) into one `read_exact` per chunk and one `extend` (amortized, and exact if `data` is pre-sized).

Impact: O(body_len) redundant byte-wise syscall-shaped reads and log-N reallocations per received message. Modest count, but the per-byte structure is the dominant allocation/copy cost on the receive path.

Confidence: High. Effort: Low.
Verification: `cargo test` (existing `reads_back_a_framed_block`, 300-byte/multi-chunk case covers it); confirm byte-identical output. Optionally `dhat` before/after on a 20 KB body to see realloc count drop to ~0.

---

### [MAJOR] `frame_block` assembles the whole frame in an unsized Vec though final size is computable

Location: `transfer.rs:47-74`, `out` declared line 52.

Problem: `let mut out = Vec::new();` then header bytes + every data chunk (`out.extend_from_slice(chunk)`, line 64) + per-chunk STX/len framing are pushed into a capacity-0 buffer. The final size is exactly computable up front: header (`1 + 1 + header_len`) + per-chunk overhead (`2 * ceil(data.len()/MAX_CHUNK)`) + `data.len()` + 2 (EOT + checksum). For a tens-of-KB compressed body this is the same log-N realloc chain as H11, on the send path.

Fix: compute the final length and `Vec::with_capacity(total)`. The chunk count is `data.len().div_ceil(MAX_CHUNK)`; total = `2 + header_len + chunk_count*2 + data.len() + 2`. (Streaming to the writer is an alternative but changes the signature — pre-sizing is the minimal change.)

Impact: log-N reallocations + memcpys per sent message over the full frame (largest buffer on the send path). Modest count.

Confidence: High. Effort: Low.
Verification: `cargo test` (`frames_a_block_with_header_data_and_checksum`, `splits_data_into_chunks...`); assert byte-identical. `out.capacity()` should equal final `out.len()` exactly after the fix.

---

### [MINOR] `lzhuf::finish` clones the compressed output into an intermediate CRC buffer

Location: `lzhuf.rs:628-636`.

Problem: `finish` builds `crc_input` as a fresh `Vec` = `size_bytes.to_vec()` (line 631, 4-byte alloc) then `extend_from_slice(&self.out)` (line 632) — a full copy of the entire compressed bitstream (KB–tens of KB) purely to feed `fbb_crc`. Then `framed` is allocated again and `self.out` is copied a *second* time (line 635). So the compressed payload is copied twice on every compress.

Fix: `fbb_crc` takes `&[u8]`; it does not need a contiguous buffer if computed incrementally. Refactor `fbb_crc`'s `step` into a small stateful accumulator (or a `crc_update(&mut sum, &[u8])` helper) and feed it `&size_bytes` then `&self.out` directly, dropping the trailing two-zero-bytes step at the end. That removes the `crc_input` allocation+copy entirely. The `framed` buffer is already correctly `with_capacity`-sized (line 629) and its copy of `self.out` is unavoidable for the final layout.

Impact: one extra full-payload allocation + memcpy per compressed message (the size of the compressed body). Modest count; the second copy into `framed` is intrinsic, only the `crc_input` copy is removable.

Confidence: High. Effort: Low.
Verification: `compresses_*_byte_for_byte_against_the_reference` + `crc_matches_the_well_known_xmodem_check_value` must still pass.

---

### [MINOR] `Encoder.out` bitstream Vec is unsized from input

Location: `lzhuf.rs:388` (`out: Vec::new()` in `Encoder::new`), grown via `put_code` (lines 602-610) one or two bytes at a time across the whole compression.

Problem: the output bitstream is grown by single `push` calls with no `with_capacity`. Compressed output is bounded above by roughly the input length (lzhuf rarely expands real text, but pathological/binary input can), so a reasonable prior is `input.len()`. `compress` (line 642) knows `input.len()` but `Encoder::new` takes no size hint, so `out` starts at 0 and doubles ~log2(compressed_len) times.

Fix: give `Encoder::new` (or a `with_capacity` constructor) the input length and seed `out: Vec::with_capacity(input.len())`. Over-allocation for highly-compressible input is bounded (one input-sized buffer, transient).

Impact: log-N reallocations of the bitstream per compressed message. Smaller than H11/frame_block because compressed size < raw size, but same class. Modest count.

Confidence: Medium (the capacity heuristic is an estimate, not exact). Effort: Low.
Verification: byte-for-byte conformance tests unchanged; `encoder.out.capacity()` no longer grows from 0 through the doubling sequence.

---

### [MINOR] `read_until_nul` returns an unsized Vec that is immediately consumed

Location: `transfer.rs:132-141`, called at `read_block:84-85` for title + offset.

Problem: `read_until_nul` accumulates byte-by-byte into `Vec::new()`. Titles/offsets are tiny (header_len is a single `u8`, so ≤255 bytes total), so the realloc cost is negligible — but the title bytes are then copied a third time: `String::from_utf8_lossy(&title).into_owned()` (line 119) allocates a fresh `String` from the borrowed slice. For the common all-ASCII title, `from_utf8_lossy` returns `Cow::Borrowed` and `into_owned` allocates+copies; using `String::from_utf8(title)` (consuming the Vec) avoids the copy when valid and only allocates on the lossy path. The `offset` Vec (line 85) is discarded entirely after the length check — it could be drained/counted without retaining bytes, but it is tiny.

Impact: negligible per-message (sub-256-byte buffers). Listed for completeness of the dimension; not worth prioritizing.

Confidence: High. Effort: Low.

---

### Lower-significance notes (dimension completeness, not prioritized)

- `message.rs:99-101` `to_bytes` builds `indexed: Vec<(String, &String)>` allocating a fresh `String` per header via `canonicalize_header_key`, then `out = Vec::new()` (line 87) grows unsized while serializing headers+body+attachments. `out` could be pre-sized from a cheap sum of header/body/attachment lengths; the per-header `String` allocations are intrinsic to canonicalization. KB-scale, modest frequency — minor.
- `message.rs:161` `to_proposal` calls `self.to_bytes()` once and compresses it — correct, no redundant clone. (Tests call `to_bytes()` again separately; that is test-only.)
- `message.rs:205,227` `from_bytes` uses `.to_vec()` to copy body/attachment slices out of the input — these are owned-copy boundaries the `Message` struct requires; not removable without lifetime changes. Not flagged.
- `compose.rs:137` `encode_body` does two sequential `String::replace` allocations (`\r\n`→`\n`→`\r\n`) then `collect`s a new `Vec<u8>` — three passes/allocs over the body. A single char-stream pass folding CRLF normalization + Latin-1 mapping into one `Vec<u8>` would remove two intermediate `String`s. Compose runs once per outbound message at send time; modest. Minor.
- lzhuf fixed arrays (`text_buf`, `freq`/`prnt`/`son`, `dad`/`lson`/`rson`, CRC/position tables) are the correct fixed-size design — NOT flagged per scope.

---

## Suspected Bugs

None observed within the memory/allocation lens. (Correctness not chased per scope; the byte-for-byte conformance and round-trip tests appear to constrain the code well. One note for the bug lane, not pursued here: `read_block:97-98` treats a chunk-length byte of 0 as 256, but `frame_block` caps chunks at `MAX_CHUNK = 125` and never emits a 0-length marker — the 256 branch is only exercised by externally-sourced frames, which is the documented intent, so this is not a defect.)
