r"""Health check for a long run: the invariants a living town should hold.

A soak tells you that something drifted; it does not tell you what. Every
failure found so far came from staring at a number in a CSV and then writing a
throwaway probe to attribute it — the money leak, the hungry children, the
social firehose, the compounding wages. This makes that reading standing: run
it against any soak database and it says which invariants hold and which
broke, with the number that broke them.

    .\.venv\Scripts\python.exe scripts\soak_check.py data/soak-conf2y.db
    .\.venv\Scripts\python.exe scripts\soak_check.py --run 365 --tag check

Each invariant is a claim about a *town*, not about a function:

  money is conserved        the supply does not drain or balloon
  no deficit spiral         the town is not quietly minting its own payroll
  employment holds          working-age unemployment stays in a band
  venues are staffed        no open venue sits far below its target
  wages do not compound     pay does not outgrow what businesses can charge
  businesses do not churn   closures stay rare
  the graph has structure   the town is not a fog of one-off meetings
  the town has friction     somebody dislikes somebody
  people are content        the town is not miserable
  the town stays eventful   things keep happening (the point of the whole thing)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from miniville import db as dbmod, economy, jobs  # noqa: E402


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, ok: bool, name: str, detail: str) -> None:
        self.rows.append((bool(ok), name, detail))

    def show(self) -> int:
        failed = 0
        for ok, name, detail in self.rows:
            mark = "ok  " if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"  [{mark}] {name:26s} {detail}")
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} invariants hold")
        return 1 if failed else 0


def _series(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM economy_days WHERE money_supply_cents > 0 ORDER BY day").fetchall()


def run_checks(conn: sqlite3.Connection, r: Report) -> None:
    days = conn.execute("SELECT MAX(day) FROM events").fetchone()[0] or 1
    first_day = conn.execute("SELECT MIN(day) FROM economy_days").fetchone()[0] or 0
    span = max(1, days - first_day)
    years = span / 365

    # --- money is conserved
    series = _series(conn)
    if len(series) >= 2:
        start, end = series[0]["money_supply_cents"], series[-1]["money_supply_cents"]
        drift = (end - start) / max(1, start) / max(0.01, years)
        r.check(abs(drift) < 0.10, "money is conserved",
                f"supply {start/100:,.0f} -> {end/100:,.0f} over {years:.1f}y "
                f"({drift*100:+.1f}%/y)")
    else:
        r.check(False, "money is conserved", "no economy_days rows to read")

    # --- no deficit spiral
    deficits = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE data LIKE '%town_deficit%'").fetchone()["n"]
    per_year = deficits / max(0.01, years)
    r.check(per_year < 12, "no deficit spiral",
            f"{deficits} municipal deficits ({per_year:.1f}/y)")

    # --- employment holds
    adults = conn.execute(
        "SELECT COUNT(*) n FROM agents WHERE alive=1 AND is_child=0 AND age<65"
    ).fetchone()["n"]
    unemployed = conn.execute(
        """SELECT COUNT(*) n FROM agents a WHERE a.alive=1 AND a.is_child=0
           AND a.age>=18 AND a.age<65 AND NOT EXISTS
           (SELECT 1 FROM jobs j WHERE j.agent_id=a.id)""").fetchone()["n"]
    rate = unemployed / max(1, adults)
    r.check(0.02 <= rate <= 0.25, "employment holds",
            f"{rate*100:.1f}% of {adults} working-age adults out of work")

    # --- venues are staffed
    targets = jobs.venue_targets(conn)
    staff = {row["place_id"]: row["n"] for row in conn.execute(
        "SELECT place_id, COUNT(*) n FROM jobs GROUP BY place_id")}
    open_places = {row["place_id"] for row in conn.execute(
        """SELECT p.id place_id FROM places p LEFT JOIN businesses b ON b.place_id=p.id
           WHERE p.kind != 'home' AND COALESCE(b.status,'open')='open'""")}
    thin = [(pid, targets.get(pid, 0), staff.get(pid, 0)) for pid in open_places
            if targets.get(pid, 0) >= 6 and staff.get(pid, 0) < targets.get(pid, 0) * 0.5]
    names = ", ".join(
        conn.execute("SELECT name FROM places WHERE id=?", (pid,)).fetchone()["name"]
        for pid, _t, _s in thin[:3])
    r.check(not thin, "venues are staffed",
            f"{len(thin)} open venues below half their target"
            + (f": {names}" if names else ""))

    # --- venues can pay their way
    # Compare the two running totals rather than dividing by a day count: a
    # soak inherits its source's cumulative revenue but only accrues its own
    # days, so revenue/days understates the takings several-fold. (That
    # mistake had me briefly convinced the price fix had failed when every
    # venue was in fact profitable.)
    agg = conn.execute(
        """SELECT COALESCE(SUM(b.revenue_total),0) rev,
                  COALESCE(SUM(b.payroll_total),0) pay
           FROM businesses b JOIN places p ON p.id = b.place_id
           WHERE p.tags NOT LIKE '%health%' AND p.tags NOT LIKE '%education%'
             AND p.tags NOT LIKE '%civic%' AND p.tags NOT LIKE '%office%'
             AND p.tags NOT LIKE '%media%' AND p.tags NOT LIKE '%worship%'
             AND p.tags NOT LIKE '%community%'""").fetchone()
    ratio = agg["pay"] / max(1, agg["rev"])
    r.check(ratio < 1.0, "venues can pay their way",
            f"commercial venues have paid out {ratio:.2f}x what they took in "
            f"(${agg['pay']/100:,.0f} vs ${agg['rev']/100:,.0f} all-time)")

    # --- wages do not compound
    wages = [row["wage_cents"] for row in conn.execute("SELECT wage_cents FROM jobs")]
    if wages:
        wages.sort()
        med = wages[len(wages) // 2]
        top = wages[-1]
        ratio = top / max(1, med)
        r.check(ratio < 3.0, "wages do not compound",
                f"top wage is {ratio:.1f}x the median ({top/100:,.0f} vs {med/100:,.0f})")
    else:
        r.check(False, "wages do not compound", "nobody is employed")

    # --- businesses do not churn
    closed = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE data LIKE '%business_closed%'").fetchone()["n"]
    per_year = closed / max(0.01, years)
    r.check(per_year < 6, "businesses do not churn",
            f"{closed} closures ({per_year:.1f}/y)")

    # --- the graph has structure
    rel = conn.execute("SELECT COUNT(*) n FROM relationships").fetchone()["n"]
    thin_ties = conn.execute(
        "SELECT COUNT(*) n FROM relationships WHERE familiarity < 3").fetchone()["n"]
    share = thin_ties / max(1, rel)
    r.check(share < 0.75, "the graph has structure",
            f"{share*100:.0f}% of {rel:,} ties are one-off meetings "
            f"(a fog is 90%+)")

    # --- the town has friction
    rivals = conn.execute(
        "SELECT COUNT(*) n FROM relationships WHERE affinity <= -20").fetchone()["n"]
    r.check(rivals >= 3, "the town has friction",
            f"{rivals} rivalries (a town with none has no story)")

    # --- people are content
    moods = {row["mood"]: row["n"] for row in conn.execute(
        "SELECT mood, COUNT(*) n FROM agent_state s JOIN agents a ON a.id=s.agent_id "
        "WHERE a.alive=1 GROUP BY mood")}
    alive = sum(moods.values()) or 1
    bad = (moods.get("miserable", 0) + moods.get("hungry", 0)) / alive
    r.check(bad < 0.10, "people are content",
            f"{bad*100:.1f}% miserable or hungry ({dict(moods)})")

    # --- the town stays eventful
    events = conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
    per_day = events / max(1, span)
    r.check(per_day > 20, "the town stays eventful",
            f"{per_day:.0f} events a day")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default=None)
    ap.add_argument("--run", type=int, default=0, help="soak N days first")
    ap.add_argument("--tag", default="check")
    ap.add_argument("--source", default=None, help="db to soak (with --run)")
    a = ap.parse_args()

    if a.run:
        from soak import main as soak_main  # type: ignore
        sys.argv = ["soak.py", "--days", str(a.run), "--tag", a.tag, "--quiet"]
        if a.source:
            sys.argv += ["--source", a.source]
        soak_main()
        path = REPO / "data" / f"soak-{a.tag}.db"
    else:
        path = Path(a.db) if a.db else REPO / "data" / "miniville.db"
    if not path.is_file():
        print(f"no database at {path}")
        return 2

    conn = dbmod.connect(path)
    print(f"checking {path.name}")
    r = Report()
    run_checks(conn, r)
    return r.show()


if __name__ == "__main__":
    sys.exit(main())
