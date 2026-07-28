"""The JSON database, schema 2.

Schema 1 keyed everything by bare name and assumed .com. Schema 2 separates
what is true about a *name* (its score, what it means in other languages) from
what is true about a *domain* (whether it is registered under .com or .org):

    names:
      midako:
        score: 93.2
        flags: []
        meanings: [{lang: "Finnish", src: "wiktionary"}]
        meaning_checked_at: "..."
        tlds:
          com: {status, confidence, checked_at, source, flags}
          net: {...}

Confidence ladder, tracked per TLD:

    generated  scored only, never checked against anything
    dns        absent from (or present in) the zone
    rdap       the registry itself answered
    registrar  a registrar confirmed it, including premium pricing

Only `registrar` is authoritative about whether you can actually buy it.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

CONFIDENCE_LEVELS = ["generated", "dns", "rdap", "registrar"]
CONFIDENCE_RANK = {name: i for i, name in enumerate(CONFIDENCE_LEVELS)}

# Registry statuses meaning "taken now, but dropping". These are the pearls:
# a short name in redemption goes back into the pool within weeks.
DROPPING = {"redemptionperiod", "pendingdelete"}

# Score bands. The CLI works through candidates in this order and the site
# labels them, so "how far did we get" has a name rather than a number.
TIERS = [("S", 95.0), ("A", 92.0), ("B", 89.0), ("C", 85.0), ("D", 0.0)]


def tier_of(score: float) -> str:
    for label, floor in TIERS:
        if score >= floor:
            return label
    return "D"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def days_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def empty() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "primary_tld": "com",
        "created_at": now(),
        "updated_at": now(),
        "names": {},
    }


def blank_tld() -> dict:
    return {"status": "unknown", "confidence": "generated",
            "checked_at": None, "source": None, "flags": []}


def _migrate_v1(data: dict) -> dict:
    """Lift a schema-1 file into schema 2 rather than making the user rebuild."""
    out = empty()
    out["created_at"] = data.get("created_at", now())
    for name, rec in data.get("domains", {}).items():
        entry = {
            "score": rec.get("score", 0.0),
            "flags": [f for f in rec.get("flags", []) if f],
            "meanings": [],
            "meaning_checked_at": None,
            "tlds": {"com": {
                "status": rec.get("status", "unknown"),
                "confidence": rec.get("confidence", "generated"),
                "checked_at": rec.get("checked_at"),
                "source": rec.get("source"),
                "flags": [],
            }},
        }
        if rec.get("demo"):
            entry["demo"] = True
        out["names"][name] = entry
    return out


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{p} is not valid JSON ({e}). Move it aside and re-run.")

    schema = data.get("schema")
    if schema == 1:
        print(f"  migrating {p} from schema 1 to schema 2 ...")
        data = _migrate_v1(data)
    elif schema != SCHEMA_VERSION:
        raise SystemExit(f"{p} uses schema {schema}, this build expects {SCHEMA_VERSION}.")
    data.setdefault("names", {})
    return data


def save(db: dict, path: str | Path) -> None:
    """Atomic write, one name per line.

    Compact JSON keeps a 100k-name db near 10 MB instead of 40 MB, and the
    one-record-per-line layout keeps diffs readable. The file is gitignored by
    default and lives in a GitHub release instead -- see `pdgen release`.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db["updated_at"] = now()
    header = {k: v for k, v in db.items() if k != "names"}
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("{\n")
            for k in sorted(header):
                fh.write(f"{json.dumps(k)}: {json.dumps(header[k])},\n")
            fh.write('"names": {\n')
            names = sorted(db["names"])
            for i, name in enumerate(names):
                rec = json.dumps(db["names"][name], separators=(",", ":"),
                                 sort_keys=True, ensure_ascii=False)
                comma = "," if i < len(names) - 1 else ""
                fh.write(f"{json.dumps(name)}:{rec}{comma}\n")
            fh.write("}\n}\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add_candidate(db: dict, cand: dict) -> bool:
    """Insert a generated candidate. Returns False if it was already known."""
    name = cand["name"]
    if name in db["names"]:
        db["names"][name]["score"] = cand["score"]
        return False
    db["names"][name] = {
        "score": cand["score"],
        "flags": cand["breakdown"].get("flags", []),
        "meanings": [],
        "meaning_checked_at": None,
        "tlds": {"com": blank_tld()},
    }
    return True


def tld_rec(db: dict, name: str, tld: str) -> dict:
    return db["names"][name]["tlds"].setdefault(tld, blank_tld())


def record_check(db, name, tld, status, confidence, source, flags=None, force=False) -> None:
    """Apply a result, refusing to downgrade an existing stronger one."""
    if name not in db["names"]:
        return
    t = tld_rec(db, name, tld)
    if not force and CONFIDENCE_RANK[confidence] < CONFIDENCE_RANK[t["confidence"]]:
        return
    t.update(status=status, confidence=confidence, source=source, checked_at=now())
    if flags:
        t["flags"] = sorted(set(t.get("flags", [])) | set(flags))
    db["names"][name].pop("demo", None)


def is_dropping(t: dict) -> bool:
    return bool(set(t.get("flags", [])) & DROPPING)


def queue(db, tld="com", below="generated", older_than=None,
          min_score=0.0, top=None, only_taken=False) -> list[str]:
    """Names needing work on `tld`, highest-scoring first.

    Score ordering is the whole point: a run that gets interrupted, or a budget
    that only covers 10% of the space, should have spent itself on the best
    candidates rather than on alphabetical accident.
    """
    ceiling = CONFIDENCE_RANK[below]
    out = []
    for name, rec in db["names"].items():
        if rec["score"] < min_score:
            continue
        t = rec["tlds"].get(tld)
        if only_taken and (t is None or t["status"] != "taken"):
            continue
        if t is None:
            out.append(name)
            continue
        weak = CONFIDENCE_RANK[t["confidence"]] <= ceiling
        stale = older_than is not None and days_since(t["checked_at"]) > older_than
        if weak or stale:
            out.append(name)
    out.sort(key=lambda n: (-db["names"][n]["score"], n))
    return out[:top] if top else out


def stats(db: dict, tld: str = "com") -> dict:
    out = {"total": len(db["names"]), "by_tier": {}, "by_status": {},
           "by_confidence": {}, "available_by_confidence": {},
           "available_by_tier": {}, "dropping": 0, "with_meanings": 0,
           "syllables": {}, "tlds": {}}
    for name, rec in db["names"].items():
        tier = tier_of(rec["score"])
        out["by_tier"][tier] = out["by_tier"].get(tier, 0) + 1
        syl = sum(1 for c in name if c in "aeiou")
        out["syllables"][syl] = out["syllables"].get(syl, 0) + 1
        if rec.get("meanings"):
            out["with_meanings"] += 1
        for tl, t in rec["tlds"].items():
            out["tlds"][tl] = out["tlds"].get(tl, 0) + 1
            if tl != tld:
                continue
            out["by_status"][t["status"]] = out["by_status"].get(t["status"], 0) + 1
            out["by_confidence"][t["confidence"]] = out["by_confidence"].get(t["confidence"], 0) + 1
            if is_dropping(t):
                out["dropping"] += 1
            if t["status"] == "available":
                c, tr = t["confidence"], tier
                out["available_by_confidence"][c] = out["available_by_confidence"].get(c, 0) + 1
                out["available_by_tier"][tr] = out["available_by_tier"].get(tr, 0) + 1
    return out
