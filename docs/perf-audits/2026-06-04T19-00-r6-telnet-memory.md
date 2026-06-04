# Perf Audit R6 — Winlink Telnet Transport (Memory & Allocation)

- **Agent:** glade-knoll-shoal
- **Date:** 2026-06-04T19-00
- **Dimension:** Memory & allocation (lane `memory`), per the rust.md profile pack
- **Scope:** `src-tauri/src/winlink/{telnet.rs, telnet_listen.rs, telnet_p2p.rs, telnet_p2p_login.rs, relay_banner.rs}`
- **Load calibration:** LOW — per-session, turn-based wire glue. One login + one B2F handshake per connection; the only per-byte hot path is the WireTap, and even that is line-framed, not chunk-allocating.

## What was examined

Read all five files in full (production + tests). Traced allocation sites along the connect → login → exchange path: the `WireTap` per-line buffer, `connect_with_deadline`'s error accumulation, the single-byte `read_cr_terminated_line` / `read_line_with_eol` loops, banner classification (zero-alloc `starts_with`/`contains`), and the `format!`/`to_string`/`clone` calls in the login and listener prompt/reject paths.

## Findings

### [MINOR] `read_line_with_eol` allocates a fresh `Vec` per line without `with_capacity`

- **Location:** `telnet_p2p_login.rs:45` (`let mut buf = Vec::new();`), contrast `telnet_listen.rs:546` which uses `Vec::with_capacity(64)`.
- **Problem:** Each login line starts at zero capacity and reallocates as it grows (push-doubling). The sibling listener reader pre-sizes to 64; the dialer reader does not. Lines are short (callsign, "Password :", one B2F handshake line), so this is 1-2 small reallocs per line over ~3 lines per dial.
- **Impact:** LOW. A handful of small reallocations once per dial session.
- **Confidence:** High (factual). **Effort:** Trivial.
- **Verification:** Mirror the listener: `Vec::with_capacity(64)`.

### [MINOR] Single-byte `reader.read()` loop on a `BufReader` is per-byte call overhead, not throughput-bound

- **Location:** `telnet_listen.rs:548-549` and `telnet_p2p_login.rs:47-48` — both loop `read(&mut [0u8; 1])`.
- **Problem:** Reading one byte at a time issues a `BufRead::read` call per byte. Not an *allocation* issue (the underlying `BufReader` buffers the syscall), so it is out of this dimension's core lane; noted only because the lane brushes I/O buffering. `BufRead::read_until(b'\r', ...)` would do it in one call but would also swallow the `\n`/NUL-skip semantics these loops deliberately implement, so the current form is justified.
- **Impact:** LOW. Lines are tens of bytes; per-byte dispatch is negligible at this load.
- **Confidence:** Medium. **Effort:** N/A (not recommended — semantics depend on the byte-wise filter).
- **Verification:** None warranted.

## Non-findings (examined, deliberately not flagged)

- **`WireTap` line buffer** (`telnet.rs:112,135`): uses `std::mem::take` to reset, which preserves no capacity but is correct; `format!` per logged line (`telnet.rs:145,148`) is a logging path, not protocol hot path — acceptable.
- **`format!("{mycall}\r")` / `format!("{CMS_TELNET_PASSWORD}\r")`** (`telnet.rs:354,358`): two throwaway Strings per login, once per session. Could be `write!`-into-writer but the saving is two allocations per connection — not worth it. Same for `write!(writer, "{}\r", ...)` in `telnet_p2p_login.rs:112,129`, which already avoids the String.
- **`connect_with_deadline` error `Vec<String>`** (`telnet.rs:308`): grows only on the failure path, bounded by resolved-address count; cold.
- **`callsign_raw.clone()` / `claimed.clone()` / `config.clone()`** (`telnet_listen.rs:321,431` etc.): once per session, small structs; idiomatic and not hot.
- **`relay_banner.rs`**: pure `&str` `starts_with`/`contains` — zero allocations. Clean.
- **`PushbackReader.drain(..n)`** (`telnet_p2p.rs:101`): drains a tiny pushback buffer once; negligible.

## Suspected Bugs

None.
