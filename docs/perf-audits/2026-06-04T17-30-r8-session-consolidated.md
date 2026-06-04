---
run_schema_version: 1
run_id: 2026-06-04T17-30-r8-session
date: 2026-06-04T17:30:00Z
scope: "R8 — winlink B2F session driver (session.rs, handshake.rs, credentials.rs, secure.rs, mod.rs)"
methodology: { skill: performance-audit (REDUCED depth), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "BufRead/Write over injected transport" }]
currency_briefs: []
lanes_run: [algorithmic, memory, data-access]
lanes_skipped: { concurrency: "synchronous stream driver — no threads/locks/shared state", idiom-currency: "std-only", cost-map: "reduced depth", payload-startup: "n/a", dynamic: "needs a live CMS/peer; not runnable here" }
finding_counts: { by_impact: { critical: 0, major: 1, minor: 5 }, by_lane: { algorithmic: 1, memory: 4, data-access: 3 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 6, persisting: 0, resolved: 0 }
adjacent_context: "W0 winlink call-frequency map (docs/perf-audits/2026-06-04-W0-winlink-call-frequency-map.md)"
---
# Performance Audit (REDUCED) — R8: winlink B2F session driver

**Date:** 2026-06-04 17:30   **Scope:** `src-tauri/src/winlink/{session.rs,handshake.rs,credentials.rs,secure.rs,mod.rs}`
**Depth:** REDUCED (algorithmic, memory, data-access; concurrency/idiom/cost-map skipped — see frontmatter)
**Frequency context:** W0 map — runs per Winlink connect; `send_turn`/`receive_turn` per message; modest message counts; HF link is high-latency/low-rate
**Regression vs none (first run):** 6 new

> **W0 in action:** R8 establishes the per-message frequency for R3's impls. The
> memory lane correctly reached the R3 call sites (`session.rs:467` →
> `transfer::read_block`; `:384` → `frame_block`) and attributed their per-message
> allocations to R3 — exactly the cross-slice attribution W0 was built to enable.
> Those allocation findings are recorded here as *R3-owned* (the R3 run owns the
> fix); R8's own finding is the control-channel write batching below.

## Major findings

### R8-1. Fragmented control-channel writes — a proposal batch goes out as ~12 tiny unbuffered writes
**Lanes:** data-access (MAJOR)   **Location:** `session.rs:347-356` (`send_turn`), via `write_bytes` (`session.rs:495-499`) → unbuffered `WriteHalf` (`telnet.rs:91-94`)
**Fingerprint:** `data-access:session.rs:send_turn:fragmented-batch-writes`   **Problem:** a full 5-proposal batch is emitted as ~12 separate unbuffered `write()` calls (each proposal line + a separate `b"\r"`, then the checksum line + `\r`) — no `BufWriter`. The batch is one logical unit the remote can't act on until the checksum arrives, so it should be one coalesced `write_all` + one `flush`. Up to ~12 TCP segments where 1 suffices, **amplified by HF round-trip latency.** **Confidence:** Strong-static   **Effort:** Localized (build the batch in a buffer, one write at the turn boundary). **Verification:** count segments/`write` syscalls per turn before/after; protocol bytes unchanged. **Theme:** same root as M3-3 (ARDOP unbuffered token writes) — *the winlink transport layer writes tiny protocol tokens without coalescing; on-air bytes + round-trips are the scarce resource.*

## Minor findings
- **R8-2** No `writer.flush()` anywhere in the driver — works ONLY because the injected socket is unbuffered; a future `BufWriter` (the natural fix for R8-1) would **deadlock the strictly-alternating turn protocol** without an explicit flush at each turn boundary. So R8-1's fix MUST add the flush. `session.rs` (whole). Fingerprint `data-access:session.rs:no-flush-latent`. (Design note — latent hazard, not a current defect.)
- **R8-3** `send_turn` clones up to 5 `Proposal`s per turn (`session.rs:346`, `batch.iter().map(|m| m.proposal.clone())`) only for read-only `.line()`/checksum — a `Vec<&Proposal>` borrows instead. Localized. Fingerprint `memory:session.rs:send_turn:proposal-clone`.
- **R8-4** `wire::read_line` (`wire.rs:12-22`, called per protocol line from `session.rs:412,359`) allocates a fresh `Vec` + `from_utf8_lossy` + `.to_string()` (up to 3 allocs/line) for opaque ASCII control lines. O(lines), small. Fingerprint `memory:wire.rs:read_line:per-line-alloc`.
- **R8-5** `handshake.rs:43-53,66-73` `format!`-then-`push_str` per line; `write!` into the buffer avoids throwaway Strings. One-time per connect. Fingerprint `memory:handshake.rs:format-pushstr`.
- **R8-6 (R3-owned, surfaced via W0)** per received message, `transfer::read_block` (`transfer.rs:90-105`) builds the body via per-byte `data.push(b)` into a zero-capacity `Vec` (N pushes + log-N reallocs; this is the H11 area); `frame_block` (`transfer.rs:47-74`) assembles the whole frame unsized. Chunk/frame sizes are known → sized `extend`/`with_capacity`. **Fix belongs to R3** (transfer.rs is R3's slice); recorded here because R8 is the per-message caller. Fingerprint `memory:transfer.rs:read_block:per-byte-push` (R3).

## Examined and cleared (anti-padding)
- `session.rs:425 for b in line.bytes()` — sums ONE proposal line (≤~80 B), CRC accumulated incrementally; O(line), not O(n²) reassembly.
- `run_exchange` turn loop (`:296-319`) — no loop-invariant recompute, capped by `MAX_TURNS`.
- Proposal/answer matching — positional `zip` + count checks, no by-MID search → no accidental quadratic. Batch bounded by `MAX_BATCH=5`.
- `secure_login_response` — single MD5, ≤once/session. No `HashMap`/`HashSet`; `Vec`+positional is correct for these order-preserving bounded structures.

## Net
The session driver is algorithmically clean for its per-connect/per-message
frequency. The one schedulable finding is **R8-1** (coalesce the proposal-batch
control writes + add the turn-boundary flush, R8-2) — a transport-write-batching
theme shared with M3. The per-message body-buffer allocations (R8-6) fold into the
**R3** remediation (transfer.rs). No suspected bugs. No fix-plan this pass
(reduced + mostly-minor); folds into a future winlink-tier remediation plan.
