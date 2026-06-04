---
run_schema_version: 1
run_id: 2026-06-04T18-00-r3-compression
date: 2026-06-04T18:00:00Z
scope: "R3 — winlink compression + B2F assembly (lzhuf.rs, message.rs, compose.rs, proposal.rs, transfer.rs, wire.rs)"
methodology: { skill: performance-audit (REDUCED depth), plugin_version: superpowers-plus@0.2.0 }
dispatch: { model_requested: "latest-opus (Agent subagents)", reasoning_effort: "default (no harness knob)", overridden_by_user: false }
stack: [{ ecosystem: crates, framework: rust-std, version: "hand-rolled LZHUF, no compression lib" }]
currency_briefs: []
lanes_run: [algorithmic, memory, data-access]
lanes_skipped: { concurrency: "synchronous, no shared state", idiom-currency: "hand-rolled algo, no library idiom surface", cost-map: "reduced depth", payload-startup: "n/a", dynamic: "needs a corpus harness; not run" }
finding_counts: { by_impact: { critical: 0, major: 2, minor: 5 }, by_lane: { algorithmic: 2, memory: 5, data-access: 2 }, suspected_bugs: 0 }
regression: { prev_run_id: null, new: 7, persisting: 0, resolved: 0 }
adjacent_context: "W0 winlink call-frequency map; R8 surfaced transfer.rs read_block/frame_block (now owned here)"
---
# Performance Audit (REDUCED) — R3: winlink compression + B2F assembly

**Date:** 2026-06-04 18:00   **Scope:** `src-tauri/src/winlink/{lzhuf.rs,message.rs,compose.rs,proposal.rs,transfer.rs,wire.rs}`
**Depth:** REDUCED (algorithmic, memory, data-access)
**Frequency (W0):** `lzhuf::compress` per Outbox message per connect; `decompress`+`read_block` per received message; bodies KB–tens of KB; sessions infrequent, modest message counts
**Regression vs none (first run):** 7 new

> **W0 payoff + algorithm vindication.** This is the impl slice whose frequency
> W0 established and whose `transfer.rs` allocations R8 surfaced. Two precise
> results: (1) the algorithmic lane **confirmed `lzhuf::compress`'s match-finder
> is the documented Okumura per-leading-byte BST** (`insert_node`/`delete_node`,
> `lzhuf.rs:415-508`), NOT a linear O(n·window) rescan — the algorithm is
> correctly built, no finding. (2) the data-access lane **refined the H11
> per-byte-read concern**: it is NOT a syscall storm in practice — every
> production caller wraps a `BufReader` (verified: `session.rs:467`,
> `winlink_backend.rs:1297,2050`, `telnet.rs:202`, `b2f.rs:105`,
> `telnet_p2p.rs:155`), so each read is a memcpy from an 8 KiB buffer. The real
> wins are pre-sizing + encoding the buffering contract.

## Major findings

### R3-1. `read_block` builds the body via per-byte push into a zero-capacity `Vec`
**Lanes:** memory (MAJOR), data-access, algorithmic   **Location:** `transfer.rs:90-105` (+ `read_u8` `:124-130`)
**Fingerprint:** `memory:transfer.rs:read_block:per-byte-push` (the H11 area)   **Problem:** every received-message body byte is appended to a zero-capacity `Vec` → N pushes + ~log₂N doubling reallocs per message. The per-STX chunk length is known at `:96-99` and the proposal carries `compressed_size`. **Fix:** `read_exact` one chunk into a reused buffer + `extend`, and/or pre-size `data` from `compressed_size`. **Confidence:** Strong-static   **Effort:** Localized. **Verification:** alloc-count per received message before/after; identical bytes.

### R3-2. `frame_block` assembles the whole send frame in an unsized `Vec`
**Lanes:** memory (MAJOR), data-access   **Location:** `transfer.rs:47-74` (`:52`)
**Fingerprint:** `memory:transfer.rs:frame_block:unsized-vec`   **Problem:** final size is exactly computable (`2 + header_len + chunk_count*2 + data.len() + 2`) but the frame is grown by push/extend → the same realloc-doubling chain per sent message. **Fix:** `Vec::with_capacity(total)`. **Confidence:** Strong-static   **Effort:** Localized. (Write path itself is already bulk — one `write_all` per message; no write amplification here. The fragmented *control-token* writes are R8-1, a different spot.)

## Minor findings
- **R3-3** `read_block` bound is `R: Read`, not `R: BufRead` (`transfer.rs:78`) — the buffering contract isn't encoded in the type, so a future bare-`Read` caller would silently regress the per-byte loop to O(body) syscalls. **Tighten to `R: BufRead`** as a latent-regression guard. Fingerprint `data-access:transfer.rs:read_block:read-not-bufread`.
- **R3-4** `lzhuf::finish` copies the entire compressed bitstream into a throwaway `crc_input` `Vec` (`lzhuf.rs:628-636`) just to feed `fbb_crc`, then copies `self.out` again into `framed`. Make CRC incremental over `&size_bytes` + `&self.out` — removes one full-payload alloc+copy per compress. Fingerprint `memory:lzhuf.rs:finish:crc-input-copy`.
- **R3-5** `lzhuf` `Encoder.out` bitstream (`lzhuf.rs:388`) grows a byte or two at a time with no `with_capacity`; `compress` (`:642`) knows `input.len()` to seed it. Fingerprint `memory:lzhuf.rs:encoder-out:no-capacity`.
- **R3-6** `message.rs:301-303 find_subslice` is a naive `windows(4).position()` O(n·m) scan for the `\r\n\r\n` header terminator, called per received message (`:178`); early-terminates normally, worst case a full pass over the decompressed body. Constant-factor (vs `memchr::memmem`), not a blow-up. Fingerprint `algorithmic:message.rs:find_subslice:naive-scan`.
- **R3-7** `transfer.rs:119,132-141` `from_utf8_lossy(&title).into_owned()` copies the title a third time; `String::from_utf8(title)` consumes the Vec on the common ASCII path. Tiny. Fingerprint `memory:transfer.rs:read_block:title-copy`.

## Examined and cleared (anti-padding)
- `lzhuf` match BST + adaptive-Huffman `update`/`reconst`/`put_code`/`decode_char` — standard correct costs; the fixed ring buffer + Huffman tables are the RIGHT design (not flagged).
- CRC/checksum is **fused into the byte-consuming pass** (`transfer.rs:101-103`, `lzhuf::fbb_crc` single pass) — no redundant scan.
- `wire::read_line` uses buffered `read_until` correctly. `message.rs:205/227` `.to_vec()` are required owned-copy boundaries. Header sort + `retain` is tiny-n.

## Cross-cutting theme
The winlink transfer path has a consistent **"unsized growth + unencoded
buffering contract"** shape: pre-size `read_block`/`frame_block`/`lzhuf` output
buffers from their known/derivable sizes, and tighten `read_block` to `BufRead`.
Combined with R8-1 (coalesce control writes) and M3-3 (ARDOP BufWriter), the
winlink+modem transport layer's theme is **buffer/size the byte plumbing**;
the lzhuf *algorithm* itself is sound. No suspected bugs. No fix-plan this pass
(reduced depth) — these fold into a future winlink-tier remediation plan
alongside R8-1/R10.
