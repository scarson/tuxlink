# COLD-SWEEP B — Rust backends (algorithmic + allocation + data-access)

Agent: glade-knoll-shoal
Date: 2026-06-04T20:00Z
Mode: batched cold-sweep — hunting for warm items hiding in the cold batch, NOT glue nits.
Scope: `modem_commands.rs`, `modem_status.rs`, `forms/`, `grib/`, `position/`, `catalog/`,
`bootstrap.rs`, `wizard.rs`, `lib.rs`, `main.rs`, `app_backend.rs`, `compose_window.rs`,
`help_window.rs`, `tray.rs`, `consent_gate.rs`, `theme_state.rs`, `winlink/listener/`.

## Verdict: No significant findings — confirmed cold.

Every candidate hot path resolves to either operator-action-paced (one IPC command per
click), one-time-per-session work, or fixed-tiny work per tick. No per-tick heavy work,
no loop-over-all-entries on a hot path, no O(n²) over a growing collection, no per-call
config/file re-read on a hot loop.

## What was scanned (and why each is cold)

### modem_status broadcaster — the 4 Hz tick (the M3-noted hot path)
- `modem_status.rs:392` spawn loop → `tick_and_snapshot()` (`:165`).
- Per-tick work: ONE mutex lock, clone of the cached `ModemStatus` (a small fixed-size
  struct of scalars + a couple `Option<String>`), and a `drain_status_events()` call that
  the contract requires to be non-blocking (`:163`). No fs, no config read, no allocation
  over a collection. **Genuinely light per tick — confirmed cold.** M3's note stands; no
  escalation.

### winlink/listener gate (demoted R7) — per-connection path
- `decide.rs::listener_decide_at` (`:73`) takes `allowed` / `password` / `arms` BY REFERENCE.
  No fs read per connection. `AllowedStations::accept` (`allowed_stations.rs:201`) is a linear
  scan over operator-curated callsign/IP pattern lists (handful of entries) — trivial.
- All `fs::read` / `read_to_string` in the listener tree are in `#[cfg(test)]` or one-time
  `load_from` (`allowed_stations.rs:225`, `arms_record.rs:184`), called by the caller at
  arm/config time, not per inbound peer. `station_password`/`decide` keyring `.lock()` calls
  are short HashMap ops. **Confirmed cold.**

### catalog/ — parser + composer + commands
- `parser.rs:51` iterates input lines once (single-pass parse). `composer.rs:51` iterates a
  caller-supplied filename slice once to build a request STRING (already confirmed cold by
  prior note). `commands.rs:45` one `.collect()` of `&str` refs. No nested iteration over a
  growing set. **Confirmed cold.**

### forms/ — http_server + wle_templates
- `http_server.rs:137` reads the template file ONCE per `FormSession::open` (one per
  form-open user action), pre-substitutes HTML into `SessionState`, serves the cached string
  thereafter. Not per-request.
- `http_server.rs:379` (`folder_handler`) is a blocking `std::fs::read` inside an async axum
  handler, per asset request — but this serves a single localhost webview loading a handful
  of a form's own assets. Volume is tiny and operator-paced; not a hot path. Noted, not
  flagged (see Suspected Bugs for the blocking-in-async note).
- `wle_templates.rs:81` WalkDir runs once per template-list (catalog-browse user action),
  dedups via HashMap, sorts once. **Confirmed cold.**

### grib/ + position/
- grib `composer.rs` composes a request string (prior-confirmed cold). `commands.rs` and
  position (`arbiter`, `gpsd`, `maidenhead`) hold no per-tick heavy loops; gpsd spawns one
  async watch task at setup. **Confirmed cold.**

### wizard / bootstrap / lib / app_backend / modem_commands — `read_config()` callers
- `read_config()` is called per Tauri IPC command (`modem_commands.rs:38,48,398,599,738`;
  `wizard.rs:125,372`; `lib.rs:49,198`; `bootstrap.rs:96`). Each is a discrete operator-
  initiated command (connect, set-config, bootstrap-classify, GPS-permit check), NOT inside a
  loop or tick. No per-call re-read on a hot path. config.rs itself is out of scope. **Cold.**

### tray / theme_state / consent_gate / compose_window / help_window / main
- Small state-holders and one-shot window/tray builders. No loops, no fs-per-call hot paths.
  **Confirmed cold.**

## Suspected Bugs

- `forms/http_server.rs:379` — `folder_handler` uses blocking `std::fs::read` inside an async
  Tokio handler. Not a perf finding at this volume (single local webview), but a latent
  correctness/idiom note: blocking the async executor thread. Low priority; flagged for the
  concurrency lane, not chased here.
