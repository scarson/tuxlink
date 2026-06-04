# Performance Audit — Concurrency & Parallelization

**Crate:** `tuxmodem-phy` (`/home/user/tuxlink/tuxmodem/crates/tuxmodem-phy/src`)
**Dimension:** concurrency & parallelization ONLY
**Auditor:** glade-knoll-shoal
**Date:** 2026-06-04
**Scope read:** all `.rs` except `src/bin/`; `audio_device.rs` (the entire cross-thread surface) read line-by-line; `audio_io.rs`, `phy_api.rs`, and the `ofdm_main/`, `robustness_floor/`, `sync/` modules scanned for any thread/lock/channel/atomic usage (grep-confirmed: none outside `audio_device.rs`).

## Architectural framing (governs every impact rating below)

The ONLY cross-thread synchronization in the crate lives in `audio_device.rs`. Two cpal callbacks exist:

- **Output callback** (`audio_device.rs:279-299`): owns `frames: Vec<f32>` and `cursor: usize` exclusively (moved into the closure). No lock, no allocation, no syscall on the hot path — only an `mpsc::Sender::send(())` fired ONCE when the cursor reaches the end. This is a correct lock-free RT callback. No finding.
- **Input callback** (`audio_device.rs:536-548`): takes `Arc<Mutex<Vec<f32>>>::lock()` on EVERY invocation and pushes channel-0 samples under the guard. This is the one real concurrency hazard in the crate.

Both audio paths are **batch-oriented, not streaming**: `record_blocking_with_abort` accumulates a full `target_samples` buffer, drops the cpal stream, then hands the whole buffer to the demod (`WidebandLowDensityFloor::receive`). There is NO concurrent producer/consumer pipeline running the OFDM demod symbol-by-symbol against a live ring buffer. The DSP modules (`ofdm_main`, `robustness_floor`, `sync`) are single-threaded and run AFTER capture completes. This means:

- There is no EXPLOIT (parallelization) finding worth making in the hot path: the per-symbol/per-subcarrier DSP work runs off the RT thread entirely, on its own, after capture. Adding rayon to parallelize independent subcarrier work is a separate (algorithmic/throughput) dimension's call, not concurrency's, and the crate has no rayon dep — out of scope and not RT-reachable.
- The concurrency risk is concentrated entirely in whether the input callback can miss its RT deadline. That is the finding below.

---

## Findings

### [MAJOR] Input-capture RT callback acquires a blocking `std::sync::Mutex` on every device callback and holds it across the de-interleave copy loop

**Location:** `audio_device.rs:538` (lock acquire in callback) + `:542-547` (copy loop under guard); consumer side `:573-577` and `:583`.

**Problem:** The cpal input callback runs on a hard-real-time audio thread firing at the device-buffer rate (~100+×/sec at 48 kHz). On each fire it does `let mut guard = acc_cb.lock().unwrap();` (line 538) — a `std::sync::Mutex`, whose `lock()` parks the calling thread on a futex when the lock is contended. The guard is then held across the entire `for frame in samples.chunks_exact(channels) { guard.push(frame[0]); }` copy loop (lines 542-547), so the critical section spans the full per-callback sample copy, not just a pointer swap. The consumer thread contends for the same lock every poll: it locks to read `guard.len()` at line 573-574 (every ~20 ms, line 587), and again at line 583 in the timeout path. Two RT-callback anti-patterns are present simultaneously:

1. **Blocking lock in an RT callback.** If the consumer holds the lock at the instant the callback fires, the callback parks (futex syscall) until the consumer releases. The consumer's hold is short (a `.len()` read), so the window is small — but on an RT audio thread *any* unbounded park is a deadline hazard: a scheduler hiccup or priority inversion while the consumer holds the lock stalls the callback past its buffer deadline → capture overrun → dropped/duplicated input samples → corrupted demod input → frame decode failure → retransmission. The lock-free output path (lines 279-299) demonstrates the project already knows how to write an allocation-free, lock-free RT callback; the input path does not match that bar.
2. **`.unwrap()` panic in the RT callback (line 538).** If the consumer thread panics anywhere while holding (or after poisoning) the mutex, the next callback's `lock().unwrap()` panics on the RT thread. A panic in a cpal callback aborts the stream. This is a correctness-adjacent reliability hazard on the RT path; recorded again under Suspected Bugs.

**Impact:** reachability = the live receive path (every RX capture goes through this callback); frequency = ~100+ callback fires/sec for the full capture duration of every frame; per-occurrence cost = normally a single uncontended atomic CAS (cheap), but the tail event — a park under contention or a priority-inversion stall on the RT thread — costs a missed audio deadline, which is the exact failure mode (underrun/overrun) that corrupts a frame and forces retransmission. The expected-case cost is low; the tail-risk cost is a whole-frame loss. For a modem on a marginal HF channel, retransmissions are the dominant throughput killer, so even rare corruption is expensive.

**Confidence:** Strong-static. The lock acquisition and the held-across-copy critical section are directly in source. That the contention window is small (consumer holds only for `.len()`) is also static-evident, which is why this is MAJOR not CRITICAL — under the current consumer it is unlikely to bite often, but the *pattern* is a latent RT-deadline violation that gets worse the moment anyone makes the consumer's critical section heavier.

**Effort:** Contained (+low). Two viable fixes, both keep the batch-capture semantics:

- **Preferred — lock-free, mirror the output path.** Replace the `Arc<Mutex<Vec<f32>>>` accumulator + 20 ms poll with an SPSC ring (a fixed-capacity `rtrb`/`ringbuf` SPSC queue, or a hand-rolled `Arc<[UnsafeCell<f32>]>` + two `AtomicUsize` head/tail). The callback does a wait-free push of its chunk; the consumer drains on its poll and counts toward `target_samples`. No lock ever touches the RT thread. This adds one small dependency (`ringbuf`/`rtrb`) — weigh that against the no-new-dep option below.
- **No-new-dep — shrink the contention window + de-risk the panic.** Keep the `Mutex` but (a) in the callback, replace `.lock().unwrap()` with `.try_lock()`; on contention, drop the chunk into a small callback-owned staging `Vec` (pre-allocated, capacity = max device buffer) and flush it the next time the lock is free, so the RT thread NEVER parks; (b) on the consumer side, never hold the lock across anything but the `.len()`/drain. This is strictly weaker than the SPSC ring (it can still drop samples under sustained contention) but needs no new crate. Given the batch model and short consumer hold, even just switching the callback to `try_lock` and accepting "skip this chunk if contended is impossible because we drop" is wrong — staging is required so samples aren't lost.

**Verification plan:**
- *Argument for correctness of the SPSC swap:* the producer (cpal callback) and consumer (`record_blocking_with_abort` poll loop) are a strict single-producer/single-consumer pair — exactly one cpal stream, exactly one polling thread, the stream is dropped (line 590) before `Arc::try_unwrap` (line 591) reads the final buffer. An SPSC ring's wait-free push/pop is sound here precisely because there is one of each; the existing `Arc::try_unwrap` already asserts no other strong ref survives stream drop, which is the same single-consumer invariant. Guard: keep the post-drain `samples.truncate(target_samples)` (line 599) so an over-shoot chunk can't lengthen the result; assert `producer`/`consumer` handles are not `Clone`d anywhere (the ring crates enforce this at the type level — `Producer`/`Consumer` are `!Clone`), which statically prevents accidentally introducing a second producer.
- *Benchmark:* drive the input callback at the device rate with a synthetic cpal-less harness (a loop calling the closure with a fixed `&[f32]` chunk N×/sec) while a second thread runs the consumer poll; measure max producer-side stall (instrument with `Instant::now()` deltas around the push, NOT wall-clock as a metric — count the number of pushes that exceeded one buffer-period). Compare Mutex vs SPSC: the Mutex variant should show a non-zero count of stalls under induced contention (have the consumer hold the lock artificially); the SPSC variant should show zero. The correctness guard is that total samples delivered to the demod is identical between the two variants for the same input.

---

## Non-findings examined (so the next auditor doesn't re-chase them)

- **Output callback (`:279-299`)** — lock-free, allocation-free, single terminal `send`. Correct RT pattern. No finding.
- **Input callback reallocation** — `acc` is pre-sized `Vec::with_capacity(target_samples)` (line 528) and the push loop bounds-checks `guard.len() >= target_samples` *before each push* (lines 539, 543), so it cannot grow past capacity → no allocation on the RT thread from the `Vec`. (The `lock()` itself is the RT concern, not allocation.) No separate allocation finding.
- **`std::thread::sleep` poll loops** (`:351` tail-drain, `:587` capture poll, `:319-346` output poll via `recv_timeout`) — these run on the *caller's* thread, not the RT audio thread. They are sleep-poll, not busy-wait (20 ms / 100 ms sleeps, not spin). Sleep-poll on a non-RT control thread at 20 ms cadence is a cold-path latency choice, not a hot-path perf defect — CALIBRATED OUT.
- **`mpsc` unbounded channels** (`:269-270`, `:530`) — used only for one-shot `done`/`err` signalling (at most a handful of sends per playback), never as a sample pipeline. No backpressure/drop concern. No finding.
- **`Arc<Mutex>` vs `parking_lot`** — per the profile pack, do not assume `parking_lot` wins; and the real fix here is to remove the lock from the RT path entirely (SPSC), not to swap mutex implementations. Swapping to `parking_lot` would NOT fix the RT-park hazard. Deliberately not recommended.
- **DSP modules (`ofdm_main`, `robustness_floor`, `sync`, `subcarrier_snr`, `coded_modulation`, `constellations`, `modes`, `equalizer`, `bit_loader`)** — grep-confirmed zero threads/locks/channels/atomics. Single-threaded, run off the RT thread post-capture. No concurrency surface. (Subcarrier-parallel EXPLOIT opportunities, if any, belong to the algorithmic/throughput dimension and would require adding rayon — out of scope here and not RT-reachable.)

---

## Suspected Bugs (for follow-up)

1. **`acc_cb.lock().unwrap()` in the RT input callback (`audio_device.rs:538`) can panic the audio thread on mutex poisoning.** If the consumer thread panics while holding the lock (or any panic poisons it), the next callback's `.unwrap()` panics on the cpal RT thread, aborting the stream. Prefer `try_lock` / a poison-tolerant access, or eliminate the lock (see the MAJOR finding's SPSC fix). Filed as concurrency-adjacent because it lives on the RT path, but it is a reliability bug, not a throughput finding — hence here, not above.
