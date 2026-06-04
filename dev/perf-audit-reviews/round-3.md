# Adversarial Review (Round 3) — Performance-Audit Scope Partition v3

- **Reviewer:** Opus subagent (independent adversarial), agent glade-knoll-shoal
- **Target:** scope partition v3 (`dev/perf-audit-scope-plan.md`)
- **Date:** 2026-06-04
- **Mandate:** go where rounds 1–2 spent the LEAST attention (winlink protocol
  family, reduced tiers, cold sweep, frontend); VERIFY round 2's four added
  claims against source; do not rubber-stamp, do not invent.

## Verdict

**Minor-edits.** v3 is fundamentally sound and executable. The hot-path map is
honest, coverage is complete (every `src-tauri/src/*`, `src/*`, and Rust crate
lands in exactly one slice — verified against actual listings), and the tiering
is defensible. Round 2's four added claims (H1, H7, H8, H9) are **all real** — I
confirmed each against source. But I found **one factual error v3 inherited from
round 2** that should be corrected before execution, plus several precision nits:

1. **R9's "MessageList.tsx non-virtualized" claim is FALSE.** The current code
   uses `react-virtuoso` (`MessageList.tsx:18,338`). Round 2 grepped for
   `react-window` (wrong library) and missed it. This weakens — but does not
   invalidate — R9's mailbox half.
2. **H7's "Major" rank is inflated** — it's per-*call* (per-packet), not
   per-symbol; the planner is hoisted out of the symbol loop. Round 2's own
   report ranked it "Secondary," which is correct; v3's table promoted it.
3. **H9's "re-plan per call" is half-amortized** — `channel.rs` caches the
   planner object, so `fading.rs`'s repeated `plan_fft_*` are rustfft cache
   lookups, not full rebuilds. `analysis.rs:50`'s fresh-planner-per-call is the
   genuinely uncached one.

None is a rework or tier-change trigger. They are surgical corrections.

---

## Winlink protocol family — the under-examined area (independent reads)

I read representative compute paths in each winlink slice. **No new hot path or
mis-tiering found** — but the reads turned up concrete per-slice targets the
audit should aim at, and they confirm v3's reduced-tiering of this family is
correct, not lazy.

### R3 (compression + B2F) — lzhuf IS the only compute, confirmed
- `message.rs` (`to_bytes:86`, `from_bytes:176`) is header marshalling: a
  `Vec<(String,String)>` sorted once per message (`:99-108`), body/attachment
  `to_vec()` copies (`:205,227`). Small N (header count), once per message. Cold
  inside a warm slice.
- `proposal.rs` (`line:29`, `parse:43`, `batch_checksum_line:174`) is
  short-line formatting. No loop over message bodies.
- `lzhuf.rs` is the real algorithm (FBB CRC16 table `:24`, Huffman tree
  `insert_node:415`/`encode:551`, fixed ring-buffer arrays). H10 is accurate:
  real compute, ~once per message, low frequency. R3-reduced is right.

### R5 (AX.25 datalink) — NO per-bit HDLC / FCS exists here
- **`frame.rs:1-2` header is explicit: "KISS invariant: no FCS here — the
  TNC/modem owns FCS, flags, and bit-stuffing."** There is no per-bit HDLC
  framing and no FCS computation in this code. `Address::encode/decode` loop a
  fixed 6 bytes (`frame.rs:48,62`); `Path` is O(2–4 addresses).
- `kiss.rs:14,61` does per-byte FEND/FESC escape/de-escape over **frame-sized**
  buffers (AX.25 info ≤ ~256 B), once per frame — not per-bit, not a hot loop.
- `datalink.rs` is 228 prod / **1571 test** (round 2's 6.89× confirmed). It's a
  connected-mode mod-8 state machine: `std::thread::sleep(POLL_INTERVAL)`
  busy-poll loops (`:103,131,456,474`), bounded retransmit walks
  (`:546,559` — window ≤ 7). **Perf surface = concurrency/polling**, which
  R5-reduced's concurrency lane targets. Correctly tiered. *Audit note:* the
  busy-poll-with-sleep is a CPU-vs-latency knob worth the concurrency lane's
  attention.

### R6 (telnet) / R4 (VARA) / M3 (ARDOP) transports — TCP plumbing, confirmed
- `telnet.rs:121` `line.push(b)` is line-buffered command reading (small).
- `vara/transport.rs:135` `data_stream()` hands a raw `TcpStream` to the session
  layer — no per-byte processing in transport. Confirms v3's "DSP is external."
- **`ardop/data.rs` IS the bulk-data inbound path and has a concrete target:**
  `drain_leftover:104-109` is a byte-by-byte `VecDeque<u8>` drain into the read
  buffer, and `pump_decoder:145` does `self.leftover.extend(frame.payload)` —
  per-byte movement of the whole message body through a deque. This is exactly
  what M3-reduced's **alloc + data-access** lanes target. Confirms the M3
  full→reduced demote was right: the only "compute" is buffer movement, and
  throughput is capped by the external TNC anyway. *Audit note: name
  `data.rs:104,145` as M3's concrete data-access target.*

### R8 (B2F session) — checksum is over proposal lines, not bodies
- `session.rs:425-428` accumulates the batch checksum with a per-byte
  `wrapping_add` over each **proposal line** (short, small batch), not over
  message bodies. The body CRC lives in lzhuf (H10). No hidden hot loop.

### R7 (listener gate) — access-control glue
- `allowed_stations.rs` does linear `.iter().any()` scans (`:142,206,211`) over
  operator-configured allow-lists (small N) at connection-decision frequency
  (not per-packet) + an allow-list `fs::read` (`:225`). Correctly R7-reduced.

**Conclusion on the winlink family:** v3's reduced-tiering across R3–R8 + M3 is
correct. None is mis-tiered up (no hidden full-cycle hot path) or down. The one
sharp edge is `ardop/data.rs`'s per-byte deque path (M3), already in the right
slice — add it as a named target.

---

## Verification of Round 2's four added claims

### H1 — `constellations.rs::compute_llr` alphabet rebuild — **CONFIRMED (and worse than stated)**
- `receiver.rs:78` constructs a fresh `Mapper::new(constellation)` **per data
  subcarrier**, then `:83` calls `compute_llr(&[equalized[sc]], n0)` with a
  **single** symbol.
- `compute_llr:142` calls `self.alphabet()`; `alphabet:164-176` allocates
  `vec![0u8; bps]` + `Vec::with_capacity(n)` and calls `self.map()` `n=2^bps`
  times (up to 64 for QAM64), each `map()` allocating.
- So per data-subcarrier per symbol: a `Mapper` alloc **plus** a full alphabet
  rebuild (≤64 sub-allocs), to LLR one symbol. v3's H1 is accurate; the
  per-subcarrier `Mapper::new` at `:78` is an *additional* alloc v3 didn't name.
  **Critical, M1. Confirmed.**

### H7 — `narrow_fsk.rs:83` per-call FftPlanner — **CONFIRMED, but rank overstated**
- `narrow_fsk.rs:83` builds `FftPlanner::new()` and `:85` plans the FFT **once
  per `receive()` call**; the plan is then **reused** across the symbol loop
  (`:88-95`). It is hoisted OUT of the inner loop. So this is per-message
  (per-packet), not per-symbol — strictly less severe than the OFDM
  `receiver.rs:46` planner. **Tier M1 is right; v3's "Major" rank should be
  "Secondary"** (matching round 2's own report). Confirmed real, downgrade rank.

### H8 — `native_mailbox.rs::list` read-amplification — **CONFIRMED**
- `list:99-115`: `fs::read_dir` then, per `.b2f` file, `fs::read(&path)` (full
  body) + `Message::from_bytes` (full parse) + a `with_extension("read").exists()`
  stat — all to build a **header-only** list view. O(messages × body_size +
  messages stats) per folder open. Genuine N+1 / read-amplification scaling with
  mailbox size. **Warm, R10. Confirmed.** (v3's line cite `:99-104` is slightly
  narrow — the loop runs to `:115` and includes the per-message `.exists()` stat.)
- **Seam:** `winlink_backend.rs:233,1302,1716,1878` all iterate
  `mailbox.list(Outbox)` — the *consumer* side of H8, sitting in the cold sweep
  while the *producer* (`native_mailbox.rs`) is in R10. The fix lives in the
  producer, so no tier change, but R10 should note these consumers exist.

### H9 — hf-sim per-call FFT planners — **CONFIRMED, with amortization nuance**
- `channel.rs:39,68` caches the `FftPlanner` as a struct field and passes
  `&mut self.fft_planner` into the fading path (`:101,111`) — the planner OBJECT
  is reused across calls.
- `fading.rs:49,86` call `plan_fft_forward`/`plan_fft_inverse` every
  `generate_fading_block` call — but because the planner is reused, rustfft
  serves repeated same-length plans from its internal cache (HashMap +
  `Arc::clone`), NOT a full twiddle rebuild.
- `analysis.rs:50` creates a fresh `FftPlanner::new()` per
  `estimate_subcarrier_snr` call — **this one is genuinely uncached.**
- So H9 is real and correctly tiered (M5, offline, down-ranked), but the audit
  should know `fading.rs` is mostly amortized; `analysis.rs:50` is the clean
  target. **Confirmed, refine the framing.**

---

## Cold-sweep warm-buried check

Spot-read the big cold-sweep Rust files. **`native_mailbox.rs` was the only
true warm-buried file (already pulled to R10 by round 2 — correct).** Two
borderline notes, neither a tier change:

- **`modem_status.rs:392-395`** runs a background thread polling
  `tick_and_snapshot` at 4 Hz (`STATUS_POLL_INTERVAL`, `:352`) and broadcasting
  to the WebView. Steady-state always-on poll. The poll is light (snapshot under
  a lock, no I/O under lock per `:243`), so cold sweep is defensible — but the
  cold sweep's 3 lanes (complexity + allocation + data-access) don't include
  concurrency, and a 4 Hz broadcaster is a concurrency-shaped object. *Note it
  as a cold-sweep concurrency exception, or accept that 4 Hz is negligible.*
- **`winlink_backend.rs` (3262)** and `modem_commands.rs`/`ui_commands.rs`: on
  read, IPC marshalling + config/session glue, **except** the four
  `mailbox.list(Outbox)` call sites (H8 consumers, above). Cold sweep is
  otherwise correct.

No other warm-buried file found. The cold sweep is appropriately aggressive.

---

## Frontend check

- **R9 sparkline (`useSampleHistory.ts:44-49`) — CONFIRMED warm-not-hot.** A
  `setInterval` at 1 Hz does `[...prev.slice(1), latest]` — a 60-element array
  copy + a `setSamples` re-render per second per active sparkline. Trivial
  compute; the only cost is a steady 1 Hz re-render of the radio subtree. Real,
  warm, correctly R9.
- **R9 MessageList — the "non-virtualized" claim is FALSE.**
  `MessageList.tsx:18` `import { Virtuoso } from 'react-virtuoso'`; `:338`
  renders `<Virtuoso data={sortedMessages} computeItemKey itemContent>`. **The
  list IS virtualized.** Round 2's grep for `react-window` missed `Virtuoso`.
  The sort (`:294 sortMessages`) is `useMemo`'d on `[messages, sortState,
  folder]`, so it re-sorts only on input change, not every render. **R9's
  mailbox half is weaker than v3 claims** — the real large-mailbox cost is the
  *backend* H8 (R10), not a non-virtualized frontend render.
- **Shell state — no re-render storm.** `App.tsx:2,98` uses
  `@tanstack/react-query` (`QueryClientProvider`), a cached query layer;
  `useStatus.ts:302-315` was migrated off raw `setInterval` to react-query. No
  hand-rolled whole-tree context provider. Cold/warm tiering of `src/shell/` is
  defensible. **No new frontend hot path.**

---

## Coverage / seam corrections

1. **Coverage is complete — verified against actual listings.** Every
   `src-tauri/src/*.rs` (all 18 + test_helpers out-of-scope), every
   `src-tauri/src/*/` dir, every Rust crate, and every `src/*/` frontend dir
   lands in exactly one slice. All cold-sweep-named Rust files exist (checked all
   18). No orphan like round 2's `wizard.rs`. The `wizard.rs` fix landed.
2. **Path-precision nit:** `hf-channel-sim` lives at the **repo root**
   (`/home/user/tuxlink/hf-channel-sim/`), NOT under `tuxmodem/crates/`. The
   slice path (plan line 66, `hf-channel-sim/src`) is correct, but any framing
   that treats `tuxmodem/crates/*` as the universe of Rust crates is incomplete —
   `hf-channel-sim` is a sibling. Cosmetic; M5 covers it.
3. **No double-counts.** `listener/` (R7) vs `modem/ardop/listener.rs` (M3) vs
   `modem/vara/listener.rs` (R4) are distinct files in distinct dirs — verified.
   `process.rs` M3↔R4 seam (round 2's note) still holds; primary home R4.
4. **Frontend `assets/`, `fonts/`** are static non-code — correctly unmentioned.

---

## Ranked minimal edits to make v3 executable

1. **[claim, correct-before-execute] Fix the "MessageList.tsx non-virtualized"
   assertion** (plan line 82; review-log round-2 disposition 4). It IS
   virtualized via `react-virtuoso` (`MessageList.tsx:18,338`). Reframe R9's
   mailbox half as "sort-on-render (memoized) + the backend H8 read-amplification
   is the real large-mailbox cost (R10)." R9 stays a valid warm slice for the
   1 Hz sparkline churn.
2. **[rank] Downgrade H7 from "Major" to "Secondary"** in the hot-path map. It's
   per-call (planner hoisted out of the symbol loop, `narrow_fsk.rs:83-95`), not
   per-symbol. Matches round 2's own report. Tier M1 unchanged.
3. **[framing] Refine H9** — note `channel.rs` caches the planner so `fading.rs`
   re-plans are rustfft cache hits; `analysis.rs:50`'s fresh-planner-per-call is
   the genuinely uncached target. Tier M5 unchanged.
4. **[target, additive] Name `ardop/data.rs:104,145`** (per-byte `VecDeque<u8>`
   drain + `leftover.extend(payload)`) as M3's concrete data-access target — the
   only real buffer-movement cost in the demoted-to-reduced ARDOP slice.
5. **[seam, additive] Note H8's consumers** — `winlink_backend.rs:233,1302,
   1716,1878` iterate `mailbox.list(Outbox)` from the cold sweep; cross-reference
   R10 so the audit reads producer + consumers together.
6. **[note, optional] `modem_status.rs:392` 4 Hz broadcaster** — flag as a
   cold-sweep concurrency exception or explicitly accept 4 Hz as negligible.
7. **[cosmetic] hf-channel-sim is a repo-root sibling, not `tuxmodem/crates/*`.**

**Structure call:** 17 units is proportionate. **No merge or split warranted.**
The winlink family (R3–R8) is correctly reduced-tiered — I went looking for a
hidden full-cycle hot path or a mis-tiered-cold slice in there and found neither.
M3's full→reduced demote is vindicated by `data.rs`. The two PHY criticals (H0,
H1) and the two M1 planner findings (H7 here per-call, the OFDM one per-symbol)
all correctly live in M1. Coverage is airtight. v3 is **sound; ship after edits
1–3** (the substantive corrections) **plus the additive notes 4–7.**
