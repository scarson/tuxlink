# Performance Audit — M4: Search Concurrency & Parallelization

**Auditor:** glade-knoll-shoal (single-dimension: concurrency & parallelization)
**Scope:** `src-tauri/src/search/` (commands.rs, index.rs, query.rs, docs_index.rs, mod.rs) + the live indexing call sites in `native_mailbox.rs`
**Date:** 2026-06-04
**Lens prior:** `profile-packs/rust.md` — Concurrency lane (oversized critical sections; std `Mutex` vs parking_lot; offload blocking work) + Runtime notes.
**Stack:** Rust 2021, rusqlite 0.40, `Connection: Send + !Sync`, single `Arc<Mutex<Index>>` in Tauri managed state, command handlers invoked from a Tauri thread pool.

Findings are reported for the concurrency dimension only — both DEFEND (lock-hold/contention) and EXPLOIT (parallelization). Impact is calibrated to the stated load: low-to-moderate UI concurrency, with the real contention vein being **bulk re-index vs. interactive query/receive on the single connection**.

---

### [CRITICAL] `rebuild_index` holds the single `Mutex<Connection>` across the entire mailbox re-walk, blocking all queries and live indexing for the whole rebuild

**Location:** `src-tauri/src/search/commands.rs:127-184` (lock acquired at `:141`, released at end of method `:184`)

**Problem:** `rebuild_index` takes `self.index.lock().unwrap()` at line 141 and holds the guard `locked` until the function returns at line 184. Inside that single critical section it:
- opens a fresh `Index` (`:142`),
- iterates all four folders (`:147-178`),
- for **every message**: calls `mbox.list(folder)` (a `read_dir` + per-file `fs::read` + parse, `native_mailbox.rs:93-128`), then `mbox.read(folder, &meta.id)` (another blocking `fs::read` of the `.b2f` file at `:157-159`), parses RFC5322 (`:160`), runs `extractor::extract` (`:167`), and `locked.upsert(&row)` (`:174`, itself a 3-statement transaction: DELETE + INSERT into FTS + upsert into meta — `index.rs:127-185`).

So the lock spans not just the SQLite writes but the **entire filesystem walk, file reads, and message parsing** of the whole mailbox. Every interactive `tauri_search_run` (`commands.rs:52` → `index.lock()`), `docs_search` (`commands.rs:483`), and — critically — every live message **receive** (`native_mailbox.rs:71` `guard.upsert`, `:174`/`:202`/`:411` folder/unread updates) blocks on this same mutex for the full duration of the rebuild. A user who clicks "Rebuild Index" freezes the search box and stalls inbound-message indexing until the walk completes.

This is the dominant concurrency liability in scope. The doc comment at `:122-126` frames the whole-rebuild-under-one-lock as *intentional* ("any concurrent reader waits rather than seeing a half-empty index") — that's a correctness rationale, but it conflates **atomic visibility** with **holding the lock during blocking I/O and parsing that have nothing to do with maintaining the invariant**.

**Impact:** Reachability: high (Rebuild Index is an operator-facing button; bulk re-index is a named realistic load). Frequency: low per-session but long-lived per occurrence. Per-occurrence cost: the critical section scales with total mailbox size × (file read + parse + 3 SQL statements) — for a large mailbox this is the longest single lock-hold in the subsystem, and it serializes the UI-latency search path *and* the live-receive index path behind it. Lock-hold scope: entire method. UI-stall risk: high — interactive search is fully blocked for the rebuild's duration; an inbound message during rebuild has its index update queued behind the whole walk.

**Confidence:** Strong-static (lock scope and the blocking calls inside it are directly readable; the shared mutex identity across `native_mailbox.rs` and the command handlers is confirmed via the `Arc<Mutex<Index>>` passed in `with_index`).

**Effort:** Contained (+low). The fix is to shrink the critical section: build the new index into a *separate* file (e.g. `search.db.rebuilding`) on a connection owned by the rebuild call — no shared lock held — doing all the file I/O, parsing, and inserts off-lock; then take the mutex only for the final atomic swap (`*locked = Index::open(new_path)` after an `fs::rename`). Reads and live upserts continue against the old index throughout the walk and block only for the swap. This preserves the atomic-visibility invariant the doc comment wants while removing the long hold. Alternatively, batch the per-message upserts and periodically drop+reacquire the lock to yield to waiting readers (weaker; reintroduces the half-empty-visibility window the comment warns against, so the separate-file swap is preferred).

**Verification plan:** Contention test — spawn a thread issuing `svc.run(small_spec)` in a tight loop while the main thread runs `rebuild_index` over a fixture mailbox of N messages; record the max and p99 latency of `run` during the rebuild window. Today the query thread should show latency tracking the full rebuild time; after the separate-file fix, query latency should be unaffected by rebuild size. Correctness guard: assert no query during the rebuild observes a partially-populated index (0 rows or full rows, never intermediate).

---

### [MAJOR] WAL mode is never enabled — readers and the single writer fully serialize on one rollback-journal connection

**Location:** `src-tauri/src/search/index.rs:44-58` (`Index::open` — no `journal_mode` pragma) and `:63-120` (`init_schema` — sets `user_version` only). No `PRAGMA journal_mode=WAL` anywhere in the subsystem; the only WAL references in the codebase (`mod.rs:52`, `commands.rs:136`) are *deletion* of stale `-wal`/`-shm` sibling files, not enabling WAL.

**Problem:** rusqlite/SQLite default to the rollback journal (`DELETE` mode), under which a writer takes a database-wide exclusive lock and readers cannot proceed concurrently with a write. In this subsystem the *application-level* `Mutex<Index>` already serializes everything onto one `Connection`, so the immediate symptom is masked — but two consequences follow:

1. The architecture forecloses the only realistic concurrency win available under SQLite's single-writer model. SQLite in **WAL mode allows one writer concurrent with multiple readers**. With WAL enabled and a **separate read-only connection (or small read-only pool) for queries**, interactive `tauri_search_run` / `docs_search` could run *without contending for the write lock* held by live `upsert`s or a rebuild. The current single-`Mutex`-one-`Connection` design cannot exploit that; it's a structural choice made at `mod.rs:90` (`Arc::new(Mutex::new(index))`) and `index.rs:30` (one `conn` field).
2. Even within the serialized design, rollback-journal writes incur a full-database lock and an `fsync`-heavy commit per `upsert` transaction (`index.rs:183` `tx.commit()`). WAL commits are append-to-log and generally cheaper, shrinking each writer's hold of the underlying file lock — which matters on the live-receive path where each inbound message is its own `upsert` transaction.

This is the EXPLOIT side of the dimension, honestly bounded: SQLite is single-writer, so threads cannot parallelize *writes*. The realistic win is **WAL + a reader connection that doesn't take the writer mutex**, exactly as the lens and the task brief anticipate.

**Impact:** Reachability: high (every query and every write goes through this connection). Frequency: continuous. Per-occurrence cost: moderate per write (extra fsync/locking vs WAL) and, more importantly, the *opportunity cost* of reads being unable to proceed alongside writes. UI-stall risk: moderate today (masked by the app mutex) but this is the enabling change that lets Finding 1's fix and a future reader path actually decouple query latency from write/rebuild activity. Without WAL, even moving reads to a second connection wouldn't help — they'd still block on the writer's DB lock.

**Confidence:** Strong-static (absence of any `journal_mode` pragma is verified across `src-tauri/src`; default journal mode is a documented SQLite default).

**Effort:** Cross-cutting (+low to set the pragma, +high to actually realize the win). Setting `conn.pragma_update(None, "journal_mode", "WAL")` (and a sensible `busy_timeout`) in `Index::open` is one localized change. *Realizing* the concurrency benefit requires the larger structural change: a second read-only `Connection` for queries not guarded by the write `Mutex` (e.g. a separate `Arc<Mutex<Connection>>` or an `r2d2`/`deadpool`-style read pool opened read-only). That second connection must use WAL to read concurrently with the writer, and the read path must be split off from `SearchService::run`/`docs_search`.

**Verification plan:** (a) Confirm WAL is active after open: query `PRAGMA journal_mode` returns `wal`. (b) Concurrency benchmark: with WAL + a separate read connection, run the Finding-1 contention test and confirm queries proceed during an active write/rebuild. (c) Correctness/regression guard: because the schema-drift recovery and tests assume on-disk sibling files, verify the existing `build_service_clears_wal_shm_siblings_on_drift` test (`mod.rs:143`) still holds and that WAL checkpointing doesn't strand uncommitted live-receive writes across an app restart (open → write → reopen → row present). Watch for `SQLITE_BUSY` under the new reader+writer split — set `busy_timeout` and confirm no busy storm under the contention test (a busy storm here would be a regression, per the calibration note).

---

### [MINOR] Blocking per-file disk I/O (`mbox.read`) executed inside the rebuild critical section

**Location:** `src-tauri/src/search/commands.rs:153-159` — `mbox.list(folder)` and `mbox.read(folder, &meta.id)` (each a synchronous `fs` read in `native_mailbox.rs`) run with the index mutex held.

**Problem:** This is the I/O-under-lock facet of Finding 1, called out separately because it's the lens's specific "blocking I/O held under the lock on a UI-latency path" signal. Each loop iteration does at least one `fs::read` of a `.b2f` file (`mbox.read`, and `mbox.list` itself re-reads + parses every file in the folder at `native_mailbox.rs:99-115`) while no other thread can touch the index. Filesystem latency (cold cache, slow disk) is thus directly added to the lock-hold time experienced by waiting query/receive threads. Note also `mbox.list` reads and parses every file to build metas, then the loop re-reads each file via `mbox.read` — the file content is fetched twice per message during rebuild, doubling the I/O performed under the lock (a data-access inefficiency that compounds the lock-hold; flagged here for its concurrency effect, not as a standalone data-access finding outside my dimension).

**Impact:** Reachability: high (rebuild path). Frequency: low (rebuild only). Per-occurrence cost: filesystem read latency × message count, all charged to the lock-hold. Subsumed by Finding 1's fix (moving the whole walk off-lock removes this entirely), but worth recording because even a *batched/yielding* mitigation of Finding 1 that kept reads on the shared connection would still want the file I/O off the lock.

**Confidence:** Strong-static.

**Effort:** Contained (resolved by Finding 1's separate-file rebuild; if Finding 1 is deferred, the narrower fix is to read+parse all messages into an in-memory `Vec<IndexRow>` *before* taking the lock, then hold the lock only for the inserts — though that buffers the whole mailbox in memory, so the separate-file approach is still preferable).

**Verification plan:** Covered by Finding 1's contention test; additionally, instrument the lock-hold duration (time between `index.lock()` and guard drop) and confirm it no longer scales with per-file disk read latency after the fix.

---

## Suspected Bugs

(Out-of-dimension correctness observations, recorded not chased per the calibration note.)

- **`unchecked_transaction` on a shared connection (`index.rs:128`, `:188`, `:196`, `docs_index.rs:53`).** `Connection::unchecked_transaction` bypasses rusqlite's compile-time borrow check that normally prevents nested transactions on one connection. It is *safe here only because* the outer `Mutex<Index>` guarantees a single in-flight caller per connection — i.e., the concurrency-correctness of every write transaction depends on the very mutex that Findings 1–2 propose to partially relieve. **If a future change introduces a second writer path or a reader connection that also writes, `unchecked_transaction` would silently permit overlapping transactions on one connection and could corrupt FTS/meta consistency.** Any decoupling work (Finding 2's reader connection) must keep all *writes* on the single mutex-guarded connection and route only reads to the second connection. Not a bug today; a latent constraint the concurrency refactor must honor.

- **Lock poisoning is `unwrap()`-ed on the query/command path (`commands.rs:52`, `:483`, etc.) but logged-and-skipped on the receive path (`native_mailbox.rs:75`).** Inconsistent poison handling: if any thread panics while holding the index mutex, every subsequent `tauri_search_run` panics on `lock().unwrap()`, permanently wedging search, while live-receive indexing merely logs and silently stops updating the index. This is a robustness/correctness asymmetry, not a perf issue — noted because the contention-test work in the verification plans could surface a panic-under-lock that exposes it.

None further within the concurrency dimension.
