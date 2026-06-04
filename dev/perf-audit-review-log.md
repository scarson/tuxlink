# Performance-Audit Scope Partition — Adversarial Review Log

**Decision under review:** how to partition the whole tuxlink repo into bounded
slices, each one `performance-audit-cycle` run, collectively covering all code.

**Operating mandate (operator, 2026-06-04):** autonomous operation; substantive
decisions require multiple perspectives + **≥5 rounds adversarial review** +
persistent documentation. Reviewers are independent Opus subagents (Codex
unavailable in this container). Commit+push after every unit (ephemeral
container). This log + `dev/perf-audit-reviews/round-N.md` are the persistent
record for later operator review.

**Plan artifact:** `dev/perf-audit-scope-plan.md` (versioned; git history holds
each revision). **Execution ledger:** `dev/perf-audit-progress.md`.
**Skill UX feedback:** `dev/perf-audit-skill-feedback.md`.

---

## Round status

| Round | Reviewer | Target | Full report | Result → revision |
|-------|----------|--------|-------------|-------------------|
| 1 | Opus subagent | v1 | `round-1.md` | major rework → v2 |
| 2 | Opus subagent | v2 | `round-2.md` | minor-edits → v3 |
| 3 | Opus subagent | v3 | `round-3.md` | minor-edits → v4 |
| 4 | Opus subagent | v4 | `round-4.md` | pending |
| 5 | Opus subagent | v5 | `round-5.md` | pending |

Convergence rule: continue past 5 only if a round still finds a *substantive*
(tier-changing or coverage-breaking) defect. Finalize when ≥5 done and the last
round finds only nits.

---

## Round 1 — dispositions (v1 → v2)

Full report: `dev/perf-audit-reviews/round-1.md`. All five top recommendations
ACCEPTED:

1. **Re-size on production LOC** — ACCEPTED. Verified independently:
   `tuxmodem-phy` 1760 prod / 1358 test; raw ≈ 2× production. Dissolved most v1
   "oversized" flags.
2. **Delete frontend "render hot path" framing** — ACCEPTED. Verified: zero
   `canvas`/`getContext`/`WebGL`, only one one-shot `requestAnimationFrame`
   (`help/ReadingPane.tsx:41`). Tier E re-tiered warm/cold.
3. **Split D4 by perf-relevance** — ACCEPTED. `search/` → MUST-DO (M4);
   forms/grib/position/catalog → cold sweep.
4. **Add `audio_device.rs` + live-RX pipeline overlay** — ACCEPTED. M1 now =
   entire `tuxmodem-phy/src` incl. audio path; O1 overlay added.
5. **Close coverage gaps + exclude test/bin + recommend Criterion benches** —
   ACCEPTED. `main.rs`, `App.tsx`/`main.tsx`/`routing.ts` assigned; out-of-scope
   list added; benches flagged as the M1/M2 measurement gate.

Other accepted: ARDOP modem is I/O+state (not DSP) but still M3 full for
buffering/throughput; hf-channel-sim caches its FftPlanner, tiered M5 (lower
urgency); tx/rx are thin CLI drivers → R1 reduced depth.

Outcome: 22 slices → **5 full + 1 overlay + 9 reduced + 1 cold sweep = 16
units**. See v2 in plan artifact.

---

## Round 2 — dispositions (v2 → v3)

Full report: `dev/perf-audit-reviews/round-2.md`. Verdict: **minor-edits** — v2
fundamentally sound and NOT over-corrected. All findings ACCEPTED:

1. **Coverage gap `wizard.rs` (blocking)** — ACCEPTED. The 614-LOC Rust Tauri
   `WizardMutex` command module was conflated with TS `src/wizard/` and omitted
   from the cold-sweep Rust enumeration. Added to cold sweep.
2. **New hot path H1 (`constellations.rs::compute_llr:142`)** — ACCEPTED. Per-
   data-subcarrier `self.alphabet()` rebuild (up to 64 nested `map()` allocs),
   called at `receiver.rs:83` inside the RX demod inner loop — worse than the
   per-symbol planner. Added to M1 hot-path map as Critical.
3. **Demote M3 (ARDOP) full → reduced** — ACCEPTED. Verified
   `transport.rs`/`data.rs` are external-TNC socket I/O + framing + state
   machine (DSP in `ardopcf` child, `process.rs:74`); a full cycle's
   complexity/payload/startup lanes have nothing to bite. Reduced-depth with
   concurrency (split-borrow hazard `transport.rs:666`) + alloc + I/O focus.
4. **Promote storage backend out of cold sweep** — ACCEPTED. `native_mailbox.rs::list:99-104`
   does `read_dir` + `fs::read(body)` per message (N+1 / read-amplification) —
   the backend root of R9's non-virtualized `MessageList`. New reduced slice R10
   (native_mailbox + config + user_folders + session_log).
5. **Correct two soft claims** — ACCEPTED. (a) prod:test is per-file variable
   (~0.10×–6.89×), not a flat 2× (principle 2 reworded). (b) hf-sim caches its
   planner only in `channel.rs`; `fading.rs:49,86` + `analysis.rs:50` re-plan
   per call (added as H9, strengthens M5).
6. **New hot path H7 (`narrow_fsk.rs:83` per-call FftPlanner)** — ACCEPTED;
   added to M1 (Major). **Seam note:** `process.rs` shared M3↔R4, primary home
   R4 (M3 reads it as adjacent context).

Outcome: 16 → **4 full + 1 overlay + 11 reduced + 1 cold sweep = 17 units** (M3
moved full→reduced; R10 added). M5 down-ranked below M4. See v3 in plan artifact.

---

## Round 3 — dispositions (v3 → v4)

Full report: `dev/perf-audit-reviews/round-3.md`. Verdict: **minor-edits** —
v3 executable, coverage airtight, winlink-family reduced-tiering verified
correct (the area rounds 1–2 barely read). Directed at the under-examined
winlink protocol family, cold-sweep internals, and frontend.

1. **Round 2's 4 claims (H1/H7/H8/H9) — all CONFIRMED against source.** Two
   refined: H1 worse than stated (`receiver.rs:78` also allocs a fresh `Mapper`
   per subcarrier); H9 nuanced (`analysis.rs:50` is the genuinely uncached
   planner; `fading.rs` re-plans hit rustfft's internal cache).
2. **Factual error corrected (important):** R9's "`MessageList.tsx` is
   non-virtualized" is **FALSE** — it uses `react-virtuoso` (`:18,338`) and the
   sort is `useMemo`'d. Round 2 grepped for the wrong library (`react-window`).
   R9 mailbox half reframed as a light check; the real large-mailbox cost is the
   backend H8 (R10). ACCEPTED.
3. **H7 rank inflated** — it's per-*call* (planner already hoisted, `:83-95`),
   not per-symbol. Down-ranked Major → Secondary. ACCEPTED.
4. **Concrete M3 target named:** `ardop/data.rs:104,145` per-byte `VecDeque<u8>`
   drain + `leftover.extend(payload)` — the real cost in the demoted ARDOP
   slice; vindicates the full→reduced demote. ADDED.
5. **`modem_status.rs:392`** 4 Hz background broadcaster — borderline-warm but
   stays cold; sweep flagged to eyeball its per-tick work. NOTED (no tier change).
6. **Coverage:** verified airtight — every `src-tauri/src/*.rs` (all 18), every
   subdir, every crate, every `src/*/` lands once. No gaps. Structure: 17 units,
   no merge/split. Cosmetic: `hf-channel-sim` is a repo-root sibling (path in
   plan already correct).

Outcome: **17 units unchanged**; corrections are accuracy fixes (one false
claim removed) + rank/target refinements. Two clean rounds in a row (2 & 3 both
minor-edits) — convergence emerging; Rounds 4–5 will confirm or break it.
