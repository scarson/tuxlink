# Method Review (generalizability)

**Reviewer:** glade-knoll-shoal
**Angle:** Generalizability across ecosystems — does the whole-repo-scoping method, distilled from one ~96k-LOC Rust+TS desktop modem app, survive transplant onto Python web apps, Go service monorepos, large JS/TS monorepos, Java/Kotlin Spring, .NET, C/C++, Ruby/Rails, and data/ML repos?
**Verdict:** **Needs-edits — Rust/desktop-app-biased in three load-bearing places.** The *skeleton* (survey → hot-path map → slice → cross-slice frequency calibration → depth tiers → review gate) is genuinely ecosystem-agnostic and is the method's durable contribution. But three concrete heuristics leak the provenance and will mis-fire on whole classes of repo: (1) the raw LOC sweet-spot band, (2) the unqualified "language-homogeneous slice" rule, and (3) a hot-path model that silently assumes CPU-bound, single-process, single-binary, real-time execution. Each is fixable with a bounded edit. The document even says "tune per ecosystem" on the sizing header — but it tunes nothing, names no axis to tune along, and the rest of the doc contradicts the disclaimer by stating the bands as bare numbers.

---

## What generalizes well (keep, do not touch)

The phase structure is the asset and it transfers cleanly:

- **S1 build-units from manifests** already lists `go.mod`, `pyproject.toml`, `*.csproj`, `package.json` — ecosystem-aware by construction. Good.
- **S1 production-LOC-not-raw-LOC** with a non-uniform multiplier is *more* important in dynamic ecosystems, not less (Rails/Django have enormous test suites; notebooks inflate raw LOC with output cells). The principle generalizes; only the *target number* it feeds is biased (see Failure 1).
- **S4 cross-slice frequency calibration** is the method's best idea and is fully ecosystem-neutral — "impl and hot caller in different slices" happens in every language. In a Go monorepo it's *the* dominant case (a util package's frequency is set by N services), so S4 actually earns its keep harder outside Rust.
- **S5 verification-mode tagging (hardware-deferred)** generalizes to "load-deferred / prod-traffic-deferred" with trivial relabeling.
- **REVIEW GATE** — partition-design review is language-independent.

So the critique below is surgical, not structural. The bones are good.

---

## Failure 1 — The ~1–4k production-LOC band and "~100k → ~10–20 units" are Rust-biased and must scale by build-unit, not raw LOC

**The leak.** Both numbers are *information-density* constants disguised as universal constants. A slice should be sized to "as much coherent logic as one set of lanes can hold in working memory and reason about precisely." That budget is roughly constant in *semantic units* (functions, call edges, branches), **not in lines**. Lines-per-semantic-unit varies enormously by ecosystem:

- **Rust** (the provenance): verbose — explicit types, `match` arms, error enums, lifetimes, `impl` blocks. ~1–4k lines is maybe 1–3 coherent subsystems' worth of *logic*.
- **Python / Ruby**: 2–4× denser. 1–4k lines of Django/Rails is *several* subsystems — a 4k-line band over-stuffs a Python slice and the lanes lose precision exactly the way a mega-run does. A Rails app's whole `app/models` can be <4k lines and is clearly not one slice.
- **Go**: middling density but the verbosity is in `if err != nil` boilerplate that is *cold*; production-LOC-after-error-glue is what matters, and the band over-counts Go.
- **Java/Kotlin Spring**: huge lines-to-logic ratio (annotations, DI ceremony, getters, builder boilerplate). A 4k-line Spring slice may be 80% cold glue — the band *under*-stuffs it with real logic and wastes a FULL cycle on annotations.
- **JS/TS monorepo**: bimodal — hand-written app code is dense; **generated code (protobuf stubs, GraphQL codegen, OpenAPI clients, `.d.ts`) is enormous and near-zero-logic**. A 4k "slice" that's 3.5k generated is a non-audit. S1 says exclude generated code, good — but the band gives no signal that in JS/TS the *generated fraction can dominate a package* and must be excluded before the band even applies.
- **C/C++**: lines mislead in both directions — headers duplicate declarations, macros expand, templates instantiate. The natural unit is the **translation unit / build target**, not the line.

**The "100k → 10–20 units" rule** is the same constant restated (100k/15 ≈ 6.6k raw ≈ ~3k production per unit — exactly the band's midpoint). It inherits the same bias and additionally assumes the repo *is one audit*. For a **Go microservice monorepo or a JS/TS package monorepo, "the repo" is not one audit at all** — the natural top-level partition is **per service / per package**, and the per-service count drives the unit total, not a global LOC division. The method never says this. An operator pointing it at a 40-service Go monorepo would try to globally LOC-slice across service boundaries and produce slices that straddle deploy units — incoherent for perf purposes (different services have different load, different hot paths, different runtime).

**Concrete fix.**
1. Reframe the band as a **derived budget, not a constant**: "Size a slice to ~one working-memory budget of *logic* — empirically ~1–4k production LOC **in a verbose compiled language (Rust/C++/Java-minus-glue)**. Scale the line target by ecosystem density: **halve it for Python/Ruby/dense TS (~0.7–2k); raise the cold-glue exclusion aggressively for Java/Spring and Go before measuring.** When in doubt, size by **count of non-trivial functions/handlers (~15–40 per slice)** rather than lines."
2. Add a **build-unit-first rule for service/package monorepos**: "If the repo is a set of independently deployed services or published packages (Go services, JS/TS workspaces, .NET solutions with many projects), the **first partition is per service/package** — each service is its own whole-repo-scoping problem with its own load profile. Do NOT LOC-slice across a deploy boundary; load, hot paths, and frequency drivers do not cross it coherently."
3. Add a generated-code caveat to the band itself, not just S1: "In codegen-heavy ecosystems (protobuf/gRPC, GraphQL, OpenAPI, ORM scaffolds, `.d.ts`) the generated fraction can exceed the hand-written fraction of a single package — exclude it *before* applying the band, or the band sizes a non-audit."

---

## Failure 2 — "Language/ecosystem-homogeneous slice" fragments coherent polyglot features and needs a same-runtime carve-out

**The leak.** S3 principle 1 ("Never mix Rust and TS in one slice") is correct *for the case it came from* — a Rust backend and a TS frontend are genuinely different lanes/profile-packs/idiom-indexes, and they're also different processes. But stated as an absolute it mis-cuts the most common server shape: **a single-runtime service whose hot path threads through multiple languages.**

Counter-examples the rule fragments wrongly:
- **Python/Django service with embedded raw SQL** (or an ORM emitting SQL). The N+1 query, the missing index, the `SELECT *` over a wide table — these are the *dominant* perf findings, and they live at the **Python↔SQL seam**. Splitting "Python" and "SQL" into different slices puts the loop (Python) in one slice and the cost (SQL) in another — that's precisely the S4 frequency-split failure, manufactured by the homogeneity rule itself. The query and its caller are one coherent perf unit.
- **JS/TS service with inline GraphQL/SQL template strings**, or a React component with a hot `useMemo` over data shaped by a colocated GraphQL query.
- **Rails with ActiveRecord + SQL + ERB views**, **Java with JPQL/HQL in annotations**, **C with inline asm**, **data/ML pipelines mixing Python orchestration + SQL + a vectorized kernel (NumPy/C)**.

In all of these, "one language per slice" **fragments a coherent feature along a seam that is itself the performance hotspot.** The Rust+TS case is special because there the language boundary *coincides with a process boundary and a lane boundary*. The real principle is not "one language" — it's **"one lane-set + one runtime."**

**Concrete fix.** Replace principle 1 with: "**Slice along lane-set + runtime boundaries, which usually but not always coincide with language boundaries.** Keep a host language and its *embedded* sub-languages (SQL in an ORM/raw query, GraphQL/template strings, inline asm/kernels) **in the same slice** when they execute in one runtime and form one data-flow — the host↔SQL seam is frequently *the* hotspot and splitting it manufactures an S4 frequency split. Split when the boundary is also a **process/deploy/runtime boundary** (Rust backend vs TS frontend, service-to-service, app vs DB *engine*) — there the lanes, profile packs, and idiom indexes genuinely differ. Heuristic: split if crossing the boundary means a different *process*; keep together if it's a different *language in the same process*."

---

## Failure 3 — The hot-path / reachability model (S2) silently assumes CPU-bound, single-process, real-time execution

**The leak.** S2's vocabulary — "inner loops," "real-time callbacks," "per-frame," "allocation/IO on those paths," "canvas/`requestAnimationFrame`" — is a *desktop real-time app's* mental model of "hot." Its examples of hotness are CPU loops and render frames. For the dominant server/cloud shapes, "hot" means something different, and an operator running S2 by its examples would **build an imaginary hot-path map** the way the doc warns against:

- **IO-bound web services (Django/FastAPI/Rails/Spring/Express).** The cost is **not** CPU loops — it's the **N+1 query, the synchronous external HTTP call in a request handler, the un-cached serialization, the connection-pool/GIL/thread-pool contention, the chatty ORM.** "Inner loop" hotness is usually a red herring here; a tight Python loop is rarely the bottleneck next to one blocking DB round-trip. S2 names IO "on those paths" but subordinates it to loops; for services the ranking should invert.
- **Event-driven / async / serverless.** "Hot path" is **per-event / per-invocation**, and a serverless function adds **cold-start, package size, and per-invocation init** as first-class perf axes that the desktop model has no slot for. The frequency unit is "per request × QPS," set by infra/traffic config that **isn't in the repo at all** — so S2's "verify against code, never infer from names" needs a companion: "frequency often lives in deploy/infra config (autoscaling, queue concurrency, cron schedules), not the code — read it too."
- **Dynamic dispatch breaks the call-graph/reachability premise.** S2 and S4 both lean on "call-graph sketches" and "no in-tree callers ⇒ latent/dead." In **Python/Ruby/JS** (duck typing, monkey-patching, `getattr`, decorators, dependency-injection, reflection, dynamic `import`), and in **Spring/.NET DI** (wiring by annotation/config, not by static call edge), **"no in-tree caller" does NOT imply dead code** — the caller is the framework, resolved at runtime. The Rust LDPC-crate example ("zero callers ⇒ reachability ≈ 0") is *only sound in a statically-dispatched language*. Applied to a Django view, a Celery task, a Spring `@Component`, or a JS route module, it would mis-rank live, hot, framework-invoked code as dead. This is a correctness bug in the method for dynamic/framework ecosystems, not just a style nit.
- **Data/ML repos.** "Hot path" is a **pipeline DAG stage / a vectorized kernel / a data-volume-driven step**, and notebooks have *no_ stable entry points or call graph at all. S2 has no model for "cost scales with data volume, not call frequency."

**Concrete fix.** Add an ecosystem dimension to S2:
1. **Generalize the "hot" definition explicitly:** "**'Hot' is workload-relative.** CPU-bound/real-time: inner loops, per-frame/per-message callbacks, allocation. **IO-bound services: per-request DB queries (N+1), blocking external calls, serialization, pool/GIL/thread contention — rank these above CPU loops by default.** Event-driven/serverless: per-event cost **plus** cold-start/init/package-size. Data/ML: per-data-volume pipeline stages and vectorized kernels. Identify which regime the slice is in *before* mapping, and use that regime's cost vocabulary."
2. **Add a dynamic-dispatch caveat to the latent/dead-code rule:** "**'No in-tree caller ⇒ dead' is sound ONLY under static dispatch.** In dynamic languages (Python/Ruby/JS reflection, monkey-patching, decorators) and DI/annotation frameworks (Spring, .NET, Django/Celery/FastAPI routing), the caller is the **framework or runtime**, resolved dynamically — absence of a static call edge does NOT mean dead. Before tiering something latent, check for framework registration (route tables, `@Component`/`@app.task`/decorator, DI config, entrypoint manifests, dynamic-dispatch sites)."
3. **Frequency-lives-outside-the-code note for S2/S4:** "In services/serverless, the frequency multiplier (QPS, queue concurrency, autoscaling, cron cadence) often lives in **deploy/infra config, not source** — read it as adjacent context; don't infer frequency from code alone."

---

## Secondary leaks (caveat-level, not structural)

- **"External-process boundaries ⇒ reduced tier" needs widening to be useful for services.** The rule's example is a TNC/DSP child process — but for *every* web service the most important "external process" is **the database engine and downstream services.** The method should say plainly: "For typical services this boundary is the **DB and downstream RPCs** — the audited code is query *construction* and IO orchestration; the compute is in the engine. This is the **common** case, not an exotic one — but **'reduced tier' does not mean low-impact**: the N+1 *pattern* in the orchestration code is often the single highest-impact finding even though the cycles are spent elsewhere." As written, "reduced tier" risks down-ranking the exact code where service perf is won.
- **"Single binary" assumption in S6 commit/ledger discipline** is fine, but the **OVERLAY** concept ("a hot pipeline spans several slices") should explicitly cover **cross-service request paths** (a request fanning through 5 microservices) — the highest-value overlay in a service monorepo, and one the desktop framing wouldn't surface.
- **Idiom-currency / payload-startup lanes**: "payload/startup" maps beautifully to serverless cold-start and SPA bundle size — the method should *say* so (it's a strength left implicit), and note that for backend services "startup" is usually irrelevant while "payload" becomes "response/serialization size."
- **C/C++ build-unit**: S1 lists manifests but C/C++ has none of that flavor — add "for C/C++, the build unit is the **translation unit / CMake or Bazel target**, and headers are declaration-shared across units (don't double-count a header's LOC into every includer)."
- **Notebooks/data-ML**: explicitly list as a recognized shape with its own caveats (no call graph, output-cell LOC inflation, data-volume-driven cost) or scope them out by name — right now they fall through every assumption silently.

---

## Do the sizing bands/heuristics need ecosystem scaling? — Yes, unambiguously.

The header literally says "tune per ecosystem" but provides **no tuning axis and no per-ecosystem numbers**, and the body states the bands as bare constants — so in practice an operator will use 1–4k everywhere. That's a documentation defect: the disclaimer is non-actionable. The fix is a small **density/regime table** so the tuning is concrete rather than aspirational:

| Ecosystem | Slice band (production LOC) | First partition | "Hot" regime | Dead-code-by-no-caller? |
|---|---|---|---|---|
| Rust / C / C++ | ~1–4k | by crate/module/TU | CPU/real-time | Yes (static) |
| Java/Kotlin Spring, C#/.NET | ~1–4k **after stripping DI/annotation glue** | by service/project | IO-bound + DI | **No** — framework-wired |
| Python (Django/FastAPI), Ruby/Rails | **~0.7–2k** (denser) | by app/service | IO-bound (N+1, blocking IO) | **No** — dynamic/decorators |
| JS/TS monorepo | ~1–3k **after excluding codegen** | **per package/workspace** | IO + bundle/cold-start | **No** — dynamic/routing |
| Go services | ~1–3k **after err-glue** | **per service** | IO + concurrency | Mostly (but check DI/registry) |
| Data/ML / notebooks | size by **pipeline stage / kernel**, not LOC | per pipeline/DAG | data-volume-driven | N/A (no call graph) |

---

## Top edits to make it ecosystem-agnostic (priority order)

1. **Add the density/regime table above** (and reframe the band as a *derived working-memory budget*, scaled by language density, with a function-count fallback ~15–40 non-trivial functions/handlers per slice). Fixes Failure 1's core. This is the single highest-leverage edit.
2. **Add a build-unit-first / per-service-or-package partition rule** for service & package monorepos (Go, JS/TS, .NET): the deploy/publish boundary is the *first* cut; never LOC-slice across it. Fixes Failure 1's "is the repo one audit?" gap.
3. **Rewrite S3 principle 1 from "language-homogeneous" to "lane-set + runtime-homogeneous"**, with an explicit *keep embedded SQL/GraphQL/asm with its host* carve-out and a *split on process/deploy boundary* rule. Fixes Failure 2.
4. **Add the dynamic-dispatch caveat to the latent/dead-code rule** ("no static caller ⇒ dead" is static-dispatch-only; check framework registration/DI/decorators/routing first). This is the method's one outright *correctness* bug outside Rust — highest-severity fix even if lower leverage than #1.
5. **Generalize S2's definition of "hot"** into named regimes (CPU/real-time · IO-bound service · event-driven/serverless · data-volume), invert the loops-over-IO ranking for services, widen "external process ⇒ reduced tier" to "DB + downstream RPC = the common case, reduced-tier ≠ low-impact," and note that frequency often lives in deploy/infra config, not source.

Bundling #1+#2 into the sizing section, #3 into S3, #4+#5 into S2, plus the secondary-leak caveats, makes the method ecosystem-agnostic without disturbing the (excellent) phase skeleton or the S4 calibration insight that is its real contribution.
