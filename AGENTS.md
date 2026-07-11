# Hermes Agent — Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

**Never give up on the right solution.**

## Filosofia

- **Per-conversation prompt caching is sacred.** A long-lived conversation reuses a cached prefix every turn. Anything that mutates past context, swaps toolsets, or rebuilds the system prompt mid-conversation invalidates that cache and multiplies the user's cost. The only exception is context compression.
- **The core is a narrow waist; capability lives at the edges.** Every model tool we add is sent on every API call, so the bar for a new *core* tool is high. Most new capability should arrive as a CLI command + skill, a service-gated tool, or a plugin — not as core surface.

## Contribution Rubric — What We Want / What We Don't

### What we want

- **Fix real bugs, well.** The bulk of what lands is `fix(...)` against an actual reported symptom. A good fix reproduces the symptom on current `main`, points to the exact line where it manifests, and fixes the whole bug class — sibling call paths included — not just the one site the reporter hit.
- **Expand reach at the edges.** New platform adapters, channels, providers, models, and desktop/TUI/dashboard features are welcome and land routinely, including large ones. Breadth in the product is a goal, not a footprint concern — as long as it integrates with the existing setup/config UX (`hermes tools`, `hermes setup`, auto-install) rather than bolting on a raw env var.
- **Refactor god-files into clean modules.** Extracting a multi-thousand-line cluster out of `cli.py` / `run_agent.py` / `gateway/run.py` into a focused mixin or module is wanted work.
- **Keep the core narrow.** New *model tools* are the expensive exception — every tool ships on every API call. Prefer, in order: extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP server in the catalog → new core tool (last resort). See "The Footprint Ladder."
- **Extend, don't duplicate.** Before adding a module/manager/hook, check whether existing infrastructure already covers the use case. When several PRs integrate the same *category*, design one shared interface instead of merging them one at a time.
- **Behavior contracts over snapshots.** Tests should assert how two pieces of data must relate (invariants), not freeze a current value (model lists, config version literals, enumeration counts).
- **E2E validation, not just green unit mocks.** For anything touching resolution chains, config propagation, security boundaries, remote backends, or file/network I/O, exercise the real path with real imports against a temp `HERMES_HOME`. Mocks hide integration bugs.
- **Cache-, alternation-, and invariant-safe.** Preserve prompt caching, strict message role alternation (never two same-role messages in a row; never a synthetic user message injected mid-loop), and a system prompt that is byte-stable for the life of a conversation.
- **Contributor credit preserved.** Salvage external work by cherry-picking (rebase-merge) so authorship survives in git history; don't reimplement from scratch when you can build on top.

### What we don't want (rejected even when well-built)

- **Speculative infrastructure.** Hooks, callbacks, or extension points with no concrete consumer.
- **New `HERMES_*` env vars for non-secret config.** `.env` is for secrets only. All behavioral settings go in `config.yaml`. Reject PRs that tell users to "set X in your .env" unless X is a credential.
- **A new core tool when terminal + file already do the job, or when a skill would.**
- **Lazy-reading escape hatches on instructional tools.** No `offset`/`limit` pagination on tools that load content the agent must read fully.
- **"Fixes" that destroy the feature they secure.** Read the original commit's intent (`git log -p -S`) before restricting behavior.
- **Outbound telemetry / usage attribution without opt-in gating.**
- **Change-detector tests, cache-breaking mid-conversation, dead code wired in without E2E proof, and plugins that touch core files.**
- **Third-party products / other people's projects integrated into the core tree.** Observability backends, vendor SaaS integrations, analytics dashboards must ship as standalone plugin repos. PRs that add such a directory to `plugins/` are closed with a pointer to publish it as its own repo.

## Before you call it a bug — verify the premise

- **"Intentional design, not a gap."** A limitation that looks like an oversight is often deliberate. Read the original commit's intent before assuming something is unfinished.
- **"The premise doesn't hold against how X actually works."** Trace the real code/runtime before accepting the rationale. If you can't point to the exact line where the bug manifests AND show the fix changes that line's behavior, you haven't verified the premise.
- **"This fix was wrong — the absence/omission was deliberate."** Adding the obvious-looking missing piece can break things the omission was protecting.
- **"Overreached / resurrected an approach we've moved past."** Keep the change to the narrow piece that was actually agreed; offer the rest as a focused follow-up.

The throughline: **verify the claim AND the intent against the codebase before writing or merging a fix.**

## The Footprint Ladder (new capability decision)

Each rung adds more permanent surface than the one above. Choose the highest (least-footprint) rung that correctly solves the problem:

1. **Extend existing code** — zero new surface.
2. **CLI command + skill** — manages config/state/infra expressible as shell commands. Zero model-tool footprint. Default choice for subscriptions, scheduled tasks, service setup.
3. **Service-gated tool (`check_fn`)** — needs structured params/returns AND only appears when a prerequisite is configured. Zero footprint otherwise.
4. **Plugin** — third-party/niche/user-specific capability that doesn't ship in core.
5. **MCP server (in the catalog)** — zero permanent core-schema footprint, reusable by any MCP host.
6. **New core tool** — only when fundamental, broadly useful, and unreachable via terminal + file or an MCP server.

When 3+ open PRs try to integrate the same *category* of thing, don't merge them one at a time — design an ABC + orchestrator and turn competing PRs into plugins against that interface.

## Repositório (entradas de edição)

| Arquivo | Responsabilidade |
|---|---|
| `run_agent.py` | Loop de conversa, orquestração de ferramentas |
| `model_tools.py` | Registry, dispatch, schema de tools |
| `toolsets.py` | `_HERMES_CORE_TOOLS`, bundle por plataforma |
| `cli.py` | CLI interativa, skin, slash commands |
| `gateway/run.py` | Loop das plataformas de mensagem |
| `agent/` | Provider adapters, memória, cache, compression |
| `hermes_cli/` | Subcomandos, wizard, plugins loader, skin engine |
| `tools/*.py` | Tools — auto-descobertas via `tools/registry.py` |
| `plugins/` | Plugins (veja abaixo) |
| `skills/` | Skills built-in |
| `optional-skills/` | Skills opcionais |
| `ui-tui/` | TUI Ink (React) |
| `tui_gateway/` | Backend JSON-RPC da TUI |

File counts shift constantly. The canonical source is the filesystem.

## Estilo

### Python
- Snake_case para funções e métodos, PascalCase para classes.
- Todos os paths de HERMES_HOME via `get_hermes_home()`; mensagens user-facing via `display_hermes_home()`.
- Handlers de tool retornam JSON string.

### TypeScript
- Nanostores para estado compartilhado; persistência colada ao atom.
- Hooks enxutos, uma responsabilidade cada; actions colocalizadas.
- Interfaces para props públicas; estender `React.ComponentProps`.
- `src/app`: rotas e páginas. `src/store`: atoms. `src/lib`: helpers puros.

## Configuração

### config.yaml
1. Adicionar chave em `DEFAULT_CONFIG` de `hermes_cli/config.py`.
2. Bump `_config_version` **apenas** se precisa migrar dados existentes (renomear chaves, mudar estrutura). Chaves novas em seção existente não requerem bump — deep-merge cuida disso.

### .env
Somente segredos (API keys, tokens, passwords). Qualquer setting comportamental vai em config.yaml. Se código interno precisa de env var mirror para back-compat, bridgear de config.yaml para env var em código.

### Config loaders
| Loader | Usado por |
|---|---|
| `load_cli_config()` | CLI interativa |
| `load_config()` | `hermes tools`, `hermes setup` |
| YAML raw | Gateway runtime |

Não adicionar chave nova que uma das três não veja — está no loader errado.

### Working directory
- CLI: `os.getcwd()`.
- Messaging: `terminal.cwd` em config.yaml, bridgeado para `TERMINAL_CWD`. `MESSAGING_CWD` removido.

## Plugins

### General
`PluginManager` descobre em `~/.hermes/plugins/`, `./.hermes/plugins/`, pip entry points. `register(ctx)` registra hooks e tools. Hooks: `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`. Pitfall: `discover_plugins()` roda como side effect de `model_tools.py`.

### Memory providers
ABC em `agent/memory_provider.py`, orquestrado por `agent/memory_manager.py`. Built-ins existentes ok; novos memory backends devem ser repositórios standalone.

### Model providers
Cada backend em `plugins/model-providers/<name>/` registra `ProviderProfile`. Descoberta lazy, separada do PluginManager. Scan order: bundled → user → legacy.

### Third-party product plugins
Plugins de third-party (observability, SaaS, dashboards) não entram na árvore. Devem ser repositórios standalone.

### Plugin rule
Plugins MUST NOT modify core files. If a plugin needs a capability the framework doesn't expose, expand the generic plugin surface — never hardcode plugin-specific logic into core.

## Skills

- `skills/` (built-in, ativo) e `optional-skills/` (instalação manual via `hermes skills install official/<category>/<skill>`).
- Em PRs de skill, verificar se ela vai para `optional-skills/`.

### SKILL.md requirements
- `description`: ≤ 60 chars, uma frase, ponto final. Sem marketing.
- Título: `# <Nome> Skill`. Seções: intro, `## When to Use`, `## Prerequisites`, `## How to Run`, `## Quick Reference`, `## Procedure`, `## Pitfalls`, `## Verification`.
- Scripts em `scripts/`, refs em `references/`, templates em `templates/`.
- Testes: `tests/skills/test_<skill>_skill.py`, stdlib+pytest+mock.
- Prosa referencia tools nativas do Hermes pelo nome; shell utilities embutidas não precisam ser nomeadas.
- `author` credita o contribuidor humano primeiro.

## Ferramentas

### Adicionar core tool
Sempre considerar a Footprint Ladder primeiro. Se for core tool:
1. `tools/your_tool.py` com `registry.register(...)`.
2. Adicionar nome em `toolsets.py` (`_HERMES_CORE_TOOLS` ou novo toolset). Auto-discovery importa o arquivo, mas wiring no toolset é passo manual obrigatório.
Schema: usar `display_hermes_home()` em caminhos; `get_hermes_home()` para state.

### Toolsets
Definidos em `toolsets.py`, bundle default `_HERMES_CORE_TOOLS`. Plataforma escolhe base; override por `config.yaml`.

### Delegação (`delegate_task`)
Leaf: worker, sem delegação. Orchestrator: pode spawnar, gated por `delegation.orchestrator_enabled` e `max_spawn_depth`. Background é process-local — usar `cronjob` para persistência.

### Agent-level tools (todo, memory)
Interceptados por `run_agent.py` antes de `handle_function_call()`.

## Cron
`cron/jobs.py` + `cron/scheduler.py`. Hard 3-min interrupt, catchup window, grace window, file lock em `~/.hermes/cron/.tick.lock`. Cron sessions não rodam memória (`skip_memory=True`). Formatos: duração, "every", cron 5 campos, ISO timestamp. Campos: `skills`, `model/provider` overrides, `script`, `context_from`, `workdir`.

## Curator
Background skill-maintenance para skills `created_by: "agent"`. Nunca deleta; max ação é archive. Pinned skills são exempt. CLI: `hermes curator <verb>` (status, run, pause, resume, pin, unpin, archive, restore, prune, backup, rollback).

## Testing

### Sempre usar
```bash
scripts/run_tests.sh tests/<path>::<test> -v
```
Nunca `pytest` direto: garante env limpo, TZ=UTC, LANG=C.UTF-8, HERMES_HOME em tmp, subprocess isolado por arquivo.

### Change-detector tests
Não escrever testes que quebram ao atualizar catálogo de modelos ou config version. Escrever invariantes: relações entre dados, não snapshots.

### E2E > mocks
Para chains de resolução, propagação de config, I/O: testar com imports reais contra HERMES_HOME temporário. Mocks escondem bugs de integração.

## Known Pitfalls
- Não hardcodar `~/.hermes` em código ou testes.
- Não usar `simple_term_menu` em menus novos — usar `curses_ui.py`.
- Não usar `\033[K` em spinner/display — espaço-padding.
- Não referenciar tools de outro toolset por nome em schema descriptions (model pode não tê-las disponíveis); fazer em `get_tool_definitions()`.
- Gateway tem dois guards de mensagem — comandos de approval/control devem bypassar ambos inline, não via `_process_message_background()`.
- Squash merge de branch desatualizada silenciosamente reverte fixes — rebasear em main antes de squash.
- `_last_resolved_tool_names` é global; pode estar stale durante subagent runs.

## Profiles: Multi-Instance Support

HERMES_HOME perfilado via `_apply_profile_override()` antes de imports.
- `get_hermes_home()` para paths, `display_hermes_home()` para mensagens.
- `_get_profiles_root()` usa `Path.home()` intencionalmente — profiles são discoveráveis independente de qual está ativa.
- Gateway platform adapters devem usar token locks (`acquire_scoped_lock()` / `release_scoped_lock()`).
- Profile tests devem mock `Path.home()` E setar `HERMES_HOME`.

## Skin engine
Skins puramente em YAML sobre `display.skin` em config. Built-ins: `default`, `ares`, `mono`, `slate`. Custom em `~/.hermes/skins/<name>.yaml`. Ativar via `/skin` ou config.
