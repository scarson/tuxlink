# Perf Audit — tuxmodem-tx + tuxmodem-rx (combined: algorithmic + memory)

- **Agent:** glade-knoll-shoal
- **Date:** 2026-06-04T19-30 (r1)
- **Scope:** `tuxmodem-tx/src/lib.rs` + `tuxmodem-rx/src/lib.rs` (lib only; `bin/` excluded). Thin CLI driver crates around the M1 PHY hot loops.
- **Lens:** `performance-audit/profile-packs/rust.md` (algorithmic + memory lanes) as PRIOR.
- **Load model:** offline CLI — one transmission/decode per process invocation. Not real-time, not shipped-client. Impact is offline-CLI-calibrated; no wall-clock measured.
- **Out of scope (not re-reported here):** the dense DSP in `tuxmodem-phy` (M1, separately audited). These crates only CALL it.

---

## Findings

### [1] Whole-WAV-into-Vec then slice-to-one-symbol on `--decode-wav` (memory)
- **Location:** `tuxmodem-rx/src/lib.rs:174-206` (`decode_one_symbol`, `Raw` arm: `&samples[..needed]`) reached via `read_wav` at `:238-240` (`AudioBuffer::read_wav`).
- **Problem:** The Raw decode path reads the ENTIRE WAV into an `AudioBuffer` (a `Vec<f32>`), then uses only the first `symbol_size_samples()` (2560 f32 = ~10 KB) and discards the rest. Doc at `:30-37` admits longer files aren't scanned in Raw. An off-air capture trimmed loosely (or a multi-minute recording) is fully materialized when only the head is consumed. Streaming `hound` and reading just the first `needed` samples would bound the allocation.
- **Impact:** Low. Offline, one-shot; a 48 kHz f32 capture is ~192 KB/s, so even a several-minute file is tens of MB — fits in RAM on the dev Pi. Real only for pathologically large captures. Sync/MultiSync legitimately need the whole buffer (preamble scan), so the win is Raw-only.
- **Confidence:** High (behavior is plainly in source).
- **Effort:** Medium — requires a streaming WAV reader path distinct from `AudioBuffer::read_wav` (which lives in phy, out of scope to change here); a tx/rx-side fix would re-implement a bounded read.
- **Verification:** Decode a multi-minute Raw WAV under `dhat`/heaptrack; confirm peak ≈ whole-file size today, ≈ symbol-size after fix.

### [2] `record_to_wav` holds full capture in RAM and returns a clone-by-move (memory)
- **Location:** `tuxmodem-rx/src/lib.rs:301-313`.
- **Problem:** `record_blocking_with_abort(target_samples, …)` allocates the full N-second capture as one `Vec<f32>`, then `write_wav` walks it again. For `--duration` up to operator's choosing (no cap visible in this lib), a long capture is a single large allocation. No streaming-to-disk path. `target_samples` computed at `:307` is not pre-reserved here (reservation, if any, is inside phy — out of scope).
- **Impact:** Low. Offline capture; durations are operator-bounded and short in practice. Streaming-to-disk would matter only for very long records.
- **Confidence:** Medium (the alloc is in phy's `record_blocking_with_abort`; this lib just calls it — the streaming decision is upstream).
- **Effort:** Large (needs a phy-side streaming record API).
- **Verification:** Record a long duration; observe peak RSS ≈ duration×192 KB/s.

### [3] Per-call `WidebandLowDensityFloor::new()` on every decode/encode entry (memory/algorithmic)
- **Location:** `tuxmodem-rx/src/lib.rs:189, 194, 200, 222, 228`; `tuxmodem-tx/src/lib.rs:206`.
- **Problem:** Each `decode_one_symbol` / `decode_one_symbol_with_offset` / `encode_payload` constructs a fresh `WidebandLowDensityFloor`. If `::new()` precomputes FFT plans / twiddle tables / Zadoff-Chu sequences (rustfft planners commonly do), that setup is paid per call. The CLI calls each entry once per process, so this is a non-issue at the CLI layer — but any caller that loops (a future batch/BER-sweep tool, or the test suite which constructs dozens) re-pays construction each iteration.
- **Impact:** Low at the documented one-shot CLI load. Would rise to Medium if a batch driver is added.
- **Confidence:** Medium (cost depends on phy's `::new()`, which is out of scope to inspect here; flagged as a structural smell at the call sites in scope).
- **Effort:** Small (hoist the floor to a constructed-once value the entries borrow).
- **Verification:** Bench a loop of N decodes constructing-vs-reusing the floor under `criterion`.

### Examined and found clean
- **`compute_ber` (`tuxmodem-rx:281-293`):** single O(n) pass over `min(len)` bytes, `count_ones()` popcount per byte, no per-iteration allocation, no accidental quadratic. Correct and tight. No finding.
- **Arg parsing (`tx:466-536`, `rx:383-455`):** linear single pass over argv; `.clone()` / `to_string()` on matched option values are O(argv), unavoidable and trivial. No finding.
- **`resolve_payload` / `resolve_expected` (`tx:173-183`, `rx:148-158`):** `arg.as_bytes().to_vec()` is one necessary copy; `std::fs::read` for `@path` is one bounded read. No finding.
- **`run_transmission` lead-in loop (`tx:351-361`):** bounded 20 ms-step sleep loop over a ~100 ms lead-in (≤5 iterations); not a per-sample loop. No finding.
- **`AirtimeBudget` (`tx:238-273`):** pure `Duration` arithmetic. No finding.
- **`encode_payload` / `decode_one_symbol` dispatch:** straight match-and-delegate to phy; no buffer copies beyond the single `AudioBuffer::from_samples(samples)` move at `tx:218` (a move, not a clone). No finding.

---

## Suspected Bugs

None. (Note for the correctness lane, not chased here: `BerReport::is_clean()` at `rx:263-264` returns `true` for two empty inputs while `ber()` returns `NaN` — a vacuous-clean edge, behaviorally intentional per the `ber_empty_inputs_yield_nan_ber` test; not a perf issue.)
