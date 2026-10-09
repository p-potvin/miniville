r"""Write a dataset-shaped stand-in for Nemotron-Personas-USA.

The real dataset lives on the workstation (`E:\Nemotron-Personas-USA`) and is
not reachable from a cloud session. This writes parquet shards with the same
columns `ingest.load_persona_rows` reads, drawn from rough US marginals, so a
cloud agent can `init` a full town and soak it the same way the workstation
does. The personas are thin (one templated sentence) — good enough for every
system that reads a field, not for narration.

    python scripts/synth_personas.py --out data/synth-personas --n 4000
    python -m miniville.cli init --dataset data/synth-personas --agents 500

Deterministic for a given --seed.
"""
from __future__ import annotations

import argparse
import random
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

FIRST_F = ["Mary", "Linda", "Patricia", "Susan", "Karen", "Nancy", "Lisa",
           "Betty", "Sandra", "Ashley", "Emily", "Jessica", "Sarah", "Maria",
           "Ana", "Mei", "Priya", "Aisha", "Grace", "Hannah", "Rosa", "Denise",
           "Tanya", "Laverne", "Jenni", "Olivia", "Sofia", "Keisha", "Yuki"]
FIRST_M = ["James", "Robert", "John", "Michael", "David", "William", "Richard",
           "Joseph", "Thomas", "Charles", "Daniel", "Matthew", "Anthony",
           "Mark", "Carlos", "Luis", "Wei", "Raj", "Omar", "Xavier", "Aditya",
           "Marcus", "Tyrone", "Kenji", "Ethan", "Noah", "Diego", "Samuel"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson",
        "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
        "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson", "Walker",
        "Young", "Allen", "Nguyen", "Patel", "Kim", "Chen", "Yu", "Pacheco",
        "Furness", "Miles", "Okafor", "Novak", "Rossi", "Schmidt", "Kowalski"]
CITIES = [("Columbus", "OH"), ("Austin", "TX"), ("Fresno", "CA"),
          ("Tampa", "FL"), ("Omaha", "NE"), ("Tucson", "AZ"),
          ("Raleigh", "NC"), ("Spokane", "WA"), ("Albany", "NY"),
          ("Dayton", "OH"), ("Mobile", "AL"), ("Boise", "ID"),
          ("Madison", "WI"), ("Richmond", "VA"), ("Provo", "UT")]
# (occupation, weight) — the strings deliberately hit world.OCCUPATION_MAP
OCCUPATIONS = [
    ("registered_nurse", 4), ("physician", 1), ("medical_assistant", 2),
    ("elementary_school_teacher", 3), ("teacher_assistant", 2),
    ("police_officer", 1), ("office_clerk", 3), ("postal_service_clerk", 1),
    ("cook", 3), ("waiter_waitress", 3), ("bartender", 1), ("barista", 1),
    ("retail_salesperson", 4), ("cashier", 4), ("customer_service_representative", 3),
    ("electrician", 2), ("carpenter", 2), ("truck_driver", 3), ("mechanic", 2),
    ("construction_laborer", 2), ("maintenance_worker", 2),
    ("writer_or_author", 1), ("graphic_designer", 1), ("reporter", 1),
    ("accountant", 2), ("software_developer", 2), ("manager", 3),
    ("not_in_workforce", 10), ("Retired", 0),
]
EDUCATION = [("less_than_9th", 3), ("high_school", 27), ("some_college", 20),
             ("associates", 9), ("bachelors", 22), ("graduate", 14),
             ("9th_12th_no_diploma", 5)]
HOBBIES = ["reading", "fishing", "hiking", "gardening", "cooking", "baking",
           "woodworking", "knitting", "painting", "playing guitar",
           "singing in a choir", "photography", "volunteering", "running",
           "cycling", "watching football", "baseball", "board games",
           "birdwatching", "poetry", "pottery", "grilling", "crafts", "chess"]
SKILLS = ["organization", "communication", "budgeting", "repair work",
          "patient care", "teaching", "writing", "carpentry", "cooking",
          "customer service", "driving", "bookkeeping"]
# ~28% name a tradition, matching what groups.py measured on the real data
FAITH = [("Catholic", 10.4), ("Protestant", 9.0), ("Baptist", 2.9),
         ("Methodist", 2.4), ("Lutheran", 1.6), ("Jewish", 0.6),
         ("Muslim", 0.4), ("Hindu", 0.4), ("Quaker", 0.2)]


def _pick(rnd: random.Random, table):
    names, weights = zip(*table)
    return rnd.choices(names, weights=weights)[0]


def persona(rnd: random.Random) -> dict:
    sex = rnd.choice(["Female", "Male"])
    age = min(95, max(18, int(rnd.triangular(18, 92, 38))))
    first = rnd.choice(FIRST_F if sex == "Female" else FIRST_M)
    name = f"{first} {rnd.choice(LAST)}"
    if age < 25:
        marital = _pick(rnd, [("never_married", 85), ("married_present", 15)])
    elif age < 65:
        marital = _pick(rnd, [("married_present", 52), ("never_married", 28),
                              ("divorced", 13), ("separated", 3), ("widowed", 4)])
    else:
        marital = _pick(rnd, [("married_present", 50), ("widowed", 28),
                              ("divorced", 14), ("never_married", 8)])
    occ = "Retired" if age >= 67 and rnd.random() < 0.85 else _pick(rnd, OCCUPATIONS)
    city, state = rnd.choice(CITIES)
    hobbies = rnd.sample(HOBBIES, rnd.randint(2, 5))
    faith_roll = rnd.uniform(0, 100)
    acc, faith = 0.0, None
    for label, pct in FAITH:
        acc += pct
        if faith_roll < acc:
            faith = label
            break
    background = (f"Raised in {city}, {state}"
                  + (f" in a {faith} family that still attends services." if faith
                     else ", with family roots that shape their values."))
    job = occ.replace("_", " ")
    return {
        "uuid": str(uuid.UUID(int=rnd.getrandbits(128))),
        "persona": f"{name} is a {age}-year-old {job} who enjoys "
                   f"{hobbies[0]} and {hobbies[1]}.",
        "professional_persona": f"{name} works as a {job}, known for "
                                f"{rnd.choice(SKILLS)}.",
        "sex": sex, "age": age, "marital_status": marital,
        "education_level": _pick(rnd, EDUCATION),
        "bachelors_field": rnd.choice(["business", "education", "stem",
                                       "arts_humanities", ""]),
        "occupation": occ, "city": city, "state": state,
        "hobbies_and_interests_list": str(hobbies),
        "skills_and_expertise_list": str(rnd.sample(SKILLS, 3)),
        "cultural_background": background,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synth-personas")
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--shards", type=int, default=2)
    ap.add_argument("--seed", default="synth")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    out = Path(a.out) / "data"
    out.mkdir(parents=True, exist_ok=True)
    per = -(-a.n // a.shards)
    for s in range(a.shards):
        rows = [persona(rnd) for _ in range(min(per, a.n - s * per))]
        pq.write_table(pa.Table.from_pylist(rows),
                       out / f"train-{s:05d}-of-{a.shards:05d}.parquet")
    print(f"wrote {a.n} personas to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
