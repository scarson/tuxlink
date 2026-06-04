---
name: writing-plans-enhanced
description: Use when writing implementation plans for this project. Wraps superpowers:writing-plans with project-specific conventions — plan location, execution strategy recommendation, subagent-proofing requirements, TDD mandates, and pitfall review.
---

# Writing Plans (Enhanced)

Wraps `/superpowers:writing-plans` with project-specific requirements
that prevent subagent failures during execution.

## Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as shown here.

## Step 1: Invoke the base skill

Invoke `/superpowers:writing-plans`. Follow it completely.

Save the plan to `docs/plans/<date>-<slug>-plan.md`
(e.g., `docs/plans/2026-04-08-mcp-tools-plan.md`).

## Step 2: Execution strategy recommendation

When `/writing-plans` presents execution options, recommend one with
reasoning. The three options:

1. **Subagent-driven** (`/superpowers:subagent-driven-development`) —
   fresh subagent per task, review between tasks. Best for independent
   tasks needing quality gates.
2. **Parallel session** (`/superpowers:executing-plans` in a worktree) —
   batch execution with checkpoints. Best for tightly coupled sequential
   tasks.
3. **Parallel agents** (`/superpowers:dispatching-parallel-agents`) —
   concurrent agents on independent workstreams. Best for 3+ independent
   tracks with different files.

Base the recommendation on:
- How much context this session has consumed
- Whether the plan is self-contained enough for a fresh session
- How many tasks are parallelizable vs sequential
- Whether any tasks are risky enough to warrant focused attention

## Step 3: Subagent-proof the plan

Subagents start fresh with zero context. The plan MUST prevent their
predictable failure modes:

### Eliminate ambiguity
For each task, specify:
- Exact files to create or modify
- Exact behavior change (current → desired)
- Exact test to write (input, expected output, edge cases)
- Ordering dependencies with other tasks

### Prevent context gaps
Each task description must be self-contained:
- Include evidence (file:line, what's wrong or what's needed)
- Include the approach (not just "fix the bug" or "add the feature")
- Include architectural context if the task depends on a design choice
- If the task touches shared code, list other callers that must still work

### Prevent interpretation drift
- Where there's one correct approach, state it explicitly
- Where there are multiple valid approaches, pick one and specify it
- Add "do NOT" boundaries where a subagent might over-engineer

### Mandate TDD
Every task MUST include:
```
BEFORE starting work:
1. Invoke /superpowers:test-driven-development
2. Read docs/pitfalls/testing-pitfalls.md
Follow TDD: write failing test → implement → verify green.
```

Every task MUST include:
```
BEFORE marking this task complete:
1. Review tests against docs/pitfalls/testing-pitfalls.md
2. Verify test coverage (error paths? edge cases?)
3. Run tests and confirm green
```

Every logical group of tasks MUST include:
```
After completing this group:
Review the batch from multiple perspectives. Minimum 3 review rounds.
If round 3 still finds issues, keep going until clean.
```

### Preserve assertion rigor under pressure

Subagents under CI or time pressure default to weakening assertions when tests race, flake, or fail nondeterministically. This converts coverage erosion into "flake fixes" that pass review because they're framed as CI stability, not as rigor regression. Plans MUST forbid this pattern explicitly in any task that writes tests for concurrency, cancellation, timing-sensitive code, or cross-task coordination.

Every such task MUST include:

```
BEFORE marking this task complete:
If any test assertion races, flakes, or fails nondeterministically, the
fix is deterministic synchronization (e.g., TaskCompletionSource,
SemaphoreSlim, awaitable fence) — NOT assertion removal or weakening.
If synchronization cannot make the assertion pass reliably, STOP and
raise to the dispatching agent. Do not ship a weaker test. Weakened
assertions rationalized as "CI stability fixes" are the exact pattern
this rule prevents.

Prefer mechanism assertions over symptom assertions where feasible: a
timing bound ("Elapsed < 10s") proves absence of a specific symptom;
an observation-of-state assertion ("peers observed cancellation")
proves presence of the mechanism. When racing forces a choice between
them, fix the synchronization rather than dropping the mechanism
assertion.
```

The commit subject for any change touching test assertions SHOULD state what happened to them — "add", "strengthen", "preserve", or explicitly "weaken" with rationale. Subjects like "CI timing fix" or "test stabilization" obscure whether coverage eroded and let regressions slip past review.

### Review against pitfalls
Read both pitfalls docs and check if any planned work could fall into
documented traps. Add explicit warnings to relevant task descriptions:
- `docs/pitfalls/implementation-pitfalls.md`
- `docs/pitfalls/testing-pitfalls.md`

### Minimize cross-task conflicts
If two tasks touch the same file, put them in the same task or
explicitly sequence them. Parallel subagents editing the same file
create merge conflicts.

## Step 4: Run /plan-review-cycle

After writing the plan, invoke `/plan-review-cycle` before committing.

## Step 5: Living Document Contract (MANDATORY in every plan)

Every plan produced by this skill MUST include a **Living Document Contract** block immediately after the Goal / Architecture / Tech Stack header. The contract binds every future executor to keep the plan synchronized with implementation state as work progresses — not only at completion.

### Why (social proof)

Plans that go stale during execution impose a compounding cost on every future agent that re-enters the work. Reconstructing state from scattered PR notes, commit messages, and coord-log entries takes meaningful time per deferred or modified phase; updating the plan at ship time is much cheaper. The asymmetry favors writing at ship time and compounds across every downstream dispatch that consumes the plan.

Observed across multi-agent coordination cycles: when a plan executor writes per-phase ✅/⏸ banners at ship-time — each deferred phase carrying a prose description of its unblock condition + a link to the likely-unblocker artifact — the eventual follow-up dispatch becomes a short pointer ("Phase N says ⏸ DEFERRED pending X; X's own Execution Status banner now says ✅ SHIPPED; execute Phase N") instead of an archaeology session. The plan's living state carries the unblock condition and reader context across sessions with near-zero loss: the upstream unblocker updates their own plan's banner to ✅ SHIPPED when they ship, and the downstream executor reads that banner via the link embedded in their own deferred-phase banner. Reconstructing this state after the fact doesn't work — the information must be captured at the moment of deferral, by the agent who defers.

Prose description + artifact link is the resilient coordination pattern. Exact-string coordination across agents is not: paraphrases break it, scope edits on the unblocker's side break it, and it creates brittle action-at-a-distance semantics that require three separate agents (the deferrer, the unblocker, the follow-up dispatcher) to agree on a string they never negotiate.

### What the contract binds

Paste the following block verbatim into every plan, immediately after the Goal / Architecture / Tech Stack header. Do NOT paraphrase. Future executors rely on the exact phrasing to locate the contract. The MUST / SHOULD / MAY keywords in the block are interpreted per BCP 14 (RFC 2119 + RFC 8174) — capitalized only when normative.

```markdown
## Living Document Contract

This plan is a living document. Every executing agent MUST update it as
execution progresses, not only at completion.

- **On phase claim:** the executor MUST flip the banner to 🚧 IN PROGRESS
  with a claim timestamp (ISO 8601 UTC) and the active branch name. The
  banner MUST NOT include an expected-completion estimate — agents cannot
  reliably estimate their own wall-clock, and a fabricated duration
  becomes a stale anchor that misleads future readers. Followers
  encountering a 🚧 banner determine liveness by observable signals (PR
  existence, recent branch commits), not by arithmetic on expected times.
  See Step 5's stale-claim reclaim protocol.
- **On phase ship:** the executor MUST update that phase's **Execution
  Status** banner with the shipped commit SHA(s) and date. If a PR is
  open, the PR number and URL MUST appear in the top-of-plan Execution
  Status table.
- **On phase defer:** the executor MUST update the banner with ⏸ status
  AND a prose description of the unblock condition + a link to the
  likely-unblocker artifact (plan page, task, or PR whose own Execution
  Status banner will signal completion). Prose + link is durable across
  paraphrases and scope edits; exact-string coordination between agents
  is not.
- **On PR merge:** the executor MUST record the merge SHA in the banner
  + the top-of-plan Execution Status table.
- **On deviation from the written plan** (scope edits, structural
  refactors, dropped tasks, reordered phases): the executor MUST
  inline-document the deviation in the affected task AND summarize it
  in the top-of-plan Execution Status as a "Deviations" subsection.
  Deviation state MUST NOT live only in PR notes or status reports.
- **On discovery** (pre-existing drift surfaced during execution, new
  bugs found, architectural issues noted): the executor MUST add a
  "Discoveries" subsection at the top of the plan with pointers to the
  files/lines affected. Follow-up dispatches read this subsection to
  avoid duplicate discovery work.

The plan SHOULD reflect reality at the end of every session that touches
it. Anything worth putting in a status report to the user is worth
putting in the plan.

Rationale: `/writing-plans-enhanced` Step 5. Writing at ship time is
cheap; reconstruction by downstream readers is expensive, compounds
across dispatches, and fails silently when state is split across PR
notes and commit messages.
```

### What format to use

Plans MUST include per-phase **Execution Status** banners at the top of every phase section. Banners SHOULD use this format (keep the emoji markers — they are load-bearing for scan-ability):

```markdown
## Phase N — [Phase Name]

**Execution Status:** ⬜ NOT STARTED
```

_or_

```markdown
**Execution Status:** 🚧 IN PROGRESS — claimed <YYYY-MM-DD HH:MMZ>
(branch `fix/<slug>`, N/M tasks shipped; PR #<N> if open)
```

_or_

```markdown
**Execution Status:** ✅ SHIPPED at `<SHA>` on <YYYY-MM-DD>
(PR #<N> merged at `<merge-SHA>`)
```

_or_

```markdown
**Execution Status:** ⏸ DEFERRED pending [prose description of the
unblock condition — what must exist or ship for this phase to be
pickable]. See [link to the likely-unblocker artifact — its plan
page, its task, its PR — whose Execution Status banner will signal
completion]. Follow-up dispatch verifies by reading the linked
artifact's banner, not by grepping for strings.
```

Plans SHOULD include a top-of-plan **Execution Status** summary table once at least one phase has shipped or deferred:

```markdown
## Execution Status

**Overall:** N/M phases shipped, K deferred pending upstream gates.

| Phase | Status | Ship SHA(s) | Notes |
|---|---|---|---|
| 1 — [name] | ✅ Shipped | `<SHA>` | PR #N merged YYYY-MM-DD |
| 2 — [name] | 🚧 In progress | — | on branch `fix/<slug>` |
| 3 — [name] | ⏸ Deferred | — | pending [prose condition] — see banner |
| 4 — [name] | ⬜ Not started | — | — |

### Deviations
- Task N.M: [one-line summary + pointer to inline task note]
- Phase K Task K.L: [summary]

### Discoveries
- [Surfaced file/pattern + status: shipped fix / deferred / flagged only]
```

### Why banners over bottom-of-plan tables alone

Banners sit **above** every task body. An executor scanning the plan sees execution state BEFORE reading the task — no way to accidentally start a deferred phase without first hitting the banner that tells them the unblock condition and where to check its live status. A bottom-of-plan status table alone relies on the executor reading to the bottom before starting work, which is not the failure mode we're protecting against.

The top-of-plan Execution Status table provides the at-a-glance summary; the per-phase banners provide the context-at-point-of-use. Both SHOULD appear together. Neither is sufficient alone.

### Stale claim reclaim protocol

A 🚧 banner claim persists as long as no agent updates it. If the claiming agent dies (session timeout, rate limit, abandoned session, orchestrator-subagent cascade failure, compaction without handoff), the claim becomes a silent lock blocking all follower agents. Followers need a cheap way to detect and reclaim stale claims without derailing into investigation.

**The check:** two observable signals, both under a minute, neither requiring time arithmetic.

1. **Is a PR open for the claimed branch?**
   ```bash
   gh pr list --head <branch>
   ```
   If yes, the work is visible and under review. Trust the claim, move on.

2. **If no PR, has the branch had any recent commits?**
   ```bash
   git log -1 origin/<branch>
   ```
   Any commit in recent memory (the follower's own read of "recent") means the claim is active. The follower does NOT calculate elapsed time — they look for ANY activity signal and trust their instinct on whether it feels fresh.

If BOTH signals are absent (no PR AND no recent commits, or the branch does not exist on origin at all), the claim is stale. The follower MAY reclaim by:

1. Adding a reclaim note inline to the banner:
   ```markdown
   **Reclaim note:** prior claim at <prior-timestamp> reclaimed at <now-timestamp>
   — no PR, no branch activity. Prior branch `<name>` preserved for archaeology.
   ```
2. Updating the banner's timestamp and branch to the new claim.
3. Proceeding as a fresh claim.

The follower MUST NOT delete prior banner history or coord-log entries. Layer new on top; preserve the arc. Future readers should see the full transition trail.

The follower SHOULD assume any uncommitted work from the prior agent is lost. Reconstruct from the plan's task spec; do NOT try to infer the prior agent's local-only progress.

**Why observable signals instead of time-based staleness:**

- Agents cannot reliably estimate their own wall-clock. Claim banners with "expected 2h" anchor future readers to a fabricated number. Observing "there's activity" or "there isn't" is grounded in git; estimating "is it overdue" is not.
- Arbitrary staleness thresholds ("4h → stale," "24h → stale") are project-specific and drift out of calibration as work patterns change. The follower's own judgment of "is this fresh" converges correctly without a fixed threshold, because the two signals (PR + commits) are binary present/absent.
- A follower who thinks 20 min is "recent" just waits longer; a follower who thinks 24h is "recent" waits longer still. Both eventually reach the "no signal present" state and make the right decision.

**Failure modes the protocol does NOT cover:**

- **Agent died before pushing the branch.** No PR, no branch on origin, but the banner says 🚧. Follower concludes stale and reclaims. Prior agent's uncommitted state is lost. Acceptable — the alternative (wait forever for a resumed agent that isn't coming) is worse.
- **Two followers reclaiming simultaneously.** Git's push-reject-on-stale-ref handles this naturally: the second follower's banner-flip commit fails to push because the first got there first; the second re-reads and sees the claim is no longer stale.
- **Orchestrator-subagent cascade death where the subagent keeps progressing.** The branch keeps receiving commits even though the parent orchestrator's status reporting is dead. Branch-activity signal correctly shows the claim as live. The "orphaned by parent orchestrator" state is invisible at the claim level and warrants a separate discipline around parent-child lifetime management.

**When this protocol is insufficient:** if coordination gets busy enough that stale-claim detection becomes routine work (4+ concurrent agents regularly, claim disputes happening in practice), consider adopting a dedicated coordination tool with first-class claim + dependency tracking. The lightweight protocol above handles ~80% of cases at ~5% of the adoption cost of a full coordination tracker.

### What this skill does when wrapping `/superpowers:writing-plans`

When producing the initial plan:

1. Paste the Living Document Contract block (above) verbatim after the base skill's `**Tech Stack:**` header.
2. Add an `## Execution Status` section with `**Overall:** Not started.` and a table with all phases marked `⬜ Not started`.
3. Add an **Execution Status** banner at the top of every `## Phase N` section, initialized to `⬜ NOT STARTED`.
4. Include a brief "why this matters" sentence pointing at this skill's Step 5 so future executors know where the discipline comes from.

Executors reading the finished plan then inherit the contract automatically. The contract is self-propagating: every session that touches the plan leaves it in the shape the next session needs.
