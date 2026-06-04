# Storage backend — algorithmic complexity & data structures audit (r10)

Agent: glade-knoll-shoal
Scope: `src-tauri/src/{native_mailbox.rs,config.rs,user_folders.rs,session_log.rs}`
Dimension: accidental quadratics / data-structure mismatch in folder + message handling.
Calibration: dozens–hundreds of messages per folder; config infrequent; list/open/move on UI actions; store per receive.

---

### [MAJOR] Sort comparator re-parses RFC-3339 dates O(n log n) times instead of decorating once

Location: `native_mailbox.rs:116-126` (`list`) and `native_mailbox.rs:449-453` (`list_dir`).

Problem: both sorts call `sort_key_from_rfc3339(&a.date)` and `sort_key_from_rfc3339(&b.date)` *inside* the `sort_by` closure. `chrono::DateTime::parse_from_rfc3339` runs on every comparison, so each `MessageMeta::date` string is parsed ~`2·log2(n)` times rather than once. For n messages that is O(n log n) full timestamp parses against an inherent O(n) "parse each once" floor. This is the classic comparator-recompute / missing decorate-sort-undecorate (Schwartzian) pattern, and the comparator does string parsing, not a cheap field compare.

Impact: at ~100 messages, ~13× redundant parse work per list; at ~500, ~18×. `list` is on every folder-open UI action, so it is repeated, not amortized. Bounded but the multiplier grows with folder size — the exact "sorting work that should be hoisted to construction time" the lane flags (rust.md:23-25).

Fix: compute the key once per element when building `metas` (store `(Option<i64>, MessageMeta)` or add a transient key field), then `sort_by_key` / `sort_by` over the precomputed key. Collapses to one parse per message.

Confidence: High. Effort: Low (~10 lines, both call sites; they duplicate the same closure — fold into the existing `list_dir` helper which `list` does not yet use).

Verification: `criterion` bench of `list` over a 500-message folder before/after; assert parse-call count via a counting wrapper around `sort_key_from_rfc3339`. Output ordering unchanged by existing `list_returns_messages_newest_first_by_date` + `list_tiebreaks_equal_dates_by_id_ascending` tests.

---

### [MINOR] `list` duplicates `list_dir` logic instead of reusing it; full re-read+reparse of every file per list call is the inherent cost, but the index already holds the metadata

Location: `native_mailbox.rs:93-128` (`list`) vs `native_mailbox.rs:432-455` (`list_dir`).

Problem: `list` (system folders) hand-rolls the read-dir + `fs::read` + `Message::from_bytes` + sort loop that `list_dir` already implements; `list_user` delegates to `list_dir`. Beyond the duplication, every `list`/`list_user` call re-reads and re-parses the full raw bytes of *every* `.b2f` in the folder to rebuild header-only `MessageMeta` — O(messages × message_size) on each UI folder-open. A search index (`self.index`, `messages_meta` table) is already populated on `store`/`move`/`mark_read` and holds exactly the header projection `list` rebuilds, so the list view could be served from the index (one query) instead of a full folder rescan.

Impact: calibrated to dozens–hundreds this is acceptable per the brief (a per-list O(n) is fine), so MINOR — but it is O(n) *file reads + full-body parses* where an indexed read would be O(n) cheap rows or a single query, and it is the dominant cost of the hot list path. Reparsing the same files on every list is the "repeated parse of the same file" smell (rust.md:24-25, 77).

Confidence: Med (index-as-list-source is a design change; correctness parity with the FS-canonical view must be verified — spec §8 makes FS canonical and index best-effort, so a divergence risk exists). Effort: Med.

Verification: bench `list` over 200 messages reading-from-FS vs reading-from-index; confirm row parity against the FS walk in a test before switching the hot path.

---

### Notes (NOT findings — bounded small-n, correctly structured)

- `create_user_folder:256-262`, `rename_user_folder:293-299`, `delete_user_folder:321,348`, `list_user_folders:239` linear scans / sort over `reg.folders`: folder count is a handful; O(n) / O(n log n) here is fine. No map needed.
- `validate_slug` / `slug_from_display` / `validate_identity_describe`: single linear passes over short strings. Fine.
- `config.rs` read/write: infrequent; `deserialize_lenient_link` does a double parse (Value then `from_value`) but config is parsed once at open. Fine.
- `session_log.rs`: `VecDeque` ring with `pop_front`/`push_back` is O(1) append/evict — correct structure. `snapshot`/`snapshot_since` are O(n) clones of a bounded ring (`cap`), by design. No quadratic.
- `delete_user_folder:335-341` per-entry `fs::rename` loop is O(messages) renames — inherent to the operation, not quadratic.

---

## Suspected Bugs

None. (`list` and `list_dir` share the same sort semantics; the duplication is a maintenance/DRY risk, not a correctness divergence — both produce newest-first + id-ascending. No correctness defect observed in scope.)
