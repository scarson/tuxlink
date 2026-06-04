---
run_schema_version: 1
run_id: 2026-06-04T20-00-cold-sweep
date: 2026-06-04T20:00:00Z
scope: "COLD SWEEP — all cold Rust + cold/warm TS (3 batched scan-for-warm passes)"
methodology: { skill: performance-audit (COLD SWEEP — batched 3-lane scan-for-warm, split into 3 passes per the capacity cap), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus", reasoning_effort: "default", overridden_by_user: false }
lanes_run: [algorithmic+allocation+data-access (batched, scan-for-warm)]
passes: [sweep-a-rust-commands, sweep-b-rust-backends, sweep-c-frontend]
finding_counts: { by_impact: { critical: 0, major: 0, minor: 0 }, warm_in_cold: 0, suspected_bugs: 2 }
regression: { prev_run_id: null, new: 0, persisting: 0, resolved: 0 }
---
# Performance Audit — COLD SWEEP (3 passes): confirmed cold

**Result: all three passes confirmed cold — zero warm-in-cold findings.** The
sweep's job is to verify nothing warm was wrongly batched into the cold tier; it
did, and in doing so corroborated the localization of the warm/hot findings from
the audited units.

## Pass A — `ui_commands.rs` + `winlink_backend.rs` (~9k LOC): CONFIRMED COLD
Scanned: mailbox list/read/move commands (thin native_mailbox delegates),
allowlist arm/disarm loops (bounded, no nesting → no O(n²)), serial enumeration,
config read/write commands, ARDOP/VARA inbound listener loops, packet/CMS
exchange, session-log emit. **Key clearance:** every `read_config()` site is
per-command or per-*connection* (the two in listener loops fire only on connection
Accept), NOT per-tick — **R10-6's uncached-config worry does NOT reach a hot path
here.** `BlockingB2fStream::read` is an idle-nap, not busy-spin.

## Pass B — feature backends + lifecycle + listener gate: CONFIRMED COLD
`modem_status.rs:392` 4 Hz broadcaster: per-tick work is one lock + small-struct
clone + non-blocking drain — light, **no escalation of M3's note.** Listener gate
(R7) takes allowlist/password by reference, no per-connection fs; `accept` is a
linear scan over a handful of operator patterns. `forms/` caches substituted
templates (read once per open). grib composes a request string (cold). All
`read_config()` per-IPC-command, never in a loop/tick.

## Pass C — frontend (`shell/` + feature dirs + root): CONFIRMED COLD
**Every warm candidate already mitigated:** markdown render/sanitize is `useMemo`'d
(`help/ReadingPane.tsx:79-82`; `Marked` built once at module load); shell consumes
the **gated** `useModemIsActive()` (transition-only dedupe), NOT the wide 4 Hz
stream — **so R9's cascade is correctly localized to `radio/ArdopRadioPanel`, not
systemic**; search is debounced + query-gated (no per-keystroke IPC — **R9-2's
per-keystroke `invoke` is specific to the telnet peer-callsign field, not a
pattern**); Compose autosave is one guarded 2 s interval; status-bar polls are
bounded react-query (5s/2s). AppShell badge-poll matches R9-6 (not worse).

## Suspected Bugs (recorded, NOT chased)
- **SB-SWEEP-1** `forms/http_server.rs:379 folder_handler` uses blocking `std::fs::read` inside an async axum handler. Trivial volume (single localhost form webview) → no perf impact; an async-correctness idiom note.
- **SB-SWEEP-2 (cold nit, not a bug)** `wizard/wizardContext.tsx:25` passes an un-memoized `{state, dispatch}` provider value — textbook context-value churn, but behind the low-frequency wizard flow.

## Net
The cold tier is genuinely cold. The perf-relevant surface of tuxlink is the
warm/hot units already audited; the glue is glue. The sweep also **validated the
boundaries** of the R9/R10 findings (the 4 Hz cascade and the per-keystroke IPC
are localized, not systemic; the uncached config doesn't reach a hot path).
