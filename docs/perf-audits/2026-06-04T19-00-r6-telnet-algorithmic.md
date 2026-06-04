# Perf audit R6 — Winlink telnet transport — algorithmic complexity

Agent: glade-knoll-shoal
Dimension: algorithmic complexity (O(n²) line/buffer reassembly, repeated
string scans/builds, per-byte relays). Impact ceiling: LOW (cold transport glue,
one telnet session per connect, line-based control + modest bulk relay).
Scope: `src-tauri/src/winlink/{telnet.rs,telnet_listen.rs,telnet_p2p.rs,telnet_p2p_login.rs,relay_banner.rs}`.

## Result

**No significant findings.** No O(n²) buffer reassembly, no invariant
recomputation hoistable out of a loop, no unbounded per-byte relay growth on a
hot path.

## What I examined

- **`read_cr_terminated_line`** (`telnet_listen.rs:543`) and **`read_line_with_eol`**
  (`telnet_p2p_login.rs:44`): both read one byte at a time into a `Vec` via
  `reader.read(&mut byte)`. The reader is a `BufReader`, so each call is an
  in-memory copy, not a syscall; `Vec::push` is amortized O(1); the line is
  bounded (4 KiB cap at `telnet_listen.rs:564`; short login lines). Total work is
  O(line length) — no re-scan of a growing buffer, so no quadratic. Cold login
  path (a handful of CALLSIGN/Password lines per session).

- **`PushbackReader::read`** (`telnet_p2p.rs:97`): `self.pushback.drain(..n)`
  shifts remaining bytes left, O(remaining) per call. `pushback` is a single
  short B2F handshake line (~30 bytes), normally drained in one read. Not a
  growing-buffer quadratic; not on a bulk path.

- **`WireTap::observe`/`flush`** (`telnet.rs:120-150`): per-byte framing on `\r`;
  `flush` does one ASCII scan + `from_utf8_lossy` + `clean_line` + `format!` per
  completed line. Work is linear in line length; binary payloads short-circuit to
  a byte-count summary (no full re-encode). Bounded per protocol line; no
  accumulation across lines.

- **`classify_banner_line`** (`relay_banner.rs:92`): a fixed, constant number of
  `starts_with`/`contains` probes over one short banner line, called per-line by
  the caller threading state. O(line length) per call, no growing accumulator,
  no repeated build.

- **`connect_with_deadline`** (`telnet.rs:303`), `connect_stream`
  (`telnet_p2p.rs:107`), `resolve_*`: iterate a small fixed resolved-address list
  once; `errors.join("; ")` once on the failure path. No nested scans.

- **`parse_telnet_callsign`** (`telnet_listen.rs:586`): a constant number of
  `trim`/`to_uppercase`/`strip_suffix`/`rsplit_once`/`parse` over a short
  callsign. Constant scans, no loop.

Every line/banner/login routine is bounded per protocol line and called a small
number of times per session. The brief's calibration (cold glue, LOW) holds:
manufacturing a nit here would be padding.

## Suspected Bugs

None.
