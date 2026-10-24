## Update Pipeline (`hermes update`)

The updater is transactional in shape (fleet-update campaign, #91277 —
Aug 2026). Every stage exists because its absence was a real field
failure; PRs that weaken a stage need to answer for the failure class it
guards:

```
plan → snapshot → apply → restart-per-kind → verify → report
```

- **Plan** (`hermes_cli/update_inventory.py`, `hermes update --plan`):
  read-only inventory — install kind, all profiles, every live gateway
  with supervisor + running code version. Deployment kinds are
  first-class: `git` updates in place; `docker`/`nix`/`apt` are NOT
  in-place-updatable and the updater reports the correct external
  command instead of fighting the deployment model.
- **Snapshot** (`hermes_cli/backup.py`): pre-update quick snapshot for
  EVERY profile (the code swap + fleet restart touch all of them), each
  into its own `state-snapshots/`, identical file set + 1 GiB per-file
  cap + keep=1. **Never add a partial/tiered snapshot set** — mixed
  coverage creates torn-restore states across schema generations. Quick
  snapshots are FILE-LOSS RECOVERY (the per-profile cron-jobs safety
  net restores from them), NOT code-rollback insurance; `--backup` full
  mode owns rollback.
- **Apply**: git pull, or the Windows ZIP fallback — which fires ONLY
  when git itself failed (`_should_zip_fallback_on_update_error`,
  argv-classified; a dependency-install failure must never trigger a
  tree-clobbering re-download), REFUSES a dirty working tree
  (`-uall`, plus a pre-swap TOCTOU re-check), and grafts the live
  `apps/desktop/release/` into the staged swap (the GitHub source ZIP
  has no built desktop app; without the graft the swap deletes it).
- **Restart-per-kind**: systemd and launchd restarts are FLEET-WIDE
  (every `hermes-gateway*` unit / `ai.hermes.gateway*` LaunchAgent),
  drain-first (SIGUSR1) with per-unit/per-label failure isolation.
  Restarting only the invoking profile's service leaves siblings on
  stale `sys.modules` until they crash — the largest dupe-PR cluster in
  the repo's history came from that bug.
- **Verify**: gateways stamp their running `code_sha`/`code_version`
  into `gateway_state.json` on every runtime-status write
  (`gateway/status.py`); after the restart phase the updater compares
  each live gateway against the fresh checkout and prints a fleet
  version matrix. A provably-stale gateway fails the update (exit 1) —
  automation must never treat a mixed-version fleet as healthy.
- **Report**: every run writes a machine-readable receipt to
  `~/.hermes/logs/update_receipts/` (`latest.json` pointer; steps,
  skips WITH reasons, restart outcome, plan, fleet snapshot).
  Finalization is owned by the `cmd_update` command boundary — early
  `sys.exit` paths (preflight refusals, fetch failures) still persist
  a receipt with the real exit code. A begun-but-unwritten receipt is
  a bug: the refused/failed runs are the ones receipts exist for.

Architecture direction: process-scan-based coordination between the
updater, serve/dashboard, and the gateway is being replaced by a
gateway-owned control socket (#92091). Do not add new scan heuristics
without checking that design; scans are the fallback layer.

### Gateway lifecycle vs. the Desktop app

`hermes serve` (control plane, desktop-spawned child) dies with the app
— by design. The messaging gateway (`gateway run`) SURVIVES the app: the
serve backend's `/api/gateway/*` endpoints spawn it detached
(`_spawn_hermes_action` — `start_new_session` / `DETACHED_PROCESS`), so
`before-quit`'s backend SIGTERM never reaches it. Bots keep running
when the user closes the app. The known breach of this contract is the
Windows shim-unlock teardown (`taskkill /T /F` on venv-shim holders,
#85265) — it exists to let updates proceed, and its replacement is
#92091's `pause-for-update`. Do not "fix" gateway-dies-with-app reports
by re-parenting the gateway under the backend, and do not "fix" update
locks by widening the tree-kill.

---

## Important Policies

### Prompt Caching Must Not Break

Hermes-Agent ensures caching remains valid throughout a conversation. **Do NOT implement changes that would:**
- Alter past context mid-conversation
- Change toolsets mid-conversation
- Reload memories or rebuild system prompts mid-conversation

Cache-breaking forces dramatically higher costs. The ONLY time we alter context is during context compression.

Slash commands that mutate system-prompt state (skills, tools, memory, etc.)
must be **cache-aware**: default to deferred invalidation (change takes
effect next session), with an opt-in `--now` flag for immediate
invalidation. See `/skills install --now` for the canonical pattern.

### Background Process Notifications (Gateway)

When `terminal(background=true, notify_on_complete=true)` is used, the gateway runs a watcher that
detects process completion and triggers a new agent turn. Control verbosity of background process
messages with `display.background_process_notifications`
in config.yaml (or `HERMES_BACKGROUND_NOTIFICATIONS` env var):

- `concise` — one-line status message on completion; failures append a short output tail (default)
- `all` — running-output updates + final raw-output message
- `result` — only the final raw-output completion message
- `error` — only the final raw-output message when exit code != 0
- `off` — no watcher messages at all

---

## Profiles: Multi-Instance Support

Hermes supports **profiles** — multiple fully isolated instances, each with its own
`HERMES_HOME` directory (config, API keys, memory, sessions, skills, gateway, etc.).

The core mechanism: `_apply_profile_override()` in `hermes_cli/main.py` sets
`HERMES_HOME` before any module imports. All `get_hermes_home()` references
automatically scope to the active profile.

### Rules for profile-safe code

1. **Use `get_hermes_home()` for all HERMES_HOME paths.** Import from `hermes_constants`.
   NEVER hardcode `~/.hermes` or `Path.home() / ".hermes"` in code that reads/writes state.
   ```python
   # GOOD
   from hermes_constants import get_hermes_home
   config_path = get_hermes_home() / "config.yaml"

   # BAD — breaks profiles
   config_path = Path.home() / ".hermes" / "config.yaml"
   ```

2. **Use `display_hermes_home()` for user-facing messages.** Import from `hermes_constants`.
   This returns `~/.hermes` for default or `~/.hermes/profiles/<name>` for profiles.
   ```python
   # GOOD
   from hermes_constants import display_hermes_home
   print(f"Config saved to {display_hermes_home()}/config.yaml")

   # BAD — shows wrong path for profiles
   print("Config saved to ~/.hermes/config.yaml")
   ```

3. **Module-level constants are fine** — they cache `get_hermes_home()` at import time,
   which is AFTER `_apply_profile_override()` sets the env var. Just use `get_hermes_home()`,
   not `Path.home() / ".hermes"`.

4. **Tests that mock `Path.home()` must also set `HERMES_HOME`** — since code now uses
