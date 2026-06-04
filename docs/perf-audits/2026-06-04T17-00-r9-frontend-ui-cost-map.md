# r9 — Frontend UI Execution Cost Map (radio/ + mailbox/)

Agent: glade-knoll-shoal · 2026-06-04T17:00Z
Scope: `src/radio/` + `src/mailbox/` (excl. `*.test.tsx`). Warm desktop UI, Tauri/React 19/react-virtuoso 4.18.

## Execution Cost Map
> Architectural awareness, NOT a to-do list.

### Likely re-render / cost concentration regions

- **`ArdopRadioPanel` whole-component re-render @ 4 Hz** (`modes/ArdopRadioPanel.tsx:249` `useModemStatus()`) — basis: the panel subscribes to `useModemStatus`, whose underlying Rust `modem:status` broadcast is **4 Hz** (`modem/useModemStatus.ts:39-41` doc comment; `:24` `setStatus(e.payload)` fires every tick with a fresh object reference). Every tick re-runs the entire ~830-line function body and re-renders all children (ARQ grid, Live, SignalSection, SessionLogSection, Listen, Actions). The 1 Hz `useSampleHistory` tick is a *second*, smaller cadence layered on top. So the dominant periodic cost is 4 Hz, not the 1 Hz framing in the brief. — confidence: High — overlaps a likely hot-spot

- **`SignalSection` + `Sparkline` (S/N) re-render every parent tick** (`sections/SignalSection.tsx:43`, `charts/Sparkline.tsx:40`) — basis: SignalSection is an unmemoized child of ArdopRadioPanel, so it re-renders on all 4 status ticks even though `snrSamples` only changes at 1 Hz. Each render recomputes `avgSnr` via `snrSamples.reduce` over 60 entries (`SignalSection.tsx:49-52`) and Sparkline maps 60 bars building inline style objects + className branch per bar (`Sparkline.tsx:55-72`), plus `Math.max(...samples,1)` spread (`:50`). Two Sparklines exist (S/N here + throughput at `ArdopRadioPanel.tsx:945`). Cost is small-but-real and paid 4×/sec while connected. — confidence: High — overlaps a likely hot-spot

- **`SessionLogSection` list render + auto-scroll effect** (`sections/SessionLogSection.tsx:43`) — basis: re-renders on every parent (4 Hz) tick AND grows unboundedly with `entries` — `filtered.map` rebuilds the full non-virtualized log DOM each render (`:72-82`), and the `useEffect` on `[entries, autoScroll, showRaw]` writes `scrollTop` every tick (`:56-60`). `useSessionLog` re-projects ALL lines via `toSessionLogEntries(lines)` on every render (`useSessionLog.ts:213`) — O(n) array+object alloc per render, n = full session-log length. Unbounded-list × parent-tick frequency makes this the steepest-growing cost over a long session. — confidence: Med — overlaps a likely hot-spot

- **`MessageList` sort derivation** (`mailbox/MessageList.tsx:294` `useMemo(sortMessages, [messages, sortState, folder])`) — basis: `sortMessages` copies + `Array.sort` over the full message set with `localeCompare` per comparison (`messageSort.ts:131-168`). Properly memoized, so it only recomputes when messages/sort/folder change — NOT on selection or typing. Unit cost O(n log n) with locale compares; frequency LOW (re-fetch / sort-toggle only). — confidence: High — map-only

- **Virtuoso row churn on selection change** (`MessageList.tsx:338-352`) — basis: `selectedId` changes re-run the parent `MessageList` render and rebuild the `itemContent` closure; `MessageRow` is `React.memo` (`MessageList.tsx:139`) with stabilized `onSelect`/`onContextMenu` (`useCallback`, `:311`), so only the ~visible window of rows reconciles and only the two rows whose `selected` flips actually repaint. Per-row `useMemo`s (size/correspondent/subject/preview) keep row unit cost low. `dateLabel` is deliberately un-memoized (`:149`) — cheap. Frequency = per selection/scroll; unit cost low. — confidence: High — map-only

- **`MessageViewLoaded` body render on selection** (`mailbox/MessageView.tsx:141`) — basis: rendered once per selected message (gated by `useMessage` TanStack query, `useMessage.ts:82`). Body is a plain `<pre>{message.body}</pre>` (`:310-312`) — no markdown parser, no sanitizer on the body path (sanitize.ts is attachment-name only). Form path dispatches to a registered View / `KeyValueView` (`:282-297`). Largest unit cost is a big plain-text body or a complex form payload, but frequency is per-selection (event-driven), not periodic. — confidence: High — map-only

- **`FrameRibbon` re-render @ parent tick** (`charts/FrameRibbon.tsx:27`) — basis: unmemoized child of SignalSection; renders `frames.slice(-14)` + a 6-entry legend each parent render (4 Hz). `frameHistory` itself updates at 1 Hz (`ArdopRadioPanel.tsx:326` `useFrameHistory`). Tiny (≤20 nodes). — confidence: High — map-only

### Notes for architecture

- The frequency driver is **4 Hz status, not 1 Hz**. The 1 Hz `useSampleHistory`/`useFrameHistory` interval controls *buffer mutation* cadence, but the panel re-renders 4×/sec regardless because `useModemStatus` pushes a new `ModemStatus` object every status broadcast. `useModemIsActive` (`useModemStatus.ts:49`) already demonstrates the dedupe pattern (only setState on change) — the full-status subscription in the panel has no such gate. This is by design per the `:37-47` comment ("desirable for the live-meter panels"), so it is a deliberate cost concentration, not an accident.
- Re-render blast radius is wide because ArdopRadioPanel holds all section children inline with no `React.memo` boundaries between the 4 Hz status state and the largely-static sections (Connect form, Radio device pickers, Listen/allowlist). Connect+Radio are gated behind `isStopped` so they're unmounted while connected (the high-frequency window), which limits the damage.
- The only non-virtualized growing list is the **Session log** (`SessionLogSection`); the message list is virtualized. Over a long connected session the session-log re-projection + full-DOM map is the most likely place cost compounds with both frequency (4 Hz) and size (unbounded n).
- Reader (`MessageView`) and message-list sort are event-driven (selection / fetch / sort-toggle), not periodic — they sit outside the warm-loop hot path.
