# Miniville — agent entry point

Autonomous town simulation seeded by NVIDIA Nemotron-Personas-USA.

**First, every session: read `docs/STATE.md`** (mandate, decisions, blockers,
resume pointer). Update it before finishing.

**Two agents share this repo** (workstation + cloud Devin). Pull before working,
push small commits to `autodev` often, and coordinate via `docs/AGENT_SYNC.md`
(claims + append-only messages).

VaultWares protocols apply via `vaultwares-docs/instructions/ROUTER.md` —
notably: branch `autodev` (PRs to main for operator), no unapproved request
loops (Ollama narration batches need operator OK), agent-ledger entry before
replying, no git worktrees.

## Commands

```powershell
.\.venv\Scripts\python.exe -m miniville.cli init --agents 500   # bootstrap (resets world!)
.\.venv\Scripts\python.exe -m miniville.cli run --ticks 96      # advance 2 days
.\.venv\Scripts\python.exe -m miniville.cli status
.\.venv\Scripts\python.exe -m miniville.cli inspect "Name"
.\.venv\Scripts\python.exe -m miniville.cli chronicle 3         # day digest
.\.venv\Scripts\python.exe -m miniville.cli narrate --day 3     # local Ollama prose (bounded)
.\.venv\Scripts\python.exe -m pytest -q
```

Env: `MINIVILLE_DB` overrides `data/miniville.db`. Dataset: `E:\Nemotron-Personas-USA`.
