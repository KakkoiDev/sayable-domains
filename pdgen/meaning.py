"""Does this made-up string already mean something, somewhere?

Two sources, both optional and both run only on a shortlist:

  wiktionary  en.wiktionary.org has ~1.3M entries covering hundreds of
              languages on shared pages, so one lookup per name tells you every
              language that string is a word in. Reliable, batched 50 at a
              time, well-behaved under polite use. This is the default.

  panlex      api.panlex.org indexes ~20M expressions across ~9,000 language
              varieties -- far wider coverage, especially for languages with no
              Wiktionary presence. Its own docs describe the API as intended
              for small-scale experimental use, so it is rate-limited hard here
              and is opt-in.

A hit is not automatically bad. "Means 'star' in Swahili" is the pearl you were
looking for; "means something crude in Tagalog" is the landmine the blocklist
missed. The tool reports, you judge.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

UA = "sayable/2.0 (domain name research; contact: you@example.com)"

WIKTIONARY_API = "https://en.wiktionary.org/w/api.php"
PANLEX_API = "https://api.panlex.org/v2/expr"

# Wiktionary page sections that are not languages.
NON_LANGUAGE_SECTIONS = {
    "references", "see also", "further reading", "anagrams", "notes",
    "external links", "usage notes", "alternative forms",
}


def _post(url: str, payload: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get(url: str, params: dict, timeout: float = 20.0) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# --- Wiktionary -------------------------------------------------------------

def wiktionary_existing(names: Iterable[str]) -> set[str]:
    """Which of these strings have a Wiktionary page at all? 50 per request."""
    names = list(names)
    found: set[str] = set()
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        try:
            data = _get(WIKTIONARY_API, {
                "action": "query", "titles": "|".join(batch),
                "format": "json", "formatversion": "2",
            })
        except Exception:
            continue
        for page in data.get("query", {}).get("pages", []):
            if not page.get("missing"):
                found.add(page["title"].lower())
        time.sleep(0.2)
    return found


def wiktionary_languages(name: str) -> list[dict]:
    """Top-level sections on a Wiktionary page are language names."""
    try:
        data = _get(WIKTIONARY_API, {
            "action": "parse", "page": name, "prop": "sections",
            "format": "json", "formatversion": "2",
        })
    except urllib.error.HTTPError:
        return []
    except Exception:
        return []
    out = []
    for sec in data.get("parse", {}).get("sections", []):
        if str(sec.get("toclevel")) != "1":
            continue
        line = (sec.get("line") or "").strip()
        if line and line.lower() not in NON_LANGUAGE_SECTIONS:
            out.append({"lang": line, "src": "wiktionary"})
    return out


# --- PanLex -----------------------------------------------------------------

def panlex_meanings(name: str, limit: int = 12) -> list[dict]:
    """Expressions matching this string across PanLex language varieties.

    UNVERIFIED against the live API -- see HANDOFF.md. The request shape follows
    the v2 /expr documentation but has not been exercised, so failures are
    swallowed rather than allowed to abort a run.
    """
    try:
        data = _post(PANLEX_API, {
            "txt": [name], "include": ["uid"], "limit": limit,
        })
    except Exception:
        return []
    out = []
    for item in data.get("result", []):
        uid = item.get("uid") or item.get("langvar")
        if uid:
            out.append({"lang": str(uid), "src": "panlex"})
    return out


# --- orchestration ----------------------------------------------------------

def lookup(
    names: list[str],
    sources: tuple[str, ...] = ("wiktionary",),
    rps: float = 4.0,
    on_result: Callable[[str, list[dict]], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Look up meanings for a shortlist, reporting each name as it resolves.

    Single-threaded on purpose. These are courtesy APIs run by nonprofits; a
    24-thread stampede is how a project gets blocked for everyone.
    """
    interval = 1.0 / rps if rps > 0 else 0.0
    total = len(names)

    # One cheap batched pass tells us which names are worth a page fetch.
    candidates = set(names)
    if "wiktionary" in sources:
        candidates = wiktionary_existing(names)

    for i, name in enumerate(names, 1):
        hits: list[dict] = []
        if "wiktionary" in sources and name in candidates:
            hits += wiktionary_languages(name)
            if interval:
                time.sleep(interval)
        if "panlex" in sources:
            hits += panlex_meanings(name)
            if interval:
                time.sleep(interval)
        # Deduplicate on (language, source).
        seen, unique = set(), []
        for h in hits:
            key = (h["lang"].lower(), h["src"])
            if key not in seen:
                seen.add(key)
                unique.append(h)
        if on_result:
            on_result(name, unique)
        if on_progress:
            on_progress(i, total)
