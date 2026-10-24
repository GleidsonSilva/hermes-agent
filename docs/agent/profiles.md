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
   `get_hermes_home()` (reads env var), not `Path.home() / ".hermes"`:
   ```python
   with patch.object(Path, "home", return_value=tmp_path), \
        patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
       ...
   ```

5. **Gateway platform adapters should use token locks** — if the adapter connects with
   a unique credential (bot token, API key), call `acquire_scoped_lock()` from
   `gateway.status` in the `connect()`/`start()` method and `release_scoped_lock()` in
   `disconnect()`/`stop()`. This prevents two profiles from using the same credential.
   See `plugins/platforms/irc/adapter.py` for the canonical pattern.

6. **Profile operations are HOME-anchored, not HERMES_HOME-anchored** — `_get_profiles_root()`
   returns `Path.home() / ".hermes" / "profiles"`, NOT `get_hermes_home() / "profiles"`.
   This is intentional — it lets `hermes -p coder profile list` see all profiles regardless
   of which one is active.

7. **Multiplex profile-scoped env reads MUST fail closed — never borrow from `os.environ`**
   (`agent/secret_scope.py` contract; #72348, #86905). Under `gateway.multiplex_profiles`,
   `os.environ` holds the **default profile's** values; a secondary profile's `.env` lives
   only in its secret scope (installed per-turn by `_profile_runtime_scope`). Any
   profile-level env config — credentials (`app_secret`, tokens) AND authorization
   (`FEISHU_ALLOWED_USERS`, `{PLATFORM}_ALLOW_ALL_USERS`, `GATEWAY_ALLOW_ALL_USERS`,
   `group_policy`, `allow_bots`, ...) — must be read scope-aware:
   - Adapters: `_get_scoped_secret()` (canonical fail-closed copy in
     `plugins/platforms/feishu/adapter.py`, #86905).
   - Gateway authz: `_auth_env()` / `_platform_gate_env()` (`gateway/authz_mixin.py`).
   Rules:
   - Scope installed + multiplex active → a scoped miss returns the **default**.
     NEVER fall through to `os.environ` — that leaks another profile's value and
     silently breaks routing/admission (a leaked default allowlist skips the
     allow-all check and rejects every secondary-profile sender, #86905).
   - Unscoped default-profile path (`UnscopedSecretError`) and single-profile
     deployments keep the `os.environ` read — there it IS the profile's own value.
   - Authorization config is the sharpest edge: allowlist/allow-all leaks cause
     silent rejections (or worse, fail-open) that only show up as missing replies.
   - The `_get_scoped_secret` wrapper is copy-pasted across ~15 platform adapters —
     when touching any of them, make sure the fail-closed semantics are present;
     do not reintroduce the `except _UnscopedSecretError: val = os.getenv(...)`
     fallback-after-miss shape.

