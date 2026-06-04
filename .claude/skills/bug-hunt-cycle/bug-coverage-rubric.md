# Bug Coverage Rubric

**Status:** v0.1 — hypothesis-grade. Calibration pending empirical validation against bug-hunt corpus.
**Last updated:** 2026-05-13
**Purpose:** Map bug classes against the methodology stack so coverage gaps and overlaps are visible. Provides a rubric for tuning hunters, sizing the value of adding new ones, and judging whether a finding properly belongs to the skill that surfaced it.

---

## Living document contract

This is a tuning rubric, not a coverage claim. Every cell in the matrix is annotated with a confidence tier (high / medium / low) reflecting the evidence behind it. Low-confidence cells are calibration targets — places where empirical bug-hunt corpus data would either confirm or refute the rubric's current attribution.

The rubric MUST be updated when:

- A new hunter is added, modified, or removed.
- An adjacent skill (cso, project-health-review) is added, modified, or removed from the operational stack.
- Empirical evidence from bug-hunt outputs contradicts a cell's current attribution.
- A bug class is observed that doesn't fit the existing taxonomy.

When updating: rewrite the affected cells, downgrade the confidence tier if the change is conjecture-driven, upgrade it if empirical evidence is now in hand, and add a row to §Revision history.

---

## Methodology and limitations

### What this doc is

A grid mapping bug classes (rows) against skills in the operational methodology stack (columns), with each cell describing whether and how the skill addresses bugs in that class. The matrix is paired with a tuning playbook (§How to use this for tuning) that describes what to do when the matrix surfaces a gap, overlap, or confidence mismatch.

### What this doc is NOT

- An empirical study. The cells are mostly informed conjecture from reading each skill's SKILL.md and reasoning about what its framing would and would not catch. Empirical calibration against actual bug-hunt outputs is the calibration project sketched in §Calibration project below.
- A coverage guarantee. A "high confidence" cell means the skill's methodology explicitly targets the class; it does not mean the skill always finds every bug in that class.
- A model-quality statement. All cells assume frontier-tier models at appropriate reasoning effort, per each skill's own model-selection guidance.

### Confidence tiers

Every cell carries one of three tiers, applied honestly:

- **High** — the skill's SKILL.md explicitly names this bug class as in-scope and describes a methodology for finding it. Verifiable from the file.
- **Medium** — the SKILL.md doesn't name the category but the methodology shape would surface findings in it as a side effect of its primary focus.
- **Low** — conjecture. The skill plausibly catches this class but the attribution isn't grounded in either explicit SKILL.md text or empirical evidence.

A "—" entry means the skill is not expected to address this class. This is a positive claim about scope, not a confidence statement.

### Caveat on the taxonomy

The bug-class taxonomy below is borrowed from static-analysis literature (CWE, Coverity-style taxonomies, academic program-analysis classifications). Those categories were built for *deterministic algorithmic tools* — programs that decompose code along fixed axes.

LLM-based hunters do not decompose along the same axes. A bug hunter reading a function might notice a numerical edge case, a concurrency window, and a contract violation in the same pass, and which framing it brings to the report is partly stochastic. A given finding may legitimately cross multiple categories.

The taxonomy is therefore a working framework, not a partition. If empirical calibration shows findings consistently cross multiple cells, that's evidence the taxonomy isn't carving reality at its joints *for these tools* — the fix is to refine the taxonomy, not to force findings into single cells. Treat the rows as lenses that overlap, not as disjoint classes.

### What's in the operational stack

- **bug-hunter-exploratory** — depth-first thread-following from high-risk entry points.
- **bug-hunter-holistic** — read all source files, then reason about what's wrong.
- **bug-hunter-multipass** — five focused passes (contract, cross-sibling pattern, failure mode, concurrency, error propagation).
- **bug-hunter-differential** — pairs/sets of functions that should be consistent (round-trip, plan/apply, producer/consumer, etc.). Currently in empirical-validation phase: actively dispatched by bug-hunt-cycle alongside the established three; matrix cells reflect anticipated coverage pending corpus calibration (see §Known gaps target 1).
- **cso** — gstack security audit skill. Infrastructure-first (secrets, deps, CI/CD, LLM, supply chain) plus OWASP Top 10 + STRIDE on source code. Confidence-gated (see Cadence and reporting below).
- **project-health-review** — 5 parallel adversarial agents: Code Quality, Architecture, Test Quality, Ops Readiness, API Design.

### Cadence and reporting

Coverage is operationally different when it depends on a frequent skill versus an infrequent one — and different again when a skill's reporting is confidence-gated versus unfiltered. Both matter for interpreting the matrix.

| Skill | Typical cadence | Reporting filter |
|---|---|---|
| Bug hunters (via bug-hunt-cycle) | Frequent — after phases, before merges, on demand | None — every finding reaches consolidation |
| cso (daily mode) | Frequent | Confidence-gated at 8/10 |
| cso (comprehensive mode) | Monthly or pre-release | Confidence-gated at 2/10 |
| project-health-review | Quarterly or pre-milestone | None — every finding reaches validation |

The cso confidence gate is structurally important. A bug class that cso theoretically covers (High in the matrix) may still escape to production if its findings consistently fall below the confidence threshold — daily mode's 8/10 gate suppresses everything cso isn't highly certain about. When reading the matrix:

- A High cso cell means "cso's methodology targets this class." It does not mean "cso reports every finding in this class." For daily-mode operation, assume cso surfaces only the high-confidence subset.
- A bug-hunter High cell means the hunter both targets the class and surfaces every finding it has. The hunters' false-positive filtering happens at consolidation, not at the hunter level.
- This is why the same High tier can mean operationally different things across skills. Read the cadence-and-reporting table alongside the matrix when judging real-world coverage.

### What's not in this rubric

**test-coverage-cycle and test-coverage-review skills.** These are CVErt Ops-specific Go disciplines covering test coverage gaps, assertion quality, and a per-endpoint security matrix. They share methodology DNA with the bug hunters but operate on tests rather than source-bug findings. They produce *test gaps* and *security-matrix gaps*; the bug hunters produce *source bugs*. Different output classes, different cycles, different filing. Mentioned here only so future readers don't think they're missing from the rubric — they're a parallel discipline, not a column.

---

## The bug-class taxonomy

Categories below are the working taxonomy. Sources: standard bug-research literature (CWE, Coverity-style taxonomies) and academic program-analysis classifications, adapted to the lenses the hunters and adjacent skills bring. The taxonomy is not authoritative — see §Caveat on the taxonomy above. If empirical analysis surfaces a category that doesn't fit, add it and downgrade affected cells' confidence.

1. **Single-function correctness** — wrong algorithm, off-by-one, missed branch, bad default. Found by reading the function and checking its logic against its contract.
2. **Contract drift between functions** — round-trip asymmetry, plan/apply mismatch, producer/consumer schema drift. The bug requires seeing both sides of a relationship to identify.
3. **Cross-sibling pattern violation** — N implementations follow a pattern, one deviates. The bug requires comparing siblings.
4. **State machine / temporal violation** — invariants over sequences. Some legal-individually operations produce illegal-collectively states. Includes TOCTOU.
5. **Concurrency** — races, deadlocks, lock ordering, atomicity violations.
6. **Resource / lifecycle** — leaks, use-after-free, dangling references, file handles, connection pools, goroutine lifecycles.
7. **Trust boundary / input handling** — injection, deserialization, validation gaps, privilege confusion, authz/authn bypass.
8. **Configuration / environment** — works in dev, breaks in prod; missing env vars, dangerous defaults, deployment-surface assumptions.
9. **API / library misuse** — calling third-party APIs in ways the library doesn't actually support, or per docs that are incomplete or wrong.
10. **Numerical / representational** — float comparison, integer overflow, encoding mismatch, time-zone bugs, locale bugs.
11. **Error propagation** — swallowed errors, errors that lose context, errors propagating to the wrong layer.
12. **Architecture / design** — abstraction quality, coupling, scalability walls, missing capabilities, plan-vs-reality drift.
13. **Test quality** — execution-only tests, assertions on mocks, missing negative paths, brittle tests.
14. **Operational readiness** — graceful shutdown, observability gaps, failure modes under degraded dependencies, deployment safety.
15. **API design** — consistency, REST violations, error format, versioning, pagination correctness.

---

## Coverage matrix

Cells are formatted as: **Tier — note**. Note describes how/why the skill addresses the class, or what specific lens within the skill does.

| Class | Exploratory | Holistic | Multipass | Differential | cso | Health Review |
|---|---|---|---|---|---|---|
| 1. Single-function correctness | **High** — depth-first reading of risky functions catches algorithm, branch, and default bugs | **High** — full-source read with contract-vs-implementation lens | **High** — Pass 1 explicitly targets contract violations | **Low** — only catches single-function bugs incidentally if they violate a relationship invariant | **Medium** — OWASP A03 (injection) catches single-function bugs with security impact | **Medium** — Agent 1 (Code Quality) catches complexity hotspots and dead code |
| 2. Contract drift between functions | **Low** — depth-first might follow into the second side of a relationship but isn't structured for it | **Medium** — read-everything lens can see drift but isn't anchored on relationships | **Medium** — Pass 2 (cross-sibling) catches some drift; doesn't systematically enumerate relationships | **High** — explicitly the differential hunter's lane | **Low** — only if drift produces a security-relevant gap | **Low** — Agent 2 (Architecture) might catch via plan-vs-reality drift |
| 3. Cross-sibling pattern violation | **Low** — depth-first doesn't naturally compare siblings | **High** — read-everything makes the N-vs-1 pattern visible | **High** — Pass 2 explicitly targets this | — | **Low** — only if the pattern violation has a security cell in the OWASP/STRIDE matrix | **Medium** — Agent 1 (Code Quality) flags inconsistent patterns; Agent 2 (Architecture) flags inconsistent abstractions |
| 4. State machine / temporal violation | **Medium** — depth-first into multi-step flows catches some | **Medium** — full picture surfaces some temporal invariants | **High** — Pass 3 (failure mode) explicitly targets multi-step flow failure paths | **Medium** — symmetric-actor and plan/apply relationship types catch some state machines | **Medium** — OWASP A04 (insecure design) and STRIDE catch some temporal authz bugs | **Low** — Agent 4 (Ops Readiness) catches some via "what happens when X fails" |
| 5. Concurrency | **Medium** — depth-first into locks/goroutines catches some | **Medium** — full-source view exposes lock orderings | **High** — Pass 4 explicitly targets races, TOCTOU, lock ordering | **Low** — concurrency listed as a failure shape but not the hunter's primary lens | **Medium** — STRIDE tampering/spoofing catches some; not the focus | **Low** — Agent 4 (Ops Readiness) might catch via resource management |
| 6. Resource / lifecycle | **Medium** — depth-first into resource-acquiring functions catches local bugs | **Medium** — full-source view exposes ownership patterns | **Medium** — Pass 5 (error propagation) catches lifecycle bugs in error paths; doesn't systematically target cross-module lifecycle | — | **Low** — only if a leak produces an exploitable DoS | **Medium** — Agent 4 (Ops Readiness) explicitly targets resource management, connection pools, goroutine limits |
| 7. Trust boundary / input handling | **Low** — depth-first might follow untrusted-input threads but no explicit adversarial framing | **Low** — read-everything doesn't naturally adopt adversarial reading | **Low** — no pass explicitly adopts an attacker model | — | **High** — OWASP A01/A03/A05/A07/A08/A10 + STRIDE explicitly cover this | **Medium** — Agent 5 (API Design) catches missing auth and RBAC gaps |
| 8. Configuration / environment | — | — | — | — | **Medium** — Phases 4 (CI/CD), 5 (infrastructure), 8 (skill supply chain) catch some | **High** — Agent 4 (Ops Readiness) explicitly targets config footguns, deployment concerns, startup dependencies |
| 9. API / library misuse | **Low** — only if the depth-first thread leads to the misused library and the agent recognizes it | **Low** — depends on agent's library familiarity | **Medium** — Pass 1 (contract violations) catches misuse where contract is documented | — | **Medium** — Phase 3 (dependency supply chain) catches known-vulnerable usage | **Medium** — Agent 1 (Code Quality) catches non-idiomatic library use |
| 10. Numerical / representational | **Low** — only if depth-first hits a numerical hotspot | **Low** — read-everything doesn't naturally probe value-space edges | **Low** — no pass explicitly targets numerical/representational | **Medium** — round-trip relationship type catches encoding asymmetry; "asymmetric handling of edge cases" failure shape catches default-value drift | **Low** — A02 catches crypto numerical, nothing covers boring numerical | **Low** — Agent 1 might catch via complexity hotspots |
| 11. Error propagation | **Medium** — depth-first into error paths catches local issues | **High** — full-source view exposes error-handling inconsistency across the codebase | **High** — Pass 5 explicitly targets error propagation | **Low** — only via the relationship types that surface error-handling drift | **Low** — only when error swallowing produces a security gap | **Medium** — Agent 1 flags swallowed errors and inconsistent wrapping |
| 12. Architecture / design | — | — | — | — | **Low** — STRIDE may surface architectural issues | **High** — Agent 2 (Architecture) explicitly targets this |
| 13. Test quality | — (explicit out of scope) | — (explicit out of scope) | — (explicit out of scope) | — (explicit out of scope) | — | **High** — Agent 3 (Test Quality) explicitly targets this |
| 14. Operational readiness | — | — | — | — | **Medium** — Phases 4-5 cover some operational concerns | **High** — Agent 4 (Ops Readiness) explicitly targets this |
| 15. API design | — | — | — | — | **Low** — A05 (security misconfiguration) catches some API design issues with security impact | **High** — Agent 5 (API Design) explicitly targets this |

**Cell distribution:** 16 High, 25 Medium, 24 Low, 25 not-in-scope across 90 total cells (15 classes × 6 skills).

---

## How to read this matrix

A class with multiple **High** cells is well-covered by the stack. Example: class 1 (single-function correctness) has three high-confidence hunters covering it. Adding a fourth high-confidence hunter for this class would mostly duplicate existing work.

A class with one **High** cell is covered by exactly one skill. Example: class 12 (architecture/design) is covered only by the health review. If the health review runs quarterly, architectural drift in between cycles is invisible. That's a coverage frequency gap, not a methodology gap — visible because the matrix records cadence separately.

A class with no **High** cells is a structural gap. Example: class 10 (numerical/representational) currently has no high-confidence coverage. The differential hunter's medium-confidence cell is the strongest claim, and it only catches the subset of numerical bugs that present as round-trip asymmetries. This is a calibration target: either the empirical evidence shows this class is rare enough not to matter, or a future hunter should target it.

A class with one **High** cell *and* several **Low** cells is well-covered nominally but with high confidence concentrated in one place. Example: class 7 (trust boundary). If cso is in the stack and runs frequently, the class is well-covered. If cso is not in the stack, the class is poorly covered (the hunters and health review have only medium-or-low confidence cells).

**Reading the matrix when cadence differs:**

Consider class 14 (operational readiness) — High under health-review, Medium under cso. The health review runs quarterly; cso daily mode runs frequently but only surfaces 8/10-confidence findings. An operational readiness issue surfacing today has these paths to detection:

- cso daily catches it *only if* the finding is high-confidence and security-shaped.
- Health review catches it at the next quarterly cycle — potentially weeks of latency.
- Bug hunters don't catch it at all (no High cell in row 14).

Operational coverage of class 14 is therefore "high but quarterly, plus a thin daily safety net for the security-shaped subset." A class with the same matrix cells but daily-cadence coverage everywhere would be operationally very differently covered. The matrix shows the *theoretical* coverage shape; the cadence-and-reporting table is required reading alongside it for *operational* coverage.

---

## How to use this for tuning

The matrix surfaces three shapes of mismatch between methodology and reality. Each has a different remediation pattern.

### Pattern A: empty cell (no High coverage for a class)

**What it looks like:** A row with all cells at Medium, Low, or em-dash. Class 10 (numerical/representational) is the standing example.

**What it means:** Either the class is operationally rare in your projects, or it's a real coverage gap.

**Tuning options, cheapest to most expensive:**

1. **Confirm rarity before acting.** Scan recent bug-fix commits for this class. If you find few or no bugs in the class over a meaningful time window, the methodology is correctly silent. Document the finding and move on.
2. **Extend an existing hunter.** If the gap is bounded — e.g., only specific kinds of numerical bugs matter for your domain — add an enumeration step to the closest-fit hunter (Pass 5 of multipass for representational asymmetry, for instance). Cheaper than a new hunter; risks blurring the hunter's lane.
3. **Add a new hunter.** Justified only when the class is both empirically frequent and structurally distinct from existing hunters' lanes (per the differential hunter's example). Cheap to A/B test in the existing cycle; expensive if it dilutes the cross-validation step.
4. **Accept the gap.** If the class is rare and adding coverage would dilute the methodology, document the gap and leave it. Acceptance is a legitimate outcome.

### Pattern B: single High cell (covered by one skill)

**What it looks like:** A row with one High and the rest Medium-or-lower. Class 12 (architecture/design) under health-review is the example.

**What it means:** Coverage exists but depends on a single skill's cadence. If that skill runs infrequently, latency between bug introduction and detection is the operational gap.

**Tuning options:**

1. **Increase cadence on the High-cell skill.** If quarterly health reviews leave too much latency on architectural drift, run them more often. Each project should size this against the cost of running the skill versus the cost of late detection.
2. **Add a lightweight pre-screen.** A short prompt added to a frequent-cadence skill (e.g., end-of-phase bug-hunt cycle) that asks "any architectural drift visible in this scope?" gives a Medium-confidence intermediate check between the heavy quarterly reviews. Cheap to add, easy to drop if it produces noise.
3. **Accept the cadence gap.** If empirically the class produces few problems between cycles, the latency is fine. Confirm with bug-fix-commit timing data.

### Pattern C: confidence mismatch (High but cadence- or filter-limited)

**What it looks like:** A High cell whose operational coverage is constrained by cadence (quarterly) or reporting filter (cso confidence gate). Class 7 (trust boundary) under cso-daily-mode is the example — High but suppressed below 8/10 confidence.

**What it means:** Theoretical coverage is high but operational coverage is partial. The cell is honest about scope; the gap is in execution shape.

**Tuning options:**

1. **Run the comprehensive mode more often.** cso comprehensive (2/10 gate) surfaces more findings, including the speculative ones that daily mode suppresses. For high-stakes classes, monthly comprehensive may be worth the additional review load.
2. **Lower the filter threshold for high-stakes classes.** cso's confidence gate is configurable. For classes with severe downside (auth bypass, tenant isolation), accept more false-positive noise in exchange for fewer false negatives.
3. **Add a cross-checking skill at the suppressed-confidence range.** A second pair of eyes on cso's 4-7/10 findings (which daily mode discards) could surface real bugs cso's gate hides. Expensive; only warranted for the highest-stakes classes.
4. **Accept the trade.** cso's gate exists for a reason — high-noise security reports get ignored. If the filter is producing the right operational outcome, leave it. Confirm with: are real bugs in this class actually escaping to production?

### When the tuning question is "add a new hunter"

Adding a hunter is the most expensive tuning option. The differential hunter's "Empirical validation" section names the bar it must clear; any new hunter should clear an analogous bar:

- The hunter must find a class of bug the existing stack consistently misses (not "rarely catches" — *misses*).
- Its overlap with the closest sibling must be bounded — under ~30% is a reasonable starting threshold.
- The cycle skill that dispatches the hunter must absorb the additional parallel dispatch without degrading cross-validation quality. Three hunters validate cleanly; four require checking that Phase 3 deduplication still scales; more than four likely needs structural changes to the cycle.

If a candidate hunter doesn't clear these bars, the right move is to extend an existing hunter or accept the gap rather than add the hunter.

---

## Cross-validation between skills

When two skills both flag the same code area, the findings are not necessarily duplicates. Each skill operates from a different framing and produces a different type of finding about the same underlying code.

- **Bug hunter finds source bug + health review's Agent 3 flags missing test for it.** Same underlying issue, different findings. The hunter finding goes into the bug-hunt-cycle's confirmed-bugs list; the test-quality finding goes into the health-review-cycle's confirmed-issues list. Fix plans should reference both.
- **Bug hunter finds source bug + cso flags it as a security issue.** Same underlying issue, different framing. cso's confidence-gated report will include it only if the security impact is high enough; the bug hunter's report will include it regardless of security impact. If cso reports it, fix via the cso remediation path so the security framing carries through to the audit trail. If only the hunter reports it, fix via the bug-hunt remediation plan.
- **Differential hunter finds round-trip bug + multipass Pass 2 finds the same sibling-pattern violation.** Genuine duplication. Cross-validation in bug-hunt-cycle Phase 3 deduplicates these; the consensus signal ("multiple hunters found this") strengthens the finding.

The "different framing, different findings" claim is theoretical pending empirical validation — see §Calibration project for how to measure it.

---

## Known gaps and calibration targets

Cells in the matrix that are most worth empirical validation, in priority order:

1. **Class 2 (contract drift) × differential hunter** is currently **High** but unvalidated — the differential hunter is a draft. Empirical evidence is needed to confirm the hunter actually finds bugs in this class consistently. Calibration target: run the differential hunter against past phases where multipass already ran, and check whether differential surfaces bugs multipass missed.

2. **Class 10 (numerical/representational)** has no high-confidence coverage anywhere. Either the class is operationally rare in this project's codebases (most numerical bugs are caught by tests or types) or it's a real gap. Calibration target: scan historical bug-fix commits for numerical bugs; count what fraction were caught by which skill versus escaped to PR review or production.

3. **Class 4 (state machine / temporal violation)** is split across multiple medium-confidence cells. Empirical evidence would clarify whether multipass Pass 3 is doing the work or whether bugs in this class are mostly being missed. Calibration target: same approach as class 10.

4. **Class 7 (trust boundary) — cso unavailability scenarios.** The matrix assumes cso is in the operational stack. Projects without cso have only medium-or-low confidence coverage from the hunters and health review. If a project drops cso, this class becomes a major gap. Calibration target: tag bug-hunt cycles by which adjacent skills are active; check whether trust-boundary bugs escape more often in cso-less project cycles.

5. **Cross-skill duplication rates.** The "different framing, different findings" claim in §Cross-validation is theoretical. Empirical question: when bug hunters and cso both flag the same area, do they actually produce different actionable findings, or do they produce the same finding from two angles? Calibration target: take historical cycles where both ran and quantify overlap.

6. **Confidence-gate impact on cso's operational coverage.** The matrix shows cso's *theoretical* coverage of high-stakes classes. Daily mode's 8/10 gate suppresses speculative findings. Calibration target: take cso comprehensive-mode outputs (which include 2-7/10 findings) and check whether any of the suppressed findings correspond to real bugs that later escaped to production. Quantifies the cost of the filter.

---

## Calibration project

To upgrade the matrix from v0.x (hypothesis-grade) to v1 (empirically calibrated):

1. **Corpus assembly.** Collect all bug-hunt consolidated reports, cso reports, and health-review validated reports across all projects where the methodology has been used. Also collect post-cycle bug-fix commits and PR-review-caught issues from the same time windows.

2. **Classify findings against the taxonomy.** Tag each finding with its primary class. Cross-validate classification across a small sample to check inter-rater consistency. Note where findings cross multiple cells — that's evidence for refining the taxonomy.

3. **Tag findings by source skill.** Which skill surfaced each finding? Which other skills *could have* surfaced it but didn't?

4. **Compute coverage rates per class.** For each class, what fraction of bugs in that class were caught by each skill? What fraction escaped to commits or PR review?

5. **Update the matrix.** Upgrade or downgrade cell confidence tiers based on the data. Add categories the empirical work surfaces. Add cells the data falsifies. Refine the taxonomy if findings consistently cross cells.

This is a significant data-analysis project on its own — comparable in scope to a full eval cycle. It's worth doing once when the methodology has accumulated enough data to be informative (probably 50+ cycle outputs across multiple projects), and then re-doing periodically as the methodology evolves.

---

## Revision history

| Date | Change | Rationale |
|---|---|---|
| 2026-05-13 | Initial draft (v0.1, hypothesis-grade). Taxonomy, matrix, calibration plan, tuning playbook, cadence-and-reporting context. | First codification of cross-skill coverage. Most cells are conjecture pending empirical validation. |
| 2026-05-13 | bug-hunter-differential added to bug-hunt-cycle's active dispatch (Phase 2 launches four hunters in parallel rather than three). Differential column annotation in §What's in the operational stack updated from "Currently draft" to "in empirical-validation phase." Matrix cells unchanged — they were authored anticipating this transition. | Living document contract requires a rubric entry when a hunter is added to the operational stack. |

---

## Related conventions

Only conventions whose presence or behavior affects how the rubric should be read or applied:

- **bug-hunt-cycle, health-review-cycle.** The orchestration skills that dispatch the hunters and health review. The rubric describes *what* each skill catches; the cycle skills describe *how* the dispatch and consolidation work. Changes to the cycle skills (e.g., adding a fourth hunter to bug-hunt-cycle's Phase 2) affect the rubric's matrix directly.
- **test-coverage-cycle, test-coverage-review-go, test-coverage-review-hybrid-go.** Go-specific test-coverage and security-matrix audit disciplines used in CVErt Ops. Not in this rubric because they produce different output classes (test gaps and security-matrix gaps, not source bugs), but mentioned here so future readers don't think they're missing.
