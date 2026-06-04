---
run_schema_version: 1
run_id: 2026-06-04T19-00-r5-ax25
date: 2026-06-04T19:00:00Z
scope: "R5 — winlink/ax25 (AX.25 packet data link)"
methodology: { skill: performance-audit (REDUCED depth), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "KISS/AX.25 over TNC/serial" }]
lanes_run: [algorithmic, memory, data-access]
lanes_skipped: { concurrency: "covered within data-access (link locks/timers)", idiom-currency: "std-only", cost-map: "reduced", payload-startup: "n/a", dynamic: "needs TNC/radio" }
finding_counts: { by_impact: { critical: 0, major: 0, minor: 4 }, by_lane: { algorithmic: 2, memory: 2, data-access: 0 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 4, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R5: winlink/ax25

**All-minor / mostly-clean — the anti-padding test result.** AX.25 packet link at
1200/9600 baud, ARQ window hard-clamped ≤7 (`datalink.rs:659`), FCS owned by the
TNC (out of scope, `frame.rs:1-2`). data-access lane: **No significant findings**
(chunked 512-byte reads, no lock-across-IO — the `Arc<Mutex>` is TEST-only
`link.rs:217`; sleep-bounded 20 ms timers, not spin). algorithmic: **No
significant findings** (no quadratics; retransmit walk is O(w≤7)).

## Minor findings (cleanliness, NOT throughput levers)
- **R5-1** `send_i` double-clones the payload (`datalink.rs:574-585`): `info.to_vec()` for the transient Frame AND again for `unacked`; one retained copy suffices. Per outbound I-frame; LOW (RF-rate-bound). Fingerprint `memory:datalink.rs:send_i:double-clone`.
- **R5-2** inbound `info` Vec built then copied byte-wise into the `inbound` VecDeque + dropped (`frame.rs:341`→`datalink.rs:602`); a borrowed slice skips it. LOW. Fingerprint `memory:frame.rs:decode:inbound-info-alloc`.
- (algorithmic noted both above as the only in-dimension items; both bounded by the ≤7 window, not the "rescan-all-unacked" concern.)

## Cleared (anti-padding)
`unacked` BTreeMap bounded ≤7 → **no unbounded retransmit growth** (the headline concern is absent by construction); `Address`/`Path` encode/decode fixed bounded loops, pre-sized; `kiss_data_frame`/`KissDecoder` single linear pre-sized pass; `hex_dump` per-byte `format!` is env-gated diagnostic (off in prod). No suspected bugs.
