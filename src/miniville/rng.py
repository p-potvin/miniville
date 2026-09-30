"""Deterministic RNG helpers.

All randomness in the sim derives from (world_seed, tick, domain, keys) so a
run is fully reproducible and independent of call ordering. No global RNG
state is used anywhere.
"""
from __future__ import annotations

import hashlib
import random


def seed_int(world_seed: str, *parts: object) -> int:
    """Hash arbitrary parts into a stable 64-bit int."""
    h = hashlib.blake2b(digest_size=8)
    h.update(str(world_seed).encode())
    for p in parts:
        h.update(b"|")
        h.update(str(p).encode())
    return int.from_bytes(h.digest(), "big")


def rng_for(world_seed: str, *parts: object) -> random.Random:
    """A fresh Random() seeded from the parts."""
    return random.Random(seed_int(world_seed, *parts))


def chance(world_seed: str, p: float, *parts: object) -> bool:
    """Deterministic bernoulli draw."""
    r = rng_for(world_seed, *parts)
    return r.random() < p
