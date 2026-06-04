# Bug-hunt kickoff — suspected item from the 2026-06-04 R9 (frontend) performance audit

Run: `bug-hunt-cycle` with the scope below.

**Scope:** `src/radio/modes/TelnetP2pRadioPanel.tsx` peer-callsign password-status effect. Noticed during a perf audit; NOT investigated.

**Seed finding (verify, don't trust):**
- Uncancelled in-flight IPC race — `TelnetP2pRadioPanel.tsx:260-276`: the per-keystroke `p2p_peer_password_status` effect guards stale writes but does not cancel in-flight calls, so racing keyring reads can resolve out of order (a slow earlier read overwriting a newer result). A trailing debounce + AbortController/stale-guard fixes both the race and the per-keystroke IPC cost (perf finding R9-2).

A lead for the hunters, not a confirmed bug.
