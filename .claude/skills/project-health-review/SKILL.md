---
name: project-health-review
description: Run a critical, multi-dimensional quality review of the project using parallel adversarial agents. Each agent focuses on one dimension and reports only problems. Use periodically as a health check or before major milestones.
argument-hint: "[optional: specific area to focus on, or 'full' for all dimensions]"
---

# Project Health Review

## Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they appear in all capitals, as shown here.

## Overview

Running a critical quality review of the project.

Focus: **$ARGUMENTS** (default: full review across all dimensions)

---

## Philosophy

This is an adversarial review. Every agent's job is to find problems — not to praise what's working. If an agent has nothing critical to say about a dimension, that's acceptable, but the bar for "nothing to report" SHOULD be very high — agents SHOULD prefer reporting a small issue over reporting nothing.

**Anti-sycophancy rules for all agents:**
- Agents MUST NOT open with "the project is generally well-structured" or similar
- Agents MUST NOT soften findings with "but overall the code is solid"
- Agents MUST NOT give scores or grades — just report problems
- Agents MUST NOT pad the report with minor style nits to look thorough — they MUST only report findings that would matter to a senior engineer evaluating this project
- If an agent genuinely finds no significant issues in its dimension, it MUST say "No significant findings" and explain in one sentence what it looked at

---

## Execution

The runner MUST launch **5 parallel agents**, one per dimension below. Each agent MUST operate independently with no knowledge of the others' findings.

### Agent model selection

Each subagent SHOULD be invoked using the **latest available Claude Opus model** or **GPT-5 (or successor) at x-high reasoning effort**, unless the user has explicitly instructed otherwise for this run. Health review is correctness-critical adversarial analysis — it benefits asymmetrically from maximum reasoning bandwidth, and saving model cost trades poorly against missed problems that ship to production. If the agent framework requires a specific model parameter on dispatch, the runner MUST set it accordingly; if the framework inherits the parent's model, the runner MUST ensure the parent is on the strongest tier before dispatching.

### Per-agent instructions

Each agent MUST:
1. Read the relevant source files (not just `CLAUDE.md` / `AGENTS.md` — actually read the code)
2. Read the project's plan / architecture docs (e.g., `PLAN.md`, `docs/plans/`, `docs/architecture.md`) for intended design where relevant
3. Apply the "rejection memo" frame: you're evaluating whether to adopt this project or deploy it in production, and you're looking for reasons to say no
4. Report ONLY problems, ranked by severity

### Agent 1: Code Quality & Idiom

```
You are reviewing this project for code quality problems. Your job is to
find issues — not to praise what's working.

Read the actual source code in the project's primary source directories
(internal/, src/, lib/, app/, or whatever the project uses). Focus on:

- Non-idiomatic code: fighting the language's idioms, patterns lifted
  from another language, unnecessary abstraction layers, interface pollution
- Code duplication: similar logic repeated across packages without shared helpers
- Error handling: swallowed errors, errors that lose context, inconsistent
  error wrapping patterns, panics/exceptions where errors should be returned
- Naming: unclear, misleading, or overly verbose names (but skip purely
  stylistic preferences — focus on names that would confuse a new contributor)
- Complexity hotspots: functions/methods that are too long, too many parameters,
  deeply nested logic, high cyclomatic complexity
- Dead code: unused exports, unreachable branches, vestigial patterns

Start by reading the project's manifest (go.mod / package.json / pyproject.toml /
*.csproj / Cargo.toml / etc.), then sample at least 8-10 packages or modules
across the source tree. Read the largest files in each package — that's
where complexity hides.

For code-level findings, cite file:line. For pattern-level findings (e.g.,
"error handling is inconsistent across feed adapters"), cite 2-3 representative
examples but frame the finding as the pattern, not the individual instance.

Output format — a flat list of findings, each as:

### [CRITICAL|MAJOR|MINOR] <title>

**Evidence:** <file:line reference OR architectural description>
**Problem:** <what's wrong and why it matters>
**Risk:** <what could go wrong if this isn't addressed>

Do not include a summary, introduction, or conclusion. Just the findings.
```

### Agent 2: Architecture & Design

```
You are reviewing this project for architectural and design problems. Your
job is to find issues — not to praise what's working.

Read the project's plan / architecture docs first to understand the intended
architecture (PLAN.md, docs/plans/, docs/architecture.md, or equivalent),
then read the actual code to see what was built. Focus on:

- Coupling: are packages that should be independent actually entangled?
  Follow import graphs. Do high-level packages import low-level packages
  directly when they should go through abstractions? Do storage methods
  know about HTTP concerns?
- Abstraction quality: are interfaces defined where they're consumed or where
  they're implemented? Are there interfaces with only one implementation that
  add indirection without value? Are there missing abstractions where concrete
  types are passed through too many layers?
- Scalability walls: what breaks first when this handles 10x the current load?
  100x? Where are the single points of failure? What can't be horizontally
  scaled?
- Complexity budget: which parts are more complex than they need to be? Which
  are too simple for what they need to handle? Where is the accidental
  complexity?
- Plan vs reality: where does the implementation diverge from the plan/design
  docs in ways that look unintentional or problematic?
- Missing capabilities: what would a production deployment need that isn't
  here? (graceful shutdown, health checks, circuit breakers, backpressure, etc.)

These findings are typically architectural, not code-level. Reference components,
packages, and interactions — not individual lines. When a code example
illustrates an architectural issue, include it, but the finding should be about
the design, not the line of code.

Output format — a flat list of findings, each as:

### [CRITICAL|MAJOR|MINOR] <title>

**Evidence:** <component/package references, import relationships, or design pattern description>
**Problem:** <what's wrong and why it matters>
**Risk:** <what could go wrong if this isn't addressed>

Do not include a summary, introduction, or conclusion. Just the findings.
```

### Agent 3: Test Quality

```
You are reviewing this project for test quality problems. Your job is to
find issues — not to praise test coverage numbers.

Read the actual test files. Focus on:

- Tests that test mocks: any test where the assertions verify the behavior of
  a mock rather than real logic. These provide false confidence.
- Missing error path coverage: are error branches tested? Many tests only
  test the happy path and never exercise error returns.
- Brittle tests: tests coupled to implementation details that will break on
  refactoring even if behavior is unchanged (e.g., testing exact SQL strings,
  exact log messages, order-dependent assertions on unordered data)
- Missing integration tests: are the boundaries between components tested?
  E.g., adapter → store → pipeline? API handler → store → database?
- Test isolation: do tests share state? Can test order affect results? Are
  there tests that pass in isolation but fail in CI?
- Assertion quality: tests that check "no error" but don't verify the actual
  result. Tests with a single assertion that doesn't prove the behavior works.
- Missing edge cases: nil/empty inputs, boundary values, concurrent access,
  Unicode/special characters in text fields
- Test helpers: are test utilities well-designed or do they hide important
  setup that makes tests hard to understand?

Read test files across at least 6-8 packages or modules. Focus on the most
critical packages first — storage, auth, data pipelines, the API layer,
anything that handles money or user data.

For each finding, cite the test file and explain what's wrong with the test
and what real bug it would miss.

Output format — a flat list of findings, each as:

### [CRITICAL|MAJOR|MINOR] <title>

**Evidence:** <test file:line or pattern across multiple test files>
**Problem:** <what's wrong with these tests>
**Risk:** <what real bug or regression could slip through>

Do not include a summary, introduction, or conclusion. Just the findings.
```

### Agent 4: Operational Readiness

```
You are reviewing this project for operational readiness problems. Your job
is to determine what would go wrong if this were deployed to production
today. Find the problems.

Read the project's entry points, config layer, worker/scheduler code,
metrics/observability code, deployment manifests, and any deployment-related
files (cmd/, src/server.*, internal/config/, internal/worker/, internal/metrics/,
docker/, k8s/, fly.toml, render.yaml, etc. — whichever apply). Focus on:

- Failure modes: what happens when the database is down? When an external
  API is unreachable? When disk is full? When memory is exhausted? Are
  these handled gracefully or does the process crash?
- Observability gaps: are there important operations that aren't instrumented
  with metrics or structured logging? Can an operator diagnose "why did
  feature X stop working?" from logs and metrics alone?
- Graceful shutdown: does the server drain in-flight requests? Do workers
  finish current jobs? What happens to jobs claimed but not completed?
- Resource management: connection pool sizing, goroutine/thread limits,
  memory bounds on large operations, file descriptor limits
- Configuration footguns: are there config combinations that silently break?
  Missing required env vars that aren't validated at startup? Defaults that
  are dangerous in production?
- Deployment concerns: database migration safety (can you roll back?), secret
  management, TLS configuration, container health checks, startup dependencies
- Monitoring blind spots: what would you NOT know about from the current
  metrics? Queue depth? Error rates per dependency? Notification/email
  delivery latency?

This dimension is about "would I trust this in production?" Focus on what
an SRE would flag during a production readiness review.

Output format — a flat list of findings, each as:

### [CRITICAL|MAJOR|MINOR] <title>

**Evidence:** <file reference, config pattern, or operational scenario>
**Problem:** <what's wrong and why it matters for production>
**Risk:** <specific failure scenario this creates>

Do not include a summary, introduction, or conclusion. Just the findings.
```

### Agent 5: API Design & Developer Experience

```
You are reviewing this project for API design and developer experience
problems. Your job is to find issues — not to praise what's working.

Read the API/handler layer (internal/api/, src/api/, src/routes/, controllers/,
or whichever applies), the OpenAPI spec / API contract docs if present, and
any frontend / SDK code that consumes the API. Focus on:

- Consistency: are similar endpoints handled similarly? Same pagination
  pattern? Same error format? Same naming conventions? Inconsistencies
  confuse API consumers.
- REST violations: wrong HTTP methods, non-standard status codes, missing
  Location headers on 201s, inconsistent resource naming
- Error quality: do error responses give enough information to debug? Are
  they too verbose (leaking internals)? Is the error format consistent?
- Pagination: is it correct? Does keyset pagination handle edge cases
  (empty results, deleted records, concurrent modifications)?
- Input validation: are there endpoints that accept input without adequate
  validation? Over-validation that rejects legitimate input?
- Authentication/authorization gaps: are there endpoints that should require
  auth but don't? Are RBAC checks consistent?
- Versioning: is the API versioned? Is there a strategy for breaking changes?
- Documentation: does the API contract match the implementation? Are there
  undocumented behaviors?
- Frontend / SDK integration: read the consumer code to see how the API is
  consumed. Are there pain points visible from the client side? Unnecessary
  round trips? Missing endpoints that force client-side workarounds?

Output format — a flat list of findings, each as:

### [CRITICAL|MAJOR|MINOR] <title>

**Evidence:** <endpoint, handler file:line, or API pattern>
**Problem:** <what's wrong and why it matters for API consumers>
**Risk:** <what breaks or confuses downstream consumers>

Do not include a summary, introduction, or conclusion. Just the findings.
```

---

## Synthesis

After all 5 agents complete, compile findings into a single report:

1. **Deduplicate**: if multiple agents found the same issue from different angles, merge into one finding and note which dimensions flagged it (cross-dimensional findings are often the most important)
2. **Rank by severity**: CRITICAL first, then MAJOR, then MINOR
3. **Group cross-cutting concerns**: if several findings share a root cause, group them and identify the root cause
4. **Tag each finding** with its source dimension(s)

### Final Output Format

```markdown
# Project Health Review
**Date:** YYYY-MM-DD HH:MM
**Scope:** [full | specific area]

## Critical Findings
(Findings that represent significant risk if not addressed)

### 1. <title>
**Dimensions:** [which agents flagged this]
**Evidence:** ...
**Problem:** ...
**Risk:** ...
**Suggested approach:** <1-2 sentences on what fixing this would look like>

## Major Findings
(Findings that should be addressed but aren't immediately dangerous)

...

## Minor Findings
(Real issues that are lower priority)

...

## Cross-Cutting Themes
(If multiple findings share a root cause, identify it here)

...
```

### Saving Reports

Save **both** the individual agent reports and the consolidated synthesis. Filenames include a timestamp and scope slug for uniqueness across multiple runs in a day.

**Filename format:** `YYYY-MM-DDTHH-MM-<scope>-<report-type>.md`

- `<scope>` is a slugified version of the focus area: `full` for a full review, otherwise a short slug like `alert-pipeline` or `auth-layer`
- `HH-MM` is the time the review was initiated (24h format, local time)

Example for a full review started at 14:30 on 2026-03-10:

1. **Individual agent reports** — save each agent's raw output immediately when it returns:
   - `docs/health-reviews/2026-03-10T14-30-full-agent-1-code-quality.md`
   - `docs/health-reviews/2026-03-10T14-30-full-agent-2-architecture.md`
   - `docs/health-reviews/2026-03-10T14-30-full-agent-3-test-quality.md`
   - `docs/health-reviews/2026-03-10T14-30-full-agent-4-ops-readiness.md`
   - `docs/health-reviews/2026-03-10T14-30-full-agent-5-api-design.md`

   Each file should have a header like:
   ```markdown
   # Agent N: <Dimension Name>
   **Date:** YYYY-MM-DD HH:MM
   **Scope:** [full | specific area]
   ```
   followed by the agent's raw findings exactly as returned.

2. **Consolidated synthesis** — after all agents complete and synthesis is done:
   - `docs/health-reviews/2026-03-10T14-30-full-project-health-review.md`

The runner MUST save individual reports as soon as each agent completes — MUST NOT wait for synthesis. This ensures the raw analysis is preserved even if the session is interrupted.

---

## Rules

- Agents MUST NOT soften findings. If something is a problem, say so directly.
- Agents MUST NOT add a "positives" or "what's working well" section. This review is exclusively about finding problems.
- Each agent MUST read **actual source code**, not just `CLAUDE.md` / `AGENTS.md` descriptions. Those describe intent; the code reveals reality.
- Findings MUST be **actionable** — "the code could be better" is not a finding. "Function X in file Y swallows the error from Z, which means failures in Z are invisible to operators" is a finding.
- The runner MUST launch all 5 agents. Launching fewer breaks the independence-between-agents primitive that makes this review work — independence is a feature, not overhead.
- The synthesis MUST be honest about severity. The runner MUST NOT inflate minor issues to look thorough and MUST NOT downgrade critical issues to avoid alarm.
