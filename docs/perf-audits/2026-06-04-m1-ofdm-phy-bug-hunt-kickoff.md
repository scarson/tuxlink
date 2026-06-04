# Bug-hunt kickoff — suspected bugs from the 2026-06-04 M1 (tuxmodem-phy) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `tuxmodem-phy` synchronization + sync/timing — specifically
`sync/preamble.rs`, `sync/symbol_timing.rs`, and `audio_device.rs` (the
real-time capture callback). These were noticed while auditing performance and
were NOT investigated.

**Seed findings (verify, don't trust — surfaced incidentally during a perf audit):**
- Preamble scan off-by-one — `sync/preamble.rs:88` — `0..(signal.len()-n)` looks exclusive of the last valid alignment offset (should likely be inclusive); masked by the ±2-sample test tolerance.
- Gardner normaliser bias — `sync/symbol_timing.rs:43-44` — `mean_energy` appears to include leading silence, biasing the timing normaliser low.
- RT-thread panic on poisoning — `audio_device.rs:538` — `acc_cb.lock().unwrap()` on the cpal real-time callback can panic if a consumer panicked while holding the lock, aborting the stream.

These are leads for the hunters, not confirmed bugs.
