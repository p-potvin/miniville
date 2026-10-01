r"""One pass of the avatar refresh pipeline: ingest -> verify -> re-cast.

The male identity pool is smaller than the demand, so fixing the residents who
hold a wrong-sex identity has to proceed incrementally as the IMDb scraper
grows `G:\Galleries\Celebrities`. This runs the three steps in order:

  1. ingest  — drain staged downloads into the source `gallery.db`
               (ColONEL-KFC `ingest_staged.py`; the scraper only downloads).
  2. verify  — resolve any new identities' sex via TMDB
               (`verify_celebrity_gender.py`, cached and resumable).
  3. re-cast — give the mismatched residents a verified male identity
               (`recase_avatars.py`, idempotent).

Every step is resumable, so this is safe to run on a timer while the scraper
is still going.

Run:
    .\.venv\Scripts\python.exe scripts\refresh_avatars.py [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KFC = Path(r"C:\Users\Administrator\Desktop\Github Repos\ColONEL-KFC")
KFC_PY = KFC / ".venv" / "Scripts" / "python.exe"
MV_PY = REPO / ".venv" / "Scripts" / "python.exe"

# child output is UTF-8 and may contain characters the Windows console codepage
# cannot encode; never let a log line kill the pass
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                               # noqa: BLE001
        pass


def run(label: str, cmd: list[str], cwd: Path) -> int:
    print(f"\n=== {label} ===", flush=True)
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
    for ln in tail[-12:]:
        print("  " + ln, flush=True)
    if p.returncode:
        err = (p.stderr or "").strip().splitlines()
        for ln in err[-6:]:
            print("  ! " + ln, flush=True)
    return p.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-ingest", action="store_true")
    args = ap.parse_args(argv)

    rc = 0
    if not args.skip_ingest and KFC_PY.is_file():
        rc |= run("ingest staged identities",
                  [str(KFC_PY), "ingest_staged.py"], KFC)
    rc |= run("verify celebrity sex (TMDB)",
              [str(KFC_PY), str(REPO / "scripts" / "verify_celebrity_gender.py")], KFC)
    recase = [str(MV_PY), str(REPO / "scripts" / "recase_avatars.py")]
    if args.dry_run:
        recase.append("--dry-run")
    rc |= run("re-cast mismatched residents", recase, REPO)
    return rc


if __name__ == "__main__":
    sys.exit(main())
