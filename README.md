# miniville

Miniville — simulation of a mini ville and its inhabitants. Database rows living their best life, interacting with each other and procedurally discovering love and laughter, tragedy and deception in the Scenic, Quaint, Lush town of Miniville.

Residents are seeded from [NVIDIA Nemotron-Personas-USA](https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA) (CC BY 4.0). The world persists in one SQLite file; every tick is deterministic and replayable. An optional local-LLM layer (Ollama) narrates notable moments — it never alters simulation state.

## Quickstart

```powershell
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m miniville.cli init --agents 500   # bootstrap (resets world)
.\.venv\Scripts\python.exe -m miniville.cli run --ticks 96      # advance 2 days
.\.venv\Scripts\python.exe -m miniville.cli status
.\.venv\Scripts\python.exe -m miniville.cli chronicle 3
.\.venv\Scripts\python.exe -m pytest -q
```

The town has a calendar (four seasons, twelve holidays), a lifecycle (births,
coming of age, immigration, mortality) and an economy (rent, prices, businesses
that can fail, wages that move with unemployment).

Project memory between sessions: [`docs/STATE.md`](docs/STATE.md). Design notes:
[`docs/DESIGN.md`](docs/DESIGN.md), [`docs/ECONOMY.md`](docs/ECONOMY.md),
[`docs/AVATARS.md`](docs/AVATARS.md), [`docs/NARRATION.md`](docs/NARRATION.md).
