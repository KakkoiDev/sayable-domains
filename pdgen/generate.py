"""Combinatorial generation of candidate names over the safe alphabet."""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator

from . import phonetics as ph


def alphabet(extended: bool = False) -> tuple[list[str], list[str]]:
    cons = list(ph.CONSONANTS)
    if extended:
        cons += list(ph.CONSONANTS_EXTENDED)
    return sorted(cons), sorted(ph.VOWELS)


def expand(pattern: str, extended: bool = False) -> Iterator[str]:
    """Expand a C/V pattern into every string it can produce.

    'CVCVCV' -> 'bababa', 'babab e', ... (final -e is filtered downstream)
    """
    pattern = pattern.upper()
    if set(pattern) - {"C", "V"}:
        raise ValueError(f"pattern must use only C and V, got {pattern!r}")
    cons, vows = alphabet(extended)
    pools = [cons if slot == "C" else vows for slot in pattern]
    for combo in itertools.product(*pools):
        yield "".join(combo)


def count(pattern: str, extended: bool = False) -> int:
    cons, vows = alphabet(extended)
    n = 1
    for slot in pattern.upper():
        n *= len(cons) if slot == "C" else len(vows)
    return n


def viable(name: str) -> bool:
    """Hard structural rejects, applied before the (costlier) scorer."""
    if name[-1] in ph.BANNED_FINAL:
        return False
    if set(name) & ph.BANNED_LETTERS:
        return False
    # No doubled letters anywhere, even across a syllable boundary.
    if any(a == b for a, b in zip(name, name[1:])):
        return False
    return True


def candidates(
    patterns: list[str],
    extended: bool = False,
    min_score: float = 0.0,
    limit: int | None = None,
    sample: float = 1.0,
    seed: int | None = None,
) -> Iterator[dict]:
    """Yield scored, structurally-viable candidates."""
    rng = random.Random(seed)
    produced = 0
    for pattern in patterns:
        for name in expand(pattern, extended):
            if sample < 1.0 and rng.random() > sample:
                continue
            if not viable(name):
                continue
            sc, breakdown = ph.score(name)
            if sc < min_score:
                continue
            yield {
                "name": name,
                "score": sc,
                "breakdown": breakdown,
                "pattern": pattern.upper(),
                "syllables": ph.syllables(name),
                "length": len(name),
                "ipa": ph.to_ipa(name),
            }
            produced += 1
            if limit is not None and produced >= limit:
                return
