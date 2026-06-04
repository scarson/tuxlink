# Search backend — algorithmic complexity & data-structures audit (M4)

Agent: glade-knoll-shoal
Dimension: algorithmic complexity & data structures ONLY
Scope: `src-tauri/src/search/{extractor.rs,query.rs,types.rs,saved.rs,mod.rs}`
Date: 2026-06-04

Load model used for calibration:
- `extract()` / `sniff_form()` / `parse_winlink_date()` run once per message at index time
  (per-receipt or bulk re-index over dozens–hundreds of messages). Bodies are KB-scale.
- `extract_markdown()` runs over the ~32 bundled doc topics at startup, **only when the
  bundled-slug set drifts from the index** (`mod.rs:80`). Each topic's markdown is a full
  user-guide page (multi-KB).
- `compose()` (query.rs) runs once per interactive search; inputs are small (a handful of
  filters). Result post-processing operates on a bounded page (`page_size` default 200).

---

### [MAJOR] `strip_inline_md` has an O(n²) worst case on pathological/degenerate inline runs
**Location:** `extractor.rs:314-375` (the `strip_inline_md` loop), via `find_seq_md` (`369-375`)
and `find_byte_md` (`366-368`).

**Problem:** The inline stripper is a byte-cursor loop. At each position it may probe for a
closing delimiter by scanning the *rest of the line*:

- Bold (`extractor.rs:334-341`): when `bytes[i..i+2] == "**"`, it calls
  `find_seq_md(&bytes[i+2..], b"**")`, which is itself an O(n·m) hand-rolled substring scan
  (`for i in 0..=len-2 { if &s[i..i+2]==seq }`). If the `**` has **no** matching close, the
  scan walks to end-of-line and returns `None`, the cursor advances by one byte, and the next
  `*`/`**` re-scans almost the whole remainder. A line of the form `"**********…"` (k asterisks,
  no closer) costs Θ(k²): each of ~k/2 cursor positions triggers an O(k) `find_seq_md`.
- Inline code (`343-350`) and italic (`352-359`) have the same shape via `find_byte_md`: an
  unmatched `` ` `` or boundary-`_` triggers a full-remainder scan at each subsequent eligible
  position. A backtick-dense line (`` `a`b`c`… `` with a dangling final backtick) or an
  underscore-after-space-dense line repeats the tail scan.

So a single line is O(L²) in its own length in the degenerate case, and `extract_markdown`
calls `strip_inline_md` per line. The common case (well-formed markdown, delimiters close
quickly) is fine — `find_*` returns early — which is why this is MAJOR not CRITICAL.

**Impact:** reachability = docs-index repopulation at startup on slug drift (`mod.rs:80-84`);
frequency = ~32 topics × all their lines, once per drift event (not per search). Per-occurrence
cost is benign for normal prose but quadratic-per-line for any line with a long unbalanced
delimiter run. User-guide markdown is author-controlled and well-formed, so the realistic blast
radius is small; the finding is the latent complexity, not a measured hot-path stall. A single
long line of literal asterisks or backticks (e.g. an ASCII rule `*****…`, a code sample, a
table separator) is the trigger that turns a startup repopulate into a visible hitch.

**Confidence:** Strong-static (complexity argument is concrete; trigger requires a degenerate
line that may or may not exist in the current bundled docs).

**Effort:** Contained (+low). Two independent fixes:
1. Replace `find_seq_md` with `memchr::memmem` / slice `windows().position()` or std
   `str::find` on the `&str` tail (the input is already valid UTF-8 here for docs) — turns the
   inner substring search from O(n·m) into O(n).
2. The outer O(n²)-on-degenerate behavior is inherent to "rescan from each position after a
   failed open." Cap it: when an open delimiter has no close on the line, push the literal
   delimiter byte(s) and advance past them rather than leaving the cursor to re-probe from the
   next eligible position. That makes a failed-open cost O(remainder) once instead of per
   position.

**Verification plan:** micro-benchmark `extract_markdown` on (a) a 50-KB well-formed page and
(b) a 50-KB line of `*`/`` ` ``/`_`; assert (b) drops from quadratic to linear after the fix.
Correctness guard: the existing `markdown_tests` (`extractor.rs:377-425`) must still pass; add a
case for an unbalanced `**` and a long-asterisk line asserting the literal text is preserved.

---

### [MINOR] `sniff_form` scans the body twice (two `body.lines()` passes)
**Location:** `extractor.rs:157-182`.

**Problem:** First pass (`159-166`) iterates `body.lines()` via `find_map` to detect the
`FORM:` line; second pass (`173-178`) iterates `body.lines()` *again* to collect every
`Key: value` line into `values`. The body is re-split line-by-line twice. For non-form messages
the first pass returns early at line 1 and the second pass never runs (`167-169` short-circuits),
so the cost only doubles for actual form payloads. `String::lines()` is itself a linear scan, so
this is 2× linear, not quadratic.

**Impact:** runs once per message at index time, body is KB-scale, and only form messages pay
the double scan. The extra pass is a modest constant factor on bulk re-index of a form-heavy
mailbox — real but small. Calibrated as MINOR.

**Confidence:** Strong-static.

**Effort:** Localized (+low). A single pass can both detect the `FORM:` marker and accumulate
`Key: value` lines: iterate `body.lines()` once, branch on `strip_prefix("FORM:")` for the
type, else `split_once(':')` for a value. The subject-fallback for `form_type` (`162-166`) stays
outside the loop.

**Verification plan:** complexity argument (2n → n line traversals). Correctness guard: the
`extracts_form_payload_into_form_field_values` test (`extractor.rs:230-250`) plus a non-form
message must produce byte-identical `form_field_values`. Note the existing two-pass code skips
the `FORM:` line in pass 2 via `!l.starts_with("FORM:")`; a single-pass rewrite must preserve
that skip (the `FORM:` line is consumed for type, not emitted as a value).

---

### [MINOR] `find_seq_md` is an O(n·m) hand-rolled substring search where std/`memchr` is O(n)
**Location:** `extractor.rs:369-375`.

**Problem:** `find_seq_md` does the naive `for i in 0..=len-m { if &s[i..i+m]==seq }` substring
match. For the only caller (`seq == b"**"`, m=2) the multiplier is tiny, so in isolation this is
a cold micro-op. It is called out separately from the MAJOR finding because it is the *inner*
cost that compounds the outer quadratic above: fixing it to `str::find` / `memchr::memmem`
removes one of the two `n` factors in the degenerate case and is a trivially-correct swap.

**Impact:** subsumed by the MAJOR finding's blast radius; on its own (well-formed docs, m=2)
negligible. Listed so the fix is not overlooked when addressing the MAJOR item.

**Confidence:** Strong-static.

**Effort:** Localized (+low). Replace the body with `s.windows(seq.len()).position(|w| w == seq)`
(std, vectorizable) or `memchr::memmem::find` (O(n); verify it's already a transitive dep with
`cargo tree`).

**Verification plan:** unit-equivalence on random inputs vs. the current implementation; the
markdown tests guard end-to-end behavior.

---

## Examined and explicitly NOT findings

- **`parse_winlink_date` (`extractor.rs:126-152`)** — `split_once`/`split('/')`/`parse` over a
  ~16-char date header; one tiny `Vec<&str>` of 3 elements. `days_from_civil` is pure arithmetic.
  No scan over the body, no per-call recompiled work. Bounded-tiny-n; not a finding.
- **`compose` (`query.rs:11-112`)** — builds SQL by pushing into two small `Vec<String>` and one
  `format!` at the end. Filter count is a handful (≤9, BTreeMap-bounded by `FilterKey` variant
  count). `where_clauses.join` and the final `format!` are linear in clause count. No quadratic,
  no per-call regex, nothing hoistable. The `format!("%{}%", a)` LIKE-wrapping allocates per
  filter but that is one small alloc per active filter, once per search — not a finding.
- **No regex anywhere in scope** — the markdown stripper and form sniffer are hand-rolled byte
  loops, not regex. So the "regex compiled per call instead of `OnceLock`/`Lazy`" lens item has
  no target here. (If a future rewrite reaches for `regex`, that's where `OnceLock` would apply.)
- **`saved.rs`** — `record_recent` (`139-148`) does `retain` (O(n)) + `insert(0,…)` (O(n) shift)
  + `truncate`, with `RECENT_CAP = 20`. `reorder` (`125-137`) is O(n·m) `find` per id but the
  saved-search list is human-curated (single digits). `save`'s `max()` order scan is O(n) over
  the same tiny list. All bounded-tiny-n with a disk `flush()` dominating each call; the in-Rust
  work is noise next to the JSON serialize + `fs::write`. Not findings.
- **`extract` header collection (`extractor.rs:64-99`)** — each `msg.header(...)` /
  `header_all(...)` is an O(headers) linear scan of the header vec (see
  `winlink/message.rs:128-143`), and `extract` calls them ~8 times, so header lookup is
  O(8·H). Header counts are small (single/low-double digits) and this is the message layer's
  container choice, not the search lane's. Out-of-dimension/bounded; noted, not filed.
- **Result post-processing / page assembly** — page is bounded by `page_size` (default 200,
  `types.rs:64-68`); any per-result work is over a bounded-small set. Per the brief, a quadratic
  over the result set would not be a finding here, and none is present in scope anyway.

---

## Suspected Bugs

(Out-of-dimension; recorded per instructions, not chased.)

- **`strip_inline_md` mangles non-ASCII via `bytes[i] as char` (`extractor.rs:360`).** The
  fallthrough `out.push(bytes[i] as char)` casts a single raw byte to `char`, which is only
  correct for ASCII. Any multi-byte UTF-8 sequence (or Latin-1 ≥0x80) in markdown body text is
  pushed byte-by-byte as `as char`, producing mojibake (each continuation byte becomes a
  `U+0080`-range scalar). The slice indexing in the delimiter branches
  (`input[i+1..i+1+close]`, etc.) is byte-indexed; the ASCII delimiter bytes (`[`,`` ` ``,`*`,
  `_`) never appear inside a valid UTF-8 continuation byte, so the panic risk is low, but the
  `as char` corruption is a real silent defect. A char-based or UTF-8-aware iteration is the fix.
- **`sniff_form` body-vs-subject FORM detection asymmetry (`extractor.rs:159-166`).** Body match
  uses `strip_prefix("FORM:")` (no space required); subject fallback uses
  `strip_prefix("FORM: ")` (space required). A subject `"FORM:ICS-213"` (no space) is not
  detected while the same text in the body is. Cosmetic/consistency, not perf.
