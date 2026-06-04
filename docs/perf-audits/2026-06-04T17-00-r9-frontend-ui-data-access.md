# Perf audit — Data access & I/O (Tauri IPC / event flow)

Agent: glade-knoll-shoal · 2026-06-04T17:00 · READ-ONLY
Scope: `src/radio` + `src/mailbox` (excl `*.test.tsx`). Tauri desktop radio client (React 19, `@tauri-apps/api`@2, TS 6).
Dimension: chatty/over-frequent `invoke`, N+1 IPC, over-fetching, refetch-on-focus/mount, listener churn, large payload deserialize.
Lens prior: profile-packs/javascript-typescript.md (data-access lane) + Tauri `invoke` treated as a network/IPC round-trip.

Impact expressed as IPC round-trips per interaction / events per second. NOT wall-clock.

---

### [MAJOR] `p2p_peer_password_status` invoked on every keystroke of the peer-callsign input

Location: `src/radio/modes/TelnetP2pRadioPanel.tsx:260-276` (effect keyed `[peerCallsign]`); input wired at `:295` (`setPeerCallsign(raw.toUpperCase())`) and `:405` (`value={peerCallsign}`).

Problem: The effect fires one `invoke('p2p_peer_password_status', { callsign })` per `peerCallsign` change. `peerCallsign` is bound directly to the text input's onChange with no debounce — the inline comment explicitly declines it ("debounce not needed — the backend lookup is a fast keyring read"). Typing a 6-char callsign (`KX4ABC`) issues 6 sequential IPC round-trips, one per character, each a keyring read on the Rust side. Each round-trip also re-renders the panel via `setPasswordStatus`. The cancellation flag prevents stale-state writes but does NOT cancel the in-flight IPC — the backend work still happens.

Impact: ~1 IPC round-trip per typed character on the most interactive field of the P2P panel; 6–10 round-trips for a normal callsign entry, vs. 1 with a trailing debounce. Keyring reads are not free at the OS layer (the comment's "fast" assumption is the justification, not a measurement).

Confidence: High (code is unambiguous; debounce explicitly omitted).
Effort: Low — wrap in a 250–400 ms trailing debounce (`setTimeout` cleared in the effect cleanup), keyed on the same dep.
Verification: Type a 6-char callsign with `invoke` instrumented (or a Rust-side counter on `p2p_peer_password_status`); expect 1 call after typing stops instead of 6.

---

### [MINOR] Fixed 4× concurrent `mailbox_list` polls regardless of which folder is visible

Location: `src/shell/AppShell.tsx:288-299` (out of scope file but the consumer of the in-scope `useMailbox`, `src/mailbox/useMailbox.ts:67-74`, `refetchInterval: 10_000`).

Problem: AppShell instantiates `useMailbox` for `selectedFolder` plus a fixed `inbox`/`sent`/`outbox`/`archive` set (for sidebar badges). Each carries an independent 10 s `refetchInterval`. TanStack Query dedupes by `queryKey: ['mailbox', folder]`, so `useMailbox(selectedFolder)` collapses into the matching system-folder query when the active folder is a system folder (no duplicate there — good). But the four system-folder badge queries each poll every 10 s *whether or not their folder is on screen*: 4 `mailbox_list` round-trips / 10 s = 0.4 IPC/s steady-state, continuous for the app's lifetime, purely to keep sidebar count badges fresh. Badge counts change only on CMS connect / message move — events the backend already knows about.

Impact: ~0.4 mailbox-list IPC round-trips/s at idle (4 every 10 s), unbounded in time. Each returns a full folder's metadata array (not bodies — see calibration), so payload is bounded, but the round-trip + JSON deserialize on the UI thread recurs forever for folders the user isn't looking at.

Confidence: Medium (polling is real; whether it's a felt cost depends on folder sizes + Pi IPC latency).
Effort: Medium — either gate badge queries behind a backend `mailbox:changed` event + invalidate (event-push instead of poll), or raise `refetchInterval`/add `refetchIntervalInBackground: false` and a longer `staleTime` for the badge-only queries (they don't need 10 s freshness).
Verification: Count `mailbox_list` invocations over 60 s at idle on a fixed folder — expect 24 (4×6) today; target near 0 with event-driven invalidation.

---

### [MINOR] Three always-on 1 Hz `setInterval` timers in the ARDOP panel, decoupled from the live status cadence

Location: `src/radio/useSampleHistory.ts:44-49` (instantiated twice — SNR + throughput, `ArdopRadioPanel.tsx:324-325`) and `useFrameHistory` `ArdopRadioPanel.tsx:138-143` (`:326`).

Problem: Three independent `setInterval(…, 1000)` timers each push one sample/sec into a rolling 60-element buffer via `setSamples((prev) => [...prev.slice(1), …])`. These are timers, not IPC, so they are not chatty `invoke` — but they are sampling the *event-pushed* `useModemStatus` stream (`src/modem/useModemStatus.ts`, 4 Hz `modem:status` events) on a *second, unsynchronized* clock. The result: the live status arrives at 4 Hz via one well-behaved event subscription (good — that part is correct), then three timers re-sample it at 1 Hz and each triggers a state update + array spread + panel re-render every second, for the entire time the ARDOP panel is mounted, even when the modem is Stopped and the status never changes (the buffer keeps shifting in zeros / IDLE frames).

Impact: 3 timer-driven re-renders/s + 3 60-element array reallocations/s while the ARDOP panel is open, independent of whether any new data arrived. Not an IPC cost; a steady allocation/render cost on the UI thread that runs even at idle. Borderline against the "single mount-time fetch / style" calibration, flagged MINOR because it is unconditional and compounds with the panel's other per-second `useListenerState` tick.

Confidence: Medium.
Effort: Medium — drive the buffers off the 4 Hz status event directly (push on event, decimate to 1 Hz by timestamp) instead of a free-running interval; or pause the timers when `status.state === 'stopped'`.
Verification: React Profiler with ARDOP panel open + modem stopped — expect ≥3 commits/s today; target 0 commits/s at idle-stopped.

---

### Calibration notes (NOT findings)

- `useModemStatus` (`src/modem/useModemStatus.ts`) is correctly **event-pushed** (one `listen('modem:status')` + one mount snapshot), with a deduped `useModemIsActive` selector for AppShell to avoid the 4 Hz render-storm. This is the right pattern, not a finding.
- `useSessionLog` (`src/radio/sections/useSessionLog.ts`) subscribes-then-snapshots with seq-dedup merge and a clean unlisten on unmount — no listener churn, no duplicate handlers. Correct.
- Mailbox **over-fetch check passed**: `mailbox_list` returns `MessageMeta` (`src/mailbox/types.ts:15-36`) — headers/size/flags only, NO body. The list does not pull full message bodies; `message_read` (`useMessage.ts`) fetches one body lazily on selection. No N+1 over the list. `preview` is fixture-only today.
- `listen`/`invoke` effects across the mode panels (`TelnetRadioPanel`, `VaraRadioPanel`, `PacketRadioPanel`, config loaders) are single mount-time fetches with `cancelled` guards and `[]` deps — calibrated as acceptable, not chatty.
- `useListenerState` 1 Hz countdown tick (`useListenerState.ts:120-124`) is gated on `armed` and cleans up — acceptable; noted only as it compounds with the ARDOP timers above.
- No N+1 IPC found (no per-list-item `invoke`); no `invoke` re-fired by unstable effect deps (all loaders use `[]` or stable callback deps); no large-payload UI-thread deserialize beyond the bounded metadata arrays.

---

## Suspected Bugs

- `src/radio/modes/TelnetP2pRadioPanel.tsx:260-276` — the per-keystroke `p2p_peer_password_status` effect not only over-invokes (MAJOR above) but its in-flight IPC is uncancelled; a fast typer can have several keyring reads racing, and the last-to-resolve (not last-typed) could win if the `cancelled` guard timing differs from resolution order. Low severity (the guard mostly covers it) but worth a debounce that also collapses the race. Recorded per instructions; not chased.
