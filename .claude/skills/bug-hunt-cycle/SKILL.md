---
name: bug-hunt-cycle
description: Full bug hunt cycle — dispatch 4 sibling bug-hunter methodology skills in parallel, cross-validate findings, present design decisions, and write a fix plan via writing-plans-enhanced. Use when finishing a phase or auditing a body of work.
argument-hint: "<scope, e.g. 'Phase 9', 'PR 45', 'internal/feed/'>"
---

# Bug Hunt Cycle

## Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as shown here.

## Overview

Running a full bug hunt cycle for: **$ARGUMENTS**

This is a multi-phase workflow. The runner MUST follow each phase in order and MUST NOT skip phases.

This skill orchestrates several sibling workhorses in this plugin: the hunter methodologies ([`bug-hunter-exploratory`](../bug-hunter-exploratory/SKILL.md), [`bug-hunter-holistic`](../bug-hunter-holistic/SKILL.md), [`bug-hunter-multipass`](../bug-hunter-multipass/SKILL.md), [`bug-hunter-differential`](../bug-hunter-differential/SKILL.md)) for the parallel dispatch, [`writing-plans-enhanced`](../writing-plans-enhanced/SKILL.md) for the fix plan, and [`plan-review-cycle`](../plan-review-cycle/SKILL.md) for the adversarial plan review. The cycle owns scope research, parallel dispatch, cross-validation, test-gap analysis, and the user-decision loop. It MUST NOT duplicate the subagent-proofing or plan-review discipline encoded in the delegated skills.

### Scope validation

If `$ARGUMENTS` is empty or unclear (e.g., the user invoked the cycle without specifying a scope), the runner MUST ask the user for a scope before proceeding to Phase 1. Useful scope shapes: a phase reference (e.g., "Phase 9"), a PR number (e.g., "PR 45"), a directory or package path (e.g., `internal/feed/`), a commit range, or a feature name (e.g., "the alert evaluation pipeline"). The runner MUST NOT guess a scope or default to "everything" — bug hunters perform best on a precise, bounded surface.

---

## Phase 1: Research Scope

Determine what code falls within **$ARGUMENTS**. The goal is to give each bug hunter a precise, actionable scope — not a vague "look at everything."

**For a phase reference:**
- Check `docs/plans/` for a matching plan file — it lists the files and packages involved
- Check `git log --oneline` for commits belonging to the phase
- Run `git diff --stat <first-commit>^..<last-commit>` to get the file list

**For a PR reference:**
- Use `gh pr view <number> --json files` to get changed files
- Use `gh pr view <number> --json commits` for the commit range

**For a directory/package reference:**
- List the files directly

Produce a **scope summary**: a list of packages/files, a one-paragraph description of what this code does, and any known architectural context (e.g., "this is the alert evaluation pipeline — it coordinates between the store, the DSL compiler, and the notification fan-out"). This context helps the bug hunters understand *intent*, not just *syntax*.

Also identify **adjacent code** the hunters should be aware of but that isn't the primary target — shared utilities called by the scoped code, interfaces implemented, etc. Mention these so the hunters can follow threads across package boundaries.

---

## Phase 2: Dispatch Bug Hunters

The runner MUST launch **four parallel subagents** using the Agent tool, each invoking one of the sibling hunter methodology skills (`bug-hunter-exploratory`, `bug-hunter-holistic`, `bug-hunter-multipass`, `bug-hunter-differential`). All four MUST run concurrently.

The runner MUST determine today's date and the scope slug (e.g., `phase9`, `pr-45`, `feed-adapters`) for file naming. Each agent MUST write its report to `docs/bug-hunts/`.

### Agent model selection

Each subagent SHOULD be invoked using the **latest available Claude Opus model** or **GPT-5 (or successor) at x-high reasoning effort**, unless the user has explicitly instructed otherwise for this run. Bug hunting is correctness-critical analysis — it benefits asymmetrically from maximum reasoning bandwidth, and saving model cost trades poorly against missed bugs that ship to production. If the agent framework requires a specific model parameter on dispatch, the runner MUST set it accordingly; if the framework inherits the parent's model, the runner MUST ensure the parent is on the strongest tier before dispatching.

### Agent prompts

Each agent gets:
1. The scope summary and file list from Phase 1
2. The adjacent code context
3. Its specific methodology (below)
4. The output file path
5. This instruction: **"Write your full report to the specified file AND return your findings in your response. The file is the persistent record; the response is for consolidation."**

**Exploratory agent:**
```
You are a bug hunter using depth-first exploration. Invoke the
`bug-hunter-exploratory` skill (or, if your framework cannot
invoke skills by name, read the skill's SKILL.md from the plugin install
location) and follow it exactly.

Scope: [paste scope summary + file list]
Adjacent code to be aware of: [paste adjacent context]
Output file: docs/bug-hunts/<date>-<slug>-exploratory.md

Write your report to the output file. Also return your full findings in
your response so they can be consolidated with the other hunters' results.
```

**Holistic agent:**
```
You are a bug hunter using holistic read-everything-then-reason analysis.
Invoke the `bug-hunter-holistic` skill (or, if your framework cannot
invoke skills by name, read the skill's SKILL.md from the plugin install
location) and follow it exactly.

Scope: [paste scope summary + file list]
Adjacent code to be aware of: [paste adjacent context]
Output file: docs/bug-hunts/<date>-<slug>-holistic.md

Write your report to the output file. Also return your full findings in
your response so they can be consolidated with the other hunters' results.
```

**Multipass agent:**
```
You are a bug hunter using five focused analysis passes (contract violations,
cross-sibling patterns, failure modes, concurrency, error propagation).
Invoke the `bug-hunter-multipass` skill (or, if your framework cannot
invoke skills by name, read the skill's SKILL.md from the plugin install
location) and follow it exactly.

Scope: [paste scope summary + file list]
Adjacent code to be aware of: [paste adjacent context]
Output file: docs/bug-hunts/<date>-<slug>-multipass.md

Write your report to the output file. Also return your full findings in
your response so they can be consolidated with the other hunters' results.
```

**Differential agent:**
```
You are a bug hunter using differential and invariant-based analysis —
finding bugs in the gap between paired functions that should agree
(round-trip pairs, plan/apply pairs, producer/consumer pairs, forward/inverse
pairs, inclusion/exclusion pairs). Invoke the `bug-hunter-differential` skill
(or, if your framework cannot invoke skills by name, read the skill's SKILL.md
from the plugin install location) and follow it exactly.

Scope: [paste scope summary + file list]
Adjacent code to be aware of: [paste adjacent context]
Output file: docs/bug-hunts/<date>-<slug>-differential.md

Write your report to the output file. Also return your full findings in
your response so they can be consolidated with the other hunters' results.
```

The runner MUST wait for all four subagents to complete before proceeding to Phase 3.

---

## Phase 3: Cross-Validate and Consolidate

Read all four reports (both from agent responses and the files in `docs/bug-hunts/`). Build a unified findings list.

**COMPLETENESS REQUIREMENT:** The runner MUST account for every single finding from every hunter report. Before starting cross-validation, the runner MUST enumerate all findings across all 4 reports. Every finding MUST appear in the consolidated report as one of: confirmed bug, design decision, false positive, or out-of-scope. **The runner MUST NOT decide what's "too minor" to include — that's the user's decision in Phase 5.** Silently dropping findings defeats the entire purpose of the bug hunt.

### 3a. Deduplicate

Many findings will overlap. Group findings that describe the same underlying issue. Note consensus — "all four found this" is a strong signal; "only one found this" needs extra scrutiny.

### 3b. Cross-validate EVERY finding

For each unique finding, determine its validity:

1. **Read the actual code** at the cited location. Do not trust the hunter's description alone — verify the evidence yourself.
2. **Check if another hunter examined the same code and found it correct** (or intentional). If the holistic hunter flags X as a bug but the exploratory hunter followed that thread and noted it was intentional, that's a resolution — document it.
3. **Check if the "bug" is actually a documented design decision** in the project's plan, research notes, or implementation-pitfalls doc (typically under `docs/`).
4. **Verify the impact claim.** Is the failure mode actually reachable? Under what conditions?

Classify each finding as:
- **Confirmed bug** — verified incorrect behavior with evidence
- **Design decision needing user input** — legitimate concern but the correct fix depends on product intent, architectural tradeoffs, or scope decisions that require the user's judgment
- **False positive** — incorrect finding, explain why
- **Out of scope / pre-existing** — valid bug but clearly unrelated to the specified scope (still document it)

### 3c. Blast radius analysis

For confirmed bugs and out-of-scope bugs, assess blast radius:
- What other code calls/uses the buggy code?
- Would the fix require changes outside the scoped packages?
- Could the fix break existing behavior that callers depend on (even if that behavior is technically wrong)?

If a fix has **larger scope** than the scoped work (e.g., modifying shared utility code that other packages use), flag it explicitly. These will be surfaced to the user in Phase 5.

### 3d. Write consolidated report

Write the consolidated report to `docs/bug-hunts/<date>-<slug>-consolidated.md` using this structure:

```markdown
# <Scope> Bug Hunt — Consolidated Findings

**Date:** <YYYY-MM-DD>
**Scope:** <description of what was analyzed>
**Hunters:** Exploratory, Holistic, Multipass, Differential

---

## Confirmed Bugs

### B1. <Title>
**Consensus:** <which hunters found it, or "verified by consolidation">
**Location:** <file:line>
**Evidence:** <what the code does vs what it should do>
**Impact:** <what goes wrong in practice>
**Blast radius:** <what else would need to change>
**Fix approach:** <brief description>

(Repeat for each confirmed bug)

---

## Design Decisions Requiring User Input

### D1. <Title>
**Location:** <file:line>
**The concern:** <what the hunters flagged>
**Why this needs a decision:** <what tradeoffs are involved>
**Options:** <enumerate the choices with pros/cons>
**Recommendation:** <if you have one, state it with reasoning>

---

## False Positives

### FP1. <Title>
**Flagged by:** <which hunter>
**Why invalid:** <brief explanation>

---

## Bugs Outside Primary Scope

### O1. <Title>
**Location:** <file:line>
**Blast radius:** <what would need to change>
**Recommendation:** <fix in this cycle or document for later>
```

**COMPLETENESS CHECK:** Before moving on, re-read every hunter report and verify that every finding is accounted for in the consolidated report. Count the findings: the total of confirmed + design decisions + false positives + out-of-scope MUST equal or exceed the total unique findings across all hunter reports. If any are missing, add them now.

After writing the consolidated report, update your private journal (or equivalent reflection store) with key observations: what patterns emerged across hunters, which findings surprised you, what the false-positive rate looked like, and any insights about the codebase's risk profile.

---

## Phase 4: Test Gap Analysis

For each **confirmed bug**, reflect on why existing tests didn't catch it. This phase improves the project's testing safety net — not just the code.

### 4a. Why didn't tests catch this?

For each confirmed bug, answer:

1. **Do tests exist** for the code path where the bug lives? If not, why not — was it an oversight, or was the code path considered untestable?
2. **If tests exist**, why didn't they catch it? Common reasons:
   - Tests only cover the happy path
   - Tests mock the component where the bug actually lives
   - Tests assert on the wrong thing (e.g., "no error returned" instead of "correct value produced")
   - Test inputs don't exercise the edge case
   - Integration between components isn't tested (unit tests pass individually but the composition is broken)
3. **What test would have caught this?** Briefly describe the test — input, expected behavior, why it would fail against the buggy code. (This feeds into the fix plan in Phase 6.)

### 4b. Review against the project's testing-pitfalls doc

Read the project's testing-pitfalls doc (typically `docs/pitfalls/testing-pitfalls.md`; some projects use `dev/testing-pitfalls.md`) and check each confirmed bug's test gap against the documented pitfalls:

- **Pitfall already covers this scenario** — the test gap exists because the pitfall guidance wasn't followed. Note which pitfall applies. No doc update needed, but flag it in the fix plan so the subagent knows to follow that specific pitfall.
- **Pitfall doesn't cover this scenario** — the bug reveals a testing blind spot not yet documented. Draft a candidate addition to the testing-pitfalls doc.

### 4c. Update testing-pitfalls doc if warranted

For each candidate addition from 4b, assess whether it's **generalizable** — would this pitfall apply to future code in this project, or is it a one-off specific to this bug?

- **Generalizable:** Write the addition to the testing-pitfalls doc. Follow the existing format and conventions in the file. Keep it concise — a pitfall entry SHOULD be actionable, not a narrative.
- **One-off:** Don't update the file. Instead, include a specific testing note in the fix plan task for this bug.

### 4d. Add test gap summary to consolidated report

Append a section to `docs/bug-hunts/<date>-<slug>-consolidated.md`:

```markdown
---

## Test Gap Analysis

### B1. <Bug title>
**Why missed:** <reason tests didn't catch it>
**Pitfall coverage:** <"covered by pitfall X — not followed" or "new pitfall added" or "one-off — noted in fix plan">
**Catch test:** <brief description of the test that would have caught it>

(Repeat for each confirmed bug)

### Testing Pitfalls Updates
- <List any additions made to the testing-pitfalls doc, or "None">
```

---

## Phase 5: Present to User

Present the findings to the user. Structure the presentation as:

1. **Executive summary** — X confirmed bugs, Y design decisions needing input, Z false positives, W out-of-scope findings
2. **Confirmed bugs** — brief table (title, severity, location, fix complexity)
3. **Design decisions** — present each one with enough context for an informed decision. Think through each decision point in the context of the overall project architecture (plan docs, research notes). Make recommendations where you have a well-reasoned opinion, but be clear about what's a recommendation vs what's a clear correct answer.
4. **Out-of-scope bugs with larger blast radius** — for each, ask: include in fix plan, or document for later?

**The runner MUST wait for the user's input on all design decisions and scope questions before proceeding to Phase 6.**

---

## Phase 6: Write Fix Plan

After the user has provided input on all decisions, the runner MUST invoke [`writing-plans-enhanced`](../writing-plans-enhanced/SKILL.md) to create an implementation plan for all confirmed bugs + any out-of-scope bugs the user chose to include. `writing-plans-enhanced` owns the subagent-proofing discipline (eliminating ambiguity, preventing context gaps, mandating TDD, reviewing pitfalls, minimizing cross-task conflicts, the Living Document Contract) — the cycle MUST NOT duplicate those rules here.

When invoking `writing-plans-enhanced`, the runner MUST pass these bug-hunt-specific instructions as additional context to layer on top of the wrapper's standard discipline:

- **Plan file path:** `docs/plans/<date>-<slug>-bug-hunt-remediation-plan.md` (e.g., `docs/plans/2026-03-18-phase11-mfa-bug-hunt-remediation-plan.md`). The `-bug-hunt-remediation-plan.md` suffix distinguishes bug-hunt remediation plans from other plan types so they are easy to identify and search for.
- **Source:** the consolidated bug-hunt report at `docs/bug-hunts/<date>-<slug>-consolidated.md`, including its appended Test Gap Analysis section.
- **Task traceability:** each task MUST cite the originating finding ID from the consolidated report (`B1`, `B2`, ... for confirmed bugs; `O1`, `O2`, ... for out-of-scope bugs the user chose to include).
- **Test gap follow-through:** if Phase 4 identified a specific testing pitfall (existing or newly added) for a particular bug, the plan task for that bug MUST include an explicit "follow this pitfall" reference so the executing subagent reads and applies it.
- **Deferred bugs appendix:** if the user chose to defer any out-of-scope bugs, the plan MUST include an appendix listing them (see template below).

When `writing-plans-enhanced` presents execution options, the runner MUST include a recommendation for which approach would be most effective. Common options: (1) subagent-driven in this session, (2) parallel session with `executing-plans` in a worktree, or (3) parallel agent dispatch for multi-agent execution. Base the recommendation on: how much context this session has consumed, whether the plan is self-contained enough for a fresh session, how many tasks are parallelizable vs sequential, and whether any tasks are risky enough to warrant focused attention rather than parallel dispatch. Explain the reasoning concisely.

### Deferred bugs appendix

If the user chose to defer any out-of-scope bugs, the plan MUST include this appendix:

```markdown
## Appendix: Bugs Identified But Not Fixed in This Cycle

### <Title>
**Location:** <file:line>
**Evidence:** <what's wrong>
**Why deferred:** <user's reasoning or scope decision>
**Recommended fix:** <brief approach for when this is addressed>
```

This appendix is the persistent record. It MUST be written to the plan file — not left in conversation memory.

---

## Phase 7: Plan Review Cycle

Before committing, the runner MUST rigorously review the fix plan for subagent-readiness by invoking [`plan-review-cycle`](../plan-review-cycle/SKILL.md) (a sibling skill in this plugin — always present when this cycle is). `plan-review-cycle` owns the multi-round adversarial review discipline; the cycle MUST NOT duplicate it here.

After the review cycle completes, the runner SHOULD log observations about plan quality and recurring patterns to a private journal (or whatever pattern-store the project uses — an MCP journal, a `gstack-learn`-style command, a dated `docs/learnings/` file, etc.). Capture:

- **Type:** pattern
- **Key:** `plan-review-[slug]`
- **Insight:** what patterns emerged, what was most commonly wrong

---

## Phase 8: Commit Reports

The runner MUST stage and commit all bug hunt artifacts:

```bash
git add docs/bug-hunts/<date>-<slug>-*.md
git add docs/plans/<plan-file>            # if the plan was written
git add docs/pitfalls/testing-pitfalls.md # if updated in Phase 4 (path may vary by project)
git commit -m "docs(bug-hunt): <slug> — consolidated findings and fix plan"
```
