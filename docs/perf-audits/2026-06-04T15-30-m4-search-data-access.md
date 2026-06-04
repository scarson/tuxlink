# Performance Audit — Data Access & I/O (M4 search backend)

Agent: glade-knoll-shoal
Scope: `/home/user/tuxlink/src-tauri/src/search` (9 files)
Dimension: data access & I/O (CORE)
Date: 2026-06-04
Stack: Rust 2021, rusqlite@0.40 (bundled SQLite, FTS5), outer `Mutex<Index>`
Load model: interactive (human-typed) searches at low frequency; per-message indexing on receipt; bulk re-index on rebuild; mailboxes dozens–hundreds of messages.

Dialect note: the SQL profile pack ships postgres + tsql modules; neither matches SQLite/FTS5 exactly. General SQL-pack signals (sargability, index column order, transaction batching, plan-vs-text) apply; SQLite-specific facts (PRAGMA journal_mode/synchronous semantics, FTS5 `rank`/`MATCH`/contentless-table cost) carry reduced dialect specificity and are flagged where they matter.

---

### [MAJOR] Bulk rebuild commits one transaction per message — N fsyncs instead of 1

**Location:** `commands.rs:156-177` (the `for meta in metas` loop) calling `index.rs:127-185` (`Index::upsert`, which opens `self.conn.unchecked_transaction()` and `tx.commit()` per call).
**Problem:** `rebuild_index` walks every folder and calls `locked.upsert(&row)` once per message. Each `upsert` opens its own `unchecked_transaction()` and commits it. With the default SQLite journal mode (rollback / `DELETE`) and default `synchronous=FULL`, every `COMMIT` forces an fsync (often two: journal + db file) to durably flush. So a rebuild of N messages performs **N transactions = N commits ≈ N–2N fsyncs**, plus the per-row FTS5 `DELETE`+`INSERT` and the `messages_meta` upsert each as separate statements inside those N transactions. The correct shape for a bulk regenerate-from-source is a single transaction wrapping the whole walk (or chunked batches), turning N fsyncs into 1 (or N/chunk). On a "hundreds of messages" mailbox this is the single highest-aggregate-cost data-access pattern in scope, and it is exactly the path the profile pack calls out ("each INSERT its own implicit transaction = fsync per row" / RBAR bulk load).
**Impact:** rebuild does ~N commit-fsyncs where 1 would suffice. For a 300-message mailbox that is ~300 durability barriers vs 1 — the dominant cost of the rebuild command, and rebuild is the heaviest operation the module performs. Per-message-on-receipt indexing (the single-`upsert` path) is correctly one transaction and is fine; this finding is specifically the **bulk** path looping the single-row primitive.
**Confidence:** Strong-static (transaction boundaries and the loop are directly visible; fsync-per-commit is the documented SQLite default for rollback-journal + `synchronous=FULL`).
**Effort:** Contained (+low). Add an `upsert_many`/batch entry point on `Index` that opens one transaction and loops the two/three statements inside it, or expose a `begin`/`commit` pair the rebuild loop brackets. Must keep the FTS `DELETE`-before-`INSERT` per row. Guard: rebuild test `rebuild_picks_up_messages_already_on_disk` (commands.rs:454) already asserts count==3 and round-trips; correctness is covered.
**Verification plan:** Count `COMMIT`s: wrap the connection with a `trace`/`profile` hook (`Connection::profile`) or count via a busy-handler/commit-hook, run `rebuild_index` over an M-message fixture, assert commits drop from M to 1. Bench: `criterion` over a 200-row rebuild before/after, release build, on a real fsync-backed FS (tmpfs hides the win — must use a disk-backed temp dir).

---

### [MAJOR] No PRAGMA tuning — default rollback journal + synchronous=FULL on every write path

**Location:** `index.rs:44-120` (`Index::open` / `init_schema` — no `pragma_update` for `journal_mode` or `synchronous`; only `user_version` is set). No PRAGMA appears anywhere in the module.
**Problem:** The connection runs with SQLite defaults: `journal_mode=DELETE` (rollback journal) and `synchronous=FULL`. Every committed write (`upsert`, `delete`, `update_folder`, `update_unread`, `populate_docs`) therefore creates+fsyncs+deletes a rollback journal file per transaction. Switching to `journal_mode=WAL` plus `synchronous=NORMAL` is the standard SQLite write-throughput lever: WAL appends instead of rewriting the journal, and `NORMAL` under WAL fsyncs only at checkpoint rather than per-commit, while remaining crash-safe (no corruption, only the last few unsynced commits at risk on power loss — acceptable for a *regenerable-from-mbox* derived index, which mod.rs:32-40 explicitly documents this DB to be). The interaction with the per-message-on-receipt path and especially the bulk rebuild (previous finding) is multiplicative: WAL+NORMAL cuts the per-commit fsync cost that the N-commit rebuild pays N times. The rebuild path already deletes `search.db-wal`/`search.db-shm` siblings (commands.rs:136-137, mod.rs:52-53), so the code anticipates WAL files existing — but nothing turns WAL on.
**Impact:** Affects every write. Combined with the N-commit rebuild it is the largest write-throughput lever available; on the single-message-on-receipt path it removes one journal-file create/fsync/unlink per received message.
**Confidence:** Strong-static (no PRAGMA in source; defaults are documented). The *magnitude* of the win is Heuristic without a measurement on the target FS.
**Effort:** Localized (+low). Two `conn.pragma_update(None, "journal_mode", "WAL")` / `pragma_update(None, "synchronous", "NORMAL")` calls in `Index::open` after the connection opens (journal_mode must be set on an open connection, outside an explicit transaction; it persists in the file header). Re-verify the drift/rebuild WAL-sibling cleanup still holds (tests `build_service_clears_wal_shm_siblings_on_drift` already plant + assert on these).
**Verification plan:** After setting WAL, `pragma_query_value(None, "journal_mode")` must return `"wal"`. Bench rebuild + a loop of single upserts before/after on a disk-backed dir; expect the largest delta on the bulk path. Confirm crash-safety posture is acceptable with the operator (regenerable index → yes).

---

### [MINOR] Non-sargable `from_addr` / `to_addrs` filters use leading-wildcard LIKE, defeating the `from_addr` index

**Location:** `query.rs:36-43` (`FilterKey::From` → `m.from_addr LIKE '%KX5DD%'`; `FilterKey::To` → `m.to_addrs LIKE '%KX5DD%'`).
**Problem:** Both `From` and `To` chips build `%{}%` patterns. A leading-wildcard `LIKE` is non-sargable — SQLite cannot use `idx_meta_from` (index.rs:99) to seek; it falls back to a full scan of `messages_meta` (or of the FTS-join subset). The `idx_meta_from` index therefore serves no query and is pure write tax on every upsert (the profile pack's "unused index" + "non-sargable predicate" signals both apply). `to_addrs` is additionally a JSON-encoded array string (index.rs:171) with no index at all, so its `LIKE '%x%'` is inherently a scan-and-substring — and a substring match against the JSON text can mismatch (e.g. a partial-callsign collision), but that is a correctness concern, noted below.
**Impact:** Per-query full scan of `messages_meta` on any From/To chip. At hundreds of rows on a human-interactive path this is cheap in absolute terms (the load calibration), so MINOR — but it means `idx_meta_from` is paying insert cost for zero read benefit, which is the more durable waste.
**Confidence:** Strong-static.
**Effort:** Contained. Options, in increasing scope: (a) drop `idx_meta_from` since the leading-wildcard query can't use it anyway (removes write tax, no read regression); (b) if exact/prefix From-match is acceptable UX, switch to `from_addr = ?` or `from_addr LIKE 'KX5DD%'` (prefix is sargable, uses the index); (c) normalize recipients into a child table or an FTS column for proper indexed address search. Pick based on desired From/To semantics — verify with the operator whether substring-anywhere is a real requirement.
**Verification plan:** `EXPLAIN QUERY PLAN SELECT … WHERE from_addr LIKE '%KX5DD%'` → expect `SCAN messages_meta`; same query with `LIKE 'KX5DD%'` → expect `SEARCH … USING INDEX idx_meta_from`. Confirms (a)/(b).

---

### [MINOR] ORDER BY on `COALESCE(date_received, date_sent)` cannot use either date index → forced sort

**Location:** `query.rs:93-96` (`ORDER BY COALESCE(m.date_received, m.date_sent) DESC/ASC`); indexes `idx_meta_date_recv` / `idx_meta_date_sent` at index.rs:97-98.
**Problem:** Sorting on an *expression* over two columns means neither single-column index (`idx_meta_date_recv`, `idx_meta_date_sent`) can deliver rows in `ORDER BY` order, so SQLite materializes the result and runs a `USE TEMP B-TREE FOR ORDER BY`. The same `COALESCE` expression is also used in the `DateRange` WHERE clauses (query.rs:71-78), so the date-range filter is likewise non-sargable against those indexes. Both indexes (`idx_meta_date_recv`, `idx_meta_date_sent`) are then unused for the queries that actually need date ordering/filtering — write tax for no read benefit, mirroring the previous finding.
**Impact:** A temp-b-tree sort per query over (at most) the filtered row set, then `LIMIT 200`. At hundreds of rows this sort is cheap (calibration → MINOR), but the two date indexes earn nothing on the ordering/range paths they were presumably created for.
**Confidence:** Strong-static.
**Effort:** Contained. Either (a) add a generated/stored `date_effective` column = `COALESCE(date_received, date_sent)` with an index on it (sargable for both ORDER BY and the range filter), or (b) if one date column is reliably populated per direction, decide ordering on it directly. (a) is the clean fix and lets `LIMIT` short-circuit via an index range scan. Re-evaluate whether `idx_meta_date_recv`/`idx_meta_date_sent` should then be dropped.
**Verification plan:** `EXPLAIN QUERY PLAN` on a default-sort query → expect `USE TEMP B-TREE FOR ORDER BY` today; with a `date_effective` index → expect `SCAN … USING INDEX` and no temp b-tree.

---

### [MINOR] Statements re-prepared on every call — no `prepare_cached` on the hot query/upsert paths

**Location:** `query.rs:250` (`self.conn.prepare(&sql)`), `docs_index.rs:45`/`77` (`prepare`), and every `tx.execute(<static SQL literal>, …)` in `index.rs:130-213` and `docs_index.rs:54-60`.
**Problem:** rusqlite's `Connection::prepare` / `Statement`-via-`execute` compiles the SQL each call; `prepare_cached` keeps a compiled-statement cache keyed on SQL text, amortizing the parse/plan across calls. The upsert statements (`index.rs:131`, `134`, `139`) are **static literals** — ideal `prepare_cached` candidates, and they run once per message on the bulk-rebuild loop (N×3 re-compiles). The docs `INSERT` in `populate_docs` (docs_index.rs:57) likewise re-prepares per topic (32× on populate). The `query.rs` search SQL is *dynamically composed* (query.rs:102), so its text varies by which chips are active — `prepare_cached` helps less there (cache keyed on exact text; different chip-sets miss), and on the human-interactive low-frequency search path the re-compile cost is negligible anyway, so query.rs is not the concern; the **bulk insert loops** are.
**Impact:** N×3 statement compilations on rebuild and 32 on docs populate that `prepare_cached` would collapse to 3 and 1 compilations respectively. This is dwarfed by the fsync cost (first two findings) — SQLite statement compilation is cheap relative to a durability barrier — so MINOR, and it rides along for free once the bulk path is restructured to one transaction with reused statements.
**Confidence:** Strong-static.
**Effort:** Localized (+low). In a batched `upsert_many`, `prepare_cached` (or prepare-once-outside-the-loop) the FTS delete/insert and the meta upsert; same for `populate_docs`. Naturally folds into the MAJOR rebuild fix.
**Verification plan:** Argument-based: re-compile count drops from N×3 to a constant. Optional `Connection::profile` hook to confirm statement count, or a `criterion` micro-bench on a prepared-once vs re-prepared insert loop (release build) showing the (small) delta.

---

### [MINOR] `populate_docs` rejected nothing extra, but extracts markdown inside the write transaction

**Location:** `docs_index.rs:52-64` (`populate_docs`): `extract_markdown(t.markdown)` is called *inside* the `tx` for each of 32 topics.
**Problem:** `extract_markdown` (extractor.rs:278) is pure CPU (line-by-line markdown stripping, per-byte inline scan) and holds no DB state, yet it runs while the write transaction is open — extending the window the transaction (and, via the outer `Mutex`, any concurrent reader) is blocked. The profile/SQL packs' "keep heavy computation outside the transaction; minimize the write window" signal applies. The set is bounded (32 compile-time topics) and `populate_docs` runs at most once per startup-with-drift, so the absolute cost is tiny — MINOR. But the pattern (CPU work bracketed by BEGIN/COMMIT) is the generalizable smell worth noting.
**Impact:** Lengthens the populate transaction by 32 markdown-strip passes. Once per startup-on-drift only → negligible aggregate, flagged for pattern hygiene.
**Confidence:** Strong-static.
**Effort:** Localized. Pre-compute `Vec<(slug,title,body_text)>` before `unchecked_transaction()`, then loop pure inserts inside the tx.
**Verification plan:** Argument-based (transaction window shrinks by the markdown-extraction time). No plan change.

---

## Examined-and-clear (not findings)

- **No N+1 across the Tauri command boundary.** `SearchService::run` (commands.rs:50-65) issues exactly one `query` per search; `hit_to_dto` maps in-memory rows with no per-row DB callback. The FTS join (`query.rs:20`) folds the meta lookup into one statement rather than one-query-per-hit. Good.
- **`messages_meta` upsert uses `ON CONFLICT … DO UPDATE`** (index.rs:151) — a single statement, not a select-then-insert round-trip. Good.
- **Projection is explicit**, not `SELECT *` (query.rs:103-106); it pulls only the 16 columns `QueryHit` needs and does not fetch the FTS `body` blob on the search path. Over-fetch is avoided. (The two JSON `to_addrs`/`cc_addrs` columns are deserialized per row at query.rs:268-271, but that is CPU, out of this dimension.)
- **docs search** uses `MATCH … ORDER BY rank LIMIT 30` with `snippet()` (docs_index.rs:77-83) — correct FTS5 shape (BM25 `rank`, not LIKE; bounded result set). Good.
- **`saved.rs` rewrites the whole JSON file on every mutation** (`flush`, saved.rs:74-81) — full read-modify-write of the saved/recent store per save/record/reorder. This is a filesystem-I/O write-amplification pattern, but the store is bounded (RECENT_CAP=20 + a handful of saved searches), the file is tiny, and writes are user-action-triggered (not hot). Below the MINOR bar at this load; noted for completeness, not ranked.

---

## Suspected Bugs

- **`total_matches` reports the page size, not the true match count.** `commands.rs:54` sets `total_matches = items.len() as u32`, but `items` is the LIMIT-`page_size`-capped page (query.rs:107, default page_size=200 per types.rs:65). For any result set larger than one page, `total_matches` undercounts (caps at page_size) — the UI's "N matches" would read "200" for any larger set. file:line: `commands.rs:53-56`. Why: there is no separate `COUNT(*)` over the unpaginated predicate; the count is derived from the truncated page. (Data-access note: fixing it correctly is a second `COUNT(*)` query with the same WHERE — a deliberate extra round-trip — or accepting "200+"; flagging the discrepancy, not prescribing.)
- **`to_addrs` filter substring-matches the JSON-encoded array string.** `query.rs:42` does `m.to_addrs LIKE '%KX5DD%'` against the `serde_json`-serialized array stored at index.rs:171. A substring search over JSON text can match across element boundaries or partial callsigns and ignores array structure. file:line: `query.rs:40-43` + `index.rs:171`. Why: recipients are stored as an opaque JSON string with no element-level query path. Correctness, not chased.
