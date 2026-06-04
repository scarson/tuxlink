# Bug-hunt kickoff — suspected item from the 2026-06-04 R10 (storage) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `src-tauri/src/native_mailbox.rs` message-move paths. Noticed during a perf audit; NOT investigated.

**Seed finding (verify, don't trust):**
- Non-atomic move / data-loss window — `native_mailbox.rs:160-161` (`move_to`) and `:392-393` (`move_between`): `fs::write(dst)` then `fs::remove_file(src)` with no atomicity. A crash between the two leaves the message in both folders (duplicate), or a half-written copy. Switching to `fs::rename` (perf finding R10-2, same root) makes the move atomic and closes the window.

A lead for the hunters, not a confirmed bug.
