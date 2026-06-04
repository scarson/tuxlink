# Performance Audit — Data Access & I/O (storage backend)

Agent: glade-knoll-shoal · 2026-06-04 · dimension: data-access/IO · READ-ONLY
Scope: `src-tauri/src/{native_mailbox.rs,config.rs,user_folders.rs,session_log.rs}`
Lens: profile-packs/rust.md §"Data access & I/O" (over-fetching, N+1, unbuffered/whole-file rewrite).

Impact is expressed in fs syscalls / bytes per UI operation against folder size N (dozens–hundreds of messages).

---

### [CRITICAL] `list` reads + parses every full message body to build a header-only view (read-amplification / N+1)

**Location:** `native_mailbox.rs:93-128` (`Mailbox::list`); identical pattern in the shared helper `list_dir` (`native_mailbox.rs:432-455`), used by `list_user`.

**Problem:** Each folder listing does `fs::read_dir` then, per `.b2f` entry, a full `fs::read(&path)` of the entire message file (`:104`), a full `Message::from_bytes` parse (`:105`), plus a `path.with_extension("read").exists()` stat (`:112`) for inbox unread state. The produced `MessageMeta` only needs header fields (Mid, Subject, From, To, Date, Body-size — see `meta_from_message` `:497-515`). Bodies (attachments, message text) are read and parsed but discarded. There is **no header cache and no index for listing** — the search SQLite index (`store`/`move`/`mark_read` keep it current) already holds exactly these meta columns (`messages_meta`: mid, folder, unread, … queried at `:801`,`:836`,`:1037`) but `list` never consults it. Every list is a cold full-folder read.

**Impact:** Listing an N-message folder = `1 read_dir + N full-file reads + N parses + (N stats for inbox)`. For a 200-message inbox with, say, 4 KB average message + occasional attachment-bearing messages of 50–500 KB, that is ~200 file-open/read/close syscall triples + 200 stats + 200 parses, and **megabytes read to render subjects/dates**. Cost scales with total folder *bytes*, not message count — a few large attachment messages dominate. This fires on every sidebar folder click and every post-receive refresh. A header-prefix read (read only until the blank-line header terminator via `BufRead::read_until`) or, better, serving the list from the existing search index would cut bytes read by ~1–2 orders of magnitude and eliminate the body parse.

**Confidence:** High (verified against source).
**Effort:** Medium. Two tiers: (a) cheap — bounded prefix read instead of `fs::read`, parse headers only; (b) larger — back `list` with the search index (it already mirrors store/move/mark_read), making list O(1) query + sort instead of O(folder-bytes). (b) needs an index-rebuild/consistency fallback for folders the index may lag.
**Verification:** `strace -f -e read,openat` a `list` Tauri command over a 200-msg inbox; count read bytes vs. sum of header sizes. Or instrument `fs::read` call count in `list`.

---

### [MAJOR] `move`/`move_between` rewrite the whole message via read-then-write instead of `fs::rename`

**Location:** `native_mailbox.rs:152-161` (`move_to`); `:385-393` (`move_between`).

**Problem:** Both moves do `fs::read(&src)` (full body into a `Vec<u8>`), `fs::write(dst, raw)`, `fs::remove_file(src)` — a full read + full write + unlink. The `.read` marker is handled the same way (`:166`, `:397` write empty then remove). When source and destination are on the same filesystem (always true here — both under one mailbox root), `fs::rename` relocates the file with a single metadata syscall and **zero body bytes moved**. The same module already uses `fs::rename` correctly in `delete_user_folder`'s cascade (`:339`). Note the no-op-on-missing semantics (`:155`,`:388`) are preserved by `rename` returning `NotFound` the same way.

**Impact:** Per move: `1 full read + 1 full write + 1 unlink` (= 2× message bytes through the page cache + 3 syscalls) vs. `1 rename` (0 body bytes, 1 syscall). For a 500 KB attachment message that is 1 MB of avoidable I/O per Archive-button click; the UI exposes Archive as a single-key shortcut so this is a hot interactive path.
**Confidence:** High.
**Effort:** Low. Replace read+write+remove with `fs::rename`, mapping cross-device `EXDEV` to the existing copy path as a fallback (defensive; same-root means it won't trigger in practice).
**Verification:** Diff syscalls before/after on a large-attachment move; assert no `read`/`write` of body bytes.

---

### [MINOR] Registry rewritten in full on every user-folder mutation

**Location:** `user_folders.rs:182-193` (`save_registry`), called by create/rename/delete (`native_mailbox.rs:277,302,349`).

**Problem:** Each mutation does `load_registry` (full `read_to_string` + JSON parse, `:163-164`) then `save_registry` (serialize all + `fs::write` tmp + `fs::rename`). Whole-file rewrite for a one-entry change. **This is acceptable** — the registry holds at most a handful of folders and mutations are rare operator actions; flagged only for completeness as a read-then-rewrite-whole-file pattern. The atomic tmp+rename is correct and worth keeping.
**Impact:** Negligible at realistic folder counts (<dozens). No action recommended.
**Confidence:** High. **Effort:** N/A (no change advised).

---

### [MINOR] No per-process config cache; `read_config` re-reads + re-parses on each call

**Location:** `config.rs:403-417` (`read_config`).

**Problem:** Every `read_config` does `fs::read` + `serde_json::from_slice` + `validate`. Config is described as infrequently accessed (startup + settings changes), so re-reading per access is fine *if* callers are infrequent. No cache exists; if any hot path (per-message send building locator/SSID, per-connect transport lookup) calls `read_config`, the cost is a full file read + parse per operation. Within this slice there is no evidence of hot-path callers, so this is LOW priority — flagged so a reviewer confirms no per-message/per-connect call site re-reads config rather than holding a parsed `Config` in app state.
**Impact:** Per call: 1 read + full JSON parse. Bounded if startup/settings-only; would become MAJOR if a send/receive path calls it per message.
**Confidence:** Medium (depends on out-of-slice call sites). **Effort:** Low (cache parsed `Config` in Tauri state, invalidate on `write_config_atomic`).
**Verification:** grep call sites of `read_config`/`config_path` outside the wizard/settings handlers.

---

## Notes (in-dimension, no finding)

- `session_log.rs` is entirely in-memory (`RwLock<VecDeque>`); no file/socket I/O — out of this dimension. `snapshot`/`snapshot_since` clone the whole buffer under the read lock, but that's a memory/concurrency concern, not data-access I/O.
- `config.rs` `write_config_atomic` (`:445-492`) is exemplary for durability: same-dir tmp + `sync_all` + atomic `persist` + **parent-dir fsync** (`:489-490`). No I/O-correctness issue here; the only I/O-shape note is the schema-probe `fs::read` at `:464` re-reads the existing file before writing — one extra full read per save, negligible at config-write frequency.

## Suspected Bugs

- **Non-atomic message move (data-loss window).** `native_mailbox.rs:160-161` and `:392-393`: `fs::write(dst)` then `fs::remove_file(src)` are two separate ops with no fsync between, and no atomicity. A crash after a partial `write` but before `remove` can leave the message in BOTH folders (duplicate); a crash mid-`write` can leave a truncated dst while src is already... (src removed only after, so src survives that case). Switching to `fs::rename` (see MAJOR above) also fixes this — rename is atomic, eliminating the duplicate/partial window. file:line `native_mailbox.rs:160-161`, `:392-393`. Why: two-step copy+unlink is not crash-atomic; rename is. Noted per scope instruction (data-loss-risky non-atomic write); not chased further.
