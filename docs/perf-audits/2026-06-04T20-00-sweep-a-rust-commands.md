# Cold-Sweep A — Rust IPC commands + winlink backend orchestrator

Agent: glade-knoll-shoal
Date: 2026-06-04T20:00
Scope: `src-tauri/src/ui_commands.rs` (6093 LOC) + `src-tauri/src/winlink_backend.rs` (3262 LOC)
Mode: COLD-SWEEP — catch warm-in-cold; do not nitpick cold glue.
Pre-excluded (do not re-report): `build_outbound_proposals` Outbox→lzhuf loop (W0/R3); vara write-half unbuffered (R4-1).

## Method

grep + targeted reads across: fs/IO (`read_config`, `fs::read*`, `read_dir`, `fs::write`), poll/tick/broadcast/emit fns, per-message/per-folder/per-contact loops, clones on command paths, and the AX25/B2F read loops. Command groups scanned: mailbox list/read/move/mark (`mailbox_list`, `read_folder`, parse_message §Task13), allowlist arm/disarm contact loops, serial-device enumeration, config read/write commands, ARDOP + VARA inbound listener loops, packet/CMS native-exchange orchestration, session-log emit, P2P status emit.

## Findings

### No significant warm findings — confirmed cold

Every `read_config()` call site (1057, 1228, 1617, 1945, 2345, 3536, 3578, 3624, 4236, 4289, etc.) sits on a user-triggered command or a per-*connection* path. Notably the two inside listener loops (`ui_commands.rs:2571` ARDOP, `ui_commands.rs:3129` VARA) fire only on a connection **Accept**, not per 1-second poll tick — the tick body (`serve_inbound_one` / `wait_for_listener_connect`, 1s budget) does no heavy work and the config read is amortised across an entire session. The cross-ref worry from R10-6 (uncached `read_config` on a hot path) does **not** reach a hot path from these two files; the hottest invocation is once-per-inbound-link.

`mailbox_list` (`ui_commands.rs:216`) → `backend.list_messages` (`winlink_backend.rs:841`) is a thin delegate to `native_mailbox` (out of scope) and is user-view-triggered, bounded by CMS message-size limits. The per-message `MessageMetaDto::from` map (`ui_commands.rs:229`) and `parse_message` (§Task13) run on a folder-open click, not a tick — cold.

`emit_session_line` (`ui_commands.rs:1139`) clones one `LogLine` per emitted line; lines emit at protocol/progress cadence, not per-byte — cheap.

`BlockingB2fStream::read` (`winlink_backend.rs:1238`) loops but is an idle-nap poll (delegates to `Ax25Stream::read` which sleeps a poll interval), not a busy-spin; documented as such in-line.

Connect-setup `.clone()` cluster (`winlink_backend.rs:971–1138`) is per-connect Arc/handle cloning — once per dial, cold.

Allowlist arm/disarm loops (`ui_commands.rs:2213, 2828, 3287, 4139, 4171`) iterate `callsigns()` + `ips()` — bounded small collections (operator allowlist), user-command-triggered, no nesting → no O(n²).

`serial enumerate` (`ui_commands.rs:1681` `read_dir`) is a settings-screen command, not a tick.

## Suspected Bugs

None.
