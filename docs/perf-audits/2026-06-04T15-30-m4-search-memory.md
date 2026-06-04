# Perf Audit M4 — Search backend: Memory & Allocation dimension

**Agent:** glade-knoll-shoal
**Scope:** `src-tauri/src/search/{extractor,index,query,docs_index,docs_bundle,types,saved,commands,mod}.rs`
**Dimension:** avoidable allocations / copies — text extraction, docs-bundle materialization, result-row materialization, per-row index-loop allocations.
**Load model:** index = per-receipt or bulk re-index over dozens–hundreds of KB-scale messages; query = bounded small result sets; docs populate = ~32 compiled-in topics, once at startup.
**Lens prior:** `profile-packs/rust.md` "Memory & allocation" + "Runtime & build notes."

Findings ranked by Impact = reachability × frequency × per-occurrence cost, calibrated to the modest stated load. No praise, no grade.

---

### [MAJOR] `form_field_values` collected into a throwaway `Vec<String>` before `join`

**Location:** `extractor.rs:173-181` (`sniff_form`)
**Problem:** Every form-bearing message body is iterated, each payload line's value `.trim().to_string()`-ed into a `Vec<String>`, the whole `Vec` heap-allocated and grown by repeated push (no `with_capacity`), then immediately consumed by `values.join(" \u{2503} ")` — which itself allocates the final `String`. The intermediate `Vec<String>` (one heap allocation per value-string + one for the Vec backbone) is pure waste: `join` only needs to walk the items once. This is the only per-value allocation cluster on the index hot path for forms.
**Impact:** Per-message (index) cost, multiplied by the number of `Key: value` lines in each form body. ICS-213-class forms have a dozen-plus fields, so this is ~12+ short-`String` allocations + 1 `Vec` allocation discarded per indexed form message. Over a bulk re-index of hundreds of form messages it is the dominant avoidable-allocation site in `extractor`. Bodies are KB-scale so each string is small, but the allocation *count* is what matters here.
**Confidence:** Strong-static — the `collect::<Vec<String>>()` then `.join()` pattern is exactly the profile-pack "collecting an iterator into a Vec only to immediately iterate" anti-pattern.
**Effort:** Localized (+low). Build the output `String` directly: iterate the filtered lines, push the separator before all but the first, `push_str(v.trim())`. Optionally `String::with_capacity(body.len())` as an upper bound. No `Vec<String>`, no per-value `to_string()`.
**Verification plan:** `dhat` alloc-count around `extract()` on an ICS-213 fixture before/after; expect allocation count to drop by ~(#fields + 1). Correctness guard: existing `extracts_form_payload_into_form_field_values` test (`extractor.rs:230`) pins the output shape — the separator-join must produce byte-identical output.

---

### [MAJOR] Body double-scanned + `to_string()` on every form-type match in `sniff_form`

**Location:** `extractor.rs:159-178`
**Problem:** Two independent full passes over `body.lines()`: once to find the `FORM:` line (`find_map` + `.trim().to_string()`), then again to collect values. Each pass re-walks the entire body string. The `form_type` `to_string()` (line 161/165) is unavoidable (it lands in the owned `IndexRow`), but the *second full body scan* (line 173) re-tokenizes the whole body a second time. For a KB-scale body this is a redundant O(body) linear walk per indexed form message on top of the value-collection walk.
**Impact:** Per-message (index), form bodies only. Not an allocation per se but a redundant whole-body scan; combined with the finding above it means form bodies are walked by `.lines()` twice plus once more by the caller's `body.len()`. Modest at the stated load but trivially foldable into a single pass.
**Confidence:** Strong-static.
**Effort:** Localized (+low). A single pass can both detect the `FORM:` prefix and accumulate values; or keep two passes but at least fuse the value-collection with the join (see prior finding) so only one allocation results.
**Verification plan:** Same fixture/test as above; argument-based (one `.lines()` walk vs two). Correctness guard: the "body wins over subject" precedence and the FORM-line-skip behavior must be preserved — pin with the existing test.

---

### [MINOR] `String::from_utf8_lossy(...).into_owned()` always allocates even for valid-UTF-8 bodies

**Location:** `extractor.rs:78`
**Problem:** `into_owned()` on the `Cow` returned by `from_utf8_lossy` forces an owned `String`. For the common case (body is already valid UTF-8) the `Cow` is `Borrowed` and `into_owned()` performs a fresh allocation + full copy of the body that a borrow would have avoided — *if* the downstream needed only a borrow. Here `body` is consumed by `sniff_form(&subject, &body)` (borrow) and `message_size = body.len()` (borrow), and only *then* moved into `IndexRow.body`. Since `IndexRow` owns `body: String`, an owned copy is genuinely required at the struct boundary, so this allocation is *mostly* load-bearing. The avoidable part: the body is materialized as `String` up front, then `sniff_form` borrows it — fine — but there is no second copy, so this is the floor cost, not waste. Flagged MINOR only because it is the single largest per-message allocation (whole body) and worth noting it is unavoidable given `IndexRow` ownership; do **not** "optimize" it into a borrow without restructuring `IndexRow` to hold `Cow`/`&str` (which the upsert lifetime would fight).
**Impact:** Per-message (index): one body-sized allocation+copy. Unavoidable at current `IndexRow` design. Noted for completeness so a future pass doesn't mistake it for free.
**Confidence:** Strong-static.
**Effort:** Cross-cutting (+high) to eliminate (would require `IndexRow<'a>` borrowing from `Message`); not worth it at this load. **Recommend no change.**
**Verification plan:** N/A — argument: the owned body is required by `upsert`'s `params![... row.body ...]` binding and the struct's `pub body: String`.

---

### [MINOR] `folder_str` built via `match` arm `&'static str` then `.to_string()`

**Location:** `extractor.rs:80-86`
**Problem:** The folder name is a `&'static str` from the match, immediately `.to_string()`-ed into an owned `String` for `IndexRow.folder`. The owned `String` is required by the struct, so the allocation is load-bearing — but note `Direction::as_str` (line 45) already exposes the same `&'static str` pattern for direction and is bound directly into SQL via `row.direction.as_str()` (`index.rs:178`) *without* owning it. `folder` could follow the same pattern if `IndexRow.folder` were a small enum + `as_str()` rather than `String`, eliminating one per-message allocation. Low value at this load.
**Impact:** Per-message (index): one tiny (≤7 byte) `String` allocation. SSO does not apply (Rust `String` always heap-allocates; there is no small-string optimization in std). So this is a real, if tiny, per-message heap allocation.
**Confidence:** Strong-static.
**Effort:** Contained (+low) — would change `IndexRow.folder` type and the upsert binding. Marginal benefit; folder is also re-stored as a `String` column, so an enum would need `as_str()` at the bind site only.
**Verification plan:** `dhat` count on `extract()`; expect −1 small allocation. Correctness: folder string values feeding `messages_meta.folder` / `messages_fts.folder` must be byte-identical ("inbox"/"outbox"/"sent"/"archive").

---

### [MINOR] Query param marshalling clones every `Text` param then re-collects `&dyn ToSql`

**Location:** `index.rs:251-260`
**Problem:** `compose` returns `Vec<SqlParam>` (owned). `query()` then maps each into `rusqlite::types::Value`, cloning every `SqlParam::Text(s)` via `s.clone()` (line 254) into a fresh `Value::Text(String)`, collects that into `rs: Vec<Value>`, then builds a *second* `Vec<&dyn ToSql>` of references (line 259-260). Two `Vec`s plus a clone of each text param. Params are few (one per active filter, bounded small) so the absolute cost is tiny, but the `s.clone()` is avoidable: `SqlParam` is owned and discarded right after, so the `String` could be *moved* into `Value::Text` rather than cloned.
**Impact:** Per-query, bounded small (handful of filter params). Negligible at this load; flagged for the avoidable `clone` only.
**Confidence:** Strong-static.
**Effort:** Localized (+low). Consume `params` by value (`into_iter()`) and move the `String` into `Value::Text(s)` instead of `s.clone()`. The two-Vec ref-collection is required by rusqlite's `&[&dyn ToSql]` API and is not avoidable without `params_from_iter`.
**Verification plan:** Argument-based (move vs clone); `query_integration` tests in `index.rs` guard correctness.

---

### [MINOR] `From`/`To` filter builds a `format!("%{}%", a)` per query

**Location:** `query.rs:37, 41`
**Problem:** `format!` allocates a `String` for the LIKE pattern. Per-query, at most twice (From + To filters). Profile-pack flags `format!` on hot paths, but this is per-query with a bounded small N and the allocation is genuinely needed (the `%...%` wrapped pattern must be an owned bound param). Not avoidable; noted only to confirm it was examined and is acceptable.
**Impact:** Per-query, ≤2 small allocations. Acceptable.
**Confidence:** Strong-static. **Recommend no change.**
**Verification plan:** N/A.

---

### docs_bundle.rs — examined, no finding

**Location:** `docs_bundle.rs:14-175`, `docs_index.rs:52-64`
The audit brief flags "reading whole docs bundles into memory." In fact the docs bundle is **`include_str!`-compiled into the binary** as `&'static str` — it is in the read-only data segment, **not** heap-allocated at runtime and not "read into memory" in the I/O sense. `populate_docs` (`docs_index.rs:52`) iterates the 32 static topics once at startup, calling `extract_markdown(t.markdown)` per topic — each produces one owned `String` (necessary, it is the stripped body bound into the INSERT) which is dropped after the bound execute. This is a **one-time build/startup cost** over ~32 small docs, inside a single transaction. No per-message or per-query reachability. `extract_markdown` pre-sizes its output `String::with_capacity(md.len())` (`extractor.rs:279`) and `strip_inline_md` likewise (`extractor.rs:316`) — already following the profile-pack pre-size guidance. The one inefficiency (`strip_inline_md` returns a fresh `String` per line even when the line has no inline markup) is negligible at startup-only, ~32-topic frequency. **No actionable finding.**

---

### saved.rs — examined, no finding

`SavedStore` reads/writes the whole JSON file on every mutation (`flush`, `saved.rs:74-81`) and `record_recent` does a `retain` + `insert(0, …)` (`saved.rs:142-143`, O(n) shift) capped at `RECENT_CAP = 20`. Both are user-action-frequency (save/promote/record on explicit commit), not hot-path, over ≤20 recent + a handful of saved entries. `list_saved`/`list_recent` `.to_vec()` (`commands.rs:68, 72`) clone the small bounded lists per command call — acceptable at this load. **No actionable finding within the memory/allocation dimension at the stated load.**

---

### index.rs upsert — examined, two notes

1. `serde_json::to_string(&row.to_addrs)` / `cc_addrs` (`index.rs:171-172`) allocate a JSON `String` per upsert per address-list. Per-message (index) cost, but the serialized form is what gets stored in the TEXT column — genuinely required. Address lists are tiny. **No finding** (the round-trip back through `serde_json::from_str` at `index.rs:268-270` is likewise required). Note: `.unwrap()` on the to_string is a panic risk (correctness — see Suspected Bugs), not a perf issue.
2. `upsert` runs `DELETE` then `INSERT` on the FTS side per message (`index.rs:130-138`); that is an FTS5 API constraint (no upsert), not an allocation concern.

---

### commands.rs result assembly — examined, no finding

`run()` does `hits.into_iter().map(hit_to_dto).collect()` (`commands.rs:53`). `hit_to_dto` (`commands.rs:278`) **moves** every owned field out of `QueryHit` into `MessageMetaDto` (`id: h.mid`, `to: h.to_addrs`, etc.) — no clones, fields are moved. This is the correct, allocation-minimal pattern. The result set is bounded small (`page_size` default 200). The brief's hypothesized "cloning full bodies into result structs when snippets suffice" **does not occur**: `QueryHit`/`MessageMetaDto` carry no body field — the SELECT (`query.rs:103-106`) never projects `body`, only `message_size`. The message-side query is already body-free; only `docs_index::search_docs` materializes text, and it uses FTS5 `snippet()` (`docs_index.rs:78`) rather than the full body. **The "snippet suffices" design is already implemented correctly.** No finding.

---

## Suspected Bugs (out of dimension — recorded, not chased)

1. **`index.rs:171-172` — `serde_json::to_string(...).unwrap()`** on `to_addrs`/`cc_addrs` will panic the upsert if serialization ever fails. Serializing `Vec<String>` effectively never fails, so low real risk, but `.unwrap()` in a per-message index path is a latent panic. (Correctness/robustness, not perf.)

2. **`query.rs:107-108` — `LIMIT {} OFFSET {}` embed `page_size`/`offset` as literals.** The doc-comment justifies this as safe because they are `u32` (no string injection). True for injection, but note there is no upper bound on `page_size` — a caller passing `page_size = u32::MAX` would request an unbounded result materialization. The brief assumes "bounded small result sets"; that boundedness is enforced by the frontend default (`PageRequest::default` = 200, `types.rs:64`), not by the backend. Out of memory-dimension scope as a *current* concern but worth noting the backend itself does not cap the page size.

No other suspected bugs.
