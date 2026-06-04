# M4 — Search backend Execution Cost Map (`src-tauri/src/search`)

Agent: glade-knoll-shoal · 2026-06-04 · DESCRIPTIVE (where does time concentrate),
not adversarial. Scope: the FTS5 search backend behind `Mutex<Connection>`.

## Execution Cost Map
> Architectural awareness, NOT a to-do list. Time concentration is mapped by
> FREQUENCY × UNIT COST across the two real paths: INDEX (per-message + bulk
> re-index) and QUERY (interactive human search). Numbers are NOT invented;
> reasoning is from structure (SQL round-trips, fsync points, lock-hold regions,
> result materialization).

### The serialization point (frames everything below)

- **`Mutex<Index>` (`commands.rs:19`, locked at `commands.rs:52`, `:141`, `:483`)** — basis: every query, every upsert, the whole `rebuild_index` walk, and `docs_search` all acquire the *same* `self.index.lock()`. `rusqlite::Connection` is `Send + !Sync`, so the Mutex is load-bearing for `tauri::State` correctness, not just a convenience. Consequence: an interactive search and a `rebuild_index` cannot overlap — a search issued mid-rebuild blocks for the *entire* rebuild (lock is held across the whole folder walk, `commands.rs:141–178`). — confidence: High — map-only (single-user desktop; contention is rare but the worst-case stall is the full rebuild duration).

### Likely time-concentration regions — INDEX path

- **fsync-per-upsert (the dominant index cost) — `Index::upsert` `index.rs:127–185`** — basis: `init_schema` (`index.rs:63–120`) sets NO `journal_mode` and NO `synchronous` pragma (confirmed: the only pragmas anywhere in the module are `user_version`). The DB therefore runs in SQLite's *default* rollback-journal + `synchronous=FULL`. Each `upsert` opens `unchecked_transaction()` and `tx.commit()` — so every single message indexed forces a durable commit (journal write + fsync, DB write + fsync). On the bulk re-index path this is **one fsync-bearing commit per message**, executed serially inside the held lock (`commands.rs:174`). For a mailbox of dozens–hundreds of messages, wall-clock is dominated by N × (commit fsync latency), which on spinning disk / SD card (a Pi target) is the single largest cost in the whole module. — confidence: High — overlaps a likely hot-spot (bulk re-index is the burstiest load per the brief).

- **Per-message extraction `extractor::extract` `extractor.rs:57–122`** — basis: per message it does header lookups (`header`, `header_all`), a full `String::from_utf8_lossy(msg.body()).into_owned()` (one allocation + copy of the entire body, `extractor.rs:78`), and `sniff_form` (`extractor.rs:157–182`) which does **two passes over `body.lines()`** — once to find the `FORM:` prefix, once to collect/concatenate `Key: value` pairs into a joined string. CPU + allocation, not I/O; cost scales with body size. Real but second-order to the fsync cost above — for typical Winlink message bodies (KB-scale) this is microseconds against milliseconds of fsync. — confidence: High — map-only.

- **Two `serde_json::to_string` calls per upsert — `index.rs:171–172`** — basis: `to_addrs` and `cc_addrs` are JSON-serialized to TEXT on every upsert. Small vectors, small cost, but it's per-message allocation on the index hot path. — confidence: High — map-only.

- **Bulk re-index I/O: mailbox walk `rebuild_index` `commands.rs:127–184`** — basis: for each of 4 folders, `mbox.list(folder)` then per message `mbox.read(folder, &meta.id)` (a filesystem read of the raw RFC5322) + `Message::from_bytes` parse + `extract` + `upsert`. So per message: 1 file read + 1 parse + 1 extraction + 1 fsync-bearing commit. The file reads and the commits are the two I/O costs; the commits dominate (read is one syscall + page-cache-friendly; commit forces durability). All serial, all under one lock. — confidence: High — overlaps a likely hot-spot. NOTE: re-index does N independent transactions; wrapping the whole walk in ONE transaction would collapse N fsyncs to ~1 — this is the structural reason the path is fsync-bound, stated as a map fact, not a recommendation.

- **DELETE-then-INSERT on the FTS side every upsert — `index.rs:130–138`** — basis: FTS5 has no UPSERT, so each message re-index issues a `DELETE FROM messages_fts WHERE mid=?` followed by an `INSERT`. On rebuild the table was just recreated empty (`commands.rs:142`), so the DELETE matches nothing but still executes — two FTS statements + the meta UPSERT = 3 statements per message inside the transaction. FTS5 tokenization (porter/unicode61) of subject+body+form_field_values happens on each INSERT; tokenization cost scales with body size. — confidence: High — map-only (subordinate to the commit fsync).

- **Schema-drift recovery + docs repopulation at startup — `mod.rs:41–99`** — basis: once-per-launch, not a hot path. `build_service` may delete + recreate the .db, then `populate_docs` runs `extract_markdown` over ~32 bundled topics (`docs_bundle.rs`, `include_str!`'d markdown) and INSERTs each into `docs_fts` inside ONE transaction (`docs_index.rs:52–64`). One commit, one fsync — cheap. The slug-set diff (`mod.rs:72–84`) is one `SELECT slug` query + a HashSet compare. Sub-millisecond as documented. — confidence: High — map-only.

### Likely time-concentration regions — QUERY path

- **FTS5 MATCH + meta JOIN — `Index::query` `index.rs:248–286`, SQL from `query::compose` `query.rs:11–112`** — basis: the interactive path. `compose` builds SQL: when free-text is present it's `messages_fts AS f JOIN messages_meta AS m ON m.mid = f.mid` with `messages_fts MATCH ?`; otherwise a plain `messages_meta` scan. The MATCH walks the FTS index (sub-linear in corpus); the JOIN resolves each FTS hit to its meta row by `mid`. **`m.mid` is the PRIMARY KEY of `messages_meta`** (`index.rs:78`), so the join probe is an index lookup, not a scan — good. — confidence: High — map-only (corpus is small; FTS MATCH is fast).

- **`ORDER BY COALESCE(date_received, date_sent)` is unindexed — `query.rs:93–96`** — basis: the sort expression is `COALESCE(m.date_received, m.date_sent)`. There ARE separate indexes `idx_meta_date_recv` and `idx_meta_date_sent` (`index.rs:97–98`), but SQLite cannot use either to satisfy an ORDER BY over the *COALESCE of both* — so the result set is materialized and sorted in memory. Same for the DateRange filter's `COALESCE(...) >= ?` (`query.rs:71`): the per-column indexes don't cover the COALESCE predicate, so it's evaluated row-by-row. Cost scales with the number of rows passing the WHERE, bounded by `LIMIT 200` default (`types.rs:66`) only AFTER the sort. For a few-hundred-message mailbox this is a small in-memory sort — negligible now, but it's the part of the query path that grows with corpus size rather than with result count. — confidence: Med (depends on SQLite's planner choice; the COALESCE-defeats-index reasoning is structural and standard) — map-only.

- **`LIKE '%addr%'` filters are full scans — `query.rs:36–43`** — basis: `From`/`To` chips compile to `m.from_addr LIKE '%KX5DD%'` / `m.to_addrs LIKE '%...%'`. The leading `%` defeats `idx_meta_from`; `to_addrs` is a JSON-string column with no usable index at all. These predicates are evaluated per candidate row. Combined with a free-text MATCH the candidate set is already narrowed by FTS first; standalone (no free-text) they scan all of `messages_meta`. — confidence: High — map-only (small corpus keeps this cheap).

- **Row materialization: `query_map` closure — `index.rs:262–284`** — basis: per result row, the closure does 16 `row.get(...)` column extractions plus **two `serde_json::from_str`** deserializations (`to_addrs`, `cc_addrs`, `index.rs:268–271`). Then `SearchService::run` (`commands.rs:53`) maps each `QueryHit → MessageMetaDto` via `hit_to_dto`, which calls `unix_to_rfc3339` → `civil_from_days_and_seconds` (`commands.rs:296–322`) — pure arithmetic date formatting per row. Bounded by `LIMIT` (≤200), so it's a fixed small ceiling; per-row JSON parse is the largest unit cost here. — confidence: High — map-only.

- **`docs_search` — `docs_index.rs:73–92`** — basis: debounced help-window search, one prepared statement, `MATCH ? ORDER BY rank LIMIT 30`, with `snippet()` extraction per hit. Corpus is ~32 bundled topics — tiny. The `snippet(docs_fts, 2, ...)` call re-reads the body column for each of ≤30 hits to build the highlighted fragment; trivial at this scale. — confidence: High — map-only.

- **`lock().unwrap()` poison-panic surface (not perf, noted in passing)** — every accessor uses `.lock().unwrap()`; a panic while holding the lock (e.g. inside a `query_map` closure on malformed data) poisons the Mutex and wedges all subsequent search/index calls. Reliability note, not a time cost. — confidence: Med — map-only.

### Notes for architecture

- **The index path is fsync-bound, not CPU-bound.** The decisive structural fact: no `journal_mode`/`synchronous` pragma + one `tx.commit()` per `upsert` (`index.rs:183`) + `rebuild_index` calling `upsert` in a per-message loop (`commands.rs:174`). Bulk re-index cost ≈ N × commit-fsync latency. Extraction, tokenization, and JSON serialization are all real but subordinate (microseconds vs. the milliseconds of a durable commit, especially on Pi-class storage). Whatever the corpus size, the burst cost concentrates at the commit boundary.

- **Single lock couples the two paths.** `rebuild_index` holds `self.index.lock()` across the entire 4-folder walk (`commands.rs:141`), so the worst-case interactive-search latency is "blocked for a full rebuild," not "blocked for one query." For a single-user desktop this is acceptable, but it's the one place where INDEX-path cost leaks into QUERY-path latency.

- **Query path scales with corpus on the sort/filter, not on the FTS lookup.** FTS MATCH and the PK-join are sub-linear/indexed. The parts that grow with mailbox size are the unindexed `COALESCE(date_received, date_sent)` sort (`query.rs:94`), the `COALESCE(...)` date-range predicate (`query.rs:71`), and the leading-wildcard `LIKE '%...%'` address filters (`query.rs:37–42`). At dozens–hundreds of messages all are negligible; they are the dimensions that would matter first if the corpus grew by orders of magnitude.

- **Per-row JSON in `messages_meta`.** `to_addrs`/`cc_addrs` are stored as JSON TEXT, so they're serialized on every upsert (`index.rs:171–172`) and deserialized on every result row (`index.rs:268–271`). Symmetric small cost on both paths; also the reason `To` filtering can only be a `LIKE` against the JSON blob rather than an indexed lookup.

- **Startup work is one-shot and cheap.** Schema-drift recovery, slug-diff, and docs repopulation (`mod.rs:41–99`, `docs_index.rs:52–64`) all run at most once per launch inside single transactions — not a steady-state concern.
