---
run_schema_version: 1
run_id: 2026-06-04T19-30-r2-rig
date: 2026-06-04T19:30:00Z
scope: "R2 — tux-rig-rts + tux-rig-cm108 (PTT/rig control, timing-only)"
methodology: { skill: performance-audit (REDUCED, combined data-access+concurrency lane), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "serial RTS/DTR + CM108 HID GPIO" }]
lanes_run: [data-access+concurrency (combined — thin slice)]
verification_mode: "hardware-deferred — dynamic/timing lane cannot run (needs a real rig + /dev/ttyUSB*//dev/hidraw*); findings rest on structure only; allocation fallback inapplicable (PTT control has ~no allocation)"
finding_counts: { by_impact: { critical: 0, major: 0, minor: 1 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 1, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED) — R2: tux-rig-rts + tux-rig-cm108

**No significant findings** — one MINOR sleep-poll note, as expected for
timing-only PTT control. **Hardware-unfalsifiable** (see verification_mode):
this is timing code and timing is exactly what's unmeasurable without a rig.

## Examined (both dimensions)
- **data-access:** both backends hold the device fd open for the writer's lifetime (`linux.rs:25-78`, `hidraw.rs:31-62`) — no per-toggle reopen; one `ioctl`/one 5-byte HID `write` per toggle, correctly unbuffered (control ops must hit the wire immediately).
- **concurrency:** watchdog is sleep-poll not busy-wait (`watchdog.rs:90`, `thread::sleep(poll_interval.min(remaining))`); lock-free `AtomicBool` Acquire/Release; no Mutex anywhere → no lock-across-I/O.

## Minor finding
- **R2-1** watchdog exit latency bounded below by `poll_interval` (`watchdog.rs:90`) — standard sleep-poll tradeoff; only matters as post-crash dead-air; the actual interval lives in the out-of-scope binary. LOW, MEDIUM-confidence. Fingerprint `concurrency:tux-rig-rts/watchdog.rs:exit-latency-poll`.

## Safety note (cleared, not a bug)
The watchdog reaches `ptt.release()` on every exit path with `Drop` as backstop (`watchdog.rs:93`) — no observed path where it fails to attempt key-down. No suspected bugs.
