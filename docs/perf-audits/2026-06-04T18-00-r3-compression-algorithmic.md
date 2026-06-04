# R3 Performance Audit — Winlink compression & B2F assembly (algorithmic complexity & data structures)

Agent: glade-knoll-shoal. Dimension: algorithmic complexity & data structures ONLY. Problems only.
Scope: `src-tauri/src/winlink/{lzhuf.rs,message.rs,compose.rs,proposal.rs,transfer.rs,wire.rs}`.
Frequency prior (W0): `compress` runs per-Outbox-msg per-connect; `decompress`+`read_block` per received msg. KB–tens-of-KB bodies, modest message counts, infrequent sessions. Constant factors calibrated against modest aggregate.

---

### [MINOR] `read_block` reads the data payload one byte at a time via `read_exact([u8;1])`

Location: `transfer.rs:78-122`, hot loop at `:100-104` (`for _ in 0..len { let b = read_u8(reader)?; ... }`), `read_u8` at `:124-130`.

Problem: Every payload byte is fetched with a separate `read_u8` → `reader.read_exact(&mut [0u8;1])` call. For a tens-of-KB compressed body that is tens of thousands of per-byte `read_exact` invocations. Two costs: (a) if the caller passes an unbuffered reader (a raw `TcpStream` / serial handle), each call is a syscall — O(n) syscalls instead of O(n/chunk); (b) even over an in-memory `Cursor`, it is O(n) trait-dispatched calls plus the per-call slice setup, versus a single `read_exact` into a pre-sized chunk buffer. The accumulating `data: Vec` (`:90`) also has no `with_capacity`, so it reallocates through log(n) doublings as bytes arrive. The chunk length (≤256) is known at `:96-99`; the data could be read in one `read_exact` per STX chunk into a reused buffer, and `data` could be reserved per chunk.

Impact: per-byte constant × (received msgs/connect). Modest aggregate at low session frequency; the syscall-amplification sub-case (unbuffered reader) is the only part that could matter, and only if the live transport reader is not wrapped in `BufReader`. Bounded; not on a high-frequency path.

Confidence: High (mechanism), Low (materiality) — depends on whether callers buffer the reader; out of this file's scope to confirm.
Effort: Low — read each chunk with one `read_exact` into a `[u8; 256]`/reused buffer; `data.reserve(len)` before extending.
Verification: `criterion` bench of `read_block` over a 30 KB framed block, BufReader vs raw Cursor, before/after; count `read` syscalls via `strace -c` on an unbuffered-reader harness.

---

### [MINOR] `find_subslice` is a naive O(n·m) scan; runs over the whole decompressed message on every receive

Location: `message.rs:301-303` (`haystack.windows(needle.len()).position(...)`), called at `:178` from `from_bytes` with `needle = b"\r\n\r\n"`.

Problem: `windows().position()` is the textbook naive substring scan, O(haystack · needle). needle is 4, so effectively O(message length) with a 4× window-comparison constant, scanning until the first `\r\n\r\n`. For the normal case (header block near the front) this terminates early and is cheap; the pathological case is a body with no early CRLFCRLF, forcing a full pass over the whole decompressed message (header+body+attachments) per received message.

Impact: per-received-msg, one linear pass bounded by message size; modest counts. The two `windows(4).position(...)` calls in tests don't ship. Not a quadratic blow-up — the concern is only the redundant 4-wide comparison vs `memchr`-style search, a constant-factor item on a linear, low-frequency path.

Confidence: High. Effort: Low (use `memchr::memmem` or split on the first `\n` then check the preceding `\r`).
Verification: bench `from_bytes` over a 30 KB message; compare against a `memmem` finder.

---

## Examined and found NOT a problem (in-scope claims checked)

- **lzhuf match finder (the big question):** `lzhuf.rs:415-474` `insert_node` IS the documented per-leading-byte binary search tree (`dad`/`lson`/`rson`, roots at `N+1+byte`), with `delete_node` (`:477-508`) sliding the window. This is the classic Okumura LZHUF BST, NOT a linear O(n·window) rescan. Match comparison is bounded by `F=60` (`:439`). Typical O(n·F) with small F; window walk depth is the only variability and is bounded by the 2048-byte window. Correct data structure; no finding.
- **Adaptive Huffman update:** `update` (`:202-244`) and `reconst` (`:160-198`) are O(tree depth) per symbol and O(T) on rebuild (triggered only at `MAX_FREQ`); standard adaptive-Huffman cost, not quadratic.
- **`put_code`/`encode_char`/`decode_char`:** O(code length) per token, O(1)-amortized bit packing. No re-scan.
- **`to_bytes` header sort (`message.rs:99-110`):** `sort_by` over the header list — n = header count (single digits to low tens). Bounded small n; not a finding per scope.
- **`set_header`/`set_attachments` `retain` (`message.rs:54,67`):** O(headers) linear scan per call; header count is tiny. Bounded small n.
- **`proposal.rs` `batch_checksum_line` (`:174-184`):** calls `p.line()` (one `format!` alloc) per proposal then sums bytes — O(total proposal text), linear in batch size. Modest.
- **`compose.rs` `encode_body` (`:136-141`):** two `String::replace` passes (each allocates) + a char map collect — O(body) linear, runs once at compose time (not per-connect). Fine.

## Suspected Bugs

None.
