# W0 — Winlink call-frequency map (pre-artifact for R3/R8/R10)

**Why this exists:** Round 4 of the scope review found that the winlink data
path's *frequency-establishing callers* live in **different slices** from the
*impls* they drive — so a per-slice audit of the impl (R3) can't see how often
it runs, and would mis-rank Impact. This map hands the missing frequency context
to the R3 (compression + B2F assembly), R8 (session driver), and R10 (storage)
runs as **adjacent context**. Verified against source 2026-06-04.

## Outbound (compression) path
- **Driver:** `winlink_backend.rs:229 build_outbound_proposals` loops the ENTIRE
  Outbox — `for meta in mailbox.list(Outbox)` (`:233`) → `mailbox.read` (`:239`)
  → `message.to_proposal()` (`:250`).
- **Impl:** `message.rs:158 to_proposal` → `lzhuf::compress(&bytes)` (`message.rs:161`).
- **Frequency:** **once per Outbox message, per connect** — the whole Outbox is
  compressed to build proposals at the start of each session.
- **Per-occurrence size:** the full message body + attachments (KB to tens of KB).
- **Slice bridge:** the driver is in the **cold sweep** (`winlink_backend.rs`);
  the impl is **R3** (`lzhuf.rs`/`message.rs`). → **R3 must rank lzhuf::compress
  at "per-Outbox-message × per-connect" frequency over KB-scale bodies**, NOT as
  an unreached library function.
- **Watch (for R3/R10):** is the proposal/compression result **cached across
  connects**, or is the entire Outbox **re-compressed on every connection
  attempt** (including already-proposed/rejected messages)? If recompressed each
  connect, that's the real aggregate cost for a station with a backlog — confirm
  in `winlink_backend.rs` + the mailbox/storage layer (R10).

## Inbound (decompression) path
- **Driver:** `session.rs:396 receive_turn` — per received block:
  `transfer::read_block(reader)` (`:467`) + `lzhuf::decompress(&block.data)`
  (`:468`) + `Message::from_bytes`.
- **Impls:** `transfer.rs:100 read_block` (the per-byte `read_exact` loop, H11)
  and `lzhuf::decompress` — both **R3**.
- **Frequency:** **once per received message** (per accepted inbound proposal).
- **Per-occurrence size:** the compressed block (KB), decompressed to the full body.
- **Slice bridge:** driver **R8** (`session.rs`), impls **R3**. → **R8 establishes
  R3's per-received-message frequency**; this is why the execution order runs
  **R8 before R3**.

## Framing path
- **Driver:** `session.rs:384 send_turn` → `transfer::frame_block(title, offset, data)`
  per sent block (data is the ALREADY-compressed bytes from `to_proposal` — note
  **compression is done once** in build_outbound_proposals, NOT re-done per turn;
  no double-compress).
- **Impl:** `transfer.rs frame_block` — **R3**, per sent message.

## Frequency table (hand to R3, R8, R10)

| Impl (slice) | Driver (slice) | Frequency | Per-occurrence |
|---|---|---|---|
| `lzhuf::compress` (R3, `message.rs:161`) | `build_outbound_proposals` Outbox loop (cold sweep, `winlink_backend.rs:233,250`) | per Outbox msg × per connect | full body, KB–tens of KB |
| `lzhuf::decompress` (R3, `session.rs:468`) | `receive_turn` (R8, `session.rs:396`) | per received msg | compressed block, KB |
| `transfer::read_block` per-byte loop (R3/H11, `transfer.rs:100`) | `receive_turn` (R8, `session.rs:467`) | per received msg | compressed block bytes |
| `transfer::frame_block` (R3) | `send_turn` (R8, `session.rs:384`) | per sent msg | block |

## Overall calibration note
Winlink **sessions are infrequent** (a user connects periodically) and message
counts are **modest** (an Outbox is usually a handful, occasionally dozens of
messages); HF transfer is slow. So these impls are **bounded-frequency**
(per-message × per-session), NOT tight inner loops — R3's findings should be
ranked accordingly (the lzhuf algorithm itself runs on fixed-size arrays; its
aggregate cost is |mailbox| per connect, not per-byte-of-a-stream). The one
thing that could elevate it is the "re-compress the whole Outbox every connect"
question above — R3/R10 should resolve it.
