"""Pgvector memory plugin — MemoryProvider interface for Glena's PostgreSQL + pgvector backend.

Uses the existing Glena pgvector infrastructure (Docker container at port 5433,
Cloudflare Workers AI embeddings @cf/baai/bge-large-en-v1.5, 1024 dims,
PostgreSQL 16 + pgvector + pg_trgm with HNSW vector index and GIN full-text index).

Configuration lives in $HERMES_HOME/.env (secrets) and is read by the existing
memory_config.py module in the glena profile's scripts/ directory.

This plugin wraps the existing scripts:
- memory_context.py (search relevant memories)
- memory_store.py (store facts with embedding + dedup)
- memory_search.py (hybrid search wrapper)
- memory_ensure.py (pgvector container health/start)

It does NOT replace those scripts — it provides a Hermes MemoryProvider interface
so the agent can use the native `memory()` tool, while the underlying data
stays in the pgvector database managed by the glena profile.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path as _Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from agent.secret_scope import get_secret
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# Delay in seconds before we treat the provider unavailable.
_DEFAULT_TIMEOUT = 30

# Path to the glena profile's memory scripts (absolute for reliability under
# multiplexing / different CWD). The plugin lives in the repo; the data lives
# in the active profile's HERMES_HOME.
_PROFILE_SCRIPTS_DIR: Optional[_Path] = None


def _resolve_script_dir() -> _Path:
    global _PROFILE_SCRIPTS_DIR
    if _PROFILE_SCRIPTS_DIR is not None:
        return _PROFILE_SCRIPTS_DIR
    try:
        from hermes_constants import get_hermes_home
        script_dir = get_hermes_home() / "scripts"
    except Exception:
        # Fallback: derive from this file's location (repo install) and look
        # up the active profile via HERMES_HOME env override.
        try:
            from hermes_constants import get_hermes_home
            script_dir = get_hermes_home() / "scripts"
        except Exception:
            script_dir = _Path(__file__).resolve().parents[4] / ".hermes" / "profiles" / "glena" / "scripts"
    _PROFILE_SCRIPTS_DIR = script_dir
    return script_dir


def _ensure_pgvector() -> bool:
    """Run memory_ensure.py to start the pgvector Docker container if needed."""
    script_dir = _resolve_script_dir()
    mem_ensure = script_dir / "memory_ensure.py"
    if not mem_ensure.exists():
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(mem_ensure), "--json"],
            capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT,
        )
        if result.returncode == 0:
            try:
                status = json.loads(result.stdout)
                return bool(status.get("connection"))
            except Exception:
                pass
        return False
    except Exception:
        return False


def _read_config() -> dict:
    """Load pgvector config from the glena profile's memory_config.py constants
    by importing it (not parsing — the module defines DATABASE_URL, scopes)."""
    try:
        script_dir = _resolve_script_dir()
        sys.path.insert(0, str(script_dir))
        # Import the module that defines DATABASE_URL and scopes.
        # The module uses dotenv to load .env automatically.
        import importlib.util

        spec = importlib.util.spec_from_file_location("memory_config", str(script_dir / "memory_config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return {
            "database_url": getattr(mod, "DATABASE_URL", ""),
            "user_scope": getattr(mod, "DEFAULT_USER_SCOPE", "gleidson"),
            "agent_scope": getattr(mod, "DEFAULT_AGENT_SCOPE", "glena"),
            "embed_model": getattr(mod, "CLOUDFLARE_EMBED_MODEL", ""),
            "embed_dims": getattr(mod, "CLOUDFLARE_EMBED_DIMS", 1024),
        }
    except Exception as exc:
        logger.debug("Could not load memory_config module: %s", exc)
        return {
            "database_url": "postgresql://glena:glena_memory_local@127.0.0.1:5433/glena_memory",
            "user_scope": "gleidson",
            "agent_scope": "glena",
            "embed_model": "@cf/baai/bge-large-en-v1.5",
            "embed_dims": 1024,
        }
    finally:
        # Clean up sys.path insertion so we don't leak.
        try:
            script_dir_str = str(_resolve_script_dir())
            if script_dir_str in sys.path:
                sys.path.remove(script_dir_str)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tool schemas (expose native pgvector operations to the model)
# ---------------------------------------------------------------------------

PGVECTOR_SEARCH_SCHEMA = {
    "name": "pgvector_search",
    "description": (
        "Search the Glena agent's persistent long-term memory (PostgreSQL pgvector + "
        "Cloudflare embeddings). Returns semantically relevant facts ranked by relevance. "
        "Always call this before answering questions that could depend on prior context: "
        "preferences (food, habits, people), past decisions, ongoing projects, family details, "
        "or anything mentioned in previous turns not visible in the current window. For multi-hop "
        "questions, run multiple searches with different wording and combine results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query describing what to recall.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum results (default 10, max 30).",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}

PGVECTOR_ADD_SCHEMA = {
    "name": "pgvector_add",
    "description": (
        "Store a durable fact about the user, the environment, or the conversation in Glena's "
        "long-term pgvector memory. Call the moment the user states a lasting preference, "
        "personal detail, correction, or decision worth recalling — don't wait to be asked. "
        "The fact is embedded with Cloudflare bge-large-en-v1.5 (1024 dims) and stored with "
        "hybrid vector + full-text search for reliable future recall. Skip transient chit-chat, "
        "temporary intents, or implementation details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The verbatim fact to store.",
            },
            "category": {
                "type": "string",
                "description": "Category for organization: preferencia | evento | tarefa | pessoal | tecnico | culinaria | saude | outra",
                "default": "pessoal",
            },
        },
        "required": ["content"],
    },
}

PGVECTOR_UPDATE_SCHEMA = {
    "name": "pgvector_update",
    "description": (
        "Update an existing memory entry (identified by its content text). Use when a stored "
        "fact has changed or was wrong — correct it in place instead of adding a duplicate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "old_content": {
                "type": "string",
                "description": "The existing content to replace (use exact text from pgvector_search result).",
            },
            "new_content": {
                "type": "string",
                "description": "The corrected content.",
            },
        },
        "required": ["old_content", "new_content"],
    },
}

PGVECTOR_DELETE_SCHEMA = {
    "name": "pgvector_delete",
    "description": (
        "Delete a memory entry by its content text. Use when a stored fact is obsolete or the user asks to forget it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The content text of the entry to delete.",
            },
        },
        "required": ["content"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class PgvectorMemoryProvider(MemoryProvider):
    """Glena profile's PostgreSQL pgvector memory backend.

    Connects to the Dockerized pgvector instance (glena_memory_db, port 5433,
    DB glena_memory) that stores agent_memories with hybrid vector + FTS search,
    multi-scope isolation (user=gleidson / agent=glena / session), categories,
    entities, confidence, linked task/project/program IDs, and soft delete.
    """

    def __init__(self):
        self._cfg: dict = {}
        self._script_dir: Optional[_Path] = None
        self._connection_ok: bool = False
        self._last_check_monotonic: float = 0.0
        self._check_interval_sec: float = 30.0

    # ------------------------------------------------------------------
    # Identity / config
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "pgvector"

    def is_available(self) -> bool:
        """Check config + connection, but do not block. Cached briefly."""
        import time

        if time.monotonic() - self._last_check_monotonic < self._check_interval_sec:
            return self._connection_ok
        self._last_check_monotonic = time.monotonic()
        cfg = self._load_config_safe()
        # Must have a database URL configured (either via memory_config import
        # or a direct DATABASE_URL env override).
        has_db = bool(cfg.get("database_url") and cfg.get("database_url").strip())
        if not has_db:
            self._connection_ok = False
            return False
        # Try to connect quickly.
        self._connection_ok = self._test_connection_fast(cfg)
        return self._connection_ok

    def _test_connection_fast(self, cfg: dict) -> bool:
        """Quick, non-blocking liveness probe of the pgvector database.

        Opens a short-timeout psycopg2 connection and runs SELECT 1. Any
        failure (container down, DB unreachable, psycopg2 missing) returns
        False instead of raising — the provider must never block the agent
        loop or crash initialization.
        """
        try:
            import psycopg2

            url = cfg.get("database_url", "")
            if not url:
                return False
            # Strip password for logs — never log credentials.
            conn = psycopg2.connect(url, connect_timeout=3)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            finally:
                conn.close()
            return True
        except Exception as exc:
            logger.debug("pgvector fast connection test failed: %s", exc)
            return False

    def unavailable_reason(self) -> str:
        return "pgvector Docker container not running or DATABASE_URL not configured (run scripts/memory_ensure.py --start)"

    def _load_config_safe(self) -> dict:
        if self._cfg:
            return self._cfg
        self._cfg = _read_config()
        return self._cfg

    def initialize(self, session_id: str, **kwargs) -> None:
        self._load_config_safe()
        # Ensure the pgvector container is running (best-effort, non-blocking).
        # We don't fail initialization if the container isn't up — the tool
        # calls will fail gracefully with clear errors, and memory_ensure
        # can be run independently.
        if not self._connection_ok:
            try:
                _ensure_pgvector()
            except Exception as exc:
                logger.debug("pgvector container not available at initialize(): %s", exc)

    # ------------------------------------------------------------------
    # Prompt / prefetch / sync hooks
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self.is_available():
            return ""
        return (
            "# Glena Long-Term Memory (pgvector)\n"
            f"Active. Scope: user={self._load_config_safe().get('user_scope', 'gleidson')} "
            f"agent={self._load_config_safe().get('agent_scope', 'glena')}.\n"
            "The agent has persistent semantic memory stored in a PostgreSQL pgvector database. "
            "Use pgvector_search before answering anything that could depend on past context. "
            "Use pgvector_add when a lasting preference, personal fact, correction, or important event is mentioned. "
            "Memory supports categories (preferencia, evento, tarefa, pessoal, tecnico, culinaria, saude, outra) "
            "and links to tasks/projects via linked_program_id/project_id/task_id.\n"
            "Search is hybrid (vector cosine + full-text Portuguese). Deduplication is automatic by content hash."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.is_available():
            return ""
        try:
            script_dir = _resolve_script_dir()
            # Call the existing memory_context.py script, which does a hybrid
            # search and formats results as context lines.
            result = subprocess.run(
                [sys.executable, str(script_dir / "memory_context.py"), query],
                capture_output=True, text=True, timeout=10,
            )
            out = result.stdout.strip()
            if out and out != "Nenhuma memória relevante encontrada.":
                return out
            return ""
        except Exception as exc:
            logger.debug("Pgvector prefetch failed: %s", exc)
            return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # Best-effort sync — don't fail the agent turn.
        # The pgvector system manages writes via the memory_store.py script
        # (called through the pgvector_add tool) and through the built-in
        # memory() tool when the plugin is active. We don't attempt an
        # automatic ingestion of every turn here to avoid duplicating what
        # the user's explicit memory writes already cover, and to avoid
        # the complexity of deciding what is worth storing automatically.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            PGVECTOR_SEARCH_SCHEMA,
            PGVECTOR_ADD_SCHEMA,
            PGVECTOR_UPDATE_SCHEMA,
            PGVECTOR_DELETE_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self.is_available():
            return tool_error("Pgvector memory backend not available. Check that the Docker container is running (scripts/memory_ensure.py --start) and DATABASE_URL is configured.")
        cfg = self._load_config_safe()
        try:
            if tool_name == "pgvector_search":
                return self._handle_search(args, cfg)
            if tool_name == "pgvector_add":
                return self._handle_add(args, cfg)
            if tool_name == "pgvector_update":
                return self._handle_update(args, cfg)
            if tool_name == "pgvector_delete":
                return self._handle_delete(args, cfg)
            return tool_error(f"Unknown pgvector tool: {tool_name}")
        except Exception as exc:
            return tool_error(f"Pgvector error: {exc}")

    # ------------------------------------------------------------------
    # Tool handlers — delegate to existing scripts
    # ------------------------------------------------------------------

    def _handle_search(self, args: dict, cfg: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        top_k = max(1, min(30, int(args.get("top_k", 10))))
        script_dir = _resolve_script_dir()
        # memory_search.py takes query and outputs JSON results.
        result = subprocess.run(
            [sys.executable, str(script_dir / "memory_search.py"), query],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return tool_error(f"Memory search failed: {result.stderr.strip()[:200]}")
        try:
            search_result = json.loads(result.stdout)
        except Exception:
            # Fallback to raw stdout if not JSON.
            return result.stdout.strip()
        # Format for the agent.
        results = search_result.get("results", []) if isinstance(search_result, dict) else search_result
        if not results:
            return json.dumps({"results": [], "count": 0, "message": "No relevant memories found."})
        formatted = []
        for item in results[:top_k]:
            entry = {
                "id": item.get("id", item.get("memory_id", "")),
                "content": item.get("content", item.get("memory", "")),
                "category": item.get("category", ""),
                "confidence": item.get("confidence", 1.0),
            }
            formatted.append(entry)
        return json.dumps({"results": formatted, "count": len(formatted)})

    def _handle_add(self, args: dict, cfg: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")
        category = args.get("category", "pessoal")
        # Build the JSON fact and write to stdin for memory_store.py.
        fact = {
            "content": content,
            "category": category,
            "confidence": 1.0,
            "source": "whatsapp",
        }
        script_dir = _resolve_script_dir()
        result = subprocess.run(
            [sys.executable, str(script_dir / "memory_store.py")],
            input=json.dumps([fact]),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return tool_error(f"Memory store failed: {result.stderr.strip()[:200]}")
        try:
            stats = json.loads(result.stdout)
        except Exception:
            stats = {"stored": 1, "skipped": 0, "errors": 0}
        return json.dumps({"stored": stats.get("stored", 1), "skipped": stats.get("skipped", 0), "preview": content[:120]})

    def _handle_update(self, args: dict, cfg: dict) -> str:
        old_content = args.get("old_content", "")
        new_content = args.get("new_content", "")
        if not old_content or not new_content:
            return tool_error("old_content and new_content are required")
        # The pgvector schema uses content_hash for dedup and doesn't have an
        # easy "update by old text" mechanism at the SQL level (would require a
        # SELECT by hash then UPDATE). We implement this as: delete the old entry
        # by content match, then add the new entry.
        # First, try to delete the old entry (best-effort by content text match
        # using LIKE on the content column via a terminal SQL call through the
        # memory scripts). We use a Python inline SQL approach via psycopg2.
        # If delete fails (no match), we just add the new content.
        try:
            import psycopg2

            # Resolve memory_config from the profile's scripts dir (it is NOT on
            # sys.path inside the agent process — only the repo root is).
            script_dir = _resolve_script_dir()
            if str(script_dir) not in sys.path:
                sys.path.insert(0, str(script_dir))
            from memory_config import DATABASE_URL

            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            with conn.cursor() as cur:
                # Find matching entry by content text (exact or partial).
                cur.execute(
                    "SELECT id FROM agent_memories WHERE deleted = FALSE AND user_scope = %s AND agent_scope = %s AND (content = %s OR content LIKE %s) LIMIT 1",
                    (cfg.get("user_scope", "gleidson"), cfg.get("agent_scope", "glena"), old_content, f"%{old_content[:60]}%"),
                )
                row = cur.fetchone()
                if row:
                    # Soft delete the old entry.
                    cur.execute(
                        "UPDATE agent_memories SET deleted = TRUE, deleted_at = NOW() WHERE id = %s",
                        (row[0],),
                    )
                    conn.commit()
                else:
                    conn.rollback()
            conn.close()
        except Exception as exc:
            # Non-fatal for update — if delete fails, just add new.
            logger.debug("Pgvector delete (update) best-effort failed: %s", exc)

        # Now store the new content.
        result = subprocess.run(
            [sys.executable, str(_resolve_script_dir() / "memory_store.py")],
            input=json.dumps([{"content": new_content, "category": args.get("category", "pessoal"), "source": "whatsapp", "confidence": 1.0}]),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return tool_error(f"Memory update failed (delete may have failed): {result.stderr.strip()[:200]}")
        try:
            stats = json.loads(result.stdout)
        except Exception:
            stats = {"stored": 1, "skipped": 0, "errors": 0}
        return json.dumps({"updated": stats.get("stored", 1) > 0, "old_deleted": row is not None if 'row' in locals() else False, "new_content_preview": new_content[:120]})

    def _handle_delete(self, args: dict, cfg: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")
        try:
            import psycopg2

            # Resolve memory_config from the profile's scripts dir (it is NOT on
            # sys.path inside the agent process — only the repo root is).
            script_dir = _resolve_script_dir()
            if str(script_dir) not in sys.path:
                sys.path.insert(0, str(script_dir))
            from memory_config import DATABASE_URL

            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            deleted = False
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_memories SET deleted = TRUE, deleted_at = NOW() WHERE deleted = FALSE AND user_scope = %s AND agent_scope = %s AND (content = %s OR content LIKE %s)",
                    (cfg.get("user_scope", "gleidson"), cfg.get("agent_scope", "glena"), content, f"%{content[:60]}%"),
                )
                deleted = cur.rowcount > 0
                conn.commit()
            conn.close()
            if deleted:
                return json.dumps({"deleted": True, "message": f"Memory entry deleted: {content[:60]}..."})
            return json.dumps({"deleted": False, "message": "No matching memory entry found to delete."})
        except Exception as exc:
            return tool_error(f"Delete failed: {exc}")

    # ------------------------------------------------------------------
    # Optional hooks (minimal overrides)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "database_url",
                "description": "PostgreSQL connection URL for pgvector (default reads from profile scripts).",
                "required": False,
                "env_var": "PGVECTOR_DATABASE_URL",
            },
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        # No-op: this plugin uses the existing profile-level memory_config
        # module rather than a native JSON config file.
        pass

    def backup_paths(self) -> List[str]:
        # The pgvector database is managed externally via Docker (memory_ensure.py)
        # and is NOT stored inside HERMES_HOME. It must be backed up separately
        # via `pg_dump` or Docker volume snapshots. Declare no extra paths here.
        return []


def register(ctx) -> None:
    ctx.register_memory_provider(PgvectorMemoryProvider())
