# Adversarial Review (Round 5, final gate) — Performance-Audit Scope Partition v5

- **Reviewer:** Opus subagent (agent `glade-knoll-shoal`)
- **Date:** 2026-06-04
- **Target:** `dev/perf-audit-scope-plan.md` v5 (16 units + W0 pre-artifact)
- **Lens:** GO/NO-GO gate — verify v5 fixes, hunt the DSP/FEC analog of the Round-4 winlink calibration defect, one fresh holistic pass.
- **Mode:** READ-ONLY. No source modified.

---

## Verdict — GO (executable after two minor, non-blocking edits)

v5 is **EXECUTABLE as-is for M1**. All four v5 fixes verify against source. The
DSP path does **not** carry the winlink cross-slice calibration defect (the
symbol-loop driver is co-located with the impls in M1). I found **one genuinely
new substantive context gap all four prior rounds missed — the FEC crate has
zero in-tree callers (it is not yet wired into the PHY pipeline)** — but it is a
*calibration-context* defect for M2/O1, not a coverage or partition-structure
break, and it does **not** block starting M1. Two cosmetic path-precision edits
(R2 rig paths; an O1 wording correction) are recommended; neither blocks M1 and
both can be applied in-flight before the slices they affect (R2 is near the end
of the order; O1 runs after M1+M2).

**No further round required.** The new finding is context to fold into M2/O1
adjacent notes, not a tier-change or coverage break, so it does not trip the
convergence rule's "substantive defect" continuation clause.

---

## v5-fix verifications (each confirmed / refuted against source)

### 1. W0 claim — `build_outbound_proposals` is the Outbox-looping lzhuf driver → **CONFIRMED**

`winlink_backend.rs:229-257`: `build_outbound_proposals(mailbox)` does
`for meta in mailbox.list(MailboxFolder::Outbox)?` (`:233`), reads each body, and
calls `message.to_proposal()` (`:250`). `message.rs:158 to_proposal` →
`:161 let compressed = crate::winlink::lzhuf::compress(&bytes)`. The full chain
**Outbox loop → per-message `to_proposal` → `lzhuf::compress`** is real and in
source exactly as W0 describes. The driver is in `winlink_backend.rs` (cold
sweep), the impl is in R3 — the cross-slice split W0 addresses is real. The v5
re-attribution away from `session.rs` is correct: `session.rs` drives the
*receive* side (`decompress`/`read_block`), not the outbound compress loop.

### 2. R7 demotion — `winlink/listener/` is hot-loop-free → **CONFIRMED**

- `listener/decide.rs`: only loop-shaped tokens are doc comments and a test
  (`:138 MockEntry`). The decision is per-peer, allocation-light.
- `listener/packet_gate.rs`: `run`-the-gate path calls `listener_decide_at` once
  per inbound peer; the only `fs::*` is `:397 read_to_string` inside a `#[test]`.
- `listener/arms_record.rs:183 read_log` does a single `std::fs::read(path)` of
  the whole JSONL forensics log, then a `text.lines().map(...).collect()` parse —
  invoked per arm-decision (per-connection consent check), **not** in a hot loop.
  The "`fs::read` once per arm" claim is accurate; "`arms_record` density is doc
  comments" is accurate (the file is heavily doc-commented, `:3-140`).

Demotion R7 reduced→cold is correct.

### 3. H11 — `transfer.rs::read_block:100-102` is a per-byte `read_exact` loop → **CONFIRMED**

`transfer.rs:92-116`: inside the STX chunk arm, `for _ in 0..len { let b =
read_u8(reader)?; data.push(b); sum = sum.wrapping_add(...) }` (`:100-104`).
`read_u8` (`:124-127`) is `reader.read_exact(&mut b)` over a **single-byte**
buffer. So each payload byte is an independent `read_exact` syscall-class call —
a textbook per-byte read loop. Receive-side mirror of the ARDOP `data.rs` drain,
as claimed. Consumed at R8's per-received-message frequency — correct.

### 4. Execution order R8-before-R3 + W0-first resolves the calibration gap → **CONFIRMED (with nuance)**

Order (plan `:157`): `… M3 → W0 → R8 → R3 → R10 → …`. W0 (the frequency map)
is produced before any winlink-family slice; R8 (session driver, establishes
per-message receive frequency at `session.rs receive_turn`) runs before R3 (the
compress/decompress/read_block impls). So when R3 audits lzhuf (H10) and
read_block (H11), both the *outbound* frequency (W0's `build_outbound_proposals`
map) and the *inbound* frequency (R8's session driver, already audited) are in
hand. The gap Round 4 caught is closed by adjacency, not by merging slices.
**Nuance:** W0 is a *pre-artifact the operator must actually write* before R3 —
the plan correctly frames it as a gating deliverable (`docs/perf-audits/<date>-winlink-call-frequency-map.md`),
not an automatic byproduct. As long as the operator produces W0 at its ordered
slot, the calibration holds. (Flagged as an execution-discipline note, not a
defect.)

---

## DSP / FEC cross-slice frequency analysis (the new angle)

**Question (mandate §2):** does the DSP path have the winlink defect — i.e., is
the per-frame/per-second decode+demod frequency set by an rx/tx orchestration
loop in R1 (run LATE, reduced) while M1/M2 (run FIRST) audit the impls blind to
realistic call frequency?

**Answer: NO for the demod path; and the FEC path has a different, more serious
problem (no caller at all).**

### M1 (PHY demod) sees its own per-symbol frequency natively — no cross-slice split

The decode driver chain:

- `tuxmodem-rx/src/lib.rs:174 decode_one_symbol` and `:211
  decode_one_symbol_with_offset` are thin dispatch wrappers — each calls
  `WidebandLowDensityFloor::{receive,receive_with_sync,receive_multi_with_sync}`
  **once**.
- The rx **bin** (`bin/tuxmodem-rx.rs:96 run_decode_wav`) calls
  `decode_one_symbol_with_offset` **exactly once per WAV** (`:117`). R1 is NOT a
  multi-symbol loop driver.
- The per-symbol iteration that establishes demod frequency lives **inside M1**:
  `wideband_lowdensity.rs:303 receive_multi` reads a length header, computes
  `symbols_needed` (`:321`), then `for s in 1..symbols_needed { …
  decode_symbol_bytes(&samples[start..]) … }` (`:334-338`). `decode_symbol_bytes`
  (`:173`) is what calls `OfdmReceiver::demodulate_one_symbol` → `compute_llr`
  (the H0/H1 hot paths).

So the symbol-loop frequency-establishing driver (`receive_multi`) is in the
**same slice (M1)** as the impls it drives. M1 audits its own realistic call
frequency. This is the structural OPPOSITE of the winlink defect, where the
driver was exiled to the cold sweep. **The O1 pipeline overlay is sufficient for
the demod path; M1 does NOT need an rx/tx frequency note as adjacent context.**

### M2 (FEC) — the real finding: the FEC crate has ZERO in-tree callers

This is the genuinely new defect all four prior rounds missed.

- `tuxmodem-phy/Cargo.toml:23`: `# tuxmodem-fec.workspace = true` — the PHY
  crate's dependency on the FEC crate is **commented out**.
- Repo-wide search for `tuxmodem_fec` / `tuxmodem-fec` (excluding the fec crate's
  own src and all `#[cfg(test)]`): the only hits are the workspace manifest
  declaration (`tuxmodem/Cargo.toml:4,27`) and **doc comments** in
  `coded_modulation.rs:3,36`. No production code anywhere calls
  `tuxmodem_fec::Decoder::decode`.
- `coded_modulation.rs:1-7` confirms intent: "*The FEC layer is a separate
  crate… Phase 10 lands the trait + an identity stub; the real FEC plugs in once
  #4's sibling plan lands.*" `decode_symbol_bytes` (`wideband_lowdensity.rs:173-193`)
  currently does **hard-decision LLR slicing** (`if *l >= 0.0 { 0 } else { 1 }`,
  `:179`) — it bypasses FEC entirely.

**Implications for the plan:**

1. This is **not** the winlink defect's analog (hidden caller in another slice).
   It is the inverse: M2's `Decoder::decode` has **no caller at all yet**, so its
   "Critical — densest compute, per-iteration allocs (H2)" ranking reflects
   *intrinsic* algorithm cost, not realized call frequency. That ranking is still
   defensible *as an audit of the crate in isolation* — but M2's report must
   state plainly that the crate is **not yet wired into the live pipeline**, so
   H2's findings cannot be reached from any runtime path today. Otherwise M2 over-
   claims a hot path that is presently dead code.
2. **O1 is currently mis-specified.** The overlay reconciles
   "`audio_device`→`demodulate_one_symbol`→`compute_llr`→`Decoder::decode`" against
   the audio deadline (`plan :111`). But `Decoder::decode` is **not in that chain
   in the current source** — `decode_symbol_bytes` slices LLRs to hard bits with
   no FEC step. O1 should either (a) audit the *as-wired* chain (ending at the
   hard-decision slice, no FEC) and note the FEC step is a *planned future*
   insertion point, or (b) explicitly frame itself as a *forward-looking* budget
   for when #4 lands. As written it asserts a composition that does not exist.

**Recommendation (concrete):** Add a one-line "FEC integration status" note to
M2 and O1: *"`tuxmodem-fec` is a standalone crate with no in-tree caller
(`tuxmodem-phy/Cargo.toml:23` dep commented out; `decode_symbol_bytes` uses hard-
decision slicing). Audit H2 as intrinsic crate cost; do not claim a live hot
path. O1's `…→Decoder::decode` link is the planned post-#4 wiring, not current
source."* This is the DSP analog of W0 — but it is a *no-caller* annotation, not
a frequency map. It does NOT block M1 (M1 runs first and is self-contained); it
should be in hand before M2 and is mandatory before O1.

### M3 (ARDOP) frequency — also self-contained, no split

`process.rs` (at `src-tauri/src/winlink/modem/process.rs`) manages the external
`ardopcf`/VARA child; the DSP is out-of-process. M3's own cost
(`data.rs:104 drain_leftover` per-byte `drain(..n)` zip loop; `:145
leftover.extend(frame.payload)`) is driven per-received-frame by M3's own
`transport.rs` read loop — same slice. `process.rs` (R4) does not establish a
hidden cross-slice frequency for M3; the "shared seam, primary home R4, M3 reads
as adjacent context" framing already covers it. No new gap.

---

## Holistic-pass findings

1. **FEC no-caller** — see above (the headline new finding; context defect, not
   coverage/structure break).
2. **R2 rig paths don't exist as named (path-precision defect).** Plan R2 lists
   `tux-rig-rts/src` + `tux-rig-cm108/src` (`:119`) and the ledger uses the same
   bare paths (`:168`). These directories do **not** exist at the repo root; the
   crates are at `tuxmodem/crates/tux-rig-rts/src` and
   `tuxmodem/crates/tux-rig-cm108/src`. Every *other* slice uses full repo-
   relative paths (e.g. `tuxmodem/crates/tuxmodem-fec/src`), so R2 is an
   inconsistency a subagent could trip on. Cosmetic (the crates are unambiguous),
   but should be corrected for subagent-readiness.
3. **`process.rs` path imprecision (cosmetic, non-blocking).** Ledger `:170`
   reads `modem/vara+mod+process→R4`; the actual file is `modem/process.rs`
   (sibling of `modem/vara/`), not `modem/vara/process.rs`. The R4 slice row
   (`:121`) writes it correctly as `process.rs` at modem level, so this is only a
   ledger-shorthand reading ambiguity, not a real misassignment.
4. **No language-homogeneity violation.** Every Rust slice is Rust-only; R9 and
   the TS cold sweep are TS-only. Confirmed M1–M5/R1–R8/R10 are pure-Rust crate
   or `src-tauri` Rust; R9 = `src/radio`+`src/mailbox` (TS).
5. **No finding double-counted across slices.** H10/H11 → R3; H8 → R10; H2 → M2;
   H0/H1/H3/H4/H5/H7 → M1; H6 → M4; H9 → M5. Each hot path lands in exactly one
   slice. `process.rs` shared-seam is explicitly single-homed (R4 primary, M3
   adjacent-read) — not double-counted.
6. **No internal inconsistency** between hot-path map, slice table, and ledger
   beyond items 2–3 above. The H10 re-rank ("Modest→re-rank w/ W0") is internally
   consistent with W0's existence.

---

## Path / coverage / total spot-checks

**Paths spot-checked on the filesystem (11):**

| Path | Result |
|---|---|
| `tuxmodem/crates/tuxmodem-phy/src/audio_device.rs` | OK |
| `tuxmodem/crates/tuxmodem-phy/src/constellations.rs` | OK |
| `src-tauri/src/search` | OK |
| `hf-channel-sim/src` | OK |
| `src-tauri/src/winlink/modem/ardop` | OK |
| `tux-rig-rts/src` (as written in R2) | **MISS** → real: `tuxmodem/crates/tux-rig-rts/src` |
| `tux-rig-cm108/src` (as written in R2) | **MISS** → real: `tuxmodem/crates/tux-rig-cm108/src` |
| `src-tauri/src/wizard.rs` | OK |
| `src-tauri/src/winlink/telnet_p2p.rs` | OK |
| `src/radio` | OK |
| `src/mailbox` | OK |

9/11 exist as named; 2 (R2 rig crates) need the `tuxmodem/crates/` prefix.

**Line-ref spot-checks (all confirmed):** `constellations.rs:142` alphabet
rebuild inside the `receiver.rs:63` per-subcarrier loop (with `Mapper::new` at
`:78`, `compute_llr` at `:83`) — H1 real. `native_mailbox.rs:99 read_dir` +
`:104 fs::read` per message — H8 real. `data.rs:104` drain + `:145` extend — M3
target real.

**Total arithmetic:** Full = M1,M2,M4,M5 = 4 (M5 full, M3 demoted to reduced).
Overlay = O1 = 1. Reduced = M3,R1,R2,R3,R4,R5,R6,R8,R9,R10 = 10. Cold sweep = 1.
**4+1+10+1 = 16.** Matches plan `:163`. R7 correctly absent (folded into cold).
Execution order (`:157`) enumerates all 16 + W0. **Tally is correct.**

**Coverage ledger:** verified airtight per the ledger's own enumeration; no dir
appears twice, no `src-tauri/src/*.rs` or crate is unassigned. The listener
demotion is reflected (`listener→cold sweep`). No gap.

---

## Ranked edits

| # | Edit | Severity | Blocks M1? | When to apply |
|---|------|----------|-----------|---------------|
| 1 | Add FEC integration-status note to **M2** and **O1**: FEC crate has no in-tree caller (`tuxmodem-phy/Cargo.toml:23` commented; `decode_symbol_bytes` hard-slices). Audit H2 as intrinsic cost, not a live hot path; O1's `…→Decoder::decode` is planned post-#4 wiring, not current source. | Substantive (context) | **No** | Before **M2** (in hand by M2; mandatory before O1). M1 runs first and is unaffected. |
| 2 | Fix R2 paths: `tux-rig-rts/src` → `tuxmodem/crates/tux-rig-rts/src`; `tux-rig-cm108/src` → `tuxmodem/crates/tux-rig-cm108/src` (slice row `:119` + ledger `:168`). | Minor (path precision) | **No** | In-flight before **R2** (near end of order). |
| 3 | Optional: clarify ledger `:170` shorthand so `process.rs` reads as `modem/process.rs` (modem-level), not implying `modem/vara/process.rs`. | Cosmetic | **No** | Any time; R4 row is already correct. |

**None of these block starting M1.** M1 is fully self-contained (its own per-
symbol frequency driver `receive_multi` is in-slice; H0/H1/H3/H4/H5/H7 all verified
in `tuxmodem-phy/src`). Edit 1 must land before M2/O1; edits 2–3 are in-flight
cleanups.

---

## Decision: **GO**

v5 is executable. Begin with M1 now. Apply edit 1 before M2 and edit 2 before R2.
The ≥5-round mandate is satisfied: this final round found one new substantive
*context* gap (FEC has no caller) but no tier-changing or coverage-breaking
defect, so the convergence rule does not require a sixth round.
