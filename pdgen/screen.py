"""Screen candidates against a blocklist of offensive substrings.

Random CV strings will eventually spell something unfortunate in a language you
don't speak. The bundled list is a starting point only -- it covers common
offenders that are reachable from the safe alphabet. Before you ship anything,
point --blocklist at a real multilingual list, e.g.

    https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words

Only entries spellable with the safe alphabet can ever match, so a large list
costs almost nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

BUNDLED = Path(__file__).parent / "data" / "blocklist.txt"


def load(paths: list[str] | None = None) -> set[str]:
    terms: set[str] = set()
    files = [BUNDLED] + [Path(p) for p in (paths or [])]
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip().lower()
            if not line or line.startswith("#"):
                continue
            # Strip anything unspellable in the safe alphabet; if what remains
            # is too short it would match everything, so drop it.
            line = re.sub(r"[^a-z]", "", line)
            if len(line) >= 3:
                terms.add(line)
    return terms


def blocked(name: str, terms: set[str]) -> str | None:
    """Return the offending substring, or None if the name is clean."""
    for t in terms:
        if t in name:
            return t
    return None


def filter_names(names: list[str], terms: set[str]) -> tuple[list[str], list[tuple[str, str]]]:
    clean, rejected = [], []
    for n in names:
        hit = blocked(n, terms)
        if hit:
            rejected.append((n, hit))
        else:
            clean.append(n)
    return clean, rejected
