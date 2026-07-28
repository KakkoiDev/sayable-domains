"""Estimate how long a check run takes, and how far a time budget gets you.

The whole point of ranking candidates before checking them is that you will
almost never check all of them. So the useful question is not "how long does
the full sweep take" but "what refinement level do I reach in the time I have".
Both directions are answered here.

The absent-rate -- the share of names that survive the DNS stage and therefore
cost an RDAP call -- is calibrated from whatever the db has already seen, so
estimates get more accurate the more you run.
"""

from __future__ import annotations

import re

from . import db as dbmod

# Fallbacks until the db has enough observations to calibrate against.
# Short .com is picked over; longer names survive DNS far more often.
DEFAULT_ABSENT_RATE = {4: 0.01, 5: 0.05, 6: 0.30, 7: 0.55, 8: 0.75}

DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])")


def parse_duration(text: str) -> float:
    """'90m', '2h30m', '1d' -> seconds."""
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    parts = DURATION.findall(text.lower().strip())
    if not parts:
        raise ValueError(f"could not read a duration from {text!r}; try 30m, 2h, 1d")
    return sum(float(n) * mult[u] for n, u in parts)


def human(seconds: float) -> str:
    if seconds < 1:
        return "instant"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400 * 2:
        h, m = divmod(int(seconds // 60), 60)
        return f"{h}h {m:02d}m" if m else f"{h}h"
    return f"{seconds / 86400:.1f}d"


def absent_rate(db: dict, tld: str = "com", length: int | None = None) -> tuple[float, str]:
    """Share of names that pass DNS and cost an RDAP call. (rate, provenance)."""
    seen = passed = 0
    for name, rec in db["names"].items():
        if length is not None and len(name) != length:
            continue
        t = rec["tlds"].get(tld)
        if not t or t["confidence"] == "generated":
            continue
        seen += 1
        # Anything RDAP looked at, or DNS called available, got past the filter.
        if dbmod.CONFIDENCE_RANK[t["confidence"]] >= 2 or t["status"] == "available":
            passed += 1
    if seen >= 200:
        return passed / seen, f"measured from {seen:,} checks"
    fallback = DEFAULT_ABSENT_RATE.get(length or 6, 0.30)
    return fallback, "estimated (not enough history yet)"


def seconds_for(n: int, stage: str, rate: float, dns_rps: float,
                rdap_rps: float, has_zone: bool) -> float:
    """Wall-clock estimate for pushing n names through the funnel.

    The DNS and RDAP stages overlap inside the thread pool, so the run is
    bounded by whichever limiter saturates first, not by their sum.
    """
    if n <= 0:
        return 0.0
    dns_time = 0.0 if has_zone else n / max(dns_rps, 0.001)
    if stage in ("zone", "dns"):
        return dns_time
    rdap_time = (n * rate) / max(rdap_rps, 0.001)
    if stage == "registrar":
        # Registrar calls only happen for names RDAP said were free, and share
        # the same limiter, so they extend the RDAP leg rather than overlap it.
        rdap_time += (n * rate * 0.35) / max(rdap_rps, 0.001)
    return max(dns_time, rdap_time)


def tier_rows(db: dict, tld: str, stage: str, below: str, older_than: float | None,
              dns_rps: float, rdap_rps: float, has_zone: bool) -> list[dict]:
    """Per-tier work remaining and the time to clear it."""
    buckets: dict[str, dict] = {
        label: {"tier": label, "floor": floor, "names": 0, "todo": 0,
                "dns": 0, "rdap": 0, "stale": 0, "available": 0}
        for label, floor in dbmod.TIERS
    }
    todo = set(dbmod.queue(db, tld=tld, below=below, older_than=older_than))

    for name, rec in db["names"].items():
        b = buckets[dbmod.tier_of(rec["score"])]
        b["names"] += 1
        if name in todo:
            b["todo"] += 1
        t = rec["tlds"].get(tld)
        if t:
            conf = dbmod.CONFIDENCE_RANK[t["confidence"]]
            if conf == 1:
                b["dns"] += 1
            elif conf >= 2:
                b["rdap"] += 1
            if t["status"] == "available":
                b["available"] += 1
            if older_than is not None and t["checked_at"] and \
                    dbmod.days_since(t["checked_at"]) > older_than:
                b["stale"] += 1

    rows = []
    cumulative = 0.0
    for label, _ in dbmod.TIERS:
        b = buckets[label]
        if not b["names"]:
            continue
        rate, _ = absent_rate(db, tld)
        b["seconds"] = seconds_for(b["todo"], stage, rate, dns_rps, rdap_rps, has_zone)
        cumulative += b["seconds"]
        b["cumulative"] = cumulative
        rows.append(b)
    return rows


def budget_reach(db: dict, tld: str, stage: str, below: str, older_than: float | None,
                 seconds: float, dns_rps: float, rdap_rps: float,
                 has_zone: bool) -> dict:
    """How far down the ranked queue a given time budget gets you."""
    q = dbmod.queue(db, tld=tld, below=below, older_than=older_than)
    if not q:
        return {"names": 0, "min_score": None, "tier": None, "share": 1.0, "total": 0}
    rate, _ = absent_rate(db, tld)
    per_name = seconds_for(1000, stage, rate, dns_rps, rdap_rps, has_zone) / 1000
    reach = len(q) if per_name <= 0 else min(len(q), int(seconds / per_name))
    if reach <= 0:
        return {"names": 0, "min_score": None, "tier": None, "share": 0.0, "total": len(q)}
    last = q[reach - 1]
    score = db["names"][last]["score"]
    return {"names": reach, "min_score": score, "tier": dbmod.tier_of(score),
            "share": reach / len(q), "total": len(q)}
