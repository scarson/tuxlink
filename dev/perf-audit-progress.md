# Performance-Audit Whole-Repo Run — Execution Progress Ledger

**Purpose:** survive ephemeral-container restarts. A fresh session reads this
to know exactly where the autonomous perf-audit run stands and what to do next.

**Session moniker:** glade-knoll-shoal · **Branch:**
`claude/superpowers-agent-skills-setup-BtMEr` · **Started:** 2026-06-04

## How to resume (if container restarted)
1. Read `dev/perf-audit-review-log.md` (scope-decision state) and
   `dev/perf-audit-scope-plan.md` (the slice plan, latest version).
2. Read this ledger's "Status board" — pick the first non-DONE unit and continue.
3. Per-unit artifacts live in `docs/perf-audits/` (validated findings) and
   `docs/plans/*-perf-audit-remediation-plan.md` (fix plans). If a unit's
   artifacts exist but it's marked IN-PROGRESS, verify completeness before moving on.
4. Append skill UX observations to `dev/perf-audit-skill-feedback.md`.
5. Commit + push after every unit.

## Phase board

| Phase | State |
|-------|-------|
| Scope partition + ≥5-round adversarial review | **DONE** — finalized at v6/GO (5 Opus rounds; see review-log) |
| Execute audit units (per finalized plan) | IN PROGRESS — DONE: M1 (full cycle), M2/M4/M5 (audits), O1 (overlay), W0 (pre-artifact), M3 (reduced). PENDING: R1-R6,R8,R9 (reduced) + cold sweep. R7 folded into sweep (demoted v5). |
| **Generalizable scope-slicing METHOD** (new operator ask, 2026-06-04) | written + committed (`.claude/skills/performance-audit-cycle/whole-repo-scoping.md` + SKILL.md routing); FINAL (v3/SHIP — 5 review passes). **Port-back to scarson/agent-skills required** (no write access this session). |

## Status board — audit units (order per plan v2; may change after rounds 2–5)

> Tiers: FULL = 8-phase cycle; REDUCED = trimmed lanes; OVERLAY = analysis only;
> SWEEP = batched 3-lane cold pass. State: PENDING / IN-PROGRESS / DONE / SKIPPED.

| Unit | Tier | Scope | State | Artifacts |
|------|------|-------|-------|-----------|
| M1 | FULL | tuxmodem-phy (OFDM + real-time audio) | **DONE (full cycle)** | audit: `docs/perf-audits/2026-06-04T14-24-m1-ofdm-phy-*` (6 lanes + consolidated + runs.jsonl + bug-hunt-kickoff); plan: `docs/plans/2026-06-04-m1-ofdm-phy-perf-audit-remediation-plan.md` (12 tasks, all P1–P12); plan-review: `dev/perf-audit-reviews/m1-plan-review.md` (needs-minor-edits → fixes applied). 13 findings (3C/6M/4m) + 3 suspected bugs. NOT executed (fix-plan is for operator-reviewed execution). |
| M2 | FULL | tuxmodem-fec (LDPC) | **AUDIT DONE**; fix-plan DEFERRED (crate is latent/dead-code) | `docs/perf-audits/2026-06-04T15-00-m2-fec-*` (6 lanes+consolidated+kickoff). 9 findings (1C/4M/4m), all latent. Headline: one SPA-loop rewrite (O(d_c²)→O(d_c) check update + flat CSR + scratch reuse). |
| O1 | OVERLAY | live RX/TX pipeline (M1+M2) | **DONE** | `docs/perf-audits/2026-06-04-O1-live-pipeline-overlay.md` — per-symbol allocs are batch (not RT); only audio_device mutex is the RT-deadline item; sequence M2 SPA rewrite before FEC integration. |
| W0 | PRE | winlink call-frequency map (R3/R8/R10 context) | **DONE** | `docs/perf-audits/2026-06-04-W0-winlink-call-frequency-map.md` |
| M3 | REDUCED | winlink/modem/ardop (external-TNC transport) | **AUDIT DONE** (reduced, 4 lanes) | `docs/perf-audits/2026-06-04T16-30-m3-ardop-*` (4 lanes+consolidated). 7 findings, ALL MINOR (validates the demote). Actionable: BufWriter for tiny B2F tokens (on-air bytes); status-meters-dark-during-exchange. No Mutex-across-IO (sound by construction). |
| M4 | FULL | src-tauri/src/search | **AUDIT DONE**; fix-plan DEFERRED | `docs/perf-audits/2026-06-04T15-30-m4-search-*` (6 lanes + consolidated + ledger + bug-hunt-kickoff). 10 findings (1C/5M/4m) + 6 bugs. |
| M5 | FULL | hf-channel-sim (offline sim, dev-only) | **AUDIT DONE**; fix-plan DEFERRED (dev-only tool) | `docs/perf-audits/2026-06-04T16-00-m5-hfsim-*` (6 lanes+consolidated+kickoff). 9 findings (5M/4m), dev-sweep-calibrated. Headline: FadingShaper refactor + rayon-parallelize the sweep. |
| R1 | REDUCED | tuxmodem-tx + tuxmodem-rx | PENDING | |
| R2 | REDUCED | tux-rig-rts + tux-rig-cm108 | PENDING | |
| R3 | REDUCED | winlink compression + B2F assembly | PENDING | |
| R4 | REDUCED | winlink/modem/vara + shared | PENDING | |
| R5 | REDUCED | winlink/ax25 | PENDING | |
| R6 | REDUCED | winlink telnet/P2P transport | PENDING | |
| R7 | ~~REDUCED~~ | winlink/listener gate | **FOLDED into SWEEP** (demoted v5 — no hot loop) | see cold sweep |
| R8 | REDUCED | winlink B2F session driver | PENDING | |
| R9 | REDUCED | src/radio + src/mailbox (warm React/TS UI) | **AUDIT DONE** (reduced, 4 frontend lanes) | `docs/perf-audits/2026-06-04T17-00-r9-frontend-ui-*` (4 lanes+consolidated+kickoff). 7 findings (3M/4m). Headline: 4 Hz modem:status re-render cascade (whole ArdopRadioPanel + unbounded SessionLog re-projection) + per-keystroke Tauri invoke. Cleared: Virtuoso/memoized-sort/event-push (anti-padding). |
| SWEEP | SWEEP | all cold Rust + cold/warm TS (see plan) | PENDING | |

## Decision log (substantive autonomous calls during execution)
- (none yet)
