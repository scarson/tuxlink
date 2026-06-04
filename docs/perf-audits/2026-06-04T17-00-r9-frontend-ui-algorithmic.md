# Perf Audit r9 — Frontend UI, Algorithmic Complexity (per-render & data transforms)

Auditor: glade-knoll-shoal. Dimension: expensive per-render computation / data
transforms not memoized. Scope: `src/radio` + `src/mailbox` (excl. `*.test.tsx`).
Read-only. Load calibration: interactive Tauri UI; modem status streams at 4 Hz;
three `useSampleHistory`/`useFrameHistory` intervals tick at 1 Hz each; mailbox
holds dozens–hundreds of messages, virtualized.

---

### [MAJOR] Whole session-log re-projected (`.map`) on every panel render

**Location:** `src/radio/sections/useSessionLog.ts:213` (`return { entries: toSessionLogEntries(lines), clear }`); `toSessionLogEntries` at `:45`. Consumed by `ArdopRadioPanel.tsx:297`, and the same hook drives the Telnet/Packet/VARA panels.

**Problem:** `useSessionLog` returns `toSessionLogEntries(lines)` computed inline on **every render** of the consuming panel — there is no `useMemo`. `toSessionLogEntries` is `lines.map(toSessionLogEntry)`, allocating a fresh `SessionLogEntry[]` (one new object + an `extractHms` slice + `projectLevel` scan per line) across the *entire* accumulated log. The `lines` buffer is **unbounded** — `mergeLogLine`/`mergeLogLines` only ever append/insert; nothing evicts. ArdopRadioPanel re-renders on the 4 Hz `useModemStatus` stream (`useModemStatus.ts:24`, "emits at 4 Hz") **and** on each of the three 1 Hz interval `setSamples`/`setFrames` updates (`ArdopRadioPanel.tsx:324–326`). So this full re-map runs ~4–7×/s regardless of whether the log changed. After a long emcomm session the log is thousands of lines; the per-render cost is O(n_lines) allocation + string work on the main thread, plus it hands `SessionLogSection` a new array identity every time, defeating any downstream memo and forcing its `entries`-keyed auto-scroll effect (`SessionLogSection.tsx:56–60`) to fire each tick.

**Impact:** The one genuinely periodic, render-driving hot path in the radio UI multiplied by an O(n) transform over a monotonically growing buffer. Modest while the log is short; degrades steadily over a session — exactly the "runs on the periodic tick" case the calibration flags as real. Also a latent unbounded-growth concern (memory lane, noted here only as the multiplier on this transform's cost).

**Confidence:** High (no memo at `:213`; tick cadence confirmed at `useModemStatus.ts:37–47` + the three intervals).
**Effort:** Low. `const entries = useMemo(() => toSessionLogEntries(lines), [lines]);` in `useSessionLog`. `lines` identity only changes when a merge produces a new array, so the memo recomputes only on actual log change. (Independently, consider capping `lines` length.)
**Verification:** React Profiler on ArdopRadioPanel while modem streams + log idle: confirm `toSessionLogEntries` flame disappears from the 1/4 Hz commits after the memo; confirm `SessionLogSection` auto-scroll effect no longer fires per tick.

---

### [MINOR] `SessionLogSection` re-filters the full log every render

**Location:** `src/radio/sections/SessionLogSection.tsx:62` (`const filtered = showRaw ? entries : entries.filter(e => e.level !== 'raw')`).

**Problem:** `entries.filter(...)` runs unmemoized on every render. Today it is *driven* by the MAJOR above — because `entries` gets a new identity each tick, this filter also re-runs ~4–7×/s over the whole log. Fixing the MAJOR (stable `entries` identity) removes most of the churn, but the filter itself is still recomputed on any parent render (e.g. the `showRaw`/`autoScroll` toggles trigger it anyway, which is fine). Listed separately because it is a second O(n_lines) pass over the same growing buffer in the same commit.

**Impact:** Second linear pass over the unbounded log per render; compounds the MAJOR until that is fixed, then becomes negligible. Real only as a multiplier on the tick storm.
**Confidence:** High (inline filter, no memo).
**Effort:** Low. `const filtered = useMemo(() => showRaw ? entries : entries.filter(e => e.level !== 'raw'), [entries, showRaw]);`. Best paired with the MAJOR fix so `entries` is stable.
**Verification:** Profiler: confirm the filter no longer appears in idle-log tick commits after both fixes.

---

### [MINOR] `useSampleHistory` / `useFrameHistory` rebuild the whole 60-element buffer each tick

**Location:** `src/radio/useSampleHistory.ts:46` (`setSamples((prev) => [...prev.slice(1), latest.current ?? 0])`); same pattern in `ArdopRadioPanel.tsx:140` (`useFrameHistory`).

**Problem:** Each 1 Hz tick slices + spreads a new length-60 array. This is the textbook "rebuild on every tick" pattern, but the buffer is **fixed at 60** — it is bounded and tiny by design (spec §5.3). Three of these run per second on the ARDOP panel. The allocation is intentional and necessary (a new array identity is what tells React + `Sparkline` to re-render the trace), and 60 elements × 3 buffers × 1 Hz is trivial. Flagged only because it is literally on the periodic path the calibration named; impact is well below the bar.

**Impact:** Negligible — bounded 60-element arrays at 1 Hz. No argued user-visible cost.
**Confidence:** High (code is explicit) that it is *bounded*; high that impact is minor.
**Effort:** None recommended. A ring buffer would avoid the spread but adds complexity for no measurable gain and would break the new-identity-triggers-render contract.
**Verification:** N/A — would not pursue.

---

### [MINOR] `MessageList` sort is correctly memoized — no finding

**Location:** `src/mailbox/MessageList.tsx:294` (`sortedMessages = useMemo(() => sortMessages(...), [messages, sortState, folder])`); `messageSort.ts:163` (`sortMessages` = `[...messages].sort(...)`).

**Problem:** None. `sortMessages` is O(n log n) over the message array, but it is wrapped in `useMemo` keyed on `(messages, sortState, folder)`, so it only re-runs when the data or sort actually changes — **not** per render, not per keystroke, not on the modem tick (the list pane and radio panel are separate subtrees). The list is virtualized (Virtuoso, `:338`) and rows are `memo`'d with per-row `useMemo`s (`:139–162`) and stabilized callbacks (`onSelect`, `onContextMenu` via `useCallback` `:311`). For dozens–hundreds of messages this is well within budget. `compareMessages` does `normalize()` (trim+lowercase) on each comparison rather than precomputing sort keys — an O(n log n) × normalize cost — but at hundreds of items on a change-only path this is a micro-opt with no argued impact (calibration: explicitly not a finding). Documented here to record that the named hot spot was examined and is clean.

**Impact:** None.
**Confidence:** High.
**Effort:** N/A.
**Verification:** Sort runs only on data/sort/folder change — confirmed by the memo deps.

---

### Items examined and cleared (no finding)

- `MessageRow` per-row derived data (`MessageList.tsx:142–162`): memoized; `formatRowDate` deliberately left unmemoized (`:144–149`) for wall-clock correctness, cost is one `Date` + format — correct call.
- `applyHighlights` (`MessageList.tsx:96–111`): `filter` + sort over highlight ranges, but ranges per field are tiny (search matches in one subject/preview) and the result is memoized per row (`:155–161`). Bounded-small; not a finding.
- `SignalSection` avg-S/N `reduce` (`SignalSection.tsx:49–52`): O(60) reduce per render, unmemoized, but operates on the fixed 60-sample buffer that already changes identity each tick — recompute is unavoidable and trivially bounded. Not a finding.
- `Sparkline` `Math.max(...samples,1)` + `.map` (`Sparkline.tsx:50,55`): O(60), bounded, must re-render when samples change. Not a finding.
- `mergeLogLine` insert scan (`useSessionLog.ts:90–105`): O(n) per incoming line via the dedup loop + ordered insert. This is per-*event*, not per-*render*, and event rate is human-paced log lines, not a tick. At thousands of lines the dedup `for` scan is O(n) per line → O(n²) over a session ingest, but realistic log volume and arrival cadence keep it well under budget for this UI. Noted as a watch-item, not a finding.
- `captureHardware`/`playbackHardware` filters (`ArdopRadioPanel.tsx:476–477`): unmemoized filters over device lists, but lists are a handful of entries and only rendered in the stopped state. Bounded-small; not a finding.

---

## Suspected Bugs

None. (`useSampleHistory`'s unbounded `lines` buffer in `useSessionLog` is a perf/memory concern captured under the MAJOR, not a correctness bug — projection and merge logic are internally consistent.)
