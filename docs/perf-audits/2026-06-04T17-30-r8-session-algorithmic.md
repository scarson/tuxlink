# R8 — Winlink B2F session driver: algorithmic complexity & data structures

Agent: glade-knoll-shoal. READ-ONLY audit. Dimension: algorithmic complexity & data
structures only. Frequency calibration per W0: `run_exchange` runs once per Winlink
connect; `send_turn`/`receive_turn` run per message-batch within a session; sessions
are infrequent and message counts modest (handful–dozens). Impact is therefore
low-frequency-calibrated: only super-linear behavior over *message bytes* on the
per-message path qualifies; quadratics over the bounded proposal/answer batch
(`MAX_BATCH = 5`) do not.

Scope read in full: `session.rs`, `handshake.rs`, `credentials.rs`, `secure.rs`,
`mod.rs`, plus `wire.rs` (the per-line primitive these call).

---

## Findings

### [MINOR] `wire::read_line` allocates a fresh `Vec` per protocol line read

Location: `src-tauri/src/winlink/wire.rs:12-22`, called from `session.rs:412`
(`receive_turn` proposal loop), `session.rs:359-374` (`send_turn` answer loop), and
the handshake reader.

Problem: each `read_line` allocates a new `buf: Vec<u8>` via `read_until`, then a
`String::from_utf8_lossy` (which may allocate again), then `.to_string()` on the
cleaned `&str` — three potential allocations per line. In `receive_turn` this is one
call per proposal line, so O(lines) allocations, not amortized into a reusable buffer.

Impact: LOW. Lines are short (≤~80 bytes) and the line *count* per session is small
(a few proposals + framing lines per batch, a handful of batches). This is not a
quadratic and not a per-message-byte cost — the message bodies are read by
`transfer::read_block` (out of scope), not by `read_line`. Pure allocation-count, not
complexity-class; it never compounds with message size. Noted only as the one real
container/allocation observation in scope; not worth changing on its own.

Confidence: HIGH (the code is plainly per-line allocating).
Effort: LOW (thread a reusable `&mut Vec<u8>` workhorse buffer through `read_line`),
but the win is negligible at this frequency — do not bother absent a profiler signal.
Verification: count allocations under a scripted multi-batch session with `dhat`;
confirm it scales with line count, not message bytes (it will), confirming LOW impact.

---

## Items explicitly examined and cleared (NOT findings)

- **`session.rs:425` `for b in line.bytes()` checksum accumulation.** This is the
  byte-sum of a single proposal *line* (≤~80 bytes), run once per proposal as it is
  read. It is O(line_len) per line, O(total_proposal_line_bytes) per batch — linear,
  not quadratic. There is no reassembly: each iteration reads one line, sums it, and
  pushes one `Proposal`; `proposals` grows by `push` (amortized O(1)). No O(n²)
  here. The CRC is accumulated incrementally as lines arrive, never recomputed over
  the whole batch. Correct and linear.

- **`send_turn` batch handling (`session.rs:345-390`).** `batch` is sliced to
  `MAX_BATCH = 5`. `proposals` is built once via `map().collect()`; `batch_checksum_line`
  walks it once; the answer loop `zip`s `batch` with `answers` once. All O(batch) over a
  bounded-5 batch. No repeated scans, no `contains`/`iter().any()` in a loop, no
  `Vec::remove`-in-loop. Bounded-small micro-cost — out of scope by the calibration rule.

- **`run_exchange_with_role` turn loop (`session.rs:296-319`).** Per iteration:
  `send_turn` or `receive_turn`, then `result.*.extend(...)` (amortized O(added)) and
  `remaining.clear()` (O(1), retains capacity). Nothing invariant is recomputed inside
  the loop; no map/set is rebuilt per turn. `MAX_TURNS = 1000` caps iterations. Linear
  in total work. Clean.

- **Answer / proposal matching.** Matching is purely positional `zip`
  (`session.rs:379`, `:463`) plus count-equality checks (`:375`, `:451`) — no
  nested search, no lookup-by-id scan. There is no proposal/answer *matching by MID*
  that could go quadratic; the protocol is order-preserving and the code relies on it.

- **`answer_line` (`session.rs:476-487`)** builds an ≤(3+batch+1)-char `String` by
  `push`; bounded-tiny.

- **`secure_login_response` (`secure.rs:26-43`).** One MD5 over
  challenge+password+64-byte salt, run at most once per session (only on a
  server challenge). Fixed-size; the 3-iteration bit-assembly loop is constant. Not
  recomputed in any loop. Fine.

- **`handshake.rs` readers.** `read_handshake` reads a bounded handshake prologue
  (identifier, `;FW`, `;PQ`, prompt) once per connect. `;FW` split/map/collect is over
  one short line. `parse_sid` does `rsplit('-').next()` + `to_uppercase` on one short
  string. All once-per-connect, linear in line length. No loop-invariant recompute.

- **`credentials.rs`.** No loops over collections at all; keyring lookups with at most
  one legacy-migration fallback. Out of algorithmic scope entirely.

- **Containers.** No `HashMap`/`HashSet`/`BTreeMap` in scope; no place where a wrong
  container forces a linear scan. `Vec` + positional indexing is the correct choice for
  these order-preserving, bounded-small protocol structures. No hasher concern (none used).

---

## Suspected Bugs

None. (No correctness anomalies observed within the algorithmic read of these files.
Behavioral/error-path correctness is out of this dimension's scope and was not chased.)
