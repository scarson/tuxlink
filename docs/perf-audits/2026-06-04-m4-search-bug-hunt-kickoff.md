# Bug-hunt kickoff — suspected bugs from the 2026-06-04 M4 (search) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `src-tauri/src/search` — `commands.rs`, `index.rs`, `query.rs`, `extractor.rs`. Noticed during a perf audit; NOT investigated.

**Seed findings (verify, don't trust):**
- `total_matches` is page-capped, not the true count — `commands.rs:53` / `query.rs:107` (UI shows ≤200 even when more match).
- Unguarded `.unwrap()` on `serde_json::to_string(to_addrs/cc_addrs)` in the upsert hot path — `index.rs:171` (panics the indexer on malformed addrs).
- `from`/`to` `LIKE '%x%'` over-matches callsigns + matches inside the JSON addr blob — `query.rs:36-43` (false positives).
- `strip_inline_md` `bytes[i] as char` mangles multibyte UTF-8 → mojibake in the FTS index — `extractor.rs:360`.
- `sniff_form` FORM-detection asymmetry: body `strip_prefix("FORM:")` vs subject `"FORM: "` (space) — `extractor.rs:159-166` (subject `FORM:ICS-213` missed).
- No backend `page_size` cap — relies on the frontend default 200 — `query.rs:107`, `types.rs:64`.

Leads for the hunters, not confirmed bugs.
