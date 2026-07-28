"""Fill the db with plausible, deterministic fake check results.

Use this to preview the website before running a real check pass. Every record
it writes is marked demo:true, and `pdgen publish` propagates that flag so the
site shows a warning banner instead of quietly presenting invented data.

    python3 tools/seed_demo.py --db db.json --limit 4000

Wipe it later with:

    python3 tools/seed_demo.py --db db.json --clear
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdgen import db as dbmod  # noqa: E402

# Roughly matches what a real 6-letter CVCVCV .com sweep turns up, and mixes
# the confidence levels so the "full check" path in the UI has something to do.
CONF_MIX = [("dns", 0.62), ("rdap", 0.33), ("registrar", 0.05)]
AVAILABLE_RATE = 0.34


def det(name: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}:{name}".encode()).digest()
    return int.from_bytes(h[:6], "big") / float(1 << 48)


def pick_conf(r: float) -> str:
    acc = 0.0
    for level, share in CONF_MIX:
        acc += share
        if r <= acc:
            return level
    return "dns"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db.json")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--clear", action="store_true", help="reset every demo record")
    args = ap.parse_args()

    db = dbmod.load(args.db)

    if args.clear:
        n = 0
        for rec in db["names"].values():
            if rec.pop("demo", False):
                for t in rec["tlds"].values():
                    t.update(status="unknown", confidence="generated",
                             checked_at=None, source=None, flags=[])
                n += 1
        dbmod.save(db, args.db)
        print(f"  cleared {n:,} demo records")
        return 0

    # Stratify by syllable count so the preview exercises every filter,
    # rather than showing only the 5-letter names that top the raw ranking.
    ranked = sorted(db["names"], key=lambda n: -db["names"][n]["score"])
    per_class = max(1, args.limit // 4)
    picked, seen = [], set()
    for target in (2, 3, 4):
        got = 0
        for n in ranked:
            if got >= per_class:
                break
            if n in seen or sum(1 for c in n if c in "aeiou") != target:
                continue
            picked.append(n); seen.add(n); got += 1
    for n in ranked:
        if len(picked) >= args.limit:
            break
        if n not in seen:
            picked.append(n); seen.add(n)
    names = picked
    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}

    for name in names:
        rec = db["names"][name]
        conf = pick_conf(det(name, "conf"))
        available = det(name, "avail") < AVAILABLE_RATE
        age = int(det(name, "age") * 45)

        flags = []
        if available and conf == "registrar" and det(name, "prem") < 0.12:
            flags.append("premium")
        if not available and conf in ("rdap", "registrar") and det(name, "life") < 0.05:
            flags.append("redemptionperiod")

        rec["tlds"]["com"] = {
            "status": "available" if available else "taken",
            "confidence": conf,
            "source": {"dns": "doh", "rdap": "rdap:com",
                       "registrar": "registrar:porkbun"}[conf],
            "checked_at": (now - timedelta(days=age)).replace(microsecond=0).isoformat(),
            "flags": flags,
        }
        rec["demo"] = True
        # A few alternates, so the site has something to show for taken .com
        if not available and det(name, "alt") < 0.25:
            for alt in ("net", "org"):
                rec["tlds"][alt] = {
                    "status": "available" if det(name, alt) < 0.5 else "taken",
                    "confidence": "rdap", "source": f"rdap:{alt}",
                    "checked_at": (now - timedelta(days=age)).replace(microsecond=0).isoformat(),
                    "flags": [],
                }
        # Occasional meaning hits, so the meanings UI is exercised
        if det(name, "mean") < 0.06:
            rec["meanings"] = [{"lang": ["Finnish", "Swahili", "Indonesian", "Tagalog",
                                         "Basque", "Malagasy"][int(det(name, "lang") * 6)],
                                "src": "wiktionary"}]
            rec["meaning_checked_at"] = now.replace(microsecond=0).isoformat()
        if available:
            counts[conf] = counts.get(conf, 0) + 1

    dbmod.save(db, args.db)
    print(f"  seeded {len(names):,} demo results")
    print("  available by confidence: " + "  ".join(f"{k}={v:,}" for k, v in sorted(counts.items())))
    print("  these are NOT real lookups -- run `pdgen check` before trusting anything")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
