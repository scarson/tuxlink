---
run_schema_version: 1
run_id: 2026-06-04T18-30-r10-storage
date: 2026-06-04T18:30:00Z
scope: "R10 — storage/config backend (native_mailbox.rs, config.rs, user_folders.rs, session_log.rs)"
methodology: { skill: performance-audit (REDUCED depth), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "std::fs filesystem mailbox" }]
currency_briefs: []
lanes_run: [data-access, algorithmic, memory]
lanes_skipped: { concurrency: "no shared-state hot path in scope", idiom-currency: "std-only", cost-map: "reduced depth", payload-startup: "n/a", dynamic: "needs a populated mailbox fixture; not run" }
finding_counts: { by_impact: { critical: 1, major: 3, minor: 5 }, by_lane: { data-access: 4, algorithmic: 2, memory: 3 }, suspected_bugs: 1 }
regression: { prev_run_id: null, new: 9, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R10: storage / config backend

**Date:** 2026-06-04 18:30   **Scope:** `src-tauri/src/{native_mailbox.rs,config.rs,user_folders.rs,session_log.rs}`
**Depth:** REDUCED (data-access, algorithmic, memory)
**Regression vs none (first run):** 9 new

> **Cross-unit triangulation.** R10 is the backend root of a user-facing concern
> three independent units circled: the **mailbox list** is slow because the
> backend (`native_mailbox::list`, R10) reads every full message body to render
> headers; the **frontend** (R9) renders that list; and the **search index**
> (M4, SQLite `messages_meta`) ALREADY holds exactly the header columns `list`
> recomputes from disk. The structural fix — serve the list from the index (or a
> header cache) — closes all three.

## Critical findings

### R10-1. `native_mailbox::list` reads + parses every full message body to build a header-only folder view
**Lanes:** data-access (CRITICAL), memory (MAJOR), algorithmic (MINOR)   **Location:** `native_mailbox.rs:93-128 list` + shared `list_dir :432-455`; body discarded by `meta_from_message :497-515`
**Fingerprint:** `data-access:native_mailbox.rs:list:read-amplification`   **Problem:** per folder click/refresh it does `read_dir` + a full `fs::read` of each message file (`:104`) + full `Message::from_bytes` parse (`:105`, which materializes a SECOND owned body `Vec` that's never used) + an unread stat (`:112`) — for every message — then keeps only subject/date/flags. **Cost scales with total folder BYTES**, so a few attachment-bearing messages dominate: megabytes read + parsed to render a list of subjects. No header cache, no index for listing — though the search SQLite holds these exact meta columns (mid/folder/unread). **Impact:** 1 `read_dir` + N full-file reads + N parses (+N stats) per list, transient peak ≈ 2× the largest message. **Confidence:** Strong-static   **Effort:** Contained (header-prefix read up to `\r\n\r\n`) → or Cross-cutting-but-clean (serve list from the M4 index; spec §8 makes FS canonical + index best-effort, so verify parity first). **Verification:** count bytes read + parse calls per list of an N-message folder before/after; identical list output + order.

## Major findings

### R10-2. Folder moves copy the whole body instead of `fs::rename`
**Lanes:** data-access (MAJOR)   **Location:** `native_mailbox.rs:152-161 move_to`, `:385-393 move_between`
**Fingerprint:** `data-access:native_mailbox.rs:move_to:copy-not-rename`   **Problem:** a move does `fs::read` + `fs::write(dst)` + `remove_file(src)` (2× body bytes, 3 syscalls) though src/dst always share one root — `fs::rename` is 0 body bytes, 1 syscall (the module already uses `rename` at `:339`). Archive is a single-key UI shortcut → a 500 KB message = ~1 MB avoidable I/O per click. **Also fixes the data-loss window (SB-R10-1).** **Effort:** Localized. **Verification:** syscall/bytes count per move; message present in exactly one folder after.

### R10-3. Sort comparator re-parses RFC-3339 dates O(n log n) times (missing decorate-sort)
**Lanes:** algorithmic (MAJOR)   **Location:** `native_mailbox.rs:116-126 list`, `:449-453 list_dir`
**Fingerprint:** `algorithmic:native_mailbox.rs:list:comparator-reparse-date`   **Problem:** the `sort_by` closures call `sort_key_from_rfc3339(&a.date)` INSIDE the comparator, so `chrono::parse_from_rfc3339` runs ~2·log₂(n) times per element instead of once (~13× redundant at 100 msgs, ~18× at 500), every folder-open. **Fix:** compute the key once per element, then `sort_by_key` (decorate-sort). **Effort:** Localized. **Verification:** parse-call count; existing order tests cover correctness.

### R10-4. `list` materializes a second unused body `Vec` per message
**Lanes:** memory (MAJOR)   **Location:** `native_mailbox.rs:99-115` → `Message::from_bytes` (`message.rs:176-188`)
**Fingerprint:** `memory:native_mailbox.rs:list:unused-body-vec`   Folds into R10-1's header-only-read fix (parsing the full body to read only headers is the same root). **Effort:** Localized (with R10-1).

## Minor findings
- **R10-5** `save_registry` (`user_folders.rs:182-193`) whole-file rewrite per mutation — negligible at realistic folder counts. Fingerprint `data-access:user_folders.rs:save_registry:full-rewrite`.
- **R10-6** `read_config` (`config.rs:403-417`) no parsed-config cache — full read+parse per call; fine IF startup/settings-only (Med confidence — escalates if any per-message/per-connect path re-reads it; worth confirming the callers). Fingerprint `data-access:config.rs:read_config:no-cache`.
- **R10-7** `list`/`list_dir` duplication (`native_mailbox.rs:93-128` vs `:432-455`) — DRY risk; `list` hand-rolls what `list_dir` shares. Fingerprint `algorithmic:native_mailbox.rs:list:dup-listdir`.
- **R10-8** per-message `header_all` builds a throwaway `Vec<&str>` per To-bearing message (`message.rs:137`). Heap churn × N, not peak. Fingerprint `memory:native_mailbox.rs:list:header_all-churn`.
- **R10-9** `session_log::snapshot` deep-clones the whole ring per call (`session_log.rs:64-69`) — bounded by `cap` (not unbounded), recurs on UI panel re-mount. Fingerprint `memory:session_log.rs:snapshot:deep-clone`.

## Examined and cleared (anti-padding)
- `config.rs write_config_atomic` is **exemplary** for durability (tmp + `sync_all` + atomic persist + parent-dir fsync) — no I/O-correctness issue.
- `session_log.rs` is fully in-memory with a correct `VecDeque` ring (O(1) append/evict, `pop_front` bounded by `cap`).
- `user_folders.rs` CRUD linear scans are bounded-small-n; slug validators correct.

## Suspected Bugs (for follow-up — NOT addressed here)
### SB-R10-1. Non-atomic message move (data-loss / duplicate window)
**Location:** `native_mailbox.rs:160-161`, `:392-393`   **What looks wrong:** `fs::write(dst)` then `fs::remove_file(src)` with no atomicity — a crash between the two leaves the message in BOTH folders (duplicate) or, if write half-completes, a corrupt copy.   **Why suspected:** noticed during the I/O audit; switching to `fs::rename` (the R10-2 fix) makes the move atomic and closes the window.

## Net
R10 carries the tier's standout: a **CRITICAL** list read-amplification with a
clean structural fix (header-only read, or serve from the M4 index) that also
relieves the R9 frontend mailbox cost — plus a `rename`-not-copy move (fixes a
data-loss window) and a decorate-sort. config/session_log are clean. These fold
into a future storage/winlink-tier remediation plan; the cross-unit insight
(index-backed list) is the architectural takeaway.
