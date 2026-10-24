# Glena Pgvector Memory Plugin

Plugin que conecta o Hermes `memory()` tool ao banco pgvector da Glena.

## Arquitetura

- **Container Docker**: `glena_memory_db` (porta 5433, rede `lead_service_default`)
- **DB**: `glena_memory` (user `glena`, password `glena_memory_local`)
- **Embeddings**: Cloudflare Workers AI (`@cf/baai/bge-large-en-v1.5`, 1024 dims)
- **Schema**: `agent_memories` (hybrid search: HNSW vector + GIN FTS português)

## Ativação

No `config.yaml` do perfil `glena`:

```yaml
memory:
  provider: pgvector
```

Ou via env:
```bash
HERMES_MEMORY_PROVIDER=pgvector
```

O plugin lê `DATABASE_URL` da configuração do `memory_config.py` (que lê `.env.memory` + `.env`). Se precisar de senha diferente, adicione ao `.env.memory`:

```bash
GLENA_DB_HOST=127.0.0.1
GLENA_DB_PORT=5433
GLENA_DB_NAME=glena_memory
GLENA_DB_USER=glena
GLENA_DB_PASSWORD=glena_memory_local
```

## Verificação do container

```bash
python3 scripts/memory_ensure.py --start
python3 scripts/memory_ensure.py --json
```

Se o container não estiver rodando, o plugin retorna erro claro: "Pgvector memory backend not available. Check that the Docker container is running..."

## Tools expostas

- `pgvector_search(query, top_k=10)` — busca híbrida (vector + texto)
- `pgvector_add(content, category="pessoal")` — armazena fato (embedding automático)
- `pgvector_update(old_content, new_content)` — atualiza por texto
- `pgvector_delete(content)` — remove por texto (soft delete via SQL)

## Integração com memória infinita

O sistema usa:
- Dedup automático (`content_hash` SHA256)
- Multi-scope (`user_scope=gleidson`, `agent_scope=glena`, `session_scope` opcional)
- Soft delete (`deleted=TRUE`, `deleted_at=NOW()`)
- Confiança (`confidence` 0.0-1.0)
- Categorias (`preferencia`, `evento`, `pessoal`, `tecnico`, `culinaria`, `saude`, etc.)
- Entidades (`entities[]` para linking)
- Vinculação com tarefas/projetos (`linked_program_id/project_id/task_id`)

Isso garante que a memória seja "infinita" — nunca perde fatos, apenas marca como deletados, e a busca híbrida recupera rapidamente o que é relevante.

## Scripts que continuam funcionando

O plugin delega aos scripts existentes:
- `memory_context.py` → busca contexto para injeção no prompt
- `memory_store.py` → armazena fatos com embedding
- `memory_search.py` → wrapper de busca
- `memory_ensure.py` → saúde do container
- `memory_config.py` → configuração do DB + embeddings

Não substitui esses scripts — apenas expõe uma interface `MemoryProvider` para o agente usar nativamente.
