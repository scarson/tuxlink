# Adversarial Review (Round 4) — Performance-Audit Scope Partition v4

**Reviewer:** Opus subagent (agent `glade-knoll-shoal`), independent round 4 of ≥5.
**Target:** `dev/perf-audit-scope-plan.md` v4.
**Mandate:** attack the *methodology* of the partition (cross-slice calibration,
reduced-tier right-sizing, shared abstractions, execution order, verification
feasibility, multi-run-vs-fewer-larger) — NOT re-hunt hot paths. Rounds 1–3
converged the hot-path map; they did not examine the partition design itself.

---

## Verdict: **minor-edits** (one structural defect demands a real fix)

The partition is coverage-airtight and the tiering is mostly defensible. But the
methodology has **one genuine structural flaw that prior rounds missed**: the
top-level orchestrator that *establishes the frequency of nearly every winlink
hot path* — `winlink_backend.rs` (3262 LOC) — is relegated to the 3-lane cold
sweep. Every reduced/cold winlink slice (R3 lzhuf, R5 ax25, R6 telnet, R8
session, R10 storage, M3/R4 transports) has its **call frequency** decided in
that one cold-swept file, and the cold sweep runs neither the data-access depth
nor the cross-slice reachability reasoning needed to rank those callers. This is
the cross-slice calibration risk the mandate named, and it is concrete here. It
is fixable with adjacent-context handoffs (no re-tiering of 17→N required), so
the verdict is minor-edits, not rework — but the edit is load-bearing, not a nit.

This is the third consecutive non-rework round; convergence holds, but this round
is NOT "only nits" — it is a substantive (calibration-breaking) finding, so the
convergence rule's "finalize when last round finds only nits" is **not yet
satisfied**. Round 5 should confirm the mitigation below lands.

---

## 1. Cross-slice calibration splits

A finding's Impact = reachability × **frequency** × per-occurrence cost. When the
impl lives in slice X but the loop that establishes its frequency lives in slice
Y, slice X's lane under-ranks (can't see the call count) and slice Y's lane
misses (impl out of scope). I mapped every such split in v4 by tracing the
production call graph (excluding `#[cfg(test)]`).

| Impl slice | Hot/frequency caller | Caller's slice | Severity | Evidence |
|---|---|---|---|---|
| **R3** `lzhuf::compress` (via `message.rs::to_proposal`, `message.rs:161`) | `build_outbound_proposals` loops the **entire Outbox**, compressing every message: `for meta in mailbox.list(Outbox) { … to_proposal() … }` | **cold sweep** (`winlink_backend.rs:233,250`; also `:1302,1716,1878`) | **HIGH** | The loop that makes lzhuf "once per message per send-batch" is in the cold sweep, which runs only complexity+alloc+data-access and will NOT deeply audit lzhuf (not in its scope). R3's lane sees `compress()` but no caller → under-ranks. **The plan attributes lzhuf's driver to "session.rs (R8)"; the real driver is `winlink_backend.rs` (cold).** |
| **R3** `lzhuf::decompress` + `transfer::read_block` | `session.rs::receive_turn` per-accepted-message loop (`session.rs:463-471`: `read_block` → `decompress` → `from_bytes` per accept) | **R8** | MED | session.rs (R8) is the per-message frequency caller for R3's decompress + R3's `read_block`. R8 reduced-tier WILL see this loop, but the impls (`lzhuf.rs`, `transfer.rs`) are in R3, so R8 can't audit the per-occurrence cost and R3 can't see it runs per-accepted-message. |
| **R3** `transfer.rs::read_block` per-byte read (`:100-102` `read_u8`→`read_exact` per byte; `read_until_nul:132-139` byte-by-byte) | `session.rs::receive_turn` (R8) per message | **R8** | MED | New concrete finding (see §"new findings"). The receive-side mirror of the `data.rs` per-byte drain that R3 round flagged. Impl R3, frequency R8. |
| **wire.rs** `read_line` (`read_until(b'\r')`, `wire.rs:12`) | session.rs (R8), telnet.rs (R6), telnet_p2p (R6), b2f (M3), transfer (R3) — every protocol turn | R3 (owner) + R6 + R8 + M3 | LOW | Shared line-framing primitive (see §3). `read_until` is amortized-buffered, so per-occurrence cost is low; flagged for completeness, not severity. |
| **M1** `decode_one_symbol` (DSP) | `tuxmodem-rx/src/lib.rs` (R1) | **R1** | LOW | R1 calls M1's per-symbol decode, but production R1 decodes a **single** symbol per CLI invocation (`lib.rs:174,211` take the first symbol; no multi-symbol streaming loop). Frequency is per-process-run, not a hot loop → low. Already correctly split (DSP in M1, driver R1). |
| **M1↔M2** per-symbol alloc budget | (already handled) | **O1 overlay** | — | The plan's O1 overlay exists *specifically* to reconcile the M1→M2 cross-slice budget against the audio deadline. This is the correct mitigation pattern and proves the authors know the technique — they just didn't apply it to the winlink spine. |

**Severity of the R3↔cold-sweep split (the big one):** lzhuf is ranked "Modest /
~once-per-message → low frequency" (H10). That ranking is an *assumption about
frequency* that no audited lane will validate, because the loop that sets the
frequency is in the cold sweep and the impl is in R3. If a user sends a 200-
message batch (catalog/grib auto-replies, bulk forwarding), lzhuf runs 200× per
session with no streaming — and nothing in the partition is positioned to notice.

**Mitigations (pick per split; none require re-tiering):**

- **R3 (HIGH):** Hand the R3 run **explicit adjacent context**: the
  `build_outbound_proposals` loop at `winlink_backend.rs:229-256` AND the four
  collect sites (`:250,1302,1716,1878`) AND the receive post-loop
  (`:1337,1802,1918,1979`). State the frequency assumption ("compress runs once
  per outbox message per send; batches can be large") so R3's lane ranks lzhuf
  against a *real* call count, not "modest."
- **R3↔R8 (MED):** Run R8 **before** R3 in execution order (currently R3 precedes
  R8 — see §3b) so R8's findings about the per-message receive loop are available
  as adjacent context when R3 audits `read_block`/`decompress`/`read_u8`. OR hand
  R3 the `session.rs:379-391` (send) and `:461-472` (receive) loops as context.
- **wire.rs (LOW):** note in R3 that `read_line` is shared by R6/R8/M3; no action
  beyond a one-line "shared, low-cost" disposition.

---

## 2. Reduced-tier right-sizing (per-slice keep/demote calls)

I read enough of each R-slice to judge whether a reduced-depth cycle earns its
keep or is actually cold (pure state machine + I/O, no loop/alloc/data-access
concern). Calls below are grounded in grep density + spot reads.

| Slice | Call | Evidence |
|---|---|---|
| **R1** tx/rx CLI | **KEEP** (but fix the "thin" label) | NOT thin: `tuxmodem-tx` 1291 + `tuxmodem-rx` 1026 = 2317 prod-ish LOC. rx/lib.rs has `decode_one_symbol`, `symbol_size_samples`, WAV read/write, BER loops (`lib.rs:174,211,238,284`). The DSP is in M1, but R1 has real per-symbol orchestration + WAV I/O worth the data-access+alloc lanes. Reduced is right; "thin CLI drivers" understates it — reword. |
| **R2** rig/PTT | **KEEP, but narrow the focus** | `watchdog.rs` is a **sleep-poll loop** (`watchdog.rs:78` `loop { … sleep(poll_interval.min(remaining)) }`) — zero allocation, zero data-structure churn; it sleeps. `writer.rs` (13 hits) is small byte writes. R2 has **no throughput/alloc concern** — its only perf-relevant axis is **timing correctness** (watchdog deadline accuracy, PTT latency). The plan's R2 focus "watchdog timing, I/O" is correct, but the algorithmic/memory lanes will find nothing. Consider R2 as a **timing-only** reduced run (concurrency + latency), explicitly skipping algorithmic/memory. Borderline-demote; KEEP only because PTT latency is real-time-adjacent. |
| **R5** ax25 datalink | **KEEP** | framing/CRC over frames; `datalink.rs` is ~87% test (per plan principle 2) but the prod path does per-frame CRC + KISS escaping (byte-stuffing) — real data-access work. Justified. |
| **R6** telnet/P2P transport | **KEEP** | NOT cold: telnet.rs (686 LOC) + telnet_listen.rs (1389 LOC) + p2p. Has `read`-loop draining (`telnet.rs:409,570,624` `while let Ok(n) = sock.read(&mut buf)`), WireTap byte-copy wrapper (`telnet.rs:121` `for &b in bytes`), connect-sweep over resolved addrs (`:311`). Real I/O + buffer-copy concern. Reduced justified. |
| **R7** P2P listener gate | **DEMOTE-CANDIDATE → cold** | This is the **weakest reduced slice.** `listener/` is a **consent/authorization gate**: `arms_record.rs` (records who armed a transport, TTL/disarm — UX state, `fs::read` once per arm at `:184`), `decide.rs` (6 hits), `peer.rs` (1 hit), `packet_gate.rs`, `station_password.rs`, `allowed_stations.rs`. This is **per-connection** policy evaluation, not a hot loop — a connection is a human-initiated, low-frequency event (someone calls the station). No per-byte/per-symbol/per-message loop. The "44 hits" in `arms_record.rs` are mostly doc-comment lines (`//!`), not code. **Recommend: demote R7 into the cold sweep** (its complexity+alloc+data-access concerns are adequately covered by 3 lanes; concurrency on the listener accept-loop can be flagged for the sweep to eyeball). This reduces 17→16 with no coverage loss. |
| **R8** B2F session driver | **KEEP (strongest reduced slice)** | session.rs is 1000 LOC, 101 loop/alloc hits, and is the **per-message exchange core**: `send_turn` per-proposal loop (`:347,379`), `receive_turn` per-accept decompress loop (`:463`), checksum byte-loop (`:425`). Genuinely warm. Should arguably be the FIRST reduced slice run (see §3b). |
| **R9** warm frontend | **KEEP as light check** | Already correctly reframed in v4 (MessageList IS virtualized; sparkline 1 Hz). Keep. |
| **R10** storage backend | **KEEP** | H8 read-amplification (`native_mailbox.rs::list:99-104` read_dir + fs::read per message) is a real N+1. Justified promotion. |
| **M3** ardop transport | **KEEP** | Per-byte `VecDeque` drain (`data.rs:104-106,145`) is real; reduced demote from full was correct (DSP external). |

**Conversely — anything cold that's warm enough to promote?** Yes, indirectly:
the cold-swept `winlink_backend.rs` contains the master send/receive orchestration
(`run_exchange_with_role` calls + outbound-build + received-message post-processing
loops at `:1302-1340, 1716-1805, 1878-1921, 1957-1982, 2029-2081`). It is the
frequency spine (see §1). I do **not** recommend promoting the whole 3262-LOC file
to a reduced slice — most of it is Tauri command glue — but the **frequency-
establishing loops** must be handed to R3/R8 as adjacent context (§1 mitigation).
Net reduced-tier change recommendation: **R7 → cold** (16 units).

---

## 3. Shared abstractions no single slice owns

I grepped for widely-used helper modules / hot shared types crossing slice
boundaries.

- **`wire.rs::read_line` / `clean_line`** (assigned R3, `wire.rs:12,25`): the
  shared `\r`-framed line reader used by **R6** (telnet), **R8** (session),
  **M3/R4** (b2f), **R3** (transfer). 65 LOC. Impl in R3, hot consumers elsewhere.
  Low per-occurrence cost (`read_until` is buffer-amortized), so LOW severity — but
  it IS a shared primitive the partition splits. **Disposition:** note in R3 that
  it's shared; no dedicated slice needed.
- **`transfer.rs::frame_block` / `read_block`** (R3): shared body-framing used by
  R8 (`session.rs:384,467`) and M3 b2f (`b2f.rs`). Contains the per-byte read loop
  (§1). Same owner-vs-consumer split as wire.rs but HIGHER per-occurrence cost
  (per-byte). **Disposition:** R3 must audit `read_block` with R8's per-message
  frequency in mind (§1 mitigation).
- **`num_complex::Complex`** (mandate asked): **fully contained in M1**
  (`tuxmodem-phy`: constellations, equalizer, receiver, transmitter, narrow_fsk,
  subcarrier_snr, sync/*). M2 (fec) and all winlink slices do NOT use it. **No
  cross-slice split — good.** The Complex-heavy hot inner loops are all in one
  full-cycle slice. No action.
- **`crc`/`checksum`** primitives: used in lzhuf, message, b2f, proposal, session,
  transfer — but these are *different* checksums (B2F batch checksum, transfer
  block checksum, ax25 FCS), not one shared CRC module. No single hot shared CRC
  to assign. No action.
- **No shared ring buffer / byte-vec pool** found crossing slices. The audio ring
  buffer is M1-internal (`audio_device.rs`). VecDeque drains are M3-local.

**Net:** the only shared hot primitive worth a disposition is `transfer.rs`
(R3) consumed at R8's per-message frequency — covered by the §1 mitigation. The
DSP-side shared type (`Complex`) is cleanly contained in M1.

---

## 3b. Execution order & inter-slice dependencies

Current order: `M1 → M2 → O1 → M4 → M5 → M3 → R10 → R3 → R5 → R6 → R7 → R8 → R4 → R1 → R2 → R9 → cold sweep`.

Assessment:
- **M1 → M2 → O1** correct: overlay after both producers. ✅
- **R3 before R8 is BACKWARDS for the calibration fix.** R8 (`session.rs`)
  establishes the per-message frequency for R3's `decompress`/`read_block`/`read_u8`.
  Running R3 first means R3 has no frequency context. **Recommend: move R8 before
  R3** (…→ R10 → **R8 → R3** → R5 → …) so R8's per-message-loop findings are
  adjacent context for R3. This is the cheapest mitigation for the MED-severity
  R3↔R8 split.
- **Cold sweep last is the deepest problem.** The cold sweep contains
  `winlink_backend.rs`, the frequency spine for R3/R8/R10/M3/R4. Running it LAST
  means none of those reduced slices benefit from its orchestration map. The clean
  fix is NOT to reorder the whole sweep (it's a batched 3-lane run) but to
  **extract the `winlink_backend.rs` frequency loops into an adjacent-context note
  handed to R3, R8, and R10 up front** (a one-page "winlink call-frequency map"
  the runner produces before R3). Recommend the plan add a **pre-slice artifact**:
  a winlink frequency map (which loop drives each protocol op, how often).
- R1/R2 at the tail: fine (lowest urgency, no downstream consumer).

**No ordering causes re-work** beyond the R3/R8 inversion. Net: swap R8↔R3; add a
winlink-frequency-map pre-artifact.

---

## 4. Verification-gate feasibility per slice (Phase 6: baseline+post measurement
OR complexity/allocation argument + correctness guard)

| Slice | Live device needed? | Measurable here? | Fallback adequacy |
|---|---|---|---|
| **M1** OFDM/audio | YES for `audio_device` real-time path (CPAL stream, H4); NO for demod/modulate math | Partial. The per-symbol alloc/FFT-replan (H0/H1) is measurable via **Criterion micro-bench on `demodulate_one_symbol`** with a synthetic symbol buffer — no radio. The **audio deadline** (H4) is NOT measurable without a sound device + live stream. | Complexity/alloc argument is SUFFICIENT for H0/H1/H3/H5/H7 (allocation-count is statically arguable). **H4 (deadline) is unfalsifiable here** — flag explicitly: "audio-callback deadline findings carry an allocation-budget argument only; live-device measurement deferred to operator hardware." |
| **M2** FEC | NO | YES — pure compute; Criterion bench on `Decoder::decode` with synthetic LLRs. | Fully measurable. Strongest verification posture. The plan's "add Criterion benches to tuxmodem-fec" is exactly right and should be a **hard gate** for M2 fixes. |
| **M3** ardop | YES (external TNC for end-to-end); NO for the per-byte drain | The `data.rs` VecDeque drain is measurable via a unit micro-bench feeding synthetic frames — no TNC. | Adequate via complexity arg + micro-bench. End-to-end throughput unfalsifiable without TNC — acknowledge. |
| **M5** hf-sim | NO (offline) | YES — pure offline DSP. | Fully measurable. |
| **R1** tx/rx | YES for real audio capture; NO for WAV-decode path | `--decode-wav` path is measurable on a fixture WAV. | Adequate (fixture WAV). |
| **R2** rig/PTT | **YES — hard requirement** | Watchdog timing + PTT latency CANNOT be measured without a real rig/serial/CM108 device. | **Complexity/alloc argument is NOT a meaningful fallback** for R2 because R2 has no allocation concern (§2) — its findings ARE timing, and timing is exactly what needs hardware. **R2 findings will be largely unfalsifiable here.** Flag explicitly: R2 produces design-review observations, not measured regressions. This weakens R2 specifically and reinforces the §2 "narrow R2 to timing-only / borderline-demote" call. |
| **R3/R5/R6/R8/R10** winlink | NO (loopback/fixtures) | YES — all are testable with in-memory `Cursor`/loopback sockets + fixture mailboxes. session.rs already tests this way. | Fully measurable via micro-benches on framing/compress/list. Good posture. |
| **cold sweep** | NO | Static only (3 lanes). | By design — complexity/alloc/data-access arguments only. Adequate for its tier. |

**Slices where findings are unfalsifiable without hardware (must be flagged in the
plan):** **R2** (timing, no alloc fallback — worst case), **M1's H4 audio
deadline**, M3 end-to-end throughput, R1 live-capture path. The plan's
"complexity/allocation argument" fallback is **sufficient for compute/alloc
findings** (M2, M5, the winlink family, M1's demod math) but is a **weak fallback
for pure-timing slices (R2)** because those slices have no allocation to argue
about. Recommend the plan add a per-slice "verification mode" column:
`measurable-here` vs `hardware-deferred` vs `static-only`, so the operator knows
which findings ship with evidence vs which are design observations awaiting
hardware.

---

## 5. Multi-run (fine-grained 17) vs fewer-larger language-homogeneous runs

**Steelman the alternative** (4–5 big runs: "all Rust DSP" = phy+fec+hf-sim,
"all winlink" = entire `winlink/` + `winlink_backend.rs`, "all backend glue" =
src-tauri commands/storage/forms, "all frontend"):

*For fewer-larger:* It would **solve the cross-slice calibration problem by
construction** — a single "all winlink" run sees `winlink_backend.rs`'s
orchestration loops AND lzhuf AND session.rs together, so lzhuf's frequency is
visible to the lane auditing it. No adjacent-context handoff needed. The §1 HIGH
split simply vanishes. That is a real, structural advantage and it is precisely
the failure mode this round found.

*Against fewer-larger:* `performance-audit-cycle`'s lanes degrade on huge scopes —
a single "all winlink" run is ~12k+ LOC across 30 files; the algorithmic and
data-access lanes lose precision (they can't deeply read every file), and ranking
becomes coarse. The fine-grained partition exists *because* lane precision is the
binding constraint, and rounds 1–3 already paid down the within-slice precision.

**Recommendation: keep the fine-grained-17 (→16 after R7 demote) approach, but
adopt the ONE structural lesson from the fewer-larger steelman — co-locate
frequency with impl where the split is HIGH-severity.** Specifically:

- The 17-unit approach is correct for the DSP family (M1/M2/M5): lane precision
  dominates, and `Complex`/FFT frequency is already self-contained per slice (§3).
  Splitting DSP finer is the right call.
- The winlink family is where fewer-larger has a point. **Two viable fixes**, in
  order of preference:
  1. **(Preferred, cheap)** Keep R3/R5/R6/R8/R10 separate but produce the
     **winlink call-frequency map** pre-artifact (§3b) and hand it to each as
     adjacent context. Preserves lane precision; closes the calibration gap.
  2. **(Heavier)** Merge R3+R8 into one "winlink B2F core" slice (lzhuf + message
     + transfer + session + handshake) so compress/decompress/framing and their
     per-message driver are co-audited. ~2500 prod LOC — still tractable for the
     lanes, and it eliminates the two highest splits outright. This is the
     fewer-larger philosophy applied *surgically* to the one place it pays.

**My call:** fine-grained-16 + winlink-frequency-map pre-artifact (option 1). It
keeps the lane-precision win that rounds 1–3 built while neutralizing the
calibration defect. If round 5 wants belt-and-suspenders, merge R3+R8 (option 2).
Do NOT collapse to 4–5 mega-runs — that throws away the precision the whole
review series invested in.

---

## New concrete findings tripped over (report-and-move-on, not hot-path hunting)

1. **`transfer.rs::read_block` per-byte read** (`transfer.rs:100-102` `read_u8`
   → `read_exact(&mut [b])` per byte inside the chunk loop; `read_until_nul:132-
   139` byte-by-byte). Called from `session.rs::receive_turn` (R8) once per
   accepted message body. If the `R: Read` passed in is unbuffered, this is a
   syscall per byte — the receive-side mirror of the `data.rs` per-byte drain R3
   already flagged. **Impl in R3, frequency in R8** (the calibration split made it
   visible). Worth a finding in whichever slice ends up owning the per-message
   receive loop.
2. **lzhuf compression driver mis-attributed.** Plan O1/§hot-map implies
   session.rs (R8) drives lzhuf. The actual production compress driver is
   `winlink_backend.rs::build_outbound_proposals` (cold sweep). Correct the
   attribution in the hot-path map (H10 row) and in O1's prose.

---

## Ranked minimal edits

1. **(HIGH — structural)** Add a **winlink call-frequency map** pre-artifact:
   document that `winlink_backend.rs:229-256` (+ `:1302,1716,1878`) compresses
   every outbox message via `to_proposal`→lzhuf, and `:1337+` decompresses/parses
   every received message. Hand this map to R3, R8, and R10 as adjacent context so
   their lanes rank against real frequencies. This closes the R3↔cold-sweep HIGH
   split without re-tiering.
2. **(MED)** Swap execution order **R8 before R3** so the per-message receive loop
   is context for R3's `read_block`/`decompress`/`read_u8` audit.
3. **(MED)** Demote **R7 (P2P listener gate) → cold sweep**: it's per-connection
   policy/consent state, no hot loop (17→16 units). Evidence: `listener/*` is
   authorization + arm-record UX; `arms_record.rs`'s "44 hits" are mostly doc
   comments; `fs::read` runs once per arm.
4. **(MED)** Add a **per-slice verification-mode column** (`measurable-here` /
   `hardware-deferred` / `static-only`). Flag R2, M1-H4, M3-end-to-end, R1-live as
   hardware-deferred; note R2's complexity/alloc fallback is weak because R2 has no
   alloc concern — R2 findings are timing design observations, not measured.
5. **(LOW)** Correct the lzhuf-driver attribution in the H10 hot-map row and O1
   prose (driver is `winlink_backend.rs`, not session.rs).
6. **(LOW)** Reword R1 from "thin CLI drivers" — it's 2317 LOC with real WAV I/O
   and per-symbol decode orchestration; reduced-tier is correct but "thin"
   understates the data-access surface.
7. **(LOW)** Narrow R2's stated lanes to timing/concurrency only (skip
   algorithmic/memory — nothing to bite; `watchdog.rs` is a sleep-poll loop with
   zero allocation).
8. **(LOW)** Note in R3 that `wire.rs::read_line` + `transfer.rs::frame_block`/
   `read_block` are shared primitives consumed by R6/R8/M3; dispose as
   shared-low-cost (except `read_block` per-byte, edit #1/#2).

---

*Convergence note:* this round found a substantive (calibration-breaking)
structural defect plus a real per-byte-read finding — it is NOT "only nits."
Per the log's convergence rule, round 5 is required and should verify the
winlink-frequency-map mitigation + R7 demote land before finalizing.
