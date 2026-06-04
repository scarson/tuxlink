# M4 Search — Data Access & I/O Audit

**Agent:** glade-knoll-shoal
**Dimension:** data access & I/O (CORE for this slice)
**Scope:** `src-tauri/src/search/{commands,docs_bundle,docs_index,extractor,index,mod,query,saved,types}.rs`
**Stack:** Rust 2021, rusqlite@0.40 (bundled SQLite, FTS5), `Connection` wrapped in outer `Mutex` (`commands.rs:19`).
**Load model:** interactive human-typed search (low frequency); per-message index on receipt; bulk re-index on rebuild; mailbox = dozens–hundreds of messages.

Findings ranked by Impact, calibrated to the modest load. Bulk-rebuild paths weighted higher than per-query interactive paths because the per-row fsync cost aggregates over N messages in one operation, while a query inefficiency is one human-paced round-trip.

Dialect note: the SQL pack ships Postgres + T-SQL modules; neither is exact for SQLite/FTS5. General SQL signals applied; reduced dialect specificity for FTS5-only features (`MATCH`, `rank`, `snippet`, `bm25`) is flagged where relevant.

---

### [MAJOR] No PRAGMA tuning: default `synchronous=FULL` + `journal_mode=DELETE` means a full fsync barrier per transaction on every index write

**Location:** `index.rs:44-58` (`Index::open`), `index.rs:63-120` (`init_schema`) — no `pragma_update` for `journal_mode`, `synchronous`, or `foreign_keys` anywhere.
**Problem:** A freshly-opened SQLite connection defaults to `journal_mode=DELETE` and `synchronous=FULL`. Under that combination every committed transaction incurs multiple fsync/disk-flush barriers (write the rollback journal + fsync, write the page + fsync, delete the journal + fsync the directory). Every `upsert`/`delete`/`update_folder` wraps its writes in `unchecked_transaction()` + `commit()` (`index.rs:128-184`, `187-193`, `195-207`), so each pays the full FULL-mode commit barrier. `populate_docs` (`docs_index.rs:52-64`) batches its ~32 inserts into one transaction, so it pays once — but the message-index write paths do not.
**Impact:** Per-message-on-receipt: one full fsync barrier per received message. On bulk rebuild of N messages each `upsert` is its own transaction (next finding), so N independent FULL-mode commit barriers — the dominant I/O cost of a rebuild. Switching to `journal_mode=WAL` + `synchronous=NORMAL` collapses the per-commit barrier to a WAL append (fsync deferred to checkpoint) — a large reduction in fsync count per write on the SD-card-backed Pi target. WAL also lets a concurrent search read proceed without blocking the writer.
**Confidence:** Strong-static (PRAGMA defaults are well-defined; absence verified across the module — no `journal_mode`/`synchronous` set, only `user_version`).
**Effort:** Localized (+low). Add `conn.pragma_update(None, "journal_mode", "WAL")?;` + `conn.pragma_update(None, "synchronous", "NORMAL")?;` in `Index::open` after `Connection::open` (WAL is a persistent DB property; `synchronous` is per-connection and must be re-set each open). `rebuild_index` (`commands.rs:134-137`) and `mod.rs:52-53` already delete `search.db-wal`/`search.db-shm` siblings, so adopting WAL is consistent with existing cleanup.
**Verification plan:** After change, `PRAGMA journal_mode;`/`PRAGMA synchronous;` confirm new values. Bench a ~200-message rebuild with `strace -f -e trace=fsync,fdatasync` before/after — expect fsync count to drop from ~O(N) FULL barriers to ~O(checkpoints). Correctness guard: WAL + `synchronous=NORMAL` can lose the last transaction(s) on power-loss but never corrupts; the index is explicitly regenerable from the mailbox (`mod.rs:1-6`, `rebuild_index`), so the durability relaxation is safe by design.

---

### [MAJOR] `rebuild_index` commits one transaction per message — N fsync barriers for an N-message rebuild

**Location:** `commands.rs:127-184` (`rebuild_index`), `locked.upsert(&row)` per message at `commands.rs:174`; `upsert` opens+commits its own transaction at `index.rs:128`/`183`.
**Problem:** The rebuild loop walks four folders and calls `Index::upsert` once per message. Each `upsert` does `unchecked_transaction()` … `tx.commit()`, so a rebuild of N messages is N committed transactions = N commit barriers (compounded by the FULL-mode default above). The classic "each INSERT its own transaction = fsync per row" anti-pattern. The whole rebuild already runs under one `self.index.lock()` (`commands.rs:141`), so the per-message commits buy no concurrency benefit — a reader is already excluded for the duration.
**Impact:** Bulk rebuild is the heaviest write operation in the module. Wrapping the entire folder walk in one outer transaction (or chunked batches) turns N commit barriers into 1 — combined with WAL, the bulk of the rebuild's I/O win. At hundreds of messages this is hundreds of fsync barriers vs. one. FTS5 also coalesces its `%_data` shadow-table b-tree writes inside a single transaction, lowering per-row overhead.
**Confidence:** Strong-static (loop structure + per-call transaction both in source).
**Effort:** Contained (+low). Add an `Index::upsert_in_tx(&Transaction, &IndexRow)` (or a `with_transaction` helper) and have `rebuild_index` open one transaction around the walk. Chunked commit (e.g. every 500) bounds WAL growth if mailboxes ever grow.
**Verification plan:** `strace`-count fsync/fdatasync across a rebuild before/after; expect ~N → ~1 (plus checkpoints). Correctness guard: a single wrapping transaction is *more* correct than the status quo — currently a mid-walk failure leaves a partially-populated index because each upsert auto-commits; one transaction makes rebuild all-or-nothing.

---

### [MINOR] Hot-path SQL re-prepared every call instead of `prepare_cached` — recompilation per upsert (×3 statements) and per query

**Location:** `index.rs:130,134,139` (`upsert` — three `tx.execute(<literal SQL>)`), `index.rs:189-190`/`198-204`/`210-213` (delete/update_folder/update_unread), `index.rs:250` (`query` — `self.conn.prepare(&sql)`), `docs_index.rs:45,77` (`docs_slugs`, `search_docs`).
**Problem:** `Connection::execute` / `prepare` compile the SQL to a `sqlite3_stmt` on every call and discard it. rusqlite's `prepare_cached` amortizes compile cost across repeated identical SQL. `upsert` recompiles three fixed statements per message; on a bulk rebuild that is 3N compilations.
**Impact:** Low per occurrence (SQLite codegen is fast, statements modest) and dwarfed by the fsync cost of the two MAJOR findings — hence MINOR. The `query` path is human-interactive (one prepare per debounced search) so its recompile is negligible; it is also dynamically composed (`query.rs:compose`) with a variable WHERE shape, so `prepare_cached` there has low cache-hit value (distinct text per filter combination) — leave it on `prepare`. The fixed-text upsert/delete/update statements are the worthwhile `prepare_cached` candidates, ideally folded into the rebuild-batching refactor.
**Confidence:** Strong-static.
**Effort:** Localized (+low). Convert the fixed-text `tx.execute("…", params)` calls to `tx.prepare_cached("…")?.execute(params)`. rusqlite caches per-`Connection`; the per-message `unchecked_transaction` shares the underlying connection so the cache persists across the rebuild loop.
**Verification plan:** Argument-based: 3N `sqlite3_prepare_v2` → 3 (cache warm after first message). The SQL text is provably invariant per call site; accept the static argument or confirm with a debug counter.

---

### [MINOR] `from`/`to` filters use non-sargable leading-wildcard `LIKE '%x%'`; `to_addrs` is an unindexed JSON blob; `idx_meta_from` is unused write-tax

**Location:** `query.rs:36-43` — `m.from_addr LIKE '%KX5DD%'`, `m.to_addrs LIKE '%KX5DD%'`; DDL `index.rs:99` `idx_meta_from ON messages_meta(from_addr)`; no index on `to_addrs`.
**Problem:** `LIKE '%x%'` with a leading wildcard is non-sargable — `idx_meta_from` cannot seek, so the `from` filter full-scans `messages_meta` and filters row-by-row. `to_addrs` is a serialized JSON array (`index.rs:171`) matched with `LIKE` against the blob; no index, substring-against-JSON. `idx_meta_from` is therefore maintained on every upsert but never served by the only predicate targeting `from_addr` — pure write-tax.
**Impact:** Low at this scale: scanning hundreds of rows on a debounced, once-per-committed-search query is cheap. The residual cost is the unused index's write tax on every insert/rebuild. If exact/prefix matching is acceptable, `from_addr = ?` or `from_addr LIKE 'KX5DD%'` makes `idx_meta_from` sargable and earns its keep.
**Confidence:** Strong-static.
**Effort:** Contained. Either drop `idx_meta_from` (stop paying for an unused index if `%x%` substring semantics are required) or switch the predicate to prefix/exact and keep the index. `to_addrs` substring search has no clean index without normalizing recipients into a child table — out of proportion to the load; defer.
**Verification plan:** `EXPLAIN QUERY PLAN SELECT … WHERE from_addr LIKE '%KX5DD%'` → expect `SCAN messages_meta` (index unused). After `LIKE 'KX5DD%'`: expect `SEARCH … USING INDEX idx_meta_from`. Weigh against substring-semantics requirement before changing behavior.

---

### [MINOR] `ORDER BY COALESCE(date_received, date_sent)` is non-sargable; the two date indexes can't serve it, forcing a temp-b-tree sort; both date indexes are unused write-tax

**Location:** `query.rs:93-96` (`ORDER BY COALESCE(...)`), `query.rs:71,77` (range predicate on the same COALESCE). DDL `index.rs:97-98` has separate `idx_meta_date_recv` + `idx_meta_date_sent`, none on the COALESCE expression.
**Problem:** Wrapping the two date columns in `COALESCE(...)` for the ORDER BY (and the range WHERE) makes the expression non-sargable: neither single-column index delivers rows in COALESCE order, so SQLite materializes the filtered set and sorts it (`USE TEMP B-TREE FOR ORDER BY`). The date-range filter is likewise an index-less scan-and-filter. Neither `idx_meta_date_recv` nor `idx_meta_date_sent` is ever seeked by this module's query shape.
**Impact:** Low at this scale — sorting hundreds of `LIMIT`-capped (default 200, `types.rs:67`) rows in memory once per committed search is cheap. Listed for completeness and because the two date indexes are write-tax the query never uses.
**Confidence:** Strong-static.
**Effort:** Contained. If date sort/range becomes hot, store a single `date_effective` column (received-or-sent, computed at index time) and index it — making both ORDER BY and range sargable on one index. At current load the higher-value move is dropping the two unused date indexes to remove write tax; adding `date_effective` is premature.
**Verification plan:** `EXPLAIN QUERY PLAN` on a default list query → expect `SCAN messages_meta` + `USE TEMP B-TREE FOR ORDER BY`, and confirm `idx_meta_date_recv`/`idx_meta_date_sent` appear in no plan. Validate a `date_effective` index removes the temp b-tree before adopting.

---

### [MINOR] (negative finding) `populate_docs` is correctly batched and drift-gated — the model the message rebuild should follow

**Location:** `mod.rs:72-84` (drift gate → `populate_docs`), `docs_index.rs:52-64` (`populate_docs`).
**Problem / note:** This path is *well* designed: all ~32 inserts batch into one transaction (`docs_index.rs:53`), and the drift gate (`mod.rs:80`) skips repopulation entirely when slug sets match, so steady-state startup cost is one `SELECT slug FROM docs_fts` + a HashSet diff. The only residual cost is a once-per-bundle-change re-parse (`extract_markdown` per topic) + DELETE+INSERT — not per launch.
**Impact:** Negligible — one transaction, ~32 small inserts, sub-millisecond. Documented to show the docs-index path was audited and found *not* to have the per-row-transaction / per-row-fsync problem the message-index path has. It is the model `rebuild_index` should adopt.
**Confidence:** Strong-static.
**Effort:** N/A (no change recommended).
**Verification plan:** None needed (negative finding).

---

## Suspected Bugs (recorded, not chased — outside the data-access dimension)

- **`total_matches` reports page size, not true match count.** `commands.rs:53-54`: `total_matches = items.len()` where `items` is already `LIMIT`-capped at `page_size` (default 200, `types.rs:67`). For any result set larger than one page, `total_matches` is clamped to ≤200, so result-count UI / pagination is wrong. A correct total needs a separate `SELECT COUNT(*)` over the same WHERE (a data-access consideration — adds a second query per search — but the primary defect is correctness). `commands.rs:53`.

- **`to`/`from` `LIKE '%x%'` substring match over-matches.** `query.rs:36-43`: a `from` filter of `W1A` matches `W1AW`/`KW1AB`; `to_addrs` matches against the serialized JSON array so a fragment can match across array/punctuation boundaries. Likely not the intended callsign-filter semantics. `query.rs:36-43`.

- **Un-guarded `.unwrap()` in the hot upsert path.** `index.rs:171-172`: `serde_json::to_string(&row.to_addrs).unwrap()` (and `cc_addrs`). Serializing a `Vec<String>` effectively never fails, so low-risk, but it is an unguarded `unwrap` on the per-message index path. `index.rs:171`. (Not perf; noted for the correctness lane.)
