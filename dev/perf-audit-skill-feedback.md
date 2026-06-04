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

## Running M1 (tuxmodem-phy) — Phase 2/3 observations

- 👍 **Blind-lane validity is the headline result.** I dispatched the 6 lanes
  *blind* — given only scope + realistic-load context, NOT the hot-path map that
  5 rounds of targeted review had produced. The lanes independently reproduced
  the ENTIRE review hot-path map (per-symbol FFT planner, per-subcarrier
  alphabet rebuild, preamble O(N·M) correlation) AND added findings the review
  missed (RT-callback mutex-across-copy, per-symbol pilot `HashSet`, per-symbol
  equalizer, per-symbol `Vec` churn). This is strong evidence the lane
  decomposition + anti-sycophancy + calibration actually *discovers*, not just
  restates a prior. Running a perf-audit skill blind is, in hindsight, the right
  way to TEST it — it measures discovery, not confirmation. Worth a methodology
  note in the skill.
- 👍 **Cross-lane agreement is a real confidence signal.** P1 (per-symbol FFT
  planner) was independently flagged CRITICAL by 5 of 6 lanes; the RT mutex by 2.
  The synthesis instruction to record "which lanes flagged each" + the
  fingerprint make this legible. Leading the consolidated report with the
  agreement worked well.
- 👍 **The cost-map (descriptive) lane earned its keep.** It produced the one
  architectural *correction* of the run — that the DSP is batch (record-buffer →
  then-demod), so the heavy per-symbol work is NOT on the cpal real-time callback
  path and doesn't compete with the audio deadline. The adversarial lanes were
  busy finding allocations; the descriptive map caught a *framing* error in the
  pre-audit assumption. Good argument for keeping a non-adversarial lane.
- 🟡 **Dedup burden is real on a small hot core.** 5 lanes all reported the
  FFT-planner finding in 5 different framings (algorithmic "defeats plan cache",
  memory "KB twiddle alloc", data-access "111 builds/payload", idiom-currency
  "rustfft planner-reuse fast path", cost-map "region #1"). Great for confidence,
  but the runner does real work collapsing them to one finding with one
  fingerprint. The skill could note that high overlap on a small hot core is
  expected and tell the runner to lead with the agreement (which I did).
- 🟡 **`reasoning_effort` recorded honestly as "default"** — the Agent tool sets
  `model: opus` but exposes no reasoning-effort knob (confirmed). Frontmatter
  says so rather than claiming x-high.
- 👍 **Lane-reads-its-own-pack adaptation worked** — each lane read `rust.md` +
  `version-indexes/rust.md` itself; no need for the runner to hold/paste packs.
  Recommend the skill bless this as a first-class subagent-dispatch mode.

## Cross-validation / findings model

- 👍 The fingerprint scheme (`<lane>:<file>:<symbol>:<slug>`, symbol-not-line)
  made dedup + the (empty, first-run) regression diff straightforward to emit.
- 🟡 **Suspected-bugs handoff has a small scope-bleed.** 3 SBs surfaced
  (preamble off-by-one, Gardner-normaliser bias, RT-thread poison panic); the
  kickoff file auto-wrote cleanly and the "record don't chase" discipline held.
  But SB1 (preamble off-by-one) is co-located with a perf finding (P2, same
  function), and the cycle tells the *perf* plan to fix it in P2's task — so one
  suspected bug leaks from the bug-hunt track into the perf-remediation track.
  Sensible pragmatically, but it blurs the "audit records bugs, never fixes
  them" boundary. Worth the skill calling out the co-located-bug case explicitly.

## Plan generation / review (writing-plans-enhanced, plan-review-cycle)

- (pending M1)

## Artifacts / output ergonomics

- 🟡 Phase 8 commits artifacts to `docs/perf-audits/` + `docs/plans/`, and
  references `docs/perf-audits/runs.jsonl` + `cache/` that don't pre-exist —
  the skill should state it creates them (it does `git add` paths that may not
  exist on a first run, which can error). Will confirm during M1.

## Authoring new skill content — reference-discipline trap (caught by operator)

- 🐞→📝 When I authored the new `whole-repo-scoping.md` method, I labelled its
  phases `S0/S0.5/S1–S6` and cross-referenced them opaquely ("see S4", "the S2
  tie-breaker", "S0.5 × S6"). The operator flagged it: the perf-audit skill family
  **deliberately bans opaque session-identifier references** in persistent
  artifacts — the very discipline `finding-model.md` enforces for "Lane 4"/"P3"
  (never a bare opaque label as the sole referent; describe in self-contained
  terms). The original skill phases carry descriptive titles and the family avoids
  bare-code cross-refs. **Lesson for the skill:** a CONTRIBUTING/authoring note in
  the skill (or in `writing-skills`) should state that *new* skill content must
  follow the same reference discipline — opaque `S#`/section codes used as
  cross-references are exactly what the finding-reference rule forbids, and it's
  an easy trap when drafting a multi-phase method. Fixed by renaming every phase
  to a descriptive self-contained title and rephrasing all cross-refs.

## Plan generation + plan-review (writing-plans-enhanced, plan-review-cycle) — M1

- 👍 **The plan-review-cycle earns its place — it caught a BLOCKING defect the
  plan-writer missed.** writing-plans-enhanced produced a thorough 12-task plan
  (disposition discipline held: all P1–P12 scheduled, empty Deferred appendix,
  per-task verification gates). But the plan-review (subagent-readiness lens)
  found Task 9's "no-dep lock-free callback" design **would not compile**
  (`Arc<Vec<f32>>` has no interior mutability) — plus a public-API break (Task 6)
  and drifted constructor line-refs (Task 1). i.e. the review is not ceremony; on
  a first real plan it found a compile-error, an API-contract violation, and
  navigational drift. Strong argument for keeping the review gate mandatory.
- 🟡 The plan-review found ~22 integration test files where the plan said "~6" —
  the writer under-counted the existing guard surface. Minor, but: the plan
  step "ADD bit-exact goldens" was right anyway (existing assertions are weak).

## Reduced-depth tier (M3) — validation + a gap

- 👍 **Calibration held on a low-throughput path.** M3 (ARDOP, external-TNC I/O)
  came back ALL-MINOR across 4 lanes — the lanes did NOT inflate the byte-by-byte
  `VecDeque` drain into a CRITICAL, because the load context (low HF data rates,
  radio airtime is the bottleneck) correctly bounded Impact. This is the
  finding-model's reachability/frequency axis doing real work, and it vindicated
  the round-2 decision to demote M3 from full→reduced (a full cycle would have
  found nothing more).
- 🟡 **"Reduced depth" is not a first-class skill mode — I improvised it.** I ran
  a 4-lane subset (algorithmic/memory/data-access/concurrency) and for R9 a
  frontend-tuned subset (react-idioms/algorithmic/data-access/cost-map). The
  skill's lane table is all-or-(6-core); a blessed "reduced" lane-subset mode
  (with guidance on which lanes to keep per slice class) would make the
  whole-repo-multiple-runs use-case cheaper and more consistent. (The
  whole-repo-scoping method doc now encodes this, but the underlying
  performance-audit skill doesn't expose it.)

## Calibration-from-source (R9) — a real strength

- 👍 **Lanes corrected the dispatcher's load assumption by reading the code.** I
  briefed R9's lanes "periodic re-render is ~1 Hz." Three independent lanes read
  the Rust backend and corrected it to **4 Hz** (`modem_status.rs:355`), then
  re-ranked accordingly. The "your reading of the actual code is primary" rule in
  the shared preamble is load-bearing — it makes the audit robust to a wrong
  scope summary. Worth the skill calling this out as an expected, desirable
  behavior (lanes MAY correct the scope summary's load claims, and the synthesis
  should record the correction — which I did in the consolidated frontmatter).

## Cross-unit coherence + a whole-repo gap

- 👍 **Independent units converged on the same root.** M3's `modem_status`
  4 Hz broadcaster ↔ R9's 4 Hz whole-panel re-render cascade ↔ R9's badge-poll
  IPC are the same system surface seen from the backend and frontend slices —
  they corroborate without coordination.
- 🟡 **No cross-run synthesis step exists.** `performance-audit` synthesizes
  WITHIN one run; `performance-audit-cycle` plans WITHIN one scope. For a
  whole-repo run across many slices, there's no skill-level "roll up themes
  across all the slice reports" phase — the coherence above I had to notice
  manually. The whole-repo-scoping method doc's two-level roll-up addresses this
  at the method layer; the cycle could grow an optional final cross-slice
  synthesis.

## Reachability axis (latent M2 / dev-only M5) — handled well

- 👍 The finding model's `reachability` dimension cleanly handled two
  non-standard cases: M2 (latent — zero live callers; findings tagged "fires once
  FEC is wired in") and M5 (dev-only tool — calibrated to sweep wall-time, not
  shipped runtime). Lanes did not scream CRITICAL on dead/dev code. The model
  generalizes past the "live request path" framing its examples assume.

## version-index coverage gap (idiom-currency)

- 🟡 The shipped `version-indexes/rust.md` is **build-config-centric** and carries
  **zero rustfft / num-complex entries**; there is no React index. So the
  idiom-currency lane fell back to model API-knowledge for the DSP (M1/M5) and
  React (R9) stacks. The lane still produced strong findings (rustfft planner
  reuse, `process_with_scratch`, React memo/virtualization), but flagged that the
  index didn't help. Library-API perf entries (not just toolchain/build flags)
  would make idiom-currency more grounded for these ecosystems.
