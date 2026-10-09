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
coming of age, immigration, mortality, estates), an economy (rent, prices,
pensions, businesses that residents own, found, buy and lose, wages that move
with unemployment), a housing market that sorts districts by money,
affiliations (congregations and clubs), an elected council whose motions move
real numbers, grudges that turn into slander, boycotts and schisms, petty
crime, and a newspaper with an owner and a line.

No dataset at hand (a cloud session, say)? Generate a stand-in with the same
columns and soak that:

```bash
python scripts/synth_personas.py --out data/synth-personas --n 12000
MINIVILLE_DB=data/synth.db python -m miniville.cli init --dataset data/synth-personas --agents 500
python scripts/soak_check.py --run 365 --tag mine --source data/synth.db
```

Project memory between sessions: [`docs/STATE.md`](docs/STATE.md). Design notes:
[`docs/DESIGN.md`](docs/DESIGN.md), [`docs/ECONOMY.md`](docs/ECONOMY.md),
[`docs/AVATARS.md`](docs/AVATARS.md), [`docs/NARRATION.md`](docs/NARRATION.md).
