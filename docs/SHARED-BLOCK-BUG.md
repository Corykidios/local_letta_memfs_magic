# Shared Block Git Commit Skip Bug

**Severity:** High — silently loses git history for shared memory blocks
**Affected versions:** Letta v0.16.6 (and likely earlier)
**Fix:** [oculairmedia/letta@fix/shared-block-git-commit](https://github.com/oculairmedia/letta/tree/fix/shared-block-git-commit)

---

## Symptom

When updating a memory block that is shared across multiple agents, the
git commit silently does not happen. The block update succeeds in
Postgres but the git repository shows no new commit. Container logs show:

```
[GIT_PERF] update_block_async TOTAL 181.42ms (postgres-only path)
```

instead of the expected:

```
[GIT_PERF] update_block_async TOTAL 317.53ms (git-enabled path)
```

---

## Root Cause

`_get_agent_id_for_block` in `block_manager_git.py` is responsible for
finding which agent "owns" a block so it can check whether that agent
has git memory enabled. The original implementation:

```python
async def _get_agent_id_for_block(self, block_id, actor):
    """Get the agent ID that owns a block."""
    async with db_registry.async_session() as session:
        from sqlalchemy import select
        from letta.orm.blocks_agents import BlocksAgents
        result = await session.execute(
            select(BlocksAgents.agent_id)
            .where(BlocksAgents.block_id == block_id)
        )
        row = result.first()  # <-- Non-deterministic for shared blocks
        return row[0] if row else None
```

For a block shared across 50 agents, `result.first()` returns whichever
agent the database happens to return first (depends on row insertion
order, index scan order, etc.). If that agent doesn't have the
`git-memory-enabled` tag, `_is_git_enabled_for_agent` returns `False`
and the entire git path is skipped.

Both `update_block_async` and `delete_block_async` call this method, so
both are affected.

---

## The Fix

Use a `LEFT JOIN` against the `agents_tags` table with a `CASE ORDER BY`
to sort git-enabled agents to the top:

```python
async def _get_agent_id_for_block(self, block_id, actor):
    """Get the agent ID that owns a block, preferring git-enabled agents.

    For shared blocks, a LEFT JOIN + CASE ORDER BY ensures git-enabled
    agents are returned first so block updates trigger git commits.
    """
    async with db_registry.async_session() as session:
        from sqlalchemy import case, select
        from letta.orm.agents_tags import AgentsTags
        from letta.orm.blocks_agents import BlocksAgents

        git_priority = case(
            (AgentsTags.tag == GIT_MEMORY_ENABLED_TAG, 0),
            else_=1,
        )
        result = await session.execute(
            select(BlocksAgents.agent_id)
            .outerjoin(
                AgentsTags,
                (AgentsTags.agent_id == BlocksAgents.agent_id)
                & (AgentsTags.tag == GIT_MEMORY_ENABLED_TAG),
            )
            .where(BlocksAgents.block_id == block_id)
            .order_by(git_priority)
            .limit(1)
        )
        row = result.first()
        return row[0] if row else None
```

This single method fix resolves the bug for both `update_block_async`
and `delete_block_async`.

---

## Verification

Before the fix (shared `matrix_capabilities` block, 50 agents):
```
[GIT_PERF] update_block_async TOTAL 181.42ms (postgres-only path)
```

After the fix:
```
[GIT_PERF] update_block_async TOTAL 317.53ms (git-enabled path)
[GIT_PERF] memory_repo_manager.update_block_async took 69.19ms commit=b971e1d3
```

Git log confirms the commit:
```
b971e1d Update matrix_capabilities block
513b089 Update expression block
38099ef Initial commit
```

---

## How to Apply

### Option A: Use the oculairmedia fork

The fix is on the `fix/shared-block-git-commit` branch:
```bash
git clone https://github.com/oculairmedia/letta.git
cd letta
git checkout fix/shared-block-git-commit
```

### Option B: Patch the running container

```bash
# Copy the fixed file into the container
docker cp block_manager_git.py letta-letta-1:/app/letta/services/block_manager_git.py
docker restart letta-letta-1
```

### Option C: Volume mount override

Add to your `compose.yaml`:
```yaml
volumes:
  - ./overrides/letta/services/block_manager_git.py:/app/letta/services/block_manager_git.py:ro
```

---

## Context: Why Blocks Are Shared

When `enable_git_memory_for_agent` runs, it transforms the agent's block
labels (e.g. `human` becomes `system/human`) and creates new blocks with
the prefixed labels. However, the original shared blocks (like `human`)
remain attached to all the other agents that were using them.

This means that a git-enabled agent like Meridian might have:
- `system/human` — unique to Meridian, stored in git
- `matrix_capabilities` — shared across 50 agents, also stored in git

Updates to the shared block must still trigger git commits for Meridian,
which is what this fix ensures.
