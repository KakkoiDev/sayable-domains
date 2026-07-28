"""Export a compact snapshot of the db for the static site.

Positional rows rather than objects: at a few thousand names that is roughly a
third of the size, which matters when GitHub Pages is serving it to a phone.

This file *is* committed to the repo, unlike db.json. GitHub Pages has to serve
it to the browser, and release assets cannot be fetched cross-origin.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import db as dbmod
from . import phonetics as ph
from . import screen as screenmod

STATUSES = ["unknown", "available", "taken"]
FIELDS = ["name", "score", "tier", "syllables", "length", "confidence",
          "status", "checked_at", "flags", "modifiers", "meanings", "alternates"]


def phoneme_table() -> dict:
    letters = sorted(set(ph.VOWELS) | set(ph.CONSONANTS) | set(ph.CONSONANTS_EXTENDED))
    return {ch: {"ipa": ph.IPA.get(ch, ch), "tier": ph.tier(ch),
                 "w": round(ph.weight(ch), 2), "note": ph.NOTES.get(ch, "")}
            for ch in letters}


# Names whose scores the browser re-derives at load. If its arithmetic drifts
# from the Python scorer's, the console says so instead of failing silently.
SCORE_FIXTURES = ["midako", "tesabu", "bepako", "kamin", "kulaudo",
                  "pasokon", "kaido", "naomi", "bebeko", "kanban"]


def scoring_spec() -> dict:
    """Everything the browser needs to score a name it just coined."""
    return {
        "base_scale": 90, "cons_weight": 0.62, "vowel_weight": 0.38,
        "variety_bonus": 3, "repeated_syllable": -10, "monovocalic": -6,
        "liquid_clash": -12, "triple_consonant": -5, "short_bonus": 3,
        "length_penalty_per_letter": -1.5, "length_penalty_cap": -6,
        "length_penalty_from": 6, "banned_final": sorted(ph.BANNED_FINAL),
        "vowel_runs": ph.VOWEL_RUNS, "vowel_run_default": 0.5,
        "vowel_run_scale": 14, "triphthong": -25, "coda": ph.CODA,
        "banned_letters": sorted(ph.BANNED_LETTERS),
        "fixtures": {n: ph.score(n)[0] for n in SCORE_FIXTURES},
    }


def build(db, tld="com", min_score=0.0, min_confidence="dns", limit=5000,
          include_taken=False, include_dropping=True) -> dict:
    floor = dbmod.CONFIDENCE_RANK[min_confidence]
    rows, demo_rows = [], 0

    for name, rec in db["names"].items():
        t = rec["tlds"].get(tld)
        if not t or rec["score"] < min_score:
            continue
        if dbmod.CONFIDENCE_RANK[t["confidence"]] < floor:
            continue

        dropping = dbmod.is_dropping(t)
        if t["status"] != "available" and not include_taken:
            # A name in redemption is taken today and free soon. That is the
            # most valuable thing this tool can surface, so it stays.
            if not (dropping and include_dropping):
                continue

        alternates = {
            other: STATUSES.index(o["status"]) if o["status"] in STATUSES else 0
            for other, o in rec["tlds"].items()
            if other != tld and o["confidence"] != "generated"
        }

        rows.append([
            name,
            rec["score"],
            dbmod.tier_of(rec["score"]),
            ph.syllables(name),
            len(name),
            dbmod.CONFIDENCE_RANK[t["confidence"]],
            STATUSES.index(t["status"]) if t["status"] in STATUSES else 0,
            (t["checked_at"] or "")[:10],
            sorted(set(t.get("flags", [])) | set(rec.get("flags", []))),
            # Shipped so the detail panel explains a rank using the same
            # numbers the CLI used, rather than reimplementing the scorer.
            [[m["label"], m["delta"]] for m in ph.score(name)[1]["modifiers"]],
            [m["lang"] for m in rec.get("meanings", [])],
            alternates,
        ])
        if rec.get("demo"):
            demo_rows += 1

    rows.sort(key=lambda r: (-r[1], r[0]))
    truncated = limit is not None and len(rows) > limit
    if truncated:
        rows = rows[:limit]

    return {
        "generated_at": dbmod.now(),
        "tld": tld,
        "schema": dbmod.SCHEMA_VERSION,
        "confidence_levels": dbmod.CONFIDENCE_LEVELS,
        "statuses": STATUSES,
        "tiers": [t[0] for t in dbmod.TIERS],
        "tier_floors": {t[0]: t[1] for t in dbmod.TIERS},
        "dropping_flags": sorted(dbmod.DROPPING),
        "fields": FIELDS,
        "phonemes": phoneme_table(),
        "scoring": scoring_spec(),
        # Small enough to ship, and it means names coined in the browser get
        # screened against exactly the list the CLI uses.
        "blocklist": sorted(screenmod.load(None)),
        "db_stats": dbmod.stats(db, tld),
        "published": len(rows),
        "truncated": truncated,
        "demo": demo_rows > 0,
        "rdap_endpoint": "https://rdap.verisign.com/com/v1/domain/",
        "rows": rows,
    }


def api_description(payload: dict) -> dict:
    """Machine-readable endpoint description, emitted beside the snapshot.

    Paths are relative on purpose. A GitHub project page serves the site from
    /<repo>/, so an absolute "/data/domains.json" resolves to the account root
    and 404s. Relative paths work under a subpath, a custom domain, and a local
    http.server alike.
    """
    return {
        "name": "sayable",
        "description": "Ranked, availability-checked domain names built from "
                       "cross-linguistically pronounceable sounds.",
        "agent_guide": "skill.md",
        "paths_are": "relative to this file's directory's parent (the site root)",
        "endpoints": {
            "data/domains.json": {
                "description": "Full ranked snapshot.",
                "format": "positional rows; see `fields` for column order",
                "fields": payload["fields"],
                "row_example": payload["rows"][0] if payload["rows"] else None,
                "keys": {
                    "rows": "the data", "fields": "column names, in order",
                    "phonemes": "per-letter universality weight, IPA and a note",
                    "scoring": "constants for re-deriving a score, plus fixtures",
                    "blocklist": "obscenity substrings, same list the CLI uses",
                    "db_stats": "counts by tier, status and confidence",
                    "generated_at": "when this snapshot was taken",
                    "demo": "true means seeded fake data -- do not trust it",
                },
            },
            "data/cmudict/index.json": {
                "description": "Manifest for the sharded pronunciation dictionary.",
                "shard_url": "data/cmudict/{first two letters}.json",
                "shard_format": "{word: phoneme string}",
                "note": "Absent unless `pdgen dictionary build` has run.",
            },
            "skill.md": {"description": "Agent operating guide.", "format": "markdown"},
            "llms.txt": {"description": "Human-readable orientation for agents."},
        },
        "confidence_levels": {
            "generated": "scored only, never checked -- says nothing about availability",
            "dns": "absent from the zone; MISSES registered-but-undelegated names. A lead.",
            "rdap": "the registry itself answered. Trustworthy.",
            "registrar": "a registrar confirmed purchasability and price. Authoritative.",
        },
        "cautions": [
            "Never report a 'dns' result as available.",
            "Check `checked_at`; results older than ~21 days are stale.",
            "`demo: true` means the snapshot is seeded fake data.",
            "The score is a hypothesis about pronounceability, never validated on speakers.",
            "Availability is not trademark clearance.",
        ],
        "snapshot": {
            "generated_at": payload["generated_at"], "tld": payload["tld"],
            "published": payload["published"], "demo": payload["demo"],
        },
    }


def write(payload: dict, path: str | Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    p.write_text(text, encoding="utf-8")
    # Regenerated every publish; a hand-maintained copy would go stale silently.
    (p.parent / "api.json").write_text(
        json.dumps(api_description(payload), indent=1), encoding="utf-8")
    return len(text.encode("utf-8"))
