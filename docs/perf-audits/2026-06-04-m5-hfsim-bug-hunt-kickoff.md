# Bug-hunt kickoff — suspected item from the 2026-06-04 M5 (hf-channel-sim) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `hf-channel-sim/src/analysis.rs` SNR aggregation. Noticed during a
perf audit; NOT investigated.

**Seed finding (verify, don't trust):**
- Precision asymmetry — `analysis.rs:78-83` computes per-window snapshot SNR in
  f32 while `:92-96` computes the mean SNR in f64. Confirm whether the f32
  snapshot path biases the reported mean, or is intentional.

A lead for the hunters, not a confirmed bug.
