# ARDOP modem — algorithmic complexity & data-structures audit (M3, reduced-depth)

Agent: glade-knoll-shoal
Date: 2026-06-04T16:30Z
Scope: `src-tauri/src/winlink/modem/ardop/{data,transport,session,b2f,frame,command,arq_state,wire,listener}.rs`
Lens: `performance-audit/profile-packs/rust.md` (algorithmic lane + Runtime notes)
Calibration: ARQ over HF (200–2000 Hz BW), low byte rates, KB–low-MB transfers, status polling ~4 Hz.

Problems only. Impact is calibrated to the realistic low-HF load.

---

### [MINOR] `DataDecoder::next_frame` drains a `Vec` prefix per frame — O(n) shift each, quadratic on a multi-frame backlog

**Location:** `frame.rs:60-94` (`self.buf.drain(..total)` at line 91, `self.buf.drain(..2)` at line 74), fed by `frame.rs:51-53` (`push` → `extend_from_slice`).

**Problem:** `DataDecoder` holds inbound bytes in a `Vec<u8>` (`buf`). Each completed frame is removed with `self.buf.drain(..total)`, which is O(remaining-len): every trailing byte is memmoved to the front. When one `push` deposits a backlog of K frames totalling N bytes (the common case — `data.rs:266` pushes a 4096-byte socket chunk, then `pump_decoder` at `data.rs:135` loops `next_frame` to exhaustion), draining frame 1 shifts ~N bytes, frame 2 shifts ~N−f₁, etc. → O(N·K) ≈ O(N²) over the chunk. The length field is read at a fixed offset (`buf[0..2]`), so there is NO delimiter re-scan — this is the drain cost, not the classic search-from-start footgun. The re-sync path (`drain(..2)`, line 74) has the same shift cost but only fires on corrupt frames (never on loopback TCP).

**Impact:** Bounded and modest at HF rates. The `Vec` only ever holds what one `read` chunk delivered (≤4096 from `data.rs:258`, or larger only if the OS coalesces). Frame payloads are small (ARQ BW 200–2000 Hz), so K per chunk is small and N is a few KB. The quadratic is real but the constant is tiny; on a 4 KB chunk with ~20 frames it is low-thousands of byte-moves per `read` — negligible at HF. It would only matter if a single `push` ever carried hundreds of KB of many small frames, which the 4096-byte read cap prevents.

**Confidence:** High (mechanism); High (low impact at calibrated load).

**Effort:** Low. Two clean fixes: (a) replace `buf: Vec<u8>` with `VecDeque<u8>` and use `drain(..total)` / `read` from the front (pop is amortized O(1), and `as_slices` lets you peek the header); or (b) keep `Vec` but track a `consumed: usize` cursor, advancing it instead of draining, and only compact (`buf.drain(..consumed)`) when `consumed` exceeds a threshold (e.g. half the buffer). (b) is the smaller diff and removes the per-frame shift entirely.

**Verification:** Add a bench (`criterion`, release build) that pushes one buffer of M small ARQ frames in a single `push` and times `next_frame`-to-exhaustion for M ∈ {16, 256, 4096}; confirm the cursor/`VecDeque` variant stays linear in total bytes while the current `drain` variant grows super-linearly in M.

---

### [MINOR] `DataSocket::drain_leftover` copies a `VecDeque` byte-by-byte via a zip loop

**Location:** `data.rs:104-110`.

**Problem:** `drain_leftover` moves up to `buf.len()` bytes out of the `leftover: VecDeque<u8>` with an element-wise `for (dst, src) in buf[..n].iter_mut().zip(self.leftover.drain(..n))` loop — one bounds-checked store per byte. A bulk path exists: `VecDeque` is contiguous-or-two-slices, so `as_slices()` + `copy_from_slice` (or `Read::read` which `VecDeque<u8>` implements directly via `std::io::Read for VecDeque<u8>`) does the same move with one or two `memcpy`s. This is the per-byte-loop-where-a-bulk-op-fits pattern the dimension brief calls out at `data.rs:104`.

**Impact:** Low at HF. `n` is bounded by the caller's `buf.len()` (B2F reads via a `BufReader`, typically 8 KB) and the bytes are already in cache. Per-byte vs `memcpy` is a small constant factor on a few-KB move that happens at most once per `read`. Not a quadratic; just leaves throughput on the table on larger transfers.

**Confidence:** High (mechanism); High (low impact).

**Effort:** Low. `let n = buf.len().min(self.leftover.len()); let (a, b) = self.leftover.as_slices();` then copy from `a`/`b` into `buf[..n]` and `self.leftover.drain(..n)` — or simply `Read::read(&mut self.leftover, buf)` and return its count (it advances the deque internally). Note: `read` on `&mut VecDeque<u8>` already does the bulk copy, so the whole helper can become a one-liner.

**Verification:** Microbench the move of a 4 KB `VecDeque` into a `[u8]` buffer, current loop vs `as_slices`+`copy_from_slice`, release build; expect the bulk form to be several× faster per call (small absolute time either way).

---

### Notes on areas examined and cleared

- **`wire.rs` cmd-line framing:** the production cmd reader (`session.rs:81`, `reader.read_until(b'\r', &mut buf)` with `buf.clear()` each iteration) does NOT re-scan a growing buffer — `BufRead::read_until` scans only newly-buffered bytes and the workhorse `buf` is cleared+reused per line (good: capacity-preserving). The `feed_and_drain` re-scan-from-start helper at `wire.rs:64-71` (`buf.iter().position(...)` in a loop with `drain`) IS an O(n²) reassembly pattern, but it is test-only code — not on any hot path. Not a finding.
- **`session.rs` event loops** (`set_and_ack`, `arq_connect`, `arq_disconnect`, listener `serve_inbound_one`/`set_listen`): bounded `match` over a single channel `recv`; no scans of growing collections, no per-iteration recomputation. Clean.
- **`transport.rs` status-tick** (`drain_status_events` → `record_buffer` / `current_throughput_bps`): the `throughput_samples: VecDeque` is pruned from the front with `pop_front` (amortized O(1)) and indexed at `[0]` only; window is tiny (5 s of BUFFER drops). `populate_derived_meters` is O(1). No recomputation-in-loop or wrong-container issues. The tick is capped at `MAX_DRAIN_EVENTS_PER_TICK = 64` (transport.rs:705). Clean.
- **`command.rs` parse:** per-line `splitn`/`split_whitespace`, all O(line-len) on short ASCII lines. No accidental quadratics.
- **`arq_state.rs`:** atomics only; O(1). Clean.
- **`listener.rs`:** allowlist gating delegated to the shared `listener_decide` (out of scope); local code is O(1) parsing. Clean.
- **Containers:** `leftover` as `VecDeque` is the right choice (front-drain). The decoder's `buf` as `Vec` is the one suboptimal container (finding 1).

---

## Suspected Bugs

None. (No correctness issues observed within the algorithmic-dimension reading; the `drain(..2)` re-sync guard, EOF-on-DISC gate, and BUFFER delta accounting all looked internally consistent. Correctness was not the audit target.)
