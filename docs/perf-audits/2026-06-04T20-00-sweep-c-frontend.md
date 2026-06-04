# COLD-SWEEP C — Frontend (algorithmic/render + allocation + data-access/IPC)

Agent: glade-knoll-shoal
Date: 2026-06-04T20:00Z
Scope: `src/` frontend — shell, search, compose, packet, connections, session, modem, wizard, forms, help, grib, catalog, App.tsx, main.tsx, routing.ts (excl. `*.test.tsx`). Read-only.
Purpose: catch anything warm hiding in the cold batch. NOT a memo-everything nitpick pass.

## Verdict

No significant warm findings — confirmed cold. The frontend shows consistent, deliberate perf hygiene; the known warm-exception candidates were all already mitigated in source.

## Known warm-exception checks (all PASS)

### markdown render / sanitize on large help bodies — MITIGATED
- `src/help/ReadingPane.tsx:79-82`: `sanitizeHtml(renderMarkdown(topic.body))` is wrapped in `useMemo` keyed on `topic.body`. Parse + DOMPurify only re-run when the active topic changes, not per render. Comment cites the prior bug (tuxlink-ew3k bug 6) where it ran every render. `renderMarkdown` (`src/shell/markdownRender.ts:26-42`) builds the `Marked` instance + extension chain once at module load (module-level `const marked`), not per call. Image glob is `eager` at build time. Cold.

### 4 Hz `modem:status` wide subscription — GATED, no leak into shell
- `src/modem/useModemStatus.ts`: the full `useModemStatus()` (re-renders consumer 4×/s) is consumed ONLY by `src/radio/modes/ArdopRadioPanel.tsx:249` — out of scope, owned by R9. AppShell uses `useModemIsActive()` (`AppShell.tsx:123`), the gated selector that calls `setState` only on the `stopped`↔active boolean transition (`useModemStatus.ts:49-83`, explicit `last` dedupe gate). No other in-scope panel takes the wide 4 Hz stream. Cold.

### AppShell badge-poll (R9-6: 4 folder queries at 10s) — CONFIRMED bounded, not worse
- `AppShell.tsx` ~293-302: Outbox/Archive/user-folder counts via react-query at 10s refetch; comments flag them as "cheap query." No tighter interval, no per-render invocation. Cold.

### per-keystroke IPC (R9-2 pattern) — NOT PRESENT in scope
- Search is debounced + query-gated: `src/search/useSearch.ts:22-52` (`debouncedSpec` state feeds `useQuery(['search', debouncedSpec])`; `parseQuery`/`specIsActive` memoized). `useSavedSearches.ts:47-50` explicitly records recents per-debounce, not per-keystroke (comment confirms the original per-keystroke logging was removed).
- Compose `onChange` handlers (`Compose.tsx:537-664`) set local state only — no IPC. Autosave is a single 2s `setInterval` (`Compose.tsx:187-200`) with a `sentRef` guard. Bounded.

### wide context providers re-rendering subtrees — one cold nit only
- `src/wizard/wizardContext.tsx:25`: `<WizardContext.Provider value={{ state, dispatch }}>` builds a fresh object literal each render, so every `useWizard()` consumer re-renders on any provider render. This is the textbook un-memoized-context-value pattern — BUT it lives behind the wizard flow (first-run / setup), a low-frequency, non-hot subtree with a small consumer count. Not warm. Logged here for completeness, not as a perf finding.
- `src/App.tsx:98-100`: only `QueryClientProvider` (client is a stable module/ref `queryClient`). No wide app-level state context. Cold.

### expensive un-memoized derivations in frequently-rendered components — NONE FOUND
- `useStatus.ts:326-353`: status bar polls via react-query (config 5s / backend 2s / position 2s), all `enabled: !DEV_FIXTURE`, `retry:false`, with an event-driven `setQueryData` fast-path for sub-second CMS transitions. Derivations are pure formatters over polled DTOs. Bounded.
- 1s clock ticks (`DashboardRibbon.tsx:28`, `useListenerState.ts:122`) are isolated `setNow`/`setTick` in small leaf components — standard, not warm.
- No `.filter().map()` chains, `JSON.parse`, or per-render allocations spotted in render bodies of packet/connections/session/grib/catalog; no `setInterval`/`listen` render-storms in those dirs.

## What was scanned
- grep sweep for `setInterval` / `listen(` / `useModemStatus` / `markdownRender` / `refetchInterval` / per-keystroke `invoke` / `createContext` across all in-scope dirs.
- Targeted reads: `useModemStatus.ts` (full), `markdownRender.ts` (full), `ReadingPane.tsx` (memo block), `useStatus.ts` (poll block), `Compose.tsx` (autosave + onChange), `useSearch.ts` / `useSavedSearches.ts` (debounce), `wizardContext.tsx` (full), `App.tsx` (providers), AppShell badge-poll region.

## Suspected Bugs
None.
