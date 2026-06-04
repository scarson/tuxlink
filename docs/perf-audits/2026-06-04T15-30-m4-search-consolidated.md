---
run_schema_version: 1
run_id: 2026-06-04T15-30-m4-search
date: 2026-06-04T15:30:00Z
scope: "M4 — src-tauri/src/search: SQLite-FTS5 mailbox search backend"
methodology:
  skill: performance-audit (within performance-audit-cycle)
  plugin_version: superpowers-plus@0.2.0
dispatch:
  model_requested: "latest-opus (Claude Code Agent subagents, model=opus)"
  reasoning_effort: "default (harness exposes no reasoning-effort knob)"
  overridden_by_user: false
stack:
  - { ecosystem: crates, framework: rusqlite, version: "0.40 (bundled SQLite, FTS5)" }
  - { ecosystem: crates, framework: rust, version: "edition 2021 / MSRV 1.75" }
currency_briefs:
  - { framework: rusqlite, researched_on: null, status: "library knowledge — prepare_cached / WAL / transaction-batching idioms are stable" }
  - { framework: sql, researched_on: null, status: "SQL companion pack applied; dialect SQLite/FTS5 (pack ships postgres+tsql — reduced dialect specificity noted)" }
lanes_run: [algorithmic, memory, data-access, concurrency, idiom-currency, cost-map]
lanes_skipped:
  payload-startup: "desktop-app backend, not a payload/bundle/cold-start surface"
  dynamic: "deferred — building/running src-tauri pulls the full Tauri stack; static + EXPLAIN-QUERY-PLAN reasoning used instead"
finding_counts:
  by_impact: { critical: 1, major: 5, minor: 4 }
  by_lane: { algorithmic: 3, memory: 2, data-access: 5, concurrency: 3, idiom-currency: 4, cost-map: 0 }
  suspected_bugs: 6
regression:
  prev_run_id: null
  new: 10
  persisting: 0
  resolved: 0
---
# Performance Audit — M4: search (SQLite-FTS5 mailbox search backend)

**Date:** 2026-06-04 15:30   **Scope:** `src-tauri/src/search` (9 files)
**Stack:** Rust 2021 · rusqlite@0.40 (bundled SQLite, FTS5) behind `Mutex<Index>`
**Currency brief:** rusqlite/WAL/prepare_cached idioms (stable); SQL companion pack (SQLite dialect, reduced specificity)
**Lanes run:** all six core (payload-startup + dynamic skipped — see frontmatter)
**Regression vs none (first run):** 10 new, 0 persisting, 0 resolved
**Load model:** interactive (human-typed, debounced) search — low frequency; per-message indexing on receipt; **bulk re-index** over a whole mailbox (dozens–hundreds of messages) is the burstiest path. Lanes calibrated to this (they correctly did NOT over-rank the query side at hundreds of rows).

## Executive summary

The **write/index path is the cost**, and it's both **fsync-bound** and **lock-serialized**; the query path is cheap and well-indexed at this scale. One CRITICAL (rebuild holds the global DB lock across the whole mailbox walk, freezing search + inbound indexing) and a tight MAJOR cluster (no WAL/`synchronous=NORMAL`; per-message commits on rebuild; no `prepare_cached`) together define "make rebuild fast and non-blocking." Strong cross-lane agreement: the WAL-absence + per-message-commit pair was independently surfaced by data-access, idiom-currency, concurrency, and the cost map.

## Critical

### S1. `rebuild_index` holds the single `Mutex<Connection>` across the entire mailbox re-walk
**Lanes:** concurrency (CRITICAL), cost-map   **Location:** `commands.rs:127-184` (guard `:141` → return `:184`)
**Fingerprint:** `concurrency:commands.rs:rebuild_index:lock-across-walk`   **Status:** new
**Problem:** the guard is held while, for every message across four folders, the code does blocking `fs::read` + RFC5322 parse + a 3-statement `upsert` transaction. It's the **same** `Arc<Mutex<Index>>` every interactive `query` (`commands.rs:52`) and on-receipt `upsert` (`native_mailbox.rs:71`) takes — so a rebuild fully freezes the search box AND stalls inbound-message indexing.
**Impact:** UI stall + indexing stall for the full rebuild duration (seconds on Pi-class storage, compounded by S2/S3). **Confidence:** Strong-static   **Effort:** Contained — build into a temp DB off-lock, take the lock only for an atomic swap.
**Verification plan:** measure interactive query latency while a rebuild runs, before/after; correctness guard = index contents identical post-swap + no `SQLITE_BUSY` storm.

## Major

### S2. No connection PRAGMA tuning — default `journal_mode=DELETE` + `synchronous=FULL`
**Lanes:** idiom-currency, data-access, concurrency, cost-map (4/6)   **Location:** `index.rs:44-58` (`Index::open`/schema; only `user_version` is set)
**Fingerprint:** `data-access:index.rs:open:no-wal-pragma`   **Status:** new
**Problem:** every commit is a full multi-fsync barrier; the drift-recovery code already deletes `-wal`/`-shm` (so WAL is *intended* but never set). WAL + `synchronous=NORMAL` (+ `busy_timeout`) collapses each commit to a deferred-fsync WAL append — a large win on the SD-card target; the index is regenerable so relaxed durability is safe. **Effort:** Localized (two `pragma_update` lines). Enables S1's reader-connection option too.

### S3. `rebuild_index` commits one transaction per message
**Lanes:** data-access, idiom-currency, cost-map   **Location:** `commands.rs:174` → `index.rs:128,183` (`upsert` opens+commits its own txn)
**Fingerprint:** `data-access:commands.rs:rebuild_index:per-row-commit`   **Status:** new
**Problem:** N messages = N commit barriers (× FULL-mode fsyncs), though the whole walk already holds one lock so per-message commits buy nothing. Wrap the walk in one `Transaction` → N barriers → 1. `populate_docs` (`docs_index.rs:52-64`) is the correctly-batched model to follow. **Effort:** Contained. **(S2+S3 are the headline write-throughput pair.)**

### S4. No `prepare_cached` on fixed-SQL hot paths
**Lanes:** idiom-currency, data-access   **Location:** `index.rs:130,134,139` (upsert), `docs_index.rs:57`, delete/update/count
**Fingerprint:** `idiom-currency:index.rs:upsert:no-prepare-cached`   **Status:** new
**Problem:** identical SQL recompiled every call (3N statement compiles on rebuild). Dwarfed by fsync but real; fold into the S3 batching refactor. Leave the dynamically-composed `query` SQL (`index.rs:250`) on `prepare` (cache would churn). **Effort:** Localized.

### S5. `strip_inline_md` O(n²) worst case
**Lanes:** algorithmic   **Location:** `extractor.rs:314-375` (`find_seq_md`/`find_byte_md`)
**Fingerprint:** `algorithmic:extractor.rs:strip_inline_md:on2-delimiter-scan`   **Status:** new
**Problem:** an unmatched opener advances one byte and re-scans the remainder → Θ(k²) on a long literal `*****`/backtick run; inner `find_seq_md` is O(n·m). **Bounded/latent:** reachable only at startup docs-repopulate over ~32 author-controlled pages. **Effort:** Localized (linear substring search via `memchr`/`str::find` + push-literal-on-failed-open).

### S6. Throwaway `Vec<String>` before `join` + double body scan in form extraction
**Lanes:** memory (×2), algorithmic, cost-map   **Location:** `extractor.rs:159-181` (`sniff_form`)
**Fingerprint:** `memory:extractor.rs:sniff_form:throwaway-vec-and-double-scan`   **Status:** new
**Problem:** form messages collect each `Key: value` into a heap `Vec<String>` only to `join`, and walk `body.lines()` twice (FORM-detect then value-collect). Per-message at index time; fuse to one pass + build the output `String` directly. **Effort:** Localized.

## Minor
- **S7** [idiom-currency] `messages_fts` stores a verbatim 2nd copy of every body though the query path never reads it; a `content=''` contentless FTS5 halves FTS storage + per-`upsert` write I/O. `index.rs:68-95`. Fingerprint `idiom-currency:index.rs:messages_fts:duplicated-body`.
- **S8** [data-access] Non-sargable `from`/`to` `LIKE '%x%'` + unused `idx_meta_from` (pure write-tax; `to_addrs` is an unindexed JSON blob). `query.rs:36-43`, `index.rs:99`. Fingerprint `data-access:query.rs:like:non-sargable-from-to`.
- **S9** [data-access, cost-map] Non-sargable `ORDER BY COALESCE(date_received,date_sent)` forces a temp-b-tree sort; both date indexes unused = write-tax. `query.rs:93-96`. Fingerprint `data-access:query.rs:order-by:non-sargable-coalesce`.
- **S10** [concurrency] Blocking per-file disk I/O under the lock; `mbox.list` then `mbox.read` reads each file twice during rebuild. `commands.rs:153-159`. Fingerprint `concurrency:commands.rs:rebuild_index:double-file-read`.

## Cross-cutting theme
**S1+S2+S3(+S4) = "fast, non-blocking rebuild."** The decisive change is: WAL+`synchronous=NORMAL` pragmas, one transaction wrapping the rebuild walk, build-off-lock-then-atomic-swap, and `prepare_cached`. S5/S6 are extraction CPU (minor, bounded). S7/S8/S9 are storage/index write-tax. The query path needs nothing at this corpus size — a good example of the lanes correctly NOT manufacturing query-side findings.

## Measurability
Static + EXPLAIN-QUERY-PLAN reasoning only — building/running `src-tauri` pulls the full Tauri stack (heavier than the standalone DSP crates), so the dynamic lane was deferred. The write-path findings are measurable with a standalone rusqlite harness over a synthetic mailbox (defensible: insert N synthetic messages, time rebuild with/without WAL+single-txn) at fix time.

## Suspected Bugs (for follow-up — NOT addressed here)
- `total_matches = items.len()` is page-capped at ≤200, not the true match count — `commands.rs:53` / `query.rs:107`.
- Unguarded `.unwrap()` on `serde_json::to_string(to_addrs/cc_addrs)` in the upsert hot path — `index.rs:171`.
- `from`/`to` `LIKE '%x%'` over-matches callsigns and matches inside the JSON addr blob — `query.rs:36-43`.
- `strip_inline_md` `bytes[i] as char` mangles multibyte UTF-8 into mojibake — `extractor.rs:360`.
- `sniff_form` FORM detection asymmetry — body uses `strip_prefix("FORM:")`, subject requires `"FORM: "` (with space) — `extractor.rs:159-166`.
- Backend does not cap `page_size` — boundedness relies on the frontend default 200 — `query.rs:107`, `types.rs:64`.

> Kickoff: `docs/perf-audits/2026-06-04-m4-search-bug-hunt-kickoff.md`.
