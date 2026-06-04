# Perf audit r9 — React render idioms / reactivity (React 19)

Agent: glade-knoll-shoal
Scope: `src/radio` + `src/mailbox` (non-test `.tsx`/`.ts`)
Lens: `javascript-typescript/react.md` as prior.

## Calibration correction (load-bearing)

The brief states "the ONLY periodic re-render is a ~1 Hz signal sparkline
tick." That under-states the dominant driver. The backend emits
`modem:status` at **4 Hz** (`src-tauri/src/modem_status.rs:355`,
`STATUS_POLL_INTERVAL = from_millis(250)`), and `useModemStatus`
(`src/modem/useModemStatus.ts:24`) calls `setStatus(e.payload)` on **every**
event with a fresh object reference. The whole `ArdopRadioPanel` function body
re-runs 4×/sec whenever the modem is active. So the real cadence for the ARDOP
panel is 4 Hz, and the sparkline `setInterval` adds a separate, independent 1 Hz
re-render on top. This re-frames every finding below: the ARDOP panel is the one
component in scope where re-render scope actually matters. Telnet/Packet/VARA
panels are event-only (no periodic stream) and the mailbox is Virtuoso-guarded;
those are not cascades.

No React Compiler is configured (no `babel-plugin-react-compiler` /
`react-compiler` in the build); manual memoization is load-bearing, not clutter.

---

### [MAJOR] ARDOP `SessionLogSection` re-renders + re-projects the full log array at 4 Hz

Location: `src/radio/modes/ArdopRadioPanel.tsx:1013` (render site);
`src/radio/sections/useSessionLog.ts:213`; `src/radio/sections/SessionLogSection.tsx:43`

Problem: `useSessionLog` returns `entries: toSessionLogEntries(lines)` —
`lines.map(toSessionLogEntry)` runs unconditionally on **every** hook call
(useSessionLog.ts:213), producing a brand-new array + new entry objects each
render. Because `ArdopRadioPanel` re-renders at 4 Hz (modem status), this maps
the entire accumulated log (which only grows over a session) four times a second
even when no new log line arrived. `SessionLogSection` is not `memo`'d and gets a
new `entries` array reference every time, so it always re-renders and re-runs its
`entries.filter(...)` (SessionLogSection.tsx:62) plus the `useEffect` keyed on
`[entries, ...]` (line 56) that writes `scrollTop` — i.e. a forced
scroll-to-bottom layout write 4×/sec regardless of whether the log changed.

Impact: O(log-length) array re-projection + filter + a layout-triggering
scrollTop write, 4×/sec, for the entire ARDOP session. Grows with session
length. The only in-scope finding with a sustained periodic cost.

Fix space: memoize the projection (`useMemo(() => toSessionLogEntries(lines),
[lines])` inside the hook) and wrap `SessionLogSection` in `memo` so it skips the
4 Hz status ticks entirely; gate the auto-scroll effect on a length/last-seq
change rather than array identity.

Confidence: High. Effort: Low.
Verification: React DevTools Profiler with modem active — `SessionLogSection`
shows ~4 commits/sec with "props changed: entries". After the fix it commits only
when a log line arrives. Render-count argument: status-tick renders of the parent
should not reach the log section. Correctness guard: existing `useSessionLog` /
`SessionLogSection` tests must still pass (dedupe-by-seq, auto-scroll on new line).

---

### [MINOR] Two independent 1 Hz `setInterval`s drive whole-panel re-renders alongside the 4 Hz stream

Location: `src/radio/useSampleHistory.ts:44` (×2 instances: snr + throughput,
ArdopRadioPanel.tsx:324-325); `useFrameHistory` ArdopRadioPanel.tsx:138

Problem: `useSampleHistory` and `useFrameHistory` each own a `setInterval` that
calls `setSamples`/`setFrames` once per second. These hooks live at the top of
`ArdopRadioPanel`, so each tick re-renders the **entire panel** (Connect form,
Radio device pickers, ARQ grid, Live `<pre>`, Listen section, SessionLog), not
just the sparklines/ribbon they feed. Three hooks → three independent 1 Hz
whole-panel renders, on top of the 4 Hz status renders. The state genuinely lives
too high: only `Sparkline`/`FrameRibbon` consume it.

Impact: 3 extra whole-panel renders/sec while active. Modest in absolute terms
(the panel's DOM is small and unmemoized children are cheap), but it is the
"interval lives high in the tree and re-renders siblings" pattern the brief
flagged. Combined with the MAJOR above, each of these ticks also re-pays the
log re-projection.

Fix space: push the sparkline state ownership down — a small `<SnrSparkline
current={status.snDb}/>` leaf that owns its own `useSampleHistory` so the tick
re-renders only the leaf. Or accept it and just fix the log section (the MAJOR),
which removes the expensive part of each tick.

Confidence: High (mechanism). Effort: Medium (requires restructuring where the
buffers live). Verification: Profiler — confirm 3 commits/sec of the panel root
attributable to "Hook 1/2/3 changed"; after pushing state down, only the leaf
commits.

---

### [MINOR] Memo'd `MessageRow` receives a fresh `highlights` array every render

Location: `src/mailbox/MessageList.tsx:154`, used at :155 and :159

Problem: `MessageRow` is `memo`'d (good) and most props are stabilized, but
`const highlights = matchHighlight ?? [];` creates a **new `[]`** every render
whenever `matchHighlight` is absent (the common, non-search case). That new array
is then a dep of the `subjectNode`/`previewNode` `useMemo`s (lines 155, 159), so
those memos recompute every time the row renders even though their logical inputs
(subject/preview text) are unchanged. The `memo` wrapper itself still protects
against parent re-renders (the row's other props are stable via Virtuoso's
`computeItemKey` + the `useCallback`'d handlers), so this only bites when the row
*does* re-render for another reason — bounded, hence MINOR.

Impact: wasted `applyHighlights` recompute (a `.filter` returning `[]` fast-path,
so cheap) on rows that re-render. Not a cascade; Virtuoso caps live rows to the
viewport (~20). Correctness is fine.

Fix space: hoist a module-level `const NO_HIGHLIGHTS: HighlightRange[] = []` and
use `matchHighlight ?? NO_HIGHLIGHTS` so the empty case has a stable identity.

Confidence: High. Effort: Trivial.
Verification: render-count argument — the two highlight `useMemo`s recompute only
on subject/preview/highlight change after the fix. why-did-you-render on
`MessageRow` would show the array dep churning before the fix.

---

### [MINOR] `MessageRow` `onClick`/`onContextMenu`/`onDragStart`/`onKeyDown` are inline arrows; `Virtuoso itemContent` rebuilds the element each scroll

Location: `src/mailbox/MessageList.tsx:174-194` (inline handlers);
:341 (`itemContent` closure)

Problem: every JSX handler on the row is an inline arrow (`onClick={() =>
onSelect(message.id)}` etc.). Since `MessageRow` is the memo boundary and these
are created *inside* it, they don't break the `memo` (they're not props crossing
it) — so this is NOT a cascade. Noting it only because `itemContent` at :341 is
itself an inline closure passed to `Virtuoso`: it allocates a new
`<MessageRow .../>` element every time `MessageList` renders. `memo` on
`MessageRow` still short-circuits the actual render via prop equality, so the
cost is element allocation + shallow compare for the ~20 live rows. Acceptable;
documented so it isn't re-flagged as a cascade.

Impact: negligible (≤ viewport rows, shallow compares). Confidence: High that
it's benign. Effort: n/a — do not "fix" by memoizing inline handlers; that's the
cargo-cult the brief warns against.

---

## Calibration notes (deliberately NOT findings)

- `useModemIsActive` (useModemStatus.ts:49) is the correct pattern — it dedupes
  the 4 Hz stream to a boolean so AppShell doesn't storm. Good; cite as the model
  the SessionLog projection should follow.
- `MessageList` `sortMessages` is `useMemo`'d on `[messages, sortState, folder]`
  (MessageList.tsx:294) — correct.
- `formatRowDate` deliberately un-memoized (MessageList.tsx:149) with a documented
  rationale (UTC-day-boundary correctness). Correct trade-off; not a finding.
- `useListenerState` countdown `setInterval` (useListenerState.ts:120) only runs
  while `armed`; bounded and correct.
- Telnet/Packet/VARA panels have no periodic stream — purely event-driven; no
  cascade. `AllowedStationsEditor` is leaf-local state. No issues.
- `key={i}` (index keys) in `Sparkline` (:67), `FrameRibbon` (:33), and
  `SessionLogSection` (:73): these are fixed-length, append-only / shift buffers
  rendered in order — index keys are appropriate here (the list is positional, not
  reorderable). Not flagged.

## Suspected Bugs

None.
