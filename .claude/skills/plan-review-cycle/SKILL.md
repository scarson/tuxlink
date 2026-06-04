---
name: plan-review-cycle
description: Use after writing an implementation plan, before committing. Adversarial review for subagent-readiness — checks ambiguity, context gaps, interpretation drift, cross-task conflicts, and pitfall coverage across a minimum of 3 rounds (more if any round still finds substantive issues).
---

# Plan Review Cycle

## Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as shown here.

## Overview

Rigorously review an implementation plan for subagent-readiness before
committing. The runner MUST execute a minimum of 3 review rounds, and
MUST continue running additional rounds until a single round produces
zero substantive findings.

## How to run

### Round structure

Each round MUST review the plan against ALL of these dimensions:

**Ambiguity** — Can a subagent reasonably interpret any task description
two different ways? The runner MUST eliminate every instance. Look for
"handle this correctly," "fix the issue," "update as needed" — replace
with specific behavioral descriptions.

**Context gaps** — Would a subagent starting fresh (no conversation
history) have everything it needs? Check for:
- References to "the bug we discussed" (subagent wasn't in that discussion)
- Implicit knowledge of the codebase structure
- Assumptions about what packages are installed or what patterns exist
- Missing file paths or line numbers

**Interpretation latitude** — Could a subagent "improve" or "enhance"
beyond scope? Look for:
- Tasks that describe a goal without constraining the approach
- Missing "do NOT" boundaries on adjacent code
- Opportunities for a subagent to refactor, rename, or reorganize

**Cross-task dependencies** — Are ordering constraints explicit? Would
a subagent working on Task 3 know it depends on Task 1? Look for:
- Shared files modified by multiple tasks
- Tasks that create types/interfaces consumed by later tasks
- Test fixtures needed across tasks

**Testing pitfalls** — If `docs/pitfalls/testing-pitfalls.md` (or the
project's equivalent) exists, the runner MUST read it. If it doesn't
exist, the runner SHOULD note that absence in the round's findings. If
the doc is read, the runner MUST add warnings to any task that risks
falling into a documented pitfall. Common traps:
- Testing mock behavior instead of real behavior
- Missing AOT verification
- Substring assertions instead of structural JSON checks

**Implementation pitfalls** — If `docs/pitfalls/implementation-pitfalls.md`
(or the project's equivalent) exists, the runner MUST read it. If it
doesn't exist, the runner SHOULD note that absence in the round's
findings. If the doc is read, the runner MUST add warnings to any task
that risks falling into a documented pitfall. Common:
- AOT-unsafe types in serialization contexts
- Pre-signed URL auth header leaks
- Hand-built JSON without escaping

### Round execution

For each round, the runner MUST:

1. Read the plan end-to-end
2. Check every dimension above
3. Note each finding with location (Task N, specific text)
4. Fix all findings in the plan
5. Record the round number and finding count

### Completion criteria

- Round 1: expect 5+ findings (plans always have gaps on first review)
- Round 2: expect 2-3 findings (residual from fixes in round 1)
- Round 3: expect 1-2 (second-order effects of prior fixes)
- Round 4: if 0 findings, the runner MAY stop. If any findings remain, the runner MUST run another round.
- Round 5+: the runner MUST continue running rounds until one produces 0 findings.

If round 1 produces 0 findings, the runner is not looking hard enough.
The runner MUST re-read the dimensions and run round 1 again.

### After completion

The runner SHOULD log observations about plan quality and recurring patterns to a private journal (or whatever pattern-store the project uses — an MCP journal, a `gstack-learn`-style command, a dated `docs/learnings/` file, etc.). Capture:

- **Type:** pattern
- **Key:** `plan-review-[slug]`
- **Insight:** what patterns emerged, what was most commonly wrong

Then commit the reviewed plan.
