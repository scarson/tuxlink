# Method Review (generalizability)

**Reviewer:** glade-knoll-shoal
**Angle (single):** Generalizability across ecosystems. The `whole-repo-scoping.md`
method was distilled from ONE artifact — a ~96k-LOC Rust+TypeScript desktop modem
app, hardened over five adversarial rounds that were all run against *that* repo.
This review pressure-tests it against ecosystems it was NOT distilled from:
Python web (Django/FastAPI), Go service monorepos, large polyglot JS/TS monorepos,
Java/Kotlin Spring, .NET, C/C++, Ruby/Rails, and data/ML repos.
**Scope discipline:** I attack generality ONLY. I do not re-litigate hot-path
accuracy, coverage-ledger soundness, or the review-gate design except where they
fail *because of* an ecosystem assumption.

**Verdict: NEEDS-EDITS — Rust/desktop-app-biased in four load-bearing places.**
The *skeleton* generalizes well and is the method's durable contribution:
survey → cheap hot-path/reachability map → slice into coherent bounded units →
cross-slice frequency calibration → depth tiers → adversarial review-gate-before-spend.
That control flow is ecosystem-agnostic and correct. But four concrete heuristics
leak the single-Rust-app provenance and will mis-fire on whole classes of repo:

1. The **raw-`production-LOC` sweet-spot band (~1–4k) and the "~100k → ~10–20 units"
   rule** are stated as bare numbers calibrated to one language pair. They do not
   scale by language verbosity or by build-unit, and the "tune per ecosystem"
   header names no axis to tune along.
2. The **"language-homogeneous slice, never mix"** rule is stated as an absolute
   and actively fragments coherent features in polyglot services — and it does so
   *because the audit tooling has a separate SQL pack and separate frontend/backend
   packs*, i.e. a tooling constraint is being sold as a partitioning principle.
3. The **hot-path / reachability model (S2)** silently assumes CPU-bound,
   single-process, single-binary, real-time execution. For IO-bound web services,
   event-driven/serverless code, and dynamic-dispatch languages it mislabels the
   real cost (which is IO/N+1/fan-out, not "inner loops") and makes the call-graph
   sketch unreliable.
4. The **unit-of-audit assumption** — "the repo" is one partition — has no answer
   for service monorepos (Go/Java/Node services, multi-`.csproj` solutions) where
   the natural audit boundary is the *deployable service*, not the repo, and where
   cross-service frequency is set over the *network*, not an in-tree call graph.

Each is fixable with a bounded edit. Concrete wording below. Nothing here requires
re-deriving the method — these are caveats and one new sizing axis.

---

## Where it generalizes WELL (give credit, so the edits stay surgical)

- **S1 survey on production LOC, excluding tests/generated/vendored.** Universally
  correct. The non-uniform-ratio warning is *more* true elsewhere, not less
  (Python/Ruby have low ratios; Go/Java have high test+generated ratios).
- **S4 cross-slice frequency calibration.** The single best idea in the document and
  it is fully general — arguably *the* reason whole-repo audits go wrong in every
  ecosystem. Keep verbatim. (One gap: it assumes the caller is *in-tree*; see §4.)
- **The review-gate-before-spend** and the "≥1 reviewer whose explicit lens is
  partition design" insight. Ecosystem-neutral; keep.
- **Depth tiers (FULL/REDUCED/COLD SWEEP/OVERLAY)** as a *concept*. The *batching*
  intuition is general; only the worked thresholds and the "cold glue" taxonomy are
  Rust/desktop-flavored (see §5).
- **Latent/dead-code reachability≈0 flag** generalizes, but its *detection* doesn't
  in dynamic ecosystems (see §3) — "no in-tree callers" is a static-language test.

---

## §1 — The sizing bands are Rust-calibrated and don't scale by language

### The defect
`~1–4k production LOC` (S0 trigger, S3 principle 3, Sizing header) and
`~100k → ~10–20 units` are stated as bare numbers. They were measured against
Rust+TS. The header says "(tune per ecosystem)" but then **tunes nothing and names
no axis**, and S0/S3 state the numbers without the caveat — so in practice an agent
slicing a Django repo will use 1–4k LOC directly.

Why this is biased: **LOC is a proxy for "how much *semantic surface* one cycle's
lanes can hold with precision," and LOC-per-unit-of-semantics varies ~3–5× across
languages.**
- A coherent feature is ~2–4× *fewer lines* in Python/Ruby than in Rust/Go/Java
  (no type boilerplate, no error-enum plumbing, comprehensions). A 1–4k-LOC Python
  slice may be 2–3 features fused — too coarse; lanes lose precision exactly the way
  a mega-run does.
- Conversely Java/Kotlin Spring and Go are *more* verbose than Rust (getters/setters,
  DI annotations, `if err != nil`, interface ceremony). A single Spring service of
  4k LOC may be *one* controller+service+repository triplet — finer than the band
  implies, so you'd over-fragment.
- C/C++ headers, generated protobuf/gRPC stubs, and JS/TS codegen blow raw LOC up
  with near-zero audit value — already covered by "exclude generated," but the *band
  itself* still assumes hand-written density.

### Concrete fix
Replace the bare band with a **two-axis sizing rule** and a per-ecosystem multiplier
table. Suggested wording for the Sizing section:

> **Size by audit-surface, not raw LOC.** The 1–4k figure is calibrated to
> Rust/TS density. Convert via a verbosity factor, or — preferred — size by
> **build-unit + semantic cohesion**: one coherent subsystem that fits in a single
> reviewer's head, typically **one package/crate, one Go service, one Spring
> service-triplet, one Django app, or one bounded module-cluster**. As a LOC sanity
> check, scale the band:
>
> | Ecosystem | Sweet-spot (production LOC) |
> |---|---|
> | Python / Ruby | ~0.5–2k (denser per feature) |
> | Rust / TS | ~1–4k (baseline) |
> | Go / Java / Kotlin / C# | ~2–6k (more ceremony per feature) |
> | C/C++ | by translation unit + its headers, not LOC |
>
> The `~100k → ~10–20 units` rule is a Rust/TS datapoint; expect **more** units in
> dense ecosystems and **fewer** in verbose ones for the same feature count. Count
> *features/services*, not lines, when estimating unit count.

This keeps the band as a *sanity check* while making "coherent build-unit" the
primary sizer — which is what S3 principle 2 already wants but principle 3 overrides
with a raw number.

---

## §2 — "Language-homogeneous, never mix" fragments polyglot features

### The defect
S3 principle 1 ("Never mix Rust and TS in one slice") and the §"Heuristics" row are
stated as an absolute. The stated *reason* is tooling: "the lanes, profile packs,
and idiom-currency index differ." That is true — I confirmed the sibling skill ships
**separate packs** for `python`, `go`, `dotnet`, `jvm`, `js-ts`, `rust`, **`sql`**,
and `html`. But selling a *tooling constraint* as a *partitioning principle* breaks
coherent-feature auditing in the common polyglot shapes:

- **A FastAPI/Django endpoint with embedded/ORM SQL.** The N+1 query, the missing
  index, the serialization cost, and the Python loop that drives them are *one
  performance story*. Forcing the SQL into its own slice (because there's a `sql`
  pack) and the Python into another **splits the impl from its own frequency
  driver** — the exact failure S4 warns about, now *induced by S3*. The two
  principles fight each other.
- **An RPC handler (Go/Java) whose hot cost is the serialization + DB round-trip.**
  Same fracture.
- **A JS/TS frontend feature calling a TS/JS backend** in a monorepo: arguably one
  product feature, two language slices — defensible, but the method gives no rule
  for when to keep a frontend/backend pair together as an OVERLAY vs split.

This is the place the Rust-desktop provenance hurts most: in that app, Rust↔TS was a
*process boundary* (TS UI ↔ Rust core over IPC), so "never mix" happened to coincide
with a real seam. In a Python-or-Java web service, the languages are **interleaved
within one call stack**, not across a process boundary — so the same rule cuts
through the middle of a feature.

### Concrete fix
Reframe principle 1 from "never mix" to "**one primary language per slice, with
embedded second languages kept as in-slice context**," and add an explicit polyglot
rule:

> **1. One primary ecosystem per slice — but keep embedded languages with their
> driver.** A slice has ONE primary pack (its lanes/idiom-index). Embedded second
> languages that are *driven by* the primary code — SQL in an ORM/query layer,
> a shader, an inline regex/template — stay in the slice as **adjacent context**;
> run the SQL pack as a sub-lane rather than carving a separate SQL slice that
> would be split from its caller (this is an S4 impl/caller split you induced —
> don't). Carve a separate-language slice only at a **real process/deploy boundary**
> (UI↔backend IPC, service↔service, app↔external engine). For a polyglot *feature*
> that genuinely spans a process boundary, prefer an **OVERLAY** to recover the
> end-to-end cost rather than pretending the per-language slices capture it.

This resolves the S3-vs-S4 contradiction and matches how the tooling actually
works (SQL is a sub-pack you can apply within a Python/Java slice).

---

## §3 — S2 hot-path model assumes CPU-bound, in-process, statically-dispatched code

### The defect
S2's signals are: "entry points (request/render/frame/message handlers, inner loops,
real-time callbacks), allocation/IO on those paths, and **call-graph sketches**,"
plus "VERIFY hot-path against code (grep for the loop/canvas/RAF/query)." This is a
**compiled-real-time-app worldview**. Three ways it fails to generalize:

**(a) "Hot path" ≠ CPU loop for IO-bound services.** In Django/FastAPI/Rails/Spring
and most Go/Node services, the dominant cost is **IO: DB round-trips, N+1 queries,
cache misses, external HTTP fan-out, serialization** — not inner loops. An agent
grepping "for the loop/canvas/RAF" will *miss* the real hot path, which is a
one-line ORM access inside a request handler that fans out to N queries. The
`requestAnimationFrame`/canvas examples are pure desktop-render provenance and are
noise in a web/service audit.

**(b) Call-graph sketches are unreliable in dynamic / DI / event-driven code.**
S2 and the latent-code test both lean on "in-tree callers" / call-graph reachability:
- **Python/Ruby dynamic dispatch**, duck typing, decorators, signals
  (Django signals, Rails callbacks), and string-keyed dispatch make static
  call-graph sketches *systematically incomplete*. "No in-tree callers" is NOT a
  reliable dead-code signal — the caller may be a framework registry, a URL router,
  a Celery/Sidekiq task name, a webhook, or reflection.
- **Spring/.NET DI**: the "caller" is the container; annotations (`@Scheduled`,
  `@EventListener`, `@KafkaListener`) wire entry points invisibly to grep.
- **Serverless/event-driven**: entry points are *event bindings* (queue messages,
  cron, HTTP triggers) declared in config/IaC (`serverless.yml`, SAM, function
  manifests), not in code call-graphs. Frequency is set by the event source.

**(c) Frequency/reachability is often set OUTSIDE the code** — by request rate,
queue depth, cron schedule, fan-out factor — not by an in-tree loop count. S4 handles
*in-tree* impl/caller splits; it has no concept of an *out-of-tree* frequency driver
(traffic, schedule, queue).

### Concrete fix
Add an ecosystem-classification bullet to S2 and broaden the hot-path taxonomy:

> **Classify the workload shape first (it changes what "hot" means):**
> - **CPU-bound / real-time** (desktop, games, codecs, data kernels): hot path =
>   inner loops, allocation, frame/callback handlers. (The original signals apply.)
> - **IO-bound services** (web, RPC, most microservices): hot path = **DB round-trips,
>   N+1 / unbatched queries, cache misses, external-call fan-out, serialization**,
>   sized by request/throughput rate — NOT inner loops. Grep for ORM access in
>   handlers, query-in-loop, `await` fan-out, missing batching — not for `for`/RAF.
> - **Event-driven / serverless**: entry points live in **config/IaC** (queue/cron/
>   HTTP bindings), not the call graph. Read the manifest to find entry points and
>   their frequency (queue rate, cron cadence).
>
> **Dynamic-dispatch caveat:** in Python/Ruby/JS and DI frameworks (Spring/.NET),
> static call-graph sketches are *incomplete* — framework registries, decorators,
> signals, annotations, and routers wire callers invisibly. **Do not treat "no
> in-tree caller" as dead code** without checking framework wiring (routers, DI
> config, task registries, event bindings). Reachability here is a framework-config
> question, not a grep.

And extend S4 with an **out-of-tree frequency** note:

> Frequency may be set *outside the codebase entirely* — request rate, queue depth,
> cron cadence, fan-out factor. Capture these in the frequency-map pre-artifact as
> first-class inputs (from load context / IaC), not just in-tree call counts.

---

## §4 — "The repo" is the wrong audit unit for service monorepos

### The defect
The method implicitly treats **one repo = one partition = one coverage ledger**.
S1 enumerates "packages/crates/modules/apps," and the worked example is a single
deployable app. It has no first-class concept of **the deployable service** as the
audit boundary, which is the natural unit for:
- A **Go monorepo of N microservices** (`cmd/svc-a`, `cmd/svc-b`, shared `pkg/`).
- A **Java/Kotlin multi-module Gradle** build or a **.NET solution of many `.csproj`**.
- An **Nx/Turborepo JS/TS monorepo** with dozens of publishable packages + apps.

For these, the right structure is **per-service partitions that each get their own
slice plan + ledger + run history**, with shared libraries (`pkg/`, shared packages)
audited *once* and referenced — not re-sliced per consumer (double-counting) and not
dropped (coverage gap). And cross-*service* frequency is set over the **network**
(service A calls service B's endpoint M times/request), which the in-tree-only S4
cannot see.

### Concrete fix
Add an S1 sub-step and an S4 extension:

> **S1 — pick the partition root.** For a service monorepo (multiple deployables in
> one repo), the audit unit is the **deployable service**, not the repo. Produce a
> service inventory first; run a *separate* slice plan + coverage ledger + run
> history per service. **Shared libraries** consumed by multiple services are
> audited ONCE as their own slice and *referenced* by each service plan (record in
> the coverage ledger as shared, so it's neither re-sliced per consumer nor dropped).
>
> **S4 — cross-service frequency** is set over the network: service A may call
> service B's endpoint N×/request. Capture inter-service call frequency in the
> frequency-map (from API contracts, client code, or tracing) the same way you
> capture in-tree caller frequency.

---

## §5 — Smaller provenance leaks (caveat-level, not structural)

- **"Cold glue" taxonomy** ("CRUD, IPC marshalling, config, string assembly, form
  rendering") is desktop-flavored. In Spring/.NET the equivalent cold bulk is
  **DI wiring, annotation glue, DTO mapping, boilerplate getters** — and there is a
  LOT of it, so the COLD SWEEP is *more* valuable there, but the examples don't name
  it. Add JVM/.NET examples to the cold-glue list so the agent recognizes it.
- **"External-process boundary → reduced tier"** generalizes beautifully (it's the
  DB/cache/queue/external-API case for web services — the audited code is
  orchestration, the compute is in Postgres/Redis). But the *example* (modem DSP →
  external TNC) is opaque to a web auditor. Add the web example explicitly:
  "an ORM call is orchestration; the query plan executes in the DB — reduced tier on
  the app code, and read the query, not just the Python."
- **Verification mode / hardware-deferred** (S5): generalizes well but the framing is
  radio-specific. The web analogue is "needs a load test / a production-like dataset /
  a staging service that doesn't exist locally." Add it so the agent doesn't think
  hardware-deferred only means physical devices.
- **Data/ML repos** (notebooks, pipelines) are unaddressed entirely. Notebooks aren't
  "production LOC" in the usual sense; the hot path is often a single pandas/Spark
  op or a data-loader, and "build units" are DAG stages, not packages. At minimum add
  a one-line caveat that notebooks/pipelines need a DAG-stage partition, not a
  package partition, and that cell-level execution order replaces the call graph.

---

## Does the sizing band / heuristic set need ecosystem scaling? — YES.

Concretely: the LOC band needs a **verbosity axis** (§1 table), the homogeneity rule
needs a **process-boundary qualifier** (§2), and the hot-path map needs a
**workload-shape classifier** (§3). Without these three, an agent applying the doc
verbatim to a Django or Spring repo will (a) make over-coarse slices, (b) split SQL
from its driver, and (c) hunt for CPU loops while the real cost is an N+1 query —
three independent ways to produce a confidently-wrong partition.

## Top edits to make it ecosystem-agnostic (priority order)

1. **§3 — add a workload-shape classifier to S2** (CPU-bound vs IO-bound vs
   event-driven) and the dynamic-dispatch / "no-in-tree-caller-isn't-dead-code"
   caveat. *Highest impact:* without it the hot-path map is wrong for the entire
   web/service class — which is most repos this will ever touch.
2. **§2 — reframe "language-homogeneous, never mix" → "one primary pack, embedded
   languages stay with their driver; split only at process/deploy boundaries."**
   Resolves the live S3-vs-S4 contradiction; matches the SQL-sub-pack reality.
3. **§1 — replace the bare 1–4k band with build-unit-primary sizing + a verbosity
   multiplier table**, and demote the LOC band to a sanity check. Make "coherent
   build-unit/service" the primary sizer (S3-principle-2 already wants this).
4. **§4 — make the deployable service (not the repo) the partition root for service
   monorepos**, with shared-lib-audited-once + cross-service (network) frequency in S4.
5. **§5 — de-provenance the examples:** add web/JVM/.NET analogues for cold glue,
   external-process boundary, and verification mode; add a one-line data/ML caveat.

**Net:** the method is structurally sound and worth generalizing — the skeleton is
its real contribution. But as written it is a *Rust-desktop instantiation* wearing a
"tune per ecosystem" disclaimer it doesn't honor. The five edits above convert the
disclaimer into actual ecosystem tuning without touching the (excellent) control flow.
