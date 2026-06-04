# Method Review (round 4, holistic re-attack on v2)

**Reviewer:** glade-knoll-shoal
**Angle:** Holistic re-attack on v2 (the revision that absorbed rounds 1–3:
generalizability, robustness, followability). Not re-raising those three — they
landed. This round hunts (a) contradictions/redundancy the v2 *merge* introduced,
(b) whether the merged doc is now too heavy to follow, (c) mechanical
applicability walked on concrete repos, (d) interaction effects the three
lane-bound prior reviews each missed, (e) correctness of the new operational
recipes.

**Verdict: NEEDS-EDITS.** Not rework — the skeleton and all three absorbed
angles are sound, and most of v2's new content is correct. But the merge left
**two dangling cross-references to a "Sizing" section that no longer exists**,
an **un-anchored band** that three different rules cite three different ways, a
**COLD SWEEP that can itself exceed one run's capacity** (a genuinely new defect
none of the three angles owned), **OVERLAYs with no ledger/coverage treatment**,
and **no TL;DR minimal checklist** for an agent under context pressure. The
assemble-from-three-reviews process produced a *complete* doc but not an
*internally reconciled* one.

---

## 1. CONTRADICTIONS & REDUNDANCY THE v2 MERGE INTRODUCED

### 1a. **[BLOCKER-ish] Two dangling "(see Sizing)" cross-refs — the section was never added.**
- Line 7: "one coherent subsystem — **see sizing below**".
- Line 139 (S3 principle 3): "Size to the sweet-spot, by build-unit first, LOC as
  a sanity check **(see Sizing)**."

There is **no `## Sizing` section** in v2. `grep '^##'` returns S0–S6, REVIEW
GATE, When-in-doubt, Heuristics, What-this-produces — no Sizing. The
generalizability review (round 1 §1) proposed a dedicated Sizing section with the
verbosity table; v2 instead folded the verbosity band into a **single row of the
bottom Heuristics table** (line 279) and **deleted the section the two
cross-refs point at.** An agent following line 139's "(see Sizing)" scrolls and
finds nothing — it has to *guess* that "Sizing" means heuristics-table-row-2.
This is a straight merge defect. **Fix:** either add a short `## Sizing` section
(preferred — it's referenced twice and is load-bearing) or change both crossrefs
to "(see the verbosity row in Heuristics)".

### 1b. **The band is stated three ways and never anchored where the rules use it.**
The "how big is one slice" number appears in three places with three encodings:
- **S0** (line 29/34): lightweight iff `<~8k production LOC across ≤2 languages`;
  full method iff `>~8k LOC`. This is a **whole-scope** threshold.
- **SPLIT-IFF / KEEP-IFF** (S3, lines 157–158): "exceeds **the sized band** with a
  real seam" / "fit the band". This is a **per-slice** threshold — and it names no
  number, deferring to the non-existent Sizing section (1a).
- **Heuristics table** (line 279): per-ecosystem `0.5–2k / 1–4k / 2–6k`. This is the
  only place an actual per-slice number lives.

These don't cohere. S0's `8k` whole-scope / `≤2 slices` lightweight gate implies a
per-slice size of ~**4k**. That is *mid-band for Go/Java/C# (2–6k)* but **2× over
the Python/Ruby band (0.5–2k)**. So a 7k-LOC two-slice **Django** repo routes to
the *lightweight path* (under 8k), yet each 3.5k slice is **~2× the Python
sweet-spot** — i.e. the lightweight path hands the cycle two over-band slices and
tells it not to review them. The round-1 verbosity fix and the round-3 lightweight
path were merged **without reconciling their thresholds**: S0's `8k` is a
language-blind number sitting on top of a deliberately language-scaled band.
**Fix:** make S0's LOC gate scale the same way (e.g. "`<~8k` for verbose
ecosystems, `<~4k` for dense ones — or simply **≤2 natural slices** as the primary
gate, LOC as the secondary"), and have SPLIT-IFF cite the verbosity row explicitly.

### 1c. **`>4k → SPLIT` was dropped; SPLIT-IFF now has an un-numbered size leg.**
The followability review (round 3 §4a) proposed "SPLIT a candidate IFF any:
production LOC **>4k**; …". v2's SPLIT-IFF (line 156) replaced the number with
"exceeds **the sized band**" — good in spirit (it defers to the per-ecosystem
band) **but the band it defers to is (1a) unreachable and (1b) only in a table
row.** Net: the one *quantitative* leg of the central SPLIT decision rule resolves
to nothing an agent can mechanically apply. Either re-inline a per-ecosystem
number into SPLIT-IFF or fix the crossref so "the sized band" is one click away.

### 1d. **Redundancy: hot/warm/cold is now defined twice and tier-mapped twice.**
- S2 (lines 113–120): the HOT/WARM/COLD checklist + tie-breaker.
- S5 (line 201): "Map the hot/warm/cold checklist result → tier: HOT→FULL,
  WARM→REDUCED, COLD→sweep, cross-slice-pipeline→OVERLAY."
- Heuristics table rows 3–4 restate "verify against code / classify workload" and
  "latent/external → reduced."

This is mostly *fine* redundancy (checklist in S2, mapping in S5). But the
**tie-breaker** appears **three times** with subtly different scope: S2 line 120
("can't find the loop → default WARM"), S3 line 159 ("prefer fewer/larger"),
When-in-doubt line 266 ("WARM + frequency-unresolved"), and Heuristics line 290.
A reader can't tell if "prefer fewer/larger" (a *sizing* tie-breaker) and "default
WARM" (a *tiering* tie-breaker) are one rule or two. They're two — but the doc
never says so. Minor, but it's the kind of drift the propagation contract warns
about, now *inside one file*.

### 1e. **S4 "assume hot" vs S2/When-in-doubt "default WARM" — same uncertainty, opposite default.**
This is the sharpest internal contradiction the merge introduced, and it's a
real interaction (see §4c). Two rules fire on *"I can't establish how often this
runs"*:
- **S2 tie-breaker (line 120)** + **When-in-doubt (line 266)**: unknown hotness →
  **default WARM** (REDUCED tier).
- **S4 fail-safe (line 186–189)**: caller unknown/unaudited → **`assume hot`** at
  **optimistic** Impact.

Both are defensible *in their own lane* (round 2 wrote the S4 one; round 3 wrote
the WARM one) but they were merged without a reconciling sentence, and an agent
hitting "I can't tell how often this runs" can land on **either** default depending
on which sentence it read last. The distinction the doc *intends* — "unknown
**tier/hotness of the slice** → WARM" vs "known-hot **impl** whose **frequency
multiplier** is unresolved → assume-hot Impact" — is real but **never stated**.
**Fix:** one bridging sentence: *"These are different axes: an unverified-hot
**slice** is tiered WARM (S2); a confirmed-hot **finding** whose cross-slice
**frequency** is unresolved is ranked assume-hot (S4). Don't apply the S4
optimistic-Impact rule to demote/promote a whole slice's tier."*

---

## 2. IS IT NOW TOO HEAVY TO FOLLOW? — yes at the top, and there's no TL;DR.

v2 grew to ~300 lines / 8 phases + S0.5 + a review gate + two big tables + a
when-in-doubt block + a heuristics table. Each absorbed angle added correct
content, but **nobody added a spine**. An agent under context pressure landing on
this file has to read S0→S6 + gate before it knows the shape of the job. The doc
*describes* a lightweight path but **buries it as the second bullet of S0** — the
very agent who needs "just do two slices" has to parse the whole heavy apparatus
to discover it's allowed to skip the heavy apparatus.

**This needs a TL;DR / minimal ordered checklist at the very top** (before S0).
Without it, the likely failure mode is an agent either (a) cargo-culting all 8
phases on a 6k-LOC repo, or (b) getting lost mid-S4 and producing a partial
partition. Draft (put it directly under the "Load this when" block):

> **TL;DR — minimal ordered checklist.**
> 1. **Route (S0).** Named bounded scope → run the cycle directly, stop. ≤2 slices
>    or small → **lightweight path** (S1 + eyeball + run + 3-line ledger, no gate).
>    Else → full method.
> 2. **One program or many? (S0.5).** Monorepo of services → one plan+ledger
>    *per deployable*; shared libs audited once, referenced.
> 3. **Survey (S1).** Production LOC per unit (`tokei`, minus the exclude table),
>    record raw→prod delta.
> 4. **Map (S2).** Classify workload (CPU / IO / event-driven) → run the
>    HOT/WARM/COLD checklist per candidate. "No hot path" is a valid answer.
> 5. **Slice (S3).** Coherent subsystems; SPLIT-IFF/KEEP-IFF; prefer fewer/larger;
>    complete disjoint coverage in a ledger.
> 6. **Calibrate (S4).** ONLY for a hot symbol whose caller is in another slice:
>    ≤1-page frequency map; unknown caller → `assume-hot`.
> 7. **Tier (S5).** HOT→FULL, WARM→REDUCED, COLD→one sweep, cross-slice→OVERLAY.
> 8. **Gate (REVIEW GATE).** Review depth scales with slice count (table). Skip
>    for 1–2 slices.
> 9. **Order + persist + execute (S6).** Hottest first; commit per slice; ledger
>    makes it resumable.

That's the whole method in 9 lines; the sections become the reference an agent
consults *when a step is ambiguous*, not a wall it reads first.

---

## 3. MECHANICAL APPLICABILITY — walking three concrete repos

### Repo A — 40k-LOC FastAPI + Postgres + React app (one deployable).
- **S0:** >8k, >2 languages → full method. OK.
- **S0.5:** one deployable (frontend served as static build off the same app, or a
  separate SPA) — **ambiguity #1:** is a FastAPI backend + a React SPA "one
  program or many?" S0.5's examples are all *backend* multi-service shapes
  (Go services, .csproj, Nx apps). A backend+SPA is a *process boundary* (S3
  principle 1 / OVERLAY territory) but **not** a "monorepo of deployables" in
  S0.5's sense. The agent has to infer that frontend↔backend is handled by S3-p1's
  process-boundary rule, not S0.5's per-service split. **The doc never connects
  S0.5 (deployable-unit split) to S3-p1 (process-boundary split)** — they're the
  same axis at two scales and an agent will wonder which applies. *Cite: S0.5 vs
  S3 principle 1.*
- **S1:** `tokei`, exclude `*.test.*`/`__tests__`/`migrations/`/`node_modules`.
  Fine. The Python `migrations/` exclude is right; the React side is clean.
- **S2:** workload = IO-bound → hot path = N+1/ORM/fan-out. Good, this is exactly
  what round-1's classifier fixed.
- **S3:** here's the **first real stuck point.** A FastAPI endpoint with SQLAlchemy
  ORM + the React component that calls it: S3-p1 says keep SQL with its Python
  driver (in-slice sub-lane), and carve the React out only at the process boundary.
  Fine. But **S4's frequency for the ORM impl is set by the React caller's render
  frequency** — which is now in a *different slice* (across the process boundary).
  S4's recipe (lines 163–185) is written for **in-tree** caller splits and
  **out-of-tree** = request rate / queue / cron. A **frontend render driving
  backend call frequency** is *neither* cleanly — it's an out-of-process in-repo
  caller. **Ambiguity #2:** does the React→API call count as "out-of-tree
  frequency" (read API client) or an in-tree split? The doc's S4 out-of-tree bullet
  says "inter-*service* network calls" — but this is intra-app frontend→backend.
  An agent gets a *workable* answer (treat the React fetch site as the frequency
  driver, read it as a client) but the doc doesn't name this case. *Cite: S4
  out-of-tree bullet — it covers service↔service but not SPA↔backend.*

### Repo B — Go monorepo, 6 services + shared `pkg/`.
- **S0.5:** clean win — 6 per-service plans + shared `pkg/` audited once. This is
  exactly what round-2 built. Good.
- **Stuck point — the roll-up (S6 line 236).** "Repo-level roll-up … conditionally
  REQUIRED when the request was a posture question." But S0.5 just told the agent
  the **audit unit is the service, not the repo**, and to keep **per-service
  ledgers**. So for "how's the repo's performance?" (a posture question over 6
  services) the agent now needs a **cross-service** roll-up that spans 6
  independent plans — but S6's roll-up text is written as a *within-one-partition*
  synthesis ("after the slices, a short cross-slice synthesis"). **There is no
  defined repo-level roll-up that aggregates the six per-service roll-ups.** Does
  each service get its own roll-up, then a meta-roll-up? The doc doesn't say. *Cite:
  S0.5 (per-service ledgers) vs S6 roll-up (single-partition framing) — they don't
  compose.* This is a genuine **interaction defect** (see §4d).
- **Stuck point — shared `pkg/` frequency.** S0.5 says shared libs are "referenced
  by each service's ledger (marked `shared`)" and "cross-service frequency is set
  over the network (see S4)." But a shared `pkg/util` called *in-process* by 6
  services is the **many-to-one S4 fan-in** case (round 2 edge-case-9a named it as
  "shared substrate, frequency = max/union over callers"). **v2 dropped the
  shared-substrate frequency pattern** — round-2's edge-case-9a fix ("feed it the
  union of callers' frequency classes / tier by hottest caller") is **not in v2's
  S4 or S0.5.** S0.5 handles shared-lib *coverage* (audit once, reference) but not
  shared-lib *frequency calibration*. An agent auditing `pkg/` slice has no rule for
  "whose frequency drives this?" *Cite: S0.5 shared-lib handling has no S4 fan-in
  rule; round-2 9a was not merged.*

### Repo C — 200k-LOC C# enterprise solution, ~30 `.csproj`.
- **S0.5:** multi-`.csproj` .NET solution → per-deployable. But **stuck point:** a
  .NET *solution* is not always N *deployables* — it's often **1–2 deployables
  (one web app + one worker) composed of 30 class-library `.csproj`.** S0.5's
  example "a .NET solution of many `.csproj`" implies `.csproj` ≈ deployable, which
  is **wrong** — most `.csproj` are libraries linked into a couple of entry-point
  projects. An agent reading S0.5 literally will produce **30 "service" partitions**
  for what is really **2 deployables + 28 shared libraries.** *Cite: S0.5 — equates
  `.csproj` with deployable unit; for .NET the deployable is the entry-point
  project (`Web`/`Worker`/`Api`), and the rest are shared libs.* This is a
  **correctness bug in a new recipe** (the production-LOC/ecosystem table is right,
  but the S0.5 deployable-detection example is wrong for .NET).
- **Scale stuck point — the COLD SWEEP (see §4a).** 200k LOC of enterprise C# is
  *mostly* cold glue (DI wiring, DTO mappers, getters — the doc itself says
  "JVM/.NET add a LOT of it"). The method routes all of it to **"one batched
  COLD SWEEP"** (S5 line 198, "over all COLD glue **at once**"). 28 libraries of
  cold C# is **easily 80–120k production LOC** — that single COLD SWEEP **cannot
  be one cycle run**; it's the exact mega-run the method exists to prevent, just
  wearing a COLD label. *Cite: S5 COLD SWEEP — "one batched run … at once" has no
  size cap.*

---

## 4. WHAT THE THREE LANE-BOUND ANGLES ALL MISSED (interaction effects)

### 4a. **[NEW, biggest] The COLD SWEEP can itself exceed one run's capacity.**
None of rounds 1–3 owned this because it's an *interaction* between the
sizing rule (per-slice band) and the tiering rule (COLD→"one batched sweep").
Every other tier (FULL/REDUCED) is **band-sized**; the COLD SWEEP is explicitly
**un-sized** — "one batched run … over **all** COLD glue **at once**" (S5 line
198) and "cold sweep **last**" (S6). On a small repo that's the right economy. On
a 200k-LOC enterprise app, or even a 40k app that's 70% glue, "all cold glue at
once" is **30k–120k LOC in one run** — precisely the mega-run-precision-collapse
the whole method exists to prevent. The COLD SWEEP got a *waste*-economy
("don't run 8 lanes on glue") but **no capacity bound**, so it silently
reintroduces the failure mode at the cold tier. **Fix:** "COLD SWEEP is batched
**up to the band ceiling** — a repo with cold glue exceeding one run's capacity
gets **several** cold sweeps partitioned by build-unit/area, not one. The
economy is *fewer lanes per run*, not *unbounded LOC per run*." This is the
single most important still-open issue.

### 4b. **OVERLAYs have no coverage-ledger or capacity treatment.**
OVERLAY appears in S3 (god-file synthetic-seam family), S5 (cross-slice hot
pipeline), and S6 (run after members). But:
- **Coverage:** S3-p5 demands "every code unit in **exactly one** slice" and a
  reconciled ledger. An OVERLAY **re-covers** code already in member slices (it's
  analysis-*only*, by design). The ledger rule says "exactly one slice" — does an
  OVERLAY violate disjoint coverage, or is it exempt? The doc never says OVERLAYs
  are coverage-exempt. An agent reconciling the ledger will either double-count
  overlay files or be confused. **Fix:** state "OVERLAYs are analysis-only passes,
  **not** coverage units — they don't appear in the disjoint-coverage ledger; their
  members already do."
- **Ledger state:** the progress ledger schema (S6) has `tier` ∈ implied
  {FULL/REDUCED/COLD}; OVERLAY isn't listed as a row type and "analysis-only" runs
  don't produce a `runs.jsonl` regression line the same way. Unspecified.
- **Capacity:** a cross-slice OVERLAY over a hot pipeline spanning 5 slices could
  itself be large. Like the COLD SWEEP (4a), it has no size bound.

### 4c. **Workload-classifier × verbosity-table × verification-mode interaction.**
The IO-bound classifier (S2) says "read the **query**, not just the Python — the
query plan runs in Postgres." The verbosity band (Heuristics) sizes the **Python**
LOC. But for an IO-bound slice, the *audit surface* is the **SQL + the query
plan**, which is **invisible to the LOC count** — a 200-LOC Python ORM layer can be
the hottest slice in the repo with a *tiny* production-LOC footprint. So the
sizing band (which decides slice granularity) and the hot-path map (which decides
tier) **measure different things and can disagree**: the band says "this 200-LOC
slice is trivially small, merge it"; the hot-path map says "this is THE hot slice."
No rule says hot-path character **overrides** band-sizing for merge decisions
(S3-p4 "slice by perf-relevance not raw size" *gestures* at this but the
SPLIT/KEEP rule is band-first). Also interacts with **verification mode**:
IO-bound slices are exactly the ones most often `deferred` (need a load test /
prod-like dataset — round 1 §5), so the hottest slices are the least measurable.
The three reviews each touched one of these (round-1 classifier, round-1 band,
round-1 verification) but **none noticed they collide on the same IO-bound
slice**: hottest + smallest-by-LOC + least-measurable, all at once. **Fix:** a
line in S3 — "perf-relevance **overrides** LOC for merge/split: never merge away a
hot slice because it's small (an IO orchestration layer is small by LOC, large by
Impact)."

### 4d. **Repo-level roll-up doesn't compose with per-service ledgers (S0.5 × S6).**
Covered as a stuck point in §3-Repo-B. Summarized as an interaction: S0.5
*decomposes* the repo into per-service partitions with per-service roll-ups; S6's
roll-up is written as if there's *one* partition to synthesize. For a posture
question over a service monorepo, the needed artifact is a **two-level roll-up**
(per-service, then cross-service) and the doc defines only the inner level.

### 4e. **The lightweight path skips the gate but inherits the S4 trap it can't see.**
S0 lightweight: "no formal review round." S0 also says "Only build a frequency map
(S4) if a hot impl's caller sits in the other slice." Good — but the **partition-
design review's entire reason for existing** (per the REVIEW GATE / round-3) is
that **cross-slice calibration is the defect a non-design pass reliably misses.**
The lightweight path tells the agent to self-check "against the heuristics table"
— but the heuristics table's S4 row ("Impl + caller in different slices →
demand-driven fail-safe S4") is a *pointer*, not a *check the agent can self-run*.
So at exactly the scale where the agent is told to skip the design review, the one
defect the design review exists to catch (S4 cross-slice split) is left to a
self-check that can't really catch it. Round 3 built the lightweight path; round 2
built the S4 fail-safe; **neither noticed the lightweight path's self-check is
blind to the S4 hazard.** Partial mitigation already exists (S0 says build the map
if the split exists) — but the agent has to *detect* the split first, which is the
hard part. **Fix:** the lightweight self-check should include one explicit line:
"confirm no hot symbol's frequency is driven from the other slice; if it is, build
the ≤1-page map even on the lightweight path (S0 already requires this)."

---

## 5. CORRECTNESS OF THE NEW OPERATIONAL RECIPES

### 5a. Production-LOC exclude table (S1, lines 61–68) — mostly right, three gaps.
- **Python:** excludes `migrations/` — correct (Django/Alembic). Good. Missing
  `conftest.py` (round-3 had it) and `__pycache__/`. Minor.
- **JS/TS:** `*.d.ts` gen — but **hand-written `.d.ts`** (ambient declarations) is
  production. "`*.d.ts` gen" is ambiguous; most `.d.ts` in app code are generated,
  but a blanket exclude drops hand-authored ones. Minor. Missing `*.stories.tsx`
  (round-3 had it) and snapshot dirs.
- **C#/.NET:** `*.Tests` (project naming), `obj/`/`bin/`, `*.Designer.cs`, `*.g.cs`
  — correct and good. Missing `*.AssemblyInfo.cs` (generated) and `Migrations/`
  (EF Core migrations, often huge). **EF migrations are the .NET analogue of
  Django migrations and are a real production-LOC inflator** — should be excluded.
- **Go:** correct. **Rust:** correct.
- **Cross-cutting:** the table excludes test/gen/vendored but **not protobuf/gRPC
  `.proto`-derived stubs uniformly** — Go has `*.pb.go`, Python has `*_pb2.py`, but
  C#/Java rows don't list the generated-proto path (`obj/.../G.cs`, generated
  gRPC). Round-1/round-2 both flagged generated-proto as a major inflator; the
  table covers it for 3 of 6 ecosystems.

### 5b. Dead-code / framework-wiring list (S2, lines 96–101) — good, two holes.
The list (dynamic dispatch, trait objects, FFI, plugins, routers, DI containers,
`@Scheduled`/`@EventListener`/Celery/Sidekiq, signals, webhooks, reflection) is
strong and was the round-1 fix. Holes:
- **No `__init__`/module-import side effects** (Python registries that populate on
  import; the `@app.route` decorator only "wires" if the module is imported — import
  graph ≠ call graph).
- **No serverless event bindings** — round-1 explicitly added "event-driven entry
  points live in IaC (`serverless.yml`/SAM)"; v2 has that in S2's workload bullet
  (line 85) but the **dead-code confirm list doesn't cross-ref it**, so an agent
  checking "is this Lambda handler dead?" greps callers and finds none (the caller
  is the cloud event source). Add "cloud event bindings (queue/cron/HTTP triggers in
  IaC)" to the LIVE-uncertain list.

### 5c. **[Important] The S4 "assume hot → Phase-3 demotes" fail-safe — is it actually safe?**
The rationale (lines 186–189): tag `frequency-unresolved — assume hot` at
*optimistic* Impact "so the cycle's Phase-3 cross-validation **demotes** a false
positive." **This depends on a property of the cycle the doc asserts but doesn't
establish: that Phase 3 reliably demotes an over-ranked assume-hot finding.** Two
ways it can fail to be safe:
1. **If Phase 3 only *validates findings it can reach*** and an assume-hot finding's
   frequency is *unresolved precisely because the caller is unaudited/unknown* (the
   exact trigger condition, line 186), then **Phase 3 has the same blind spot** —
   it can't demote what it can't reach either. The fail-safe assumes Phase 3 has
   *more* reachability information than S4 did, but S4 fired *because* that
   information was unavailable. If Phase 3's reachability source is the same call
   graph S4 already failed to resolve, the assume-hot finding **ships over-ranked.**
2. **Optimistic Impact affects ranking/queue order**, so even a later-demoted
   finding may have **already consumed budget** as a top-ranked item, or shipped in
   an interim report before Phase 3 ran. The "Phase 3 will catch it" safety net is
   only as good as Phase 3 running *before* anything acts on the ranking.

I could not verify Phase 3's demotion behavior from this doc alone (it's a property
of `performance-audit-cycle`, not this method). **This is the most load-bearing
unverified assumption in v2** — the entire S4 fail-safe rests on it. **Fix:** either
(a) cite the specific Phase-3 mechanism that demotes (so the claim is grounded), or
(b) soften to "tag `frequency-unresolved`; surface it in the roll-up for the
operator to confirm reachability" rather than asserting auto-demotion. As written
it's an **optimistic claim about a downstream phase the method doesn't control.**
Recommend the round-5 reviewer verify Phase 3 against the actual cycle SKILL.md.

---

## TOP EDITS (ranked)

1. **Reconcile the band: add the `## Sizing` section the two crossrefs (lines 7,
   139) point at**, put the verbosity table there (not just a heuristics row), and
   make S0's `8k`/`≤2-slice` gate + SPLIT-IFF's "the sized band" both cite it.
   Fixes 1a + 1b + 1c in one stroke — the largest pure *merge* defect.
2. **Cap the COLD SWEEP (and OVERLAY) at the band ceiling** (§4a/§4b): "batched up
   to one run's capacity; partition cold glue into several sweeps on huge repos."
   This is the biggest *correctness* hole — the cold tier silently reintroduces the
   mega-run the method exists to prevent. Add the OVERLAY-is-not-a-coverage-unit
   line.
3. **Add the TL;DR 9-line ordered checklist at the top** (§2). The doc is now too
   heavy to follow top-down under context pressure; the spine makes the sections a
   reference rather than a wall, and surfaces the lightweight path before the heavy
   apparatus.
4. **Ground or soften the S4 "Phase-3 demotes" fail-safe** (§5c) — it's the most
   load-bearing unverified claim in v2 and may be unsafe exactly when it fires
   (unresolved-caller = Phase-3-blind too). Either cite the mechanism or
   surface-to-operator instead of asserting auto-demotion.
5. **Fix the S0.5 × S6 × shared-lib interactions** (§3, §4d): (a) `.csproj` ≠
   deployable for .NET — deployable = entry-point project, rest are shared libs;
   (b) define a two-level (per-service then cross-service) roll-up; (c) re-merge
   round-2's shared-substrate **frequency** pattern (tier by hottest caller / union
   of caller frequency classes) — v2 kept its coverage half but dropped its
   calibration half. Plus the small recipe gaps (§5a EF `Migrations/`, generated
   proto for C#/Java; §5b cloud-event bindings in the dead-code list; §1e + §4c
   one-line reconciling sentences).
