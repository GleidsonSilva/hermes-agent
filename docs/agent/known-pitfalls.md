## Known Pitfalls

### DO NOT infer process identity from argv substrings
The bug class behind ~10 fleet-update issues (#90778, #87594, #78089,
#76129, #91964, ...): classifying a process by `"serve" in cmdline` or
similar. `kanban --preserve-cache` contains "serve"; a flag VALUE can
equal a subcommand (`-m dashboard serve`); truncated cmdlines hide the
real subcommand. Rules:
- Use the canonical matchers: `gateway.status.looks_like_gateway_command_line`
  (gateway run), `hermes_cli.update_cmd._hermes_holder_subcommand`
  (top-level subcommand of any Hermes argv). Never hand-roll token scans.
- Flag sets must be DERIVED from the parser
  (`_holder_value_flags()` introspects `build_top_level_parser()`), never
  hand-written lists — they drift.
- Never blanket-exclude ancestors from process scans: when `/update` runs
  as the gateway's child, a gateway ancestor must stay visible to the
  pause machinery (#87594). Exclude interactive ancestry, carve out
  gateway-shaped ancestors.
- Match on FULL cmdlines; truncate only at display time (#78089).
- Before adding any new scan heuristic, read #92091 — the gateway control
  socket replaces scans as the primary coordination mechanism; scans are
  the fallback layer for old/crashed processes.

### DO NOT hardcode `~/.hermes` paths
Use `get_hermes_home()` from `hermes_constants` for code paths. Use `display_hermes_home()`
for user-facing print/log messages. Hardcoding `~/.hermes` breaks profiles — each profile
has its own `HERMES_HOME` directory. This was the source of 5 bugs fixed in PR #3575.

### All CLI menu-pickers MUST use curses.
Interactive menus must use `hermes_cli/curses_ui.py`. See `hermes_cli/tools_config.py` for an example.

### DO NOT use `\033[K` (ANSI erase-to-EOL) in spinner/display code
Leaks as literal `?[K` text under `prompt_toolkit`'s `patch_stdout`. Use space-padding: `f"\r{line}{' ' * pad}"`.

### `_last_resolved_tool_names` is a process-global in `model_tools.py`
`_run_single_child()` in `delegate_tool.py` saves and restores this global around subagent execution. If you add new code that reads this global, be aware it may be temporarily stale during child agent runs.

### DO NOT hardcode cross-tool references in schema descriptions
Tool schema descriptions must not mention tools from other toolsets by name (e.g., `browser_navigate` saying "prefer web_search"). Those tools may be unavailable (missing API keys, disabled toolset), causing the model to hallucinate calls to non-existent tools. If a cross-reference is needed, add it dynamically in `get_tool_definitions()` in `model_tools.py` — see the `browser_navigate` / `execute_code` post-processing blocks for the pattern.

### The gateway has TWO message guards — both must bypass approval/control commands
When an agent is running, messages pass through two sequential guards:
(1) **base adapter** (`gateway/platforms/base.py`) queues messages in
`_pending_messages` when `session_key in self._active_sessions`, and
(2) **gateway runner** (`gateway/run.py`) intercepts `/stop`, `/new`,
`/queue`, `/status`, `/approve`, `/deny` before they reach
`running_agent.interrupt()`. Any new command that must reach the runner
while the agent is blocked (e.g. approval prompts) MUST bypass BOTH
guards and be dispatched inline, not via `_process_message_background()`
(which races session lifecycle).

### Streaming delivery contract (stream-is-the-message adapters) — duplicate-final class
Adapters with `draft_stream_is_message = True` (relay Slack native streaming)
keep ONE cumulative native stream per turn; the stream IS the final message.
Four invariants, each learned from a live duplicate-final incident (NS-658
canary ledger, hermes#85796 / gateway-gateway#210). Violating any of them
re-creates a duplicate or a frozen stream:

1. **Draft frames must be prefix-stable.** The connector computes append-only
   deltas: frame N must be a string prefix of frame N+1. NEVER mutate draft
   frames per-tick — no fence-closing (`ensure_closed_code_fences`), no cursor
   suffix, no segment-state resets at tool boundaries, no mrkdwn conversion.
   Any non-prefix frame triggers a whole-snapshot re-append on the platform
   ("stacked copies"). The finalize path may still transform the real final.
2. **The consumer declares the final; the adapter never guesses.**
   `finish(final_text)` carries the completed `final_response` (verifier
   footer, completion explainer included) as the authoritative finalize
   payload. New post-stream response augmentation MUST ride this payload —
   if it mutates `final_response` after the stream sealed, it re-opens the
   #11 bug (`delivered_final_matches` mismatch → corrective duplicate send).
3. **Interim sends must carry `_interim_send` metadata.** Any consumer-side
   `adapter.send()` that is NOT the turn-final (commentary, segment-tail
   flushes) must set `metadata["_interim_send"] = True`, or the relay
   adapter's seal-interception will seal the live stream with interim text.
   Seal-interception exists at BOTH egress doors (`send()` AND
   `send_for_platform()`); a new egress door needs the same two checks.
4. **Reconcile by edit, never by plain send.** Any lane that delivers a final
   beside an already-sealed stream (queued follow-ups, media-accompanied
   finals, future lanes) must first try `edit_message` on the consumer's
   `message_id`; plain `send()` is the fallback only when no editable message
   exists. A sealed native stream is a regular message — `chat.update` on it
   works (live-verified).

Contract tests: `tests/gateway/test_stream_final_contract.py` (all four
invariants, mutation-checked). Slack streaming API ground truth (live-probed,
also encoded in connector comments/tests): `chat.*Stream` speaks STANDARD
markdown, not mrkdwn; `stopStream.markdown_text` APPENDS (never replaces);
`startStream`/`stopStream` are rate-limit Tier 2 (~20/min).

Guard style note: check `draft_stream_is_message` with `is True` — MagicMock
adapters in older tests auto-create truthy attributes.

### Squash merges from stale branches silently revert recent fixes
Before squash-merging a PR, ensure the branch is up to date with `main`
(`git fetch origin main && git reset --hard origin/main` in the worktree,
then re-apply the PR's commits). A stale branch's version of an unrelated
file will silently overwrite recent fixes on main when squashed. Verify
with `git diff HEAD~1..HEAD` after merging — unexpected deletions are a
red flag.

### Don't wire in dead code without E2E validation
Unused code that was never shipped was dead for a reason. Before wiring an
unused module into a live code path, E2E test the real resolution chain
with actual imports (not mocks) against a temp `HERMES_HOME`.

### Tests must not write to `~/.hermes/`
The `_isolate_hermes_home` autouse fixture in `tests/conftest.py` redirects `HERMES_HOME` to a temp dir. Never hardcode `~/.hermes/` paths in tests.

**Profile tests**: When testing profile features, also mock `Path.home()` so that
`_get_profiles_root()` and `_get_default_hermes_home()` resolve within the temp dir.
Use the pattern from `tests/hermes_cli/test_profiles.py`:
```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
```

---
