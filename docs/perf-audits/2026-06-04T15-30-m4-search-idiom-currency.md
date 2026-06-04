---
audit: m4-search-idiom-currency
dimension: library-idiom currency (rusqlite@0.40 fast-path idioms)
auditor: glade-knoll-shoal
date: 2026-06-04T15-30
scope:
  - src-tauri/src/search/index.rs
  - src-tauri/src/search/query.rs
  - src-tauri/src/search/commands.rs
  - src-tauri/src/search/docs_index.rs
  - src-tauri/src/search/mod.rs
stack: Rust 2021 / MSRV 1.75 · rusqlite 0.40 (bundled SQLite, modern_sqlite, FTS5)
basis: rusqlite 0.40 API surface; version-index rust.md (Rust perf index, covered_through 1.96)
---

# m4 — rusqlite@0.40 fast-path idiom currency: search backend

Findings only, scoped to idioms the code is missing or using sub-optimally. No praise, no grade.
Impact = reachability × frequency × per-occurrence cost. Confidence = Measured | Strong-static | Heuristic.

Verified against actual source: `rusqlite = { version = "0.40", features = ["bundled","modern_sqlite"] }`
(`src-tauri/Cargo.toml:49`). Grep across `src-tauri/src` confirms **zero** uses of `prepare_cached`,
and **zero** `journal_mode` / `synchronous` / `busy_timeout` PRAGMA configuration on any connection.

---

## Findings

### [MAJOR] No connection PRAGMA tuning (WAL / synchronous / busy_timeout) on the search connection
**Location:** `index.rs:44-58` (`Index::open`) — the only connection-open site; also the rebuild reopen at `commands.rs:142`.
**Problem:** `Connection::open(&path)` is used raw with no follow-up PRAGMA configuration. The bundled
SQLite default is `journal_mode=DELETE` (rollback journal) with `synchronous=FULL`. Under the realistic
load — per-message indexing (`upsert`) plus interactive queries that hold the `Mutex` (`commands.rs:52`)
— every `upsert` commits its `unchecked_transaction` in rollback-journal mode, which under `synchronous=FULL`
forces multiple fsyncs per commit (journal write+sync, then DB write+sync). WAL with `synchronous=NORMAL`
collapses this to a single sequential WAL append and defers the expensive checkpoint fsync, the canonical
SQLite write-throughput configuration. The schema-drift recovery code (`mod.rs:35`, `commands.rs:136-137`)
and the rebuild path already *assume* a `-wal`/`-shm` sibling exists (it deletes `search.db-wal`/`search.db-shm`),
so WAL is clearly intended — but `journal_mode=WAL` is never actually set, so those siblings are only
created transiently by SQLite's default journal handling, not by a durable WAL-mode connection.
Additionally there is no `busy_timeout`: under WAL a reader and the writer can briefly contend, and with no
busy handler a contended write returns `SQLITE_BUSY` immediately rather than retrying.
**Impact:** High. The bulk re-index (`rebuild_index`, `commands.rs:127-184`) walks dozens–hundreds of
messages, one `upsert` per message, each its own transaction → each its own fsync chain under the default
`synchronous=FULL`. fsync cost dominates this loop. Per-message live indexing pays the same per-commit
fsync. This is the single largest idiom gap in scope.
**Confidence:** Strong-static. The absence of any PRAGMA call is grep-verified; the SQLite default journal
behaviour is documented and version-stable. The *magnitude* of the win is load-dependent (fsync cost is
disk-class-dependent), hence not Measured.
**Effort:** Localized (+low). Add to `Index::open` after `Connection::open`:
`conn.pragma_update(None, "journal_mode", "WAL")?;` (note: WAL is sticky/persistent per DB file once set),
`conn.pragma_update(None, "synchronous", "NORMAL")?;`, and `conn.busy_timeout(Duration::from_millis(...))?`
(rusqlite 0.40 exposes `Connection::busy_timeout`). `journal_mode` must be set *outside* an explicit
transaction (it is here — `init_schema`'s batch is separate).
**Verification plan:** Query-count/fsync argument — wrap a 200-message `rebuild_index` and assert via
`strace -e trace=fsync,fdatasync -c` that fsync count drops from ~O(messages×journal-syncs) to ~O(1)
checkpoint-class. Correctness guard: existing `rebuild_tests::rebuild_picks_up_messages_already_on_disk`
and the `module_smoke` drift tests must still pass (they exercise the `-wal`/`-shm` delete path).
Basis: rusqlite 0.40 `pragma_update`/`busy_timeout` API; SQLite WAL+NORMAL is the documented write-throughput
config (version-stable, not a churned API).

---

### [MAJOR] Bulk re-index uses autocommit-per-row instead of one wrapping transaction
**Location:** `commands.rs:147-178` (the folder/message loop), each iteration calling `Index::upsert` (`index.rs:127-185`).
**Problem:** `rebuild_index` calls `locked.upsert(&row)` once per message. Each `upsert` opens its own
`self.conn.unchecked_transaction()` (`index.rs:128`) and commits it (`index.rs:183`). So a 300-message
re-index is 300 separate transactions = 300 commits. The idiomatic rusqlite bulk pattern is a single
`Transaction` (`conn.transaction()`) spanning the whole batch, with all inserts inside it and one final
`commit()`. Each commit is the unit that triggers the journal/WAL durability work; collapsing 300 commits
to 1 removes ~299 commit-fsync cycles. This is independent of (and compounds with) the PRAGMA finding:
even under WAL+NORMAL, one commit per row still appends+syncs the WAL per row.
**Impact:** High on the bulk/re-index path specifically (reachable via the `Rebuild Index` UI command,
`tauri_search_rebuild_index`, `commands.rs:266`). Per-message *live* indexing legitimately stays one-txn-per-message
(messages arrive one at a time) — this finding is scoped to the bulk loop only.
**Confidence:** Strong-static. The per-row transaction is explicit in `upsert`'s body and the loop calls it N times.
**Effort:** Contained (+low). Either (a) add an `upsert_in_tx(&Transaction, &row)` helper and have
`rebuild_index` open one `Transaction` around the whole walk, or (b) wrap the loop in
`tx = locked.conn.transaction()?; … per-message INSERTs … tx.commit()?`. Watch the `Mutex` borrow:
the loop already holds `locked` for the whole walk, so the lifetime is compatible. Keep the FTS
DELETE+INSERT semantics (FTS5 has no ON CONFLICT) — on a freshly-recreated empty DB the per-row DELETE
is a no-op and can be skipped in the rebuild path, but that is a secondary micro-opt, not this finding.
**Verification plan:** Query-count: count `COMMIT` statements (or `sqlite3_total_changes` checkpoints / WAL
frame count) before/after for a fixed N-message corpus; expect 1 commit vs N. Correctness guard:
`rebuild_picks_up_messages_already_on_disk` must still assert `messages_indexed == 3` and `count() == 3`.
Basis: rusqlite 0.40 `Connection::transaction` / `Transaction` is the documented bulk-insert idiom;
SQLite commit = durability boundary (version-stable).

---

### [MAJOR] Every query/insert/update re-prepares its SQL instead of using `prepare_cached`
**Location:** `index.rs:250` (`query`), `index.rs:130/134/139` (`upsert`'s DELETE+2 INSERTs),
`index.rs:189-190` (`delete`), `index.rs:197-201` (`update_folder`), `index.rs:210` (`update_unread`),
`index.rs:219` (`count`), `docs_index.rs:31/45/57/77/84` (docs queries + per-topic insert).
**Problem:** All call sites use `conn.prepare(...)` / `conn.execute(...)` / `conn.query_row(...)`, which
compile the SQL string into a new prepared statement on every call and drop it afterward. rusqlite 0.40
provides `Connection::prepare_cached`, which keeps a per-connection LRU `StatementCache` keyed by SQL text
and reuses the compiled statement on repeat calls. For the hot, fixed-SQL paths — `upsert`'s three statements
(called once per message during re-index and per-message live indexing) and `docs_index::populate_docs`'s
per-topic INSERT (`docs_index.rs:57`, called ~32× per repopulation, `mod.rs:80-83`) — the SQL string is
*identical every iteration*, so statement re-compilation is pure repeated overhead.
Note: `Index::query` (`index.rs:250`) builds a *dynamic* SQL string in `compose` (`query.rs:102`), so its
text varies with the filter/sort/freetext combination — `prepare_cached` there yields a low hit-rate and is
not the primary target (the cache would churn). The high-value targets are the fixed-text statements.
**Impact:** Medium–High on the re-index and docs-populate loops (fixed SQL × N iterations × statement-compile
cost). Lower per-occurrence on the interactive single-shot mutations (`update_unread`, `delete`), but those
are cheap to convert and benefit under burst (e.g. marking many messages read).
**Confidence:** Strong-static. Grep confirms zero `prepare_cached` usage project-wide; all sites use `prepare`/`execute`.
**Effort:** Contained (+low). Convert the fixed-SQL hot paths to `prepare_cached`. `upsert`/`delete`/`update_folder`
currently use `tx.execute(...)`; switch to `tx.prepare_cached(sql)?.execute(params)?` (the statement cache lives
on the `Connection`, and `Transaction` derefs to it). `populate_docs`'s loop should `prepare_cached` the INSERT
once outside the loop (or rely on the cache). Leave the dynamic `Index::query` on plain `prepare`.
**Verification plan:** Argument + cache-hit check: after conversion, `Connection::cache_flush` / inspecting the
statement-cache hit behaviour, or a criterion bench of `upsert` ×1000 with `black_box`, expecting a measurable
drop in per-call time once compilation is amortized. Correctness guard: all `mutation_tests` and
`query_integration` tests unchanged.
Basis: rusqlite 0.40 `Connection::prepare_cached` + `StatementCache` (stable API; the documented fast-path for
repeated identical SQL). Version-index rust.md has no contradicting entry (it is build-config focused).

---

### [MINOR] `messages_fts` duplicates the body instead of using a `content=` external-content table
**Location:** `index.rs:68-95` — `messages_fts` (FTS5 virtual table holding `subject, body, form_field_values`)
created alongside `messages_meta` (which holds the metadata but NOT the body).
**Problem:** FTS5 supports `content=''` (contentless) or `content='<table>'` (external-content) modes so the
indexed text is not stored a second time inside the FTS B-tree. Here the body/subject/form-field text lives
*only* in `messages_fts` (it is not in `messages_meta`), and the mailbox files on disk are the canonical source
(`mod.rs:1-6`, the index is "regenerable from disk"). Because the source of truth is the mbox and the index is
derivable, a contentless FTS5 (`content=''`) would store only the inverted index, not a second verbatim copy of
every message body — halving the on-disk FTS storage and reducing the bytes written per `upsert` (less I/O per
indexed message). The query path (`query.rs:103-107`) selects body columns only from `messages_meta`/`f.mid`,
never the FTS `body` column itself, and `search_docs` already correctly uses `snippet()` (`docs_index.rs:78`)
rather than fetching+slicing the body in Rust — so the stored body in `messages_fts` appears to exist only to
satisfy `MATCH`, which a contentless index also satisfies.
**Impact:** Low–Medium. Reduces FTS write volume per `upsert` and total `search.db` size; the win scales with
average message body size × message count. Not on the interactive query hot path (queries don't read the FTS body).
This is a schema-design idiom, not a per-call fast-path, hence MINOR.
**Confidence:** Heuristic. Requires confirming no current/planned code path reads `messages_fts.body` or needs
`snippet()`/`highlight()` over message bodies (contentless FTS5 cannot run `snippet()`/`highlight()` because the
text isn't retained). If a future "highlighted message search result" feature is planned, contentless is the
wrong call and external-content (`content='messages_meta'` with body moved into meta) is the idiom instead.
Flagging as a currency gap to evaluate, not a mandated change.
**Effort:** Cross-cutting (+low) — schema change → `SCHEMA_VERSION` bump → drift-recovery already handles
recreate-from-mbox, so migration is "free" via the existing rebuild path, but it touches the contentless-FTS
delete-syntax (`'delete'` command rows) and must be validated against the DELETE+INSERT upsert pattern.
**Verification plan:** Argument + size measurement: build the index two ways over a fixed corpus, compare
`page_count`×`page_size` of the `search.db`; assert `MATCH` queries return identical mids. Correctness guard:
the full `query_integration` suite (freetext + filters) must pass unchanged.
Basis: SQLite FTS5 `content=`/contentless documented option (version-stable; bundled SQLite ≥ 3.9 per Cargo.toml
comment supports it). Not a rusqlite-version-specific claim.

---

## Calibration notes (examined, NOT findings)

- **`Index::query` uses `query_map` + `collect::<Result<Vec<_>,_>>()`** (`index.rs:261-285`) — idiomatic
  rusqlite row mapping; collecting (vs streaming) is correct here because the caller (`commands.rs:53`) needs an
  owned `Vec` to map into DTOs and the result set is LIMIT-bounded (`query.rs:107`). No finding.
- **`search_docs` uses `ORDER BY rank` + `snippet()`** (`docs_index.rs:78-82`) — this is the *correct* FTS5
  idiom (BM25 via `rank`, server-side snippet instead of Rust-side slicing). Cited as the positive contrast for
  the messages-FTS content finding; itself no finding.
- **`unchecked_transaction` in `upsert`/`delete`/`update_folder`** (`index.rs:128` etc.) — `unchecked_transaction`
  is used because `&self` (not `&mut self`) is held; this is the documented rusqlite escape hatch for shared-ref
  transactions and is correct given the `Mutex<Index>` ownership. The batching finding (MAJOR #2) is about the
  *bulk loop* calling it N times, not about this primitive. No standalone finding.
- **LIMIT/OFFSET as literal interpolation** (`query.rs:102-109`) — values are `u32` page fields, not user strings;
  no injection and no perf consequence. The accompanying doc-comment justifies it. No finding. (Correctness/SQL-shape
  is another lane's concern.)
- **`Row::get::<_, i64>()` then cast to `u32`/`bool`** (`index.rs:274-281`) — idiomatic; SQLite has no native u32,
  i64 is the correct storage class. No perf consequence. No finding.
- **No `foreign_keys` PRAGMA** — the schema declares no FOREIGN KEY constraints (`messages_fts`↔`messages_meta`
  consistency is maintained in application code via the dual-write upsert), so `foreign_keys=ON` is a no-op here.
  Correctly omitted. No finding.
- **Build-config idioms** (LTO, codegen-units, target-cpu per rust.md) are out of this dimension's scope
  (this lane is library-idiom currency, not build config). Not assessed.

---

## Suspected Bugs

None within this dimension. (Two correctness-adjacent observations noted for the differential/holistic lanes,
not chased here: (1) `serde_json::to_string(...).unwrap()` at `index.rs:171-172` can panic in theory though
the inputs are `Vec<String>` which never fail to serialize; (2) the contentless-FTS migration path, if ever
taken, changes FTS5 delete semantics — flagged in the MINOR finding's effort note. Neither is a perf bug.)
