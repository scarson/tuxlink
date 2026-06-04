# R10 Storage Backend — Memory & Allocation Audit

Dimension: peak memory & allocation. Scope:
`src-tauri/src/{native_mailbox.rs,config.rs,user_folders.rs,session_log.rs}`.
Load calibration: mailbox list/open/store, dozens–hundreds of messages,
infrequent config. Problems only.

---

### [MAJOR] `list` reads + parses every message body into memory to build a header-only view

**Location:** `native_mailbox.rs:99-115` (`list`) and the shared
`list_dir` helper `native_mailbox.rs:437-448`; corroborated by
`winlink/message.rs:176-188` (`Message::from_bytes` parses the header block
into owned `String`s AND stores `after_headers` as an owned `Vec<u8>` body).

**Problem:** `list` is documented as a "header-only view" (line 7, 83) but
its per-entry work is:

```rust
let raw = fs::read(&path)?;               // whole file into a Vec<u8>
if let Ok(msg) = Message::from_bytes(&raw) {   // owns body Vec<u8> + header Strings
    let mut meta = meta_from_message(&msg);
    ...
}
```

`fs::read` slurps the entire `.b2f` file (headers + full body, including any
embedded attachment bytes) into a fresh `Vec<u8>` each iteration, and
`Message::from_bytes` then materializes a second owned copy of the body
inside the `Message` struct. Only headers are consumed by
`meta_from_message` (`native_mailbox.rs:497-515`) — the body copy is built
and immediately discarded. This is the read-amplification finding's memory
face: a single list call's transient allocation is `2 ×` the size of the
largest message in the folder (the `raw` Vec plus the `Message.body` Vec
co-exist until `msg` drops at end of iteration). For a folder of message
bodies carrying attachments, that is the peak driver of the call, paid even
though zero body bytes are surfaced.

**Impact:** Per-list, not amortized: O(largest message size) peak transient,
× the parse churn for every file. At dozens–hundreds of messages with
multi-hundred-KB attachment bodies, list peaks at several × the biggest
message even though the result `Vec<MessageMeta>` is tiny. Reading only the
header block (read up to the `\r\n\r\n` terminator, e.g. a bounded
`BufReader::read_until` loop, or a header-only parse path that borrows
instead of owning the body) removes both the body slurp and the body copy.

**Confidence:** High — code path is direct; `from_bytes` owning the body is
confirmed in message.rs.

**Effort:** Medium — needs a header-only read/parse entry point on
`Message`; the file format already has a clean `\r\n\r\n` boundary.

**Verification:** `dhat`/heaptrack a `list` over a folder seeded with N
large-body messages; peak-bytes should fall to ~header-block size after the
change vs ~2× largest-message today.

---

### [MINOR] `meta_from_message` allocates several `String`s per listed message

**Location:** `native_mailbox.rs:502-513` (inside the `list`/`list_dir` loop).

**Problem:** Each meta builds `MessageId(...to_string())`, `subject`,
`from`, a `Vec<String>` of recipients (`header_all().iter().map(to_string)`,
`message.rs:137-143` first allocs a `Vec<&str>` then this re-allocs each into
an owned `String`), plus `winlink_date_to_rfc3339` which `format!`s a new
`String` (line 532). These owned `String`s are correct for the returned DTO
(it crosses the Tauri boundary), so they cannot simply be borrowed — but
`header_all`'s intermediate `Vec<&str>` (line 137) is built and discarded on
every `To`-bearing message purely to be mapped; collecting straight into the
owned `Vec<String>` avoids that throwaway vector.

**Impact:** Per-message, per-list: a handful of small-string allocations ×
N. Minor at dozens–hundreds of messages — heap traffic, not peak — and
dominated by the MAJOR body slurp above. Listed for completeness as the
"per-message String/Vec allocations in the list loop" item.

**Confidence:** High (allocations are literal). Impact deliberately MINOR.

**Effort:** Low (skip the intermediate `Vec<&str>`); the DTO Strings are
inherent.

**Verification:** Allocation-count delta in a `dhat` run over a To-heavy
folder; expect one fewer Vec alloc per message.

---

### [MINOR] `snapshot` / `snapshot_since` deep-clone the entire ring on every call

**Location:** `session_log.rs:64-69` (`snapshot`), `76-87`
(`snapshot_since`).

**Problem:** Both clone every retained `LogLine` (`iter().cloned().collect()`
into a fresh `Vec<LogLine>`). `LogLine` carries owned text, so each snapshot
duplicates the whole buffer's string content. `snapshot_since(after)` clones
only the tail (filtered), which is the right shape, but `snapshot` clones the
full `cap`-sized ring. The buffer is explicitly bounded by `cap`
(`session_log.rs:33-41`, `pop_front` at 57-58), so this is NOT unbounded
in-memory growth — the cap caps it. The cost is the per-call duplication: the
frontend snapshot-then-tail pattern calls `snapshot()` once per panel mount,
and a mode switch re-mounts (per the `clear` doc note, lines 95-98), so a
full-ring deep clone recurs on each mount.

**Impact:** Per snapshot call: O(cap × avg-line-size) transient + a retained
copy handed to the caller. Bounded by `cap`; magnitude depends on the cap the
constructor is given (not visible in scope). Low at typical log-cap sizes;
flagged because it is the only clone-the-whole-collection-on-each-access site
in scope and it recurs on UI re-mount.

**Confidence:** High (clone is explicit). Impact bounded by `cap`.

**Effort:** Low–Medium — `snapshot_since`-only seeding (skip the full
`snapshot`) or returning `Arc<[LogLine]>` would cut the repeat full clones;
needs a frontend-protocol check first.

**Verification:** Count `LogLine` clones across a mount→switch→mount cycle;
confirm `snapshot()` is the dominant cloner.

---

### Non-findings (examined, no material memory cost at this load)

- **config.rs** — `read_config` does one `fs::read` + one `from_slice`
  (config.rs:405-413); `write_config_atomic` one serialize. Infrequent
  (config is "infrequent" per load brief). `Config` is `Clone` but no
  clone-on-each-access pattern exists in scope; the deprecated
  `pat_mbo_address` `skip_serializing` is correctness/format, not memory.
  `deserialize_lenient_link` (config.rs:235-241) builds an intermediate
  `serde_json::Value` then `from_value`, a transient extra parse — but only
  on the single `packet.link` field at infrequent config load. Not material.
- **user_folders.rs** — registry is `Vec<UserFolder>` of small structs; load
  parses one small JSON, save serializes it. `slug_from_display` pre-sizes
  with `String::with_capacity` (line 114). Sorting `reg.folders` per
  `list_user_folders` (native_mailbox.rs:239) is a tiny vector. No
  whole-collection clone on access (`list_user_folders` moves `reg.folders`
  out). Folder counts are small. Not material.
- **`create_user_folder` / `rename_user_folder`** clone the `UserFolder` to
  echo back (native_mailbox.rs:276, 301) — single small struct, infrequent
  op. Not material.

---

## Suspected Bugs

None. (`user_folders::validate_slug` line 83 indexes `bytes[bytes.len()-1]`
after a `len() < SLUG_MIN_LEN` guard where `SLUG_MIN_LEN == 1`, so the empty
case is filtered first and the index is safe — not a bug, noted only because
it looked like a candidate.)
