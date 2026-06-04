---
run_schema_version: 1
run_id: 2026-06-04T17-00-r9-frontend-ui
date: 2026-06-04T17:00:00Z
scope: "R9 — src/radio + src/mailbox (warm React/TS UI)"
methodology: { skill: performance-audit (REDUCED depth, frontend-tuned lanes), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents, model=opus)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack:
  - { ecosystem: npm, framework: react, version: "19.2.7" }
  - { ecosystem: npm, framework: react-virtuoso, version: "4.18.7" }
  - { ecosystem: npm, framework: "@tauri-apps/api", version: "2" }
  - { ecosystem: npm, framework: typescript, version: "6" }
currency_briefs:
  - { framework: react, researched_on: null, status: "version-index lacks React entries; findings rest on React 19 idioms — no React Compiler configured, so manual memoization is load-bearing" }
lanes_run: [react-idioms (idiom-currency), algorithmic, data-access, cost-map]
lanes_skipped: { memory: "reduced depth — JS GC, low relevance vs render cascades", concurrency: "single-threaded JS event loop", payload-startup: "bundle is a separate cold-sweep concern", dynamic: "no headless render-profiling harness this pass" }
finding_counts: { by_impact: { critical: 0, major: 3, minor: 4 }, by_lane: { idiom-currency: 4, algorithmic: 3, data-access: 3, cost-map: 6 }, suspected_bugs: 1 }
regression: { prev_run_id: null, new: 7, persisting: 0, resolved: 0 }
---
# Performance Audit (REDUCED, frontend-tuned) — R9: src/radio + src/mailbox

**Date:** 2026-06-04 17:00   **Scope:** `src/radio` (20 files) + `src/mailbox` (20 files), excl. tests
**Depth:** REDUCED — frontend-tuned lanes: react-idioms, algorithmic, data-access (Tauri IPC), cost-map (memory/concurrency/payload skipped — see frontmatter)
**Stack:** React 19.2.7 · react-virtuoso 4.18 · @tauri-apps/api 2 · TS 6 (no React Compiler → manual memoization load-bearing)
**Regression vs none (first run):** 7 new

> **CALIBRATION CORRECTION (the lanes caught my dispatch error).** I briefed the
> lanes that the only periodic re-render is a ~1 Hz sparkline tick. Three lanes
> independently READ THE BACKEND and corrected it: the `modem:status` broadcast
> is **4 Hz** (`src-tauri/src/modem_status.rs:355`, 250 ms), and `useModemStatus`
> (`src/modem/useModemStatus.ts:24`) pushes a fresh object every tick — so the
> whole `ArdopRadioPanel` re-renders 4×/sec while connected, with the 1 Hz
> sparkline intervals adding renders on top. This is a good property of the skill:
> lanes calibrate from the actual source, not the dispatcher's claim — and it
> connects to M3's `modem_status` broadcaster finding (cross-unit coherence).

## Executive summary
The frontend has **no render hot loop** (no canvas/RAF — confirmed) and its
list-heavy surface is already well-optimized (Virtuoso virtualization + memoized
sort + event-push hooks + headers-only fetch — all cleared below). The real cost
is a **4 Hz re-render cascade**: the wide `modem:status` subscription re-renders
the whole `ArdopRadioPanel` (R9-3), which re-projects the **unbounded** session
log every tick (R9-1) and recomputes the sparklines (R9-5). Independently, a
**per-keystroke Tauri `invoke`** on the peer-callsign field (R9-2) does N keyring
round-trips while typing. 3 majors, 4 minors, 0 criticals.

## Major findings

### R9-1. Session log re-projected at 4 Hz over an unbounded buffer
**Lanes:** react-idioms (MAJOR), algorithmic (MAJOR), cost-map (Med)   **Location:** `radio/sections/useSessionLog.ts:213` (`toSessionLogEntries(lines)` unmemoized), `radio/sections/SessionLogSection.tsx:56,62` (not `memo`'d; re-filters + `scrollTop` write each render)
**Fingerprint:** `algorithmic:useSessionLog.ts:toSessionLogEntries:per-tick-reproject`   **Problem:** `toSessionLogEntries` `.map`s a fresh entry per line over the **append-only, never-evicted** `lines` buffer (`mergeLogLine`/`mergeLogLines` only append). With the panel re-rendering ~4 Hz, this O(n_lines) map + allocation runs every tick **even when no log line arrived**, and hands `SessionLogSection` a new array identity that re-fires its auto-scroll effect + a second O(n) `.filter`. **The one finding that degrades over a session.** **Confidence:** Strong-static   **Effort:** Localized (`useMemo([lines])` + `memo` the section). **Verification:** React DevTools Profiler render count on the section across a 4 Hz tick with an idle log; correctness = identical rendered lines. **Related watch:** the unbounded `lines` buffer is also latent memory growth + `mergeLogLine` is O(n)/line → O(n²)/session (per-event, human-paced — not per-render; cap/evict it).

### R9-2. Per-keystroke Tauri `invoke` on the peer-callsign field (no debounce)
**Lanes:** data-access (MAJOR)   **Location:** `radio/modes/TelnetP2pRadioPanel.tsx:260-276` (effect keyed `[peerCallsign]`), input `:294-295`
**Fingerprint:** `data-access:TelnetP2pRadioPanel.tsx:peer_password_status:per-keystroke-invoke`   **Problem:** `p2p_peer_password_status` is `invoke`d on every keystroke (debounce explicitly declined in a comment); typing a 6-char callsign = ~6 sequential keyring-read IPC round-trips + 6 re-renders vs 1 with a trailing debounce. **Confidence:** Strong-static   **Effort:** Localized (250–400 ms trailing debounce). **Verification:** count `invoke`s per typed callsign before/after. **Pairs with SB-R9-1** (the race) — a debounce fixes both.

### R9-3. `ArdopRadioPanel` whole-tree re-render at 4 Hz (no `memo` boundary)
**Lanes:** cost-map (High), react-idioms (MINOR→root-cause)   **Location:** `radio/modes/ArdopRadioPanel.tsx` (subscribes `useModemStatus`; ~830-line body + all sections re-render each 4 Hz tick while connected)
**Fingerprint:** `idiom-currency:ArdopRadioPanel.tsx:status-subscription:wide-4hz-rerender`   **Problem:** no `React.memo` boundary between the 4 Hz status state and section children, so the whole panel re-renders 4×/sec — the frequency amplifier behind R9-1 and R9-5. The codebase already has the right pattern elsewhere (`useModemIsActive`, `useModemStatus.ts:49`, change-only dedupe) but the live-meter panel deliberately takes the full stream. **Confidence:** Strong-static   **Effort:** Contained (memo section boundaries; or split the subscription so only meter leaves take the 4 Hz stream). **Verification:** Profiler render-count per tick before/after. **Note:** partly by-design (live meters) — the fix is to narrow WHICH subtree re-renders, not the cadence.

## Minor findings
- **R9-4** Three free-running 1 Hz `setInterval`s (`useSampleHistory` ×2 + `useFrameHistory`, `ArdopRadioPanel.tsx:138,324-326`) re-sample the already-event-pushed 4 Hz status on a second clock → 3 re-renders + 3 array reallocs/sec, even when the modem is Stopped. Drive buffers off the status event (decimate by timestamp) or pause when stopped. Fingerprint `idiom-currency:useSampleHistory.ts:second-clock-resample`.
- **R9-5** `SignalSection` + 2 `Sparkline`s recompute `avgSnr` reduce over 60 + 60 styled bars + `Math.max(...samples)` spread 4×/sec though data changes at 1 Hz. `SignalSection.tsx:49-52`, `Sparkline.tsx:50-72`. (Subsumed by fixing R9-3's boundary.) Fingerprint `algorithmic:Sparkline.tsx:recompute-4hz`.
- **R9-6** Four system-folder badge queries `refetchInterval: 10_000` (`useMailbox.ts:67-74`) → ~0.4 `mailbox_list` IPC/s at idle forever, for off-screen folders whose counts only change on connect/move. Event-driven invalidation or badge-only `staleTime`. Fingerprint `data-access:useMailbox.ts:badge-poll-idle`.
- **R9-7** `MessageRow` gets a fresh `highlights = matchHighlight ?? []` each render (`MessageList.tsx:154`), an unstable dep that defeats its subject/preview `useMemo`s (bounded to ~20 live Virtuoso rows). Hoist a module-level `const EMPTY = []`. Trivial. Fingerprint `idiom-currency:MessageList.tsx:unstable-empty-highlights`.

## Examined and cleared (recorded, NOT flagged — anti-padding)
- `MessageList.tsx:294 sortedMessages` — correctly `useMemo`'d on `[messages, sortState, folder]` (change-only, not per-render/keystroke).
- `MessageList` is Virtuoso-virtualized; rows are `memo`'d with `useCallback`-stable props + per-row `useMemo`s — only the visible window + the two rows whose `selected` flips repaint.
- `MessageView` body is a plain `<pre>` (no markdown/sanitizer on the body path); per-selection, TanStack-gated, not periodic.
- `useModemStatus` + `useSessionLog` use correct event-push + clean `unlisten` (no listener churn); `refetchOnWindowFocus:false` global; message list does NOT over-fetch (`mailbox_list` returns `MessageMeta` headers only; body fetched lazily by `useMessage`); no N+1 IPC per row; inline handlers/`itemContent` sit at/inside the `memo` boundary so they don't cascade (do NOT "fix").

## Cross-cutting theme
**R9-1, R9-3, R9-5 share one root:** the wide 4 Hz `modem:status` subscription
with no `memo` boundary. A single structural fix — a `memo` boundary around the
high-churn sections (or narrowing the subscription the way `useModemIsActive`
already does) — collapses the cascade; then memoize the log projection (R9-1).
**R9-2** (debounce the keystroke `invoke`) is independent and also closes the
race (SB-R9-1).

## Suspected Bugs (for follow-up — NOT addressed here)
### SB-R9-1. Per-keystroke keyring `invoke` leaves in-flight calls uncancelled
**Location:** `radio/modes/TelnetP2pRadioPanel.tsx:260-276`   **What looks wrong:** the effect only guards against stale writes, not in-flight cancellation, so racing keyring reads can resolve out of order (a slow earlier read overwriting a newer result).   **Why suspected:** noticed during the data-access audit; a trailing debounce + AbortController/stale-guard fixes both the perf issue and the race.
