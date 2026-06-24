# Hermes Agent — Development Guide

## Git Workflow (Gleidson's Fork)

- **Fork:** https://github.com/GleidsonSilva/hermes-agent
- **Upstream:** https://github.com/NousResearch/hermes-agent
- **Default remote:** `origin`, NOT `upstream`
- **Base branch:** `gleidson-main`
- **PRs:** always against `origin/gleidson-main`
- **Update:** `git fetch upstream && git checkout gleidson-main && git merge upstream/main`
- **Never push to `upstream`**

## What Hermes Is

AI agent running the same core across CLI, gateway (Telegram/Discord/Slack/~20 platforms), TUI, and Electron desktop. Learns across sessions (memory + skills), delegates to subagents, runs scheduled jobs, drives terminal and browser. Extended via **plugins and skills**, not core growth.

**Two sacred properties:**
- **Per-conversation prompt caching is sacred.** No mid-conversation context mutation, toolset swaps, or system prompt rebuilds (except compression).
- **Core is a narrow waist; capability at edges.** Every model tool ships on every API call. Prefer: extend code → CLI+skill → service-gated tool → plugin → MCP server → core tool (last resort).

## Contribution Rubric

Most merges are bug fixes against real symptoms. New platform adapters, channels, models, desktop/TUI features are welcome. Refactoring god-files into modules is wanted.

### Rejected even when well-built
- Speculative infrastructure (hooks without consumers)
- New `HERMES_*` env vars for non-secrets
- New core tools when terminal+file+skill suffice
- Lazy-reading pagination on instructional tools
- "Fixes" that destroy the feature they secure
- Outbound telemetry without opt-in gating
- Change-detector tests, cache-breaking mid-conversation, dead code wired without E2E proof, plugins touching core files

### Before calling it a bug
Verify the premise against the codebase. Read `git log -p -S "<intent>"` before "fixing" a limitation — it may be deliberate. A confirmed reproduction + line-level account beats a plausible rationale.

## The Footprint Ladder

Choose highest (least-footprint) rung that solves the problem:
1. Extend existing code (zero surface)
2. CLI command + skill (`hermes <subcommand>`)
3. Service-gated tool (`check_fn`)
4. Plugin (`~/.hermes/plugins/`)
5. MCP server in catalog
6. New core tool (last resort — only if fundamental and unreachable via terminal+file)

## Development Environment

```bash
source .venv/bin/activate   # or: source venv/bin/activate
```

`scripts/run_tests.sh` enforces CI parity (unset credentials, TZ=UTC, C.UTF-8, xdist).

## Project Structure

```
hermes-agent/
├── run_agent.py          # AIAgent class — core conversation loop
├── model_tools.py        # Tool orchestration, discover_builtin_tools()
├── toolsets.py           # Toolset definitions, _HERMES_CORE_TOOLS
├── cli.py                # HermesCLI class — interactive CLI orchestrator
├── hermes_state.py       # SessionDB — SQLite session store (FTS5)
├── hermes_constants.py   # get_hermes_home(), display_hermes_home()
├── hermes_logging.py     # setup_logging() — agent.log / errors.log / gateway.log
├── agent/                # Provider adapters, memory, caching, compression
├── hermes_cli/           # CLI subcommands, setup wizard, plugins loader, skin engine
├── tools/                # Auto-discovered via tools/registry.py
│   └── environments/     # Terminal backends (local, docker, ssh, modal, etc.)
├── gateway/              # run.py + session.py + platforms/
├── plugins/              # memory, context_engine, model_providers, kanban, etc.
├── skills/               # Built-in skills
├── optional-skills/      # Niche skills installed via `hermes skills install`
├── ui-tui/               # Ink (React) terminal UI
├── tui_gateway/          # Python JSON-RPC backend for TUI
├── cron/                 # Scheduler — jobs.py, scheduler.py
└── tests/                # Pytest suite (~17k tests)
```

**User config:** `~/.hermes/config.yaml` (settings), `~/.hermes/.env` (secrets only).
**Logs:** `~/.hermes/logs/` — `agent.log`, `errors.log`, `gateway.log`.

## File Dependency Chain

```
tools/registry.py  (no deps)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry + triggers discovery)
       ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

## AIAgent Class (run_agent.py)

```python
class AIAgent:
    def __init__(self, base_url=None, api_key=None, provider=None,
                 api_mode=None, model="", max_iterations=90,
                 enabled_toolsets=None, disabled_toolsets=None,
                 quiet_mode=False, save_trajectories=False,
                 platform=None, session_id=None,
                 skip_context_files=False, skip_memory=False,
                 credential_pool=None, ...): ...

    def chat(self, message: str) -> str: ...
    def run_conversation(self, user_message, system_message=None,
                         conversation_history=None, task_id=None) -> dict: ...
```

Core loop in `run_conversation()`: synchronous, with interrupt checks and budget tracking. Messages follow OpenAI format.

## CLI Architecture (cli.py)

- **Rich** for banner/panels, **prompt_toolkit** for input
- **KawaiiSpinner** (`agent/display.py`) — animated faces during API calls
- **Skin engine** (`hermes_cli/skin_engine.py`) — data-driven theming (`display.skin` config)
- `process_command()` dispatches via `resolve_command()` from central registry (`hermes_cli/commands.py`)
- Skill slash commands injected as **user message** (not system prompt) to preserve caching

### Slash Command Registry

All commands defined in `COMMAND_REGISTRY` list of `CommandDef` objects. Consumers derive automatically (CLI, Gateway, Telegram menu, Slack mapping, Autocomplete, help).

**Adding a command:** Add `CommandDef` to registry → add handler in `HermesCLI.process_command()` → optionally gateway handler.

**Adding an alias:** Just add to the `aliases` tuple — everything updates automatically.

## TUI Architecture

`hermes --tui` → Node (Ink) ⟷ stdio JSON-RPC ⟷ Python (tui_gateway + AIAgent + tools). TypeScript owns screen, Python owns sessions/tools/model calls.

### Key Surfaces

| Surface | Component | Gateway method |
|---------|-----------|----------------|
| Chat streaming | `messageLine.tsx` | `prompt.submit` → `message.delta/complete` |
| Tool activity | `thinking.tsx` | `tool.start/progress/complete` |
| Approvals | `prompts.tsx` | `approval.respond` ← `approval.request` |
| Slash commands | Local + `slash.exec` | `command.dispatch` fallback |

### Dashboard (`hermes dashboard`)

Embeds real `hermes --tui` via PTY (xterm.js). **Do not re-implement chat in React.** Sidebar widgets/panels are fine if they don't replace transcript/composer/terminal.

### Desktop App

Separate Electron + React chat surface. Slash commands curated client-side (`apps/desktop/src/lib/desktop-slash-commands.ts`), dispatched to backend.

## Adding New Tools

Built-in tools require changes in **2 files**: `tools/your_tool.py` (with `registry.register()`) and `toolsets.py` (add to `_HERMES_CORE_TOOLS` or a new toolset).

Prefer plugin route for custom tools: `~/.hermes/plugins/<name>/plugin.yaml` + `__init__.py` with `ctx.register_tool(...)`.

**All handlers MUST return JSON string.**

## Dependency Pinning Policy

All deps need upper bounds: `>=floor,<next_major` for post-1.0, `<0.(current_minor+2)` for pre-1.0. Commits use SHA pins for git URLs and GitHub Actions. Run `uv lock` after changes. See #2810.

## Adding Configuration

- **config.yaml options:** Add to `DEFAULT_CONFIG` in `hermes_cli/config.py`. Bump `_config_version` only for migrations.
- **Top-level sections:** `model`, `agent`, `terminal`, `compression`, `display`, `stt`, `tts`, `memory`, `security`, `delegation`, `curator`, `skills`, `gateway`, `logging`, `cron`, `profiles`, `plugins`, `honcho`.
- **`.env` (secrets ONLY):** Add to `OPTIONAL_ENV_VARS` in config.py with metadata.
- **Config loaders:** `load_cli_config()` (CLI), `load_config()` (most subcommands), Direct YAML (gateway).

**Messaging uses `terminal.cwd`** (not `MESSAGING_CWD`/`TERMINAL_CWD` env vars — removed).

## Skin/Theme System

Skins are **pure data** — `hermes_cli/skin_engine.py` loads from config or `~/.hermes/skins/*.yaml`. Customize: banner colors, spinner faces/verbs/wings, branding, tool prefix, per-tool emojis. Built-in: `default`, `ares`, `mono`, `slate`.

## Plugins

Two surfaces: general plugins (`hermes_cli/plugins.py`) and memory-provider plugins (`plugins/memory/`). Rule: **plugins MUST NOT modify core files.** No new in-tree memory providers (policy, May 2026) — publish as standalone repos.

## Skills

- **`skills/`** — bundled, active by default.
- **`optional-skills/`** — niche/heavy, install via `hermes skills install`.
- **SKILL.md:** description ≤60 chars, modern section order, scripts in `scripts/`, tests in `tests/skills/test_<skill>_skill.py`.

## Delegation

`delegate_task` spawns isolated subagents. Single or batch (parallel, capped by `delegation.max_concurrent_children`). Roles: `leaf` (cannot delegate) or `orchestrator` (can spawn workers). Background delegation is process-local, not durable.

## Curator

Background skill-maintenance — tracks usage, auto-archives stale agent-created skills to `~/.hermes/skills/.archive/`. Never deletes. Pinned skills exempt from auto-transitions.

## Cron

`cron/jobs.py` + `cron/scheduler.py`. Schedule formats: duration, "every" phrase, 5-field cron, ISO timestamp. **3-minute hard interrupt** prevents runaway loops. Catchup/grace windows. Not mirrored into gateway sessions.

## Kanban

SQLite-backed multi-agent board. CLI via `hermes kanban`, workers use `kanban_*` toolset. Auto-blocks after `kanban.failure_limit` consecutive failures.

## Important Policies

- **Prompt caching must not break.** Slash commands default to deferred invalidation (next session) with opt-in `--now`.
- **Background process notifications:** controlled by `display.background_process_notifications` (`all`/`result`/`error`/`off`).
- **Profiles:** use `get_hermes_home()` for paths, `display_hermes_home()` for user-facing messages. Never hardcode `~/.hermes`.

## Known Pitfalls

- **Hardcoding `~/.hermes`** breaks profiles. Use `get_hermes_home()`.
- **`simple_term_menu` has bugs** in tmux/iTerm2. Use `curses` for new menus.
- **`\033[K` leaks** under prompt_toolkit. Use space-padding instead.
- **`_last_resolved_tool_names`** is a process-global — stale during subagent runs.
- **Cross-tool references in schemas** cause hallucinations. Add dynamically in `get_tool_definitions()`.
- **Gateway has TWO message guards** — approval commands must bypass both.
- **Squash merges from stale branches** silently revert fixes. Verify with `git diff HEAD~1..HEAD`.
- **Tests must not write to `~/.hermes/`** — `_isolate_hermes_home` fixture handles isolation.

## Testing

**Always use `scripts/run_tests.sh`** for CI parity. Subprocess-per-test isolation via `tests/_isolate_plugin.py` (spawn context, no fork). Don't write change-detector tests — assert how data relates, not current values.
