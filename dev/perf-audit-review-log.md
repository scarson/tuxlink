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
| 2 | Opus subagent | v2 | `round-2.md` | pending |
| 3 | Opus subagent | v3 | `round-3.md` | pending |
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
