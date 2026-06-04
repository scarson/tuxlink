# Skill UX Feedback — `performance-audit-cycle` (and siblings)

**Context:** first real use of `scarson/agent-skills` `superpowers-plus`
`performance-audit-cycle` (+ `performance-audit`, `writing-plans-enhanced`,
`plan-review-cycle`) against the tuxlink repo. Operator explicitly requested
running feedback on the experience of *using* the skill. Reverse-chronological
within each category. Author: glade-knoll-shoal.

> Legend: 👍 worked well · 🟡 friction/ambiguity · 🐞 likely defect ·
> 💡 suggestion.

## Onboarding / discovery

- 👍 The cycle's `SKILL.md` is explicit about phase ordering and what it
  delegates vs owns (it names the sibling skills it MUST invoke and says it
  "MUST NOT duplicate" their discipline). Clear separation of concerns.
- 🟡 The cycle assumes the sibling skills are invokable *by name*. In a
  freshly-vendored project install they are discoverable as project skills, so
  this worked — but the skill's fallback ("if the framework cannot invoke skills
  by name, read its SKILL.md from the plugin install location") is the path I'll
  actually rely on for the lane workhorse, since the lanes are dispatched to
  subagents that don't share my skill registry. Worth the skill calling this out
  as the *common* case for subagent-dispatched lanes, not the exception.

## Scope handling

- 👍 The hard "MUST NOT default to everything / MUST ask for scope" guard is
  good — it forced an explicit partition instead of an unbounded run.
- 🟡 For a *whole-repo* goal, the skill gives no guidance on how to slice a repo
  into multiple bounded runs (sizing heuristic, language homogeneity, hot-path
  grouping, cold-glue batching). I had to invent the partition methodology. A
  short "covering a whole repo with multiple runs" appendix would help — esp.
  the "size on production LOC not raw LOC" and "no full cycle on cold glue"
  lessons, which an adversarial review had to surface the hard way.

## Phase mechanics (pre-run, from reading the skill internals)

- 👍 Strong internal design: lane independence as an explicit primitive,
  anti-sycophancy rules, "calibration governs generation, not post-hoc
  suppression," and the strict no-severity-deferral disposition discipline are
  all well-articulated and hard to game.
- 🟡 **Context-cost of the runner-pastes-packs model.** `lane-prompts.md` and
  `SKILL.md` Phase 2 require the *runner* to paste, per lane, the matched
  profile-pack slice + cross-cutting notes + currency brief. For a
  subagent-dispatch harness (Claude Code Agent tool) that forces the runner to
  hold every profile pack in its own context and re-paste per lane — expensive
  at 6–8 lanes × N slices. Adaptation I'm using: dispatch each lane subagent
  with instructions to **read the specific pack file(s) itself** from
  `.claude/skills/performance-audit/profile-packs/` (rust.md + the relevant
  `rust/<module>.md`), passing only scope + brief + output path. Worth the skill
  blessing "lane reads its own pack slice" as a first-class dispatch mode.
- 🟡 **`reasoning_effort` is unsettable in this harness.** `run-schema.md` wants
  `dispatch.reasoning_effort` (x-high/high/default). The Claude Code Agent tool
  lets me set the subagent *model* (opus) but not a reasoning-effort knob, so
  I'll record `reasoning_effort: "default (harness exposes no knob)"` honestly
  rather than claim x-high. The skill correctly says to record the *request*,
  not a guessed identity — this is the honest version of that.
- 👍 Rust `version-indexes/rust.md` ships, so the `idiom-currency` lane has a
  no-network baseline; live registry checks only extend past its
  `covered_through`. Good for a possibly-network-restricted container.
- 🟡 The frontmatter wants `plugin_version` from `superpowers-plus` plugin.json
  → it's `0.2.0` (vendored copy carries no plugin.json in `.claude/skills/`,
  since I flattened the skills; the version lives only in the source repo). I'll
  hardcode `superpowers-plus@0.2.0` and note the provenance. Minor: vendoring
  skills flat loses the plugin.json the schema references.

## Cross-validation / findings model

- (pending M1)

## Plan generation / review (writing-plans-enhanced, plan-review-cycle)

- (pending M1)

## Artifacts / output ergonomics

- 🟡 Phase 8 commits artifacts to `docs/perf-audits/` + `docs/plans/`, and
  references `docs/perf-audits/runs.jsonl` + `cache/` that don't pre-exist —
  the skill should state it creates them (it does `git add` paths that may not
  exist on a first run, which can error). Will confirm during M1.
