---
run_schema_version: 1
run_id: 2026-06-04T16-30-m3-ardop
date: 2026-06-04T16:30:00Z
scope: "M3 — winlink/modem/ardop (ARDOP modem driver; external-TNC TCP transport + ARQ state machine)"
methodology: { skill: performance-audit (REDUCED depth, within performance-audit-cycle), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents, model=opus)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "TcpStream + threads, no async" }]
currency_briefs: []
lanes_run: [algorithmic, memory, data-access, concurrency]
lanes_skipped: { idiom-currency: "reduced depth — std-only, no framework idiom surface", payload-startup: "n/a", cost-map: "reduced depth", dynamic: "external TNC + radio required; not runnable here" }
finding_counts: { by_impact: { critical: 0, major: 0, minor: 7 }, by_lane: { algorithmic: 2, memory: 3, data-access: 3, concurrency: 3 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 7, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — M3: winlink/modem/ardop

**Date:** 2026-06-04 16:30   **Scope:** `src-tauri/src/winlink/modem/ardop` (10 files)
**Depth:** REDUCED (algorithmic, memory, data-access, concurrency; idiom-currency/cost-map/payload skipped — see frontmatter)
**Stack:** Rust std `TcpStream` (blocking) + threads; no async
**Regression vs none (first run):** 7 new (all minor)

> **Why reduced:** this is the ARDOP modem *driver* — it speaks TCP to an external
> `ardopcf` TNC process; the DSP runs in that child, not here. Round 2 demoted it
> from a full cycle on exactly this basis. **The audit vindicates that call:**
> across all four lanes, **every finding is MINOR**, correctly calibrated to the
> LOW HF data rates (hundreds bps–few kbps) where radio airtime — not CPU or
> allocation — is the bottleneck. No criticals, no majors, no suspected bugs.

## Findings (all MINOR)

### M3-1. Triple-hop inbound payload copy + byte-by-byte drain
**Lanes:** memory, data-access, algorithmic   **Location:** `frame.rs:90` (`self.buf[5..total].to_vec()`) → `data.rs:145` (`leftover.extend(payload)`) → `data.rs:104-110` (`drain_leftover` element-wise `zip` loop)
**Fingerprint:** `memory:data.rs:drain_leftover:triple-hop-copy`   **Problem:** inbound payload is copied Vec→VecDeque→buf, the last copy byte-by-byte. **Fix:** `VecDeque::as_slices()`+`copy_from_slice` for the drain (free `memcpy`); a read-cursor/slice API removes a whole copy. **Effort:** Localized (drain) / Contained (cursor). **Impact:** modest at HF rates. **Verification:** bench at a representative block size; identical bytes out.

### M3-2. Per-frame `Vec::drain(..total)` shift in the decoder
**Lanes:** algorithmic, data-access, memory   **Location:** `frame.rs:91`
**Fingerprint:** `algorithmic:frame.rs:next_frame:per-frame-drain-shift`   **Problem:** `drain(..total)` memmoves the tail to index 0 each frame; decoding K back-to-back frames from one read is O(N·K). Bounded by the 4 KB read cap (`data.rs:258`) so immaterial now. **Fix:** `consumed` cursor with lazy compaction (or `VecDeque<u8>` for `buf`). **Effort:** Contained.

### M3-3. Unbuffered data-socket WRITE side — tiny B2F tokens each become a framed wire write
**Lanes:** data-access   **Location:** `data.rs:317-331` + `b2f.rs:105-106`
**Fingerprint:** `data-access:b2f.rs:write:unbuffered-token-frames`   **Problem:** B2F wraps only the *read* half in `BufReader`; each tiny control token (`FF\r`, `FS Y\r`) goes straight to `DataSocket::write` → a fresh `Vec` + `write_all` syscall + a 2-byte ARDOP length header = ~50–100% framing overhead on tiny tokens. **This is the most actionable M3 finding because on-air bytes are the scarce resource.** **Fix:** `BufWriter` flushed at turn boundaries to coalesce tokens. **Effort:** Localized (+low). **Verification:** count wire frames per turn before/after; protocol behavior unchanged.

### M3-4. Live status meters go dark for the whole B2F exchange
**Lanes:** concurrency   **Location:** `modem_commands.rs:692-714` (`take_transport()`) vs `modem_status.rs:171` (broadcaster skips `drain_status_events` when `transport == None`)
**Fingerprint:** `concurrency:modem_commands.rs:take_transport:status-dark-window`   **Problem:** the transport is *removed* from the session for the minutes-long B2F run (a correct way to avoid holding the lock across blocking I/O), but as a side effect every status tick sees `transport == None`, so throughput_bps / bytes_tx / PTT / Busy meters freeze at pre-exchange values exactly when the operator is watching, and accumulator byte-accounting doesn't run for in-exchange bytes. **UI-responsiveness class, not a thread stall.** **Fix:** route status events to the broadcaster during the exchange (e.g. a status channel the exchange feeds), or have the exchange publish periodic meter snapshots. **Effort:** Contained. **Note:** borderline UX/correctness — surface to the operator (it changes observable behavior).

### M3-5. Unbounded `mpsc::channel` on the command reader
**Lanes:** concurrency   **Location:** `session.rs:74` (channel), `:122` (send)
**Fingerprint:** `concurrency:session.rs:cmd_reader:unbounded-mpsc`   **Problem:** no back-pressure; reachable (not theoretical) precisely because the consumer is absent during the M3-4 dark window, so ardopcf BUFFER/STATUS events accumulate unbounded for the exchange duration. Bounded memory growth, not a stall; low at HF rates. **Fix:** a bounded channel (or drain during the exchange — pairs with M3-4's fix). **Effort:** Localized.

### M3-6. Per-line `String` alloc in the command reader
**Lanes:** memory   **Location:** `session.rs:90-91` (`s.to_owned()` per line, only to pass `&str` to `Command::parse`, which re-trims). **Fix:** pass the borrowed line. **Effort:** Localized. Negligible (few cmd msgs/sec).

### M3-7. Bind-wait sleep-poll
**Lanes:** concurrency   **Location:** `transport.rs:178-195` (100 ms × up to 5 s). Bounded, once-per-spawn, off hot path. Recorded; no change recommended.

## Examined and sound by construction (recorded, NOT flagged)
- **No `Mutex` held across blocking socket I/O** — the single `Mutex<ModemSessionInner>` (`modem_status.rs:79`) is dropped before any blocking exchange via `take_transport()` (`modem_commands.rs:692`); the broadcaster drain is hard-bounded non-blocking (`recv_event(Duration::ZERO)`, ≤64/tick, `transport.rs:675`). Correct pattern for a no-async blocking-socket design — the headline DEFEND risk is absent.
- **The documented split-borrow** at `transport.rs:666-679` (cmd `&mut` + `apply_event_to_accumulators_inline` on disjoint fields, with the SAFETY comment) — intentional, sound borrow-checker workaround; reviewed, not a defect.

## Net
ARDOP is clean for its role. The two findings worth scheduling are **M3-3**
(BufWriter — saves on-air bytes, the scarce resource) and **M3-4** (status dark
window — operator-visible). The rest are cleanliness/cleanup. No fix-plan written
this pass (reduced depth + all-minor); these fold into a future winlink-tier
remediation plan. Suspected bugs: none.
