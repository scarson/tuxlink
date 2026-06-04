# Bug-hunt kickoff — suspected items from the 2026-06-04 M2 (tuxmodem-fec) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `tuxmodem-fec` decode path — `codec.rs` (`decode_soft`) and the
floor-code construction (`codes/floor_rate14.rs`). Noticed during a perf audit;
NOT investigated.

**Seed findings (verify, don't trust):**
- Dead computation — `codec.rs:146,151` — `llr_bitvec_signs` is computed then discarded via `let _ = …` (Θ(n) dead work per decode). Confirm it isn't a dropped/incomplete step rather than intentional dead code.
- Floor-code rank deficiency — `codes/floor_rate14.rs:28-34` — the floor code is `RankDeficient`, so its encode path panics (decoder works). Pre-existing / tracked elsewhere; included for completeness.

Leads for the hunters, not confirmed bugs.
