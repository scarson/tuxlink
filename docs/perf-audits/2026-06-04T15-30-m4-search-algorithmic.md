# Performance Audit — Search backend, dimension: algorithmic complexity & data structures

**Agent:** glade-knoll-shoal
**Scope:** `/home/user/tuxlink/src-tauri/src/search/{extractor.rs,query.rs,types.rs,saved.rs,mod.rs}`
**Stack:** Rust 2021, rusqlite 0.40.
**Load profile:** extraction once per message on index (per-receipt or bulk re-index over dozens–hundreds of messages, KB-scale bodies); query construction once per interactive search; markdown extraction over ~32 bundled docs at startup.
**Lens prior:** `profile-packs/rust.md` (algorithmic lane).

This report covers ONLY accidental-quadratic / repeated-scan / wrong-container / hoistable-recomputation concerns in the in-Rust text processing. SQL is out of scope. No praise, no grade.

---

### [MAJOR] `strip_inline_md` is worst-case O(n²) per line on unmatched inline-markdown delimiters

**Location:** `extractor.rs:314-375` (`strip_inline_md`, with helpers `find_byte_md` :366 and `find_seq_md` :369).

**Problem:** The inline-strip loop advances one byte at a time, and at each `i` it speculatively probes for a closing delimiter:
- `[` → `find_byte_md(&bytes[i..], b']')` — scans to end of line (`:321`).
- `**` → `find_seq_md(&bytes[i+2..], b"**")` — scans to end of line (`:335`).
- `` ` `` → `find_byte_md(&bytes[i+1..], b'`')` (`:344`).
- `_` at a word boundary → `find_byte_md(&bytes[i+1..], b'_')` (`:353`).

When the open delimiter has **no** matching close, the probe scans the entire remaining line, the branch falls through, `i` advances by exactly 1, and the *next* position re-scans the whole remaining line again. A line of `k` unmatched `[` (or `` ` ``, or `_`, or `**`) characters performs Θ(k) work at each of `k` positions → **Θ(k²)**. `find_seq_md` (`:369-375`) is itself a hand-rolled substring search (no `memchr`/`windows`-with-SIMD), so the `**` path is the most expensive constant.

The matched/well-formed case is fine (the probe jump-advances `i` past the closing delimiter, amortizing to O(n)). The quadratic is specifically the **unmatched-delimiter** path. Markdown headed for FTS routinely contains stray `_` (snake_case identifiers — note the boundary guard at `:352` deliberately *lets* lone `_` fall through), stray `` ` ``, and stray `[` in prose, so unmatched delimiters are the common case, not an adversarial edge.

**Impact:** reachability = `extract_markdown` → `strip_inline_md` is called per non-fenced line of every bundled doc at startup (`docs_index.rs:56`, ~32 topics) AND is the markdown path for FTS ingestion generally. frequency = per line, per doc. per-occurrence cost = Θ(line_len²) on delimiter-dense lines. Bodies are KB-scale and lines are short (tens–low-hundreds of bytes), so a single line's k² is bounded and absolute cost is modest at current scale — but the structure is a genuine accidental quadratic that degrades non-linearly if doc lines grow (code blocks with long token runs, pasted tables, base64-ish content). This is the single most structurally-wrong algorithm in scope.

**Confidence:** Strong-static (the re-scan-then-advance-by-1 pattern is directly readable; quadratic is provable by construction).

**Effort:** Contained (+low). Two independent mitigations, either sufficient:
1. Make each probe bound its scan to a reasonable window OR, cleaner, restructure so a failed match advances `i` by 1 *without* having scanned the whole tail — e.g. only attempt the close-search lazily and cache "no close after position p for delimiter d" so subsequent positions don't re-scan. Simplest correct fix: when a delimiter has no close in the remainder, you know none of the intervening bytes can open a *matched* span of that delimiter either, so you can copy through to end-of-line in one shot.
2. Replace `find_byte_md` with `memchr` and `find_seq_md` with `memchr::memmem` — turns each probe into a SIMD scan; doesn't fix the asymptotics but slashes the constant.

**Verification plan:** Complexity argument above. Micro-benchmark (criterion, release build) with inputs of increasing `k`: `"[".repeat(k)`, `"`".repeat(k)`, `"**".repeat(k)`, `"_ ".repeat(k)` for k ∈ {64,256,1024,4096}; current code shows super-linear slope, fixed code shows linear. Correctness guard: existing `markdown_tests` plus a snapshot over the real bundled topics (`BUNDLED_TOPICS`) — output must be byte-identical before/after.

---

### [MINOR] `sniff_form` walks `body.lines()` twice

**Location:** `extractor.rs:157-182` (`sniff_form`). First pass `:159-161` (`body.lines().find_map(...)` to detect the `FORM:` line); second pass `:173-178` (`body.lines().filter(...).filter_map(...).collect()` to harvest field values).

**Problem:** Two independent iterations over the same body. The first finds the form-type; the second re-splits every line of the body to collect `Key: value` payloads. A single pass could detect-and-collect together (fold over `lines()`: on the first `FORM:`-prefixed line set `form_type` and `continue`; on every other line attempt the `split_once(':')` harvest). Additionally, the second pass calls `l.starts_with("FORM:")` (`:175`) on every line to re-exclude the form line it already located in pass 1 — redundant work that a single pass eliminates by construction.

**Impact:** reachability = only the form-message path (subject/body begins `FORM:`); non-form messages early-return at `:167-169` after a single `find_map` pass, so the common case is already one pass. frequency = per form message on index. per-occurrence cost = one extra full `lines()` split over a KB body. At dozens–hundreds of messages with a minority being forms, the extra pass is real but small; this is a tidy single-pass win, not a hot-path emergency.

**Confidence:** Strong-static.

**Effort:** Localized (+low).

**Verification plan:** Complexity argument (2 passes → 1). Correctness guard: `extracts_form_payload_into_form_field_values` (`:230`) plus a fixture where a non-`FORM:` line itself contains the substring `FORM:` mid-line, to confirm the single-pass exclusion still only skips the *detected* form line, not any line containing `FORM:`. (Note: pass-2's `starts_with("FORM:")` at `:175` and pass-1's `strip_prefix("FORM:")` at `:160` use slightly different anchoring than the subject branch's `"FORM: "` with trailing space — a single pass should preserve whatever the current line-classification is; see Suspected Bugs.)

---

### [MINOR] `record_recent` rebuilds the recent `Vec` and serializes the whole store on every search

**Location:** `saved.rs:139-148` (`record_recent`), and the shared `flush` at `:74-81`.

**Problem:** Each recorded search does `self.file.recent.retain(...)` (O(n) shift-compaction) then `insert(0, ...)` (O(n) shift — every element moves up one slot). `RECENT_CAP = 20` (`:13`) bounds n at 20, so the per-call cost is trivially small. The structurally-cheaper shape for a bounded most-recent-first ring is a `VecDeque` with `push_front` + `pop_back` (O(1) each) and a linear dedup scan — but at n≤20 the difference is immaterial. The dominant cost in `record_recent` is not the container ops at all: it's `flush` (`:147` → `:74-81`) doing `serde_json::to_string_pretty` over the **entire** `SavedStoreFile` (all saved searches + all 20 recent entries) and a full file rewrite on every single search execution. That serialize+write is the real per-search cost, and it scales with total store size, not with the one entry changed.

**Impact:** reachability = once per interactive search (record_recent is on the search-commit path). frequency = interactive (human-paced), so even a full JSON rewrite per search is comfortably absorbed. per-occurrence cost = full-store reserialize + fsync-class write. Flagging for completeness within the dimension (wrong-container + redundant full recompute), but at human interaction frequency and n≤20 this is **not** an actionable perf finding — calibrated as MINOR/borderline-noise. No change recommended unless the store grows unbounded (it doesn't: recent is capped, saved is user-managed).

**Confidence:** Strong-static.

**Effort:** Localized — but recommend **no action** at current load.

**Verification plan:** N/A (below the action threshold). If ever revisited: criterion over `record_recent` with a store of {1,20,100} saved entries to confirm flush dominates; only then consider `VecDeque` and/or incremental persistence.

---

## Items examined and explicitly cleared (not findings)

- **`extract` (`extractor.rs:57-122`)** — straight-line field extraction, each header read once. `header`/`header_all` (`message.rs:128-143`) are linear scans over a `Vec<(String,String)>` of headers, but header count is tiny (single digits) and each is looked up O(1) times; no loop-nested header lookup. `body.len()` for `message_size` (`:100`) reuses the already-decoded `body` String — no re-walk. Clean.
- **`parse_winlink_date` (`:126-141`) + `days_from_civil` (`:144-152`)** — fixed-shape parse, `split_once` / `split('/')` over a short date string, constant arithmetic. O(1). No regex (good — no per-call regex compile anywhere in scope, so the "regex compiled per call / needs `OnceLock`" lane signal does not apply; the markdown and date paths are hand-rolled byte scans, not regex). Clean.
- **`extract_markdown` outer loop (`:278-309`)** — single pass over `md.lines()`, `String::with_capacity(md.len())` pre-sized (`:279`), `push_str` growth amortized. The trailing `while out.ends_with('\n') { out.pop() }` (`:307`) is O(trailing-newlines), bounded. The only sub-linear hazard is the per-line `strip_inline_md` call (see MAJOR above). The `while s.starts_with('#') { s = &s[1..] }` heading strip (`:295`) is O(heading-markers), bounded. Outer loop itself is clean.
- **`query::compose` (`query.rs:11-112`)** — iterates `spec.filters` (a `BTreeMap`, bounded by the fixed `FilterKey` variant count, ≤9) once, building `where_clauses` and `params` via `push`. `where_clauses.join(" AND ")` and the final `format!` are single-pass over a tiny vec. No quadratic, no re-split, correct container (BTreeMap chosen for deterministic serialization per types.rs:77). Clean.
- **`SavedStore::{save,unsave,rename,reorder,promote_recent}` (`saved.rs:83-164`)** — linear `iter`/`find`/`retain` over `saved`, which is user-curated and small. `reorder` (`:125-137`) does a `find` per id (O(n) each → O(n²) over n ids) then a sort, but n = number of saved searches the user manually maintains (handful); not a realistic-load quadratic. Cleared.
- **Hashers / `HashSet` usage (`mod.rs:72-80`)** — `bundled_slugs` vs `indexed_slugs_set` set-diff at startup over ~32 slugs with default SipHash. n tiny, startup-once. The "FxHashMap/ahash" lane signal is a non-issue at this n. Clean.

---

## Suspected Bugs (correctness — recorded, not chased)

1. **`strip_inline_md` corrupts multi-byte UTF-8 via `bytes[i] as char` (`extractor.rs:360`).** The fallthrough copy does `out.push(bytes[i] as char)`, which casts a single raw byte to a `char`. For any non-ASCII byte (UTF-8 continuation or lead byte) this produces a Latin-1-style mojibake codepoint instead of the original character — e.g. the U+2503 separator, accented callsign text, or any UTF-8 body content passing through the markdown stripper is mangled. Bodies are decoded `from_utf8_lossy` (`:78`), so multi-byte content is reachable. The slice-based span copies (`out.push_str(text)` at `:326`, `:338`, etc.) are UTF-8-safe; only the byte-at-a-time fallthrough is wrong. (Out of my dimension's remit to fix — noting per instructions. Relevant to the MAJOR fix: a `memchr`/char-based rewrite would incidentally resolve this.)

2. **`sniff_form` form-line classification is inconsistent across its two passes and with the subject branch.** Pass 1 detects via `strip_prefix("FORM:")` (`:160`, no required space) on body lines; the subject fallback requires `strip_prefix("FORM: ")` (`:164`, with space). Pass 2 excludes lines via `starts_with("FORM:")` (`:175`). A body line like `FORM:ICS-213` (no space) is detected as the form line in pass 1 AND excluded in pass 2 — consistent. But a *value* line whose key is literally `FORM` (e.g. `FORM: see attached` appearing a second time deeper in the body) would be silently dropped from `form_field_values` by pass-2's `starts_with("FORM:")` filter even though it's payload, not the header. Low-severity / data-shape-dependent; flagging because the single-pass MAJOR refactor must preserve whatever the intended classification is rather than accidentally changing it.
