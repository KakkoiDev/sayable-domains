"""pdgen -- generate, rank, check and publish pronounceable domain candidates.

The workflow is two passes plus optional refinements:

    pass 1  generate   scored, obscenity-screened candidates. No network.
    pass 2  check      availability, best candidates first, resumable.
            meaning    what these strings mean in other languages
            alternates other TLDs for names whose .com is gone
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import check as checkmod
from . import db as dbmod
from . import dictionary as dictmod
from . import generate as gen
from . import lock as lockmod
from . import meaning as meaningmod
from . import phonetics as ph
from . import plan as planmod
from . import publish as pubmod
from . import release as releasemod
from . import screen as screenmod

DEFAULT_DB = "db.json"
DEFAULT_OUT = "docs/data/domains.json"
# CVVCV and CVVC bring in diphthong names (kaido, naumi, taiko) that strict
# CV alternation cannot express. Cheap: 12,500 and 2,500 combinations.
DEFAULT_PATTERNS = ["CVCV", "CVCVC", "CVVCV", "CVVC", "CVCVCV"]
STALE_DAYS = 21

_stop = threading.Event()


def _install_sigint() -> None:
    def handler(signum, frame):
        if _stop.is_set():
            sys.exit(130)
        _stop.set()
        print("\n  finishing in-flight checks and saving; Ctrl-C again to force",
              file=sys.stderr)
    signal.signal(signal.SIGINT, handler)


def _emit_ci_outputs(**values) -> None:
    """Publish step outputs when running inside GitHub Actions.

    Lets the workflow decide whether to chain another run without parsing
    human-formatted output. A no-op everywhere else.
    """
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
    except OSError:
        pass


def _bar(done: int, total: int, extra: str = "") -> None:
    width = 26
    frac = done / total if total else 1.0
    filled = int(frac * width)
    sys.stderr.write(f"\r  [{'#' * filled}{'.' * (width - filled)}] {done}/{total} {extra}   ")
    sys.stderr.flush()


def _rule(width: int = 66) -> None:
    print("  " + "-" * width)


# --- pass 1: generate -------------------------------------------------------

def cmd_generate(args) -> int:
    db = dbmod.load(args.db)
    terms = screenmod.load(args.blocklist)
    patterns = args.pattern or DEFAULT_PATTERNS

    total = sum(gen.count(p, args.extended) for p in patterns)
    print(f"  patterns  {', '.join(patterns)}")
    print(f"  space     {total:,} raw combinations")
    if total > 2_000_000 and args.sample >= 1.0 and not args.yes:
        print(f"\n  That is a large space. Expect a few minutes and a big db.")
        print(f"  Consider --sample 0.05 or a higher --min-score, or pass --yes.")
        return 1

    started = time.time()
    added = rescored = blocked = 0
    for cand in gen.candidates(patterns, extended=args.extended,
                               min_score=args.min_score, limit=args.limit,
                               sample=args.sample, seed=args.seed):
        if screenmod.blocked(cand["name"], terms):
            blocked += 1
            continue
        if dbmod.add_candidate(db, cand):
            added += 1
        else:
            rescored += 1

    dbmod.save(db, args.db)
    s = dbmod.stats(db)
    print(f"  screened  {blocked:,} blocked by the obscenity filter")
    print(f"  added     {added:,} new, {rescored:,} rescored, in {time.time() - started:.1f}s")
    print(f"  db        {len(db['names']):,} names -> {args.db}")
    _rule()
    print("  " + "  ".join(f"tier {t}: {s['by_tier'].get(t, 0):,}"
                           for t, _ in dbmod.TIERS if s["by_tier"].get(t)))
    print("  " + "  ".join(f"{k} syl: {v:,}" for k, v in sorted(s["syllables"].items())))
    print(f"\n  Nothing has been checked yet. Run `pdgen plan` to size the next pass.")
    return 0


# --- planning ---------------------------------------------------------------

def cmd_plan(args) -> int:
    db = dbmod.load(args.db)
    if not db["names"]:
        print("  db is empty -- run `pdgen generate` first")
        return 1

    has_zone = bool(args.zone_file)
    older = args.stale_after if args.include_stale else None
    rate, provenance = planmod.absent_rate(db, args.tld)

    print(f"  db {args.db} -- {len(db['names']):,} names, .{args.tld}")
    print(f"  stage={args.stage}  dns={args.dns_rps:g}/s  rdap={args.rdap_rps:g}/s"
          f"  workers={args.workers}{'  zone file' if has_zone else ''}")
    print(f"  survives DNS: {rate:.0%} ({provenance})")
    _rule()

    rows = planmod.tier_rows(db, args.tld, args.stage, args.recheck_below, older,
                             args.dns_rps, args.rdap_rps, has_zone)
    print(f"  {'TIER':<5}{'SCORE':>8}{'NAMES':>10}{'TO CHECK':>10}"
          f"{'RDAP+':>8}{'FREE':>7}{'TIME':>10}{'CUMULATIVE':>12}")
    for b in rows:
        print(f"  {b['tier']:<5}{b['floor']:>7.0f}+{b['names']:>10,}{b['todo']:>10,}"
              f"{b['rdap']:>8,}{b['available']:>7,}"
              f"{planmod.human(b['seconds']):>10}{planmod.human(b['cumulative']):>12}")
    _rule()
    grand = sum(b["todo"] for b in rows)
    print(f"  {'ALL':<5}{'':>8}{len(db['names']):>10,}{grand:>10,}"
          f"{'':>8}{'':>7}{planmod.human(rows[-1]['cumulative']) if rows else '-':>10}")

    print(f"\n  What a time budget buys you:")
    for label in args.budget or ["15m", "1h", "4h", "12h"]:
        try:
            secs = planmod.parse_duration(label)
        except ValueError as e:
            print(f"    {e}")
            continue
        r = planmod.budget_reach(db, args.tld, args.stage, args.recheck_below,
                                 older, secs, args.dns_rps, args.rdap_rps, has_zone)
        if not r["names"]:
            print(f"    {label:>5}  nothing (queue is empty)")
            continue
        done = "  complete" if r["share"] >= 1.0 else ""
        print(f"    {label:>5}  {r['names']:>8,} names  "
              f"down to score {r['min_score']:.1f} (tier {r['tier']})  "
              f"{r['share']:.0%} of queue{done}")

    if not has_zone:
        print(f"\n  A CZDS zone file would remove the DNS stage entirely.")
        print(f"  Apply at czds.icann.org, then pass --zone-file.")
    return 0


# --- pass 2: check ----------------------------------------------------------

def _resolve_registrar(stage: str):
    if stage != "registrar":
        return None
    key, secret = os.getenv("PORKBUN_API_KEY"), os.getenv("PORKBUN_SECRET_KEY")
    if not (key and secret):
        print("  stage=registrar needs PORKBUN_API_KEY and PORKBUN_SECRET_KEY",
              file=sys.stderr)
        return False
    return (key, secret)


def _run_check(db, args, names, tld, zone, registrar) -> dict:
    lock = threading.Lock()
    counts = {"available": 0, "taken": 0, "unknown": 0, "dropping": 0}
    started = time.time()
    # A GitHub Actions job is killed at 6 hours and the kill is a failure, so
    # any work not already persisted is lost. Stopping ourselves first turns a
    # hard timeout into a clean, resumable exit.
    deadline = started + args.max_duration if args.max_duration else None

    def on_result(name, tl, status, confidence, source, flags):
        with lock:
            counts[status] = counts.get(status, 0) + 1
            if set(flags) & dbmod.DROPPING:
                counts["dropping"] += 1
            dbmod.record_check(db, name, tl, status, confidence, source,
                               flags, force=args.force)

    def on_progress(done, total):
        if done % 25 == 0 or done == total:
            rate = done / max(0.001, time.time() - started)
            eta = (total - done) / rate if rate else 0
            _bar(done, total, f"{counts['available']} free  {rate:.0f}/s  "
                              f"eta {planmod.human(eta)}")
        if done % args.checkpoint == 0:
            with lock:
                dbmod.save(db, args.db)
        if deadline and time.time() > deadline and not _stop.is_set():
            _stop.set()
            sys.stderr.write(
                f"\n  time budget of {planmod.human(args.max_duration)} reached "
                f"after {done:,} names -- stopping cleanly\n")

    checkmod.run(names, tld=tld, stage=args.stage, zone=zone,
                 dns_rps=args.dns_rps, rdap_rps=args.rdap_rps,
                 workers=args.workers, registrar=registrar,
                 on_result=on_result, on_progress=on_progress,
                 should_stop=_stop.is_set)
    sys.stderr.write("\n")
    return counts


def cmd_check(args) -> int:
    _install_sigint()
    db = dbmod.load(args.db)

    if args.name:
        names = [n.lower().removesuffix(f".{args.tld}") for n in args.name]
        names = [n for n in names if n in db["names"]]
    else:
        names = dbmod.queue(db, tld=args.tld, below=args.recheck_below,
                            older_than=args.recheck_older_than,
                            min_score=args.min_score, top=args.top)
    if args.tier:
        wanted = set(args.tier)
        names = [n for n in names if dbmod.tier_of(db["names"][n]["score"]) in wanted]

    if not names:
        print("  nothing to check.")
        print("  try --recheck-below rdap, --recheck-older-than 21, or a lower --min-score")
        return 0

    zone = None
    if args.zone_file:
        print(f"  loading zone file {args.zone_file} ...")
        zone = checkmod.load_zone(args.zone_file, args.tld)
        print(f"  {len(zone):,} registered .{args.tld} labels loaded")

    registrar = _resolve_registrar(args.stage)
    if registrar is False:
        return 2

    rate, _ = planmod.absent_rate(db, args.tld)
    est = planmod.seconds_for(len(names), args.stage, rate, args.dns_rps,
                              args.rdap_rps, zone is not None)
    top_score = db["names"][names[0]]["score"]
    low_score = db["names"][names[-1]]["score"]
    print(f"  checking {len(names):,} names on .{args.tld}, stage={args.stage}")
    print(f"  score range {top_score:.1f} down to {low_score:.1f} "
          f"(tier {dbmod.tier_of(top_score)} to {dbmod.tier_of(low_score)})")
    print(f"  estimated {planmod.human(est)}")
    if args.dry_run:
        print("  --dry-run: stopping here")
        return 0

    with lockmod.network_lock(args.db, f"check .{args.tld} stage={args.stage}",
                              force=args.force_lock):
        counts = _run_check(db, args, names, args.tld, zone, registrar)
    dbmod.save(db, args.db)
    checked = sum(counts.values())
    print(f"  available {counts['available']:,}   taken {counts['taken']:,}   "
          f"unresolved {counts['unknown']:,}")
    remaining = dbmod.queue(db, tld=args.tld, below=args.recheck_below,
                            older_than=args.recheck_older_than,
                            min_score=args.min_score, top=args.top)
    if checked < len(names):
        print(f"  stopped early: {len(remaining):,} still in the queue. "
              f"Re-run to resume -- ordering is stable, so it picks up "
              f"exactly where this left off.")
    elif remaining:
        print(f"  {len(remaining):,} still in the queue.")
    else:
        print(f"  queue is empty -- everything is up to date at this threshold.")
    _emit_ci_outputs(remaining=len(remaining), complete=not remaining,
                     checked=checked, available=counts["available"])
    if counts["dropping"]:
        print(f"  {counts['dropping']:,} taken names are in redemption or pending "
              f"delete -- see `pdgen dropping`")
    print(f"  saved -> {args.db}")
    return 0


# --- alternates -------------------------------------------------------------

def cmd_alternates(args) -> int:
    _install_sigint()
    db = dbmod.load(args.db)
    tlds = args.tld or ["net", "org", "co"]

    unknown = [t for t in tlds if t not in checkmod.ALTERNATE_TLDS]
    if unknown:
        print(f"  note: no guidance on record for {', '.join(unknown)} -- trying anyway")
    for t in tlds:
        info = checkmod.ALTERNATE_TLDS.get(t)
        if info and not info["rdap"]:
            print(f"  warning: .{t} -- {info['note']}")

    base = dbmod.queue(db, tld=args.primary, below="registrar",
                       min_score=args.min_score, top=args.top, only_taken=True)
    if not base:
        print(f"  no names with a taken .{args.primary} yet -- run `pdgen check` first")
        return 0

    print(f"  {len(base):,} names whose .{args.primary} is taken, best first")
    registrar = _resolve_registrar(args.stage)
    if registrar is False:
        return 2

    for tld in tlds:
      with lockmod.network_lock(args.db, f"alternates .{tld}", force=args.force_lock):
        pending = [n for n in base
                   if dbmod.CONFIDENCE_RANK[
                       db["names"][n]["tlds"].get(tld, dbmod.blank_tld())["confidence"]
                   ] == 0]
        if not pending:
            print(f"  .{tld}: already checked")
            continue
        print(f"\n  .{tld} -- {len(pending):,} to check")
        counts = _run_check(db, args, pending, tld, None, registrar)
        dbmod.save(db, args.db)
        print(f"  .{tld}: {counts['available']:,} available")

    dbmod.save(db, args.db)
    print(f"\n  saved -> {args.db}")
    return 0


# --- meanings ---------------------------------------------------------------

def cmd_meaning(args) -> int:
    _install_sigint()
    db = dbmod.load(args.db)

    pool = [n for n, r in db["names"].items()
            if r["score"] >= args.min_score
            and (args.recheck or not r.get("meaning_checked_at"))]
    if args.only_available:
        pool = [n for n in pool
                if db["names"][n]["tlds"].get(args.tld, {}).get("status") == "available"]
    pool.sort(key=lambda n: -db["names"][n]["score"])
    names = pool[: args.top]

    if not names:
        print("  nothing to look up. Try --recheck, or lower --min-score.")
        return 0

    sources = tuple(args.source)
    est = len(names) * (len(sources) / max(args.rps, 0.1)) + len(names) / 50 * 0.3
    print(f"  looking up {len(names):,} names in {', '.join(sources)}")
    print(f"  estimated {planmod.human(est)} at {args.rps:g}/s (single-threaded, on purpose)")
    if args.dry_run:
        return 0

    hits = 0
    started = time.time()

    def on_result(name, meanings):
        nonlocal hits
        rec = db["names"][name]
        rec["meanings"] = meanings
        rec["meaning_checked_at"] = dbmod.now()
        if meanings:
            hits += 1
            langs = ", ".join(m["lang"] for m in meanings[:6])
            sys.stderr.write("\r" + " " * 78 + "\r")
            print(f"  {name:<10} {langs}")

    def on_progress(done, total):
        if done % 5 == 0 or done == total:
            _bar(done, total, f"{hits} with meanings")
        if done % 50 == 0:
            dbmod.save(db, args.db)
        if _stop.is_set():
            raise KeyboardInterrupt

    try:
        with lockmod.network_lock(args.db, "meaning lookup", force=args.force_lock):
            meaningmod.lookup(names, sources=sources, rps=args.rps,
                              on_result=on_result, on_progress=on_progress)
    except KeyboardInterrupt:
        pass

    sys.stderr.write("\n")
    dbmod.save(db, args.db)
    print(f"  {hits:,} of {len(names):,} already mean something somewhere "
          f"({time.time() - started:.0f}s)")
    print(f"  these are leads, not verdicts -- a hit can be a pearl or a landmine")
    return 0


# --- staleness --------------------------------------------------------------

def cmd_stale(args) -> int:
    db = dbmod.load(args.db)
    buckets = {"fresh": 0, "aging": 0, "stale": 0, "never": 0}
    stale_names = []

    for name, rec in db["names"].items():
        t = rec["tlds"].get(args.tld)
        if not t or t["confidence"] == "generated":
            buckets["never"] += 1
            continue
        age = dbmod.days_since(t["checked_at"])
        if age > args.older_than:
            buckets["stale"] += 1
            if t["status"] == "available":
                stale_names.append((name, age, rec["score"]))
        elif age > args.older_than / 2:
            buckets["aging"] += 1
        else:
            buckets["fresh"] += 1

    print(f"  .{args.tld}, freshness window {args.older_than:g} days")
    _rule(40)
    print(f"  never checked   {buckets['never']:>9,}")
    print(f"  fresh           {buckets['fresh']:>9,}")
    print(f"  aging           {buckets['aging']:>9,}")
    print(f"  stale           {buckets['stale']:>9,}")

    if stale_names:
        stale_names.sort(key=lambda x: -x[2])
        print(f"\n  {len(stale_names):,} names listed as available are past the window.")
        print(f"  Highest-scoring ones to re-verify first:")
        for name, age, score in stale_names[:12]:
            print(f"    {name}.{args.tld:<6} {score:>5.1f}  last seen {age:.0f}d ago")
        print(f"\n  Re-verify with:")
        print(f"    pdgen check --recheck-older-than {args.older_than:g} --stage rdap")
    else:
        print(f"\n  Nothing available is stale.")
    return 0


def cmd_dropping(args) -> int:
    """Taken names that are heading back to the pool. The pearl list."""
    db = dbmod.load(args.db)
    found = []
    for name, rec in db["names"].items():
        for tld, t in rec["tlds"].items():
            if dbmod.is_dropping(t):
                found.append((rec["score"], name, tld, sorted(set(t["flags"]) & dbmod.DROPPING)))
    if not found:
        print("  none found yet. Only the RDAP stage reports lifecycle status,")
        print("  so run `pdgen check --stage rdap` before expecting hits here.")
        return 0
    found.sort(reverse=True)
    print(f"  {len(found):,} names in redemption or pending delete:\n")
    for score, name, tld, flags in found[: args.top]:
        print(f"    {name}.{tld:<6} {score:>5.1f}  {', '.join(flags)}")
    print(f"\n  These are registered today and drop back to the pool within weeks.")
    print(f"  Watch them, or use a backorder service -- this tool does not register anything.")
    return 0


# --- publish / release ------------------------------------------------------

def cmd_publish(args) -> int:
    db = dbmod.load(args.db)
    payload = pubmod.build(db, tld=args.tld, min_score=args.min_score,
                           min_confidence=args.min_confidence, limit=args.limit,
                           include_taken=args.include_taken,
                           include_dropping=not args.no_dropping)
    # Refuse *before* writing. Both guards used to run after pubmod.write, so
    # `--fail-on-demo` returned 3 having already replaced the live snapshot
    # with the data it was refusing to publish. A guard downstream of the
    # destructive act is a report, not a guard.
    if args.fail_on_demo and payload["demo"]:
        print(f"  refusing to publish: this snapshot still contains demo data. "
              f"Run `python3 tools/seed_demo.py --clear` first.", file=sys.stderr)
        return 3
    if payload["published"] < args.min_rows:
        # An empty database produces a structurally valid snapshot with zero
        # rows and no demo flag, so --fail-on-demo never sees it. Publishing
        # that over a good file is silent data loss, which is exactly what a
        # sweep slice does after a failed `release pull`.
        print(f"  refusing to publish {payload['published']:,} names, under "
              f"--min-rows {args.min_rows:,}. The database at {args.db} has "
              f"{len(db['names']):,} names; check that it loaded.", file=sys.stderr)
        return 3

    size = pubmod.write(payload, args.out)
    print(f"  published {payload['published']:,} names ({size / 1024:.0f} KB) -> {args.out}")
    if payload["truncated"]:
        print(f"  truncated to --limit {args.limit}; raise it to publish more")
    if payload["demo"]:
        print(f"  WARNING: this snapshot still contains demo data. "
              f"Run `python3 tools/seed_demo.py --clear` first.")
    by_tier = payload["db_stats"]["available_by_tier"]
    if by_tier:
        print("  available by tier: " + "  ".join(f"{k}={v:,}" for k, v in sorted(by_tier.items())))
    print(f"\n  Commit this file -- GitHub Pages serves it. db.json goes to a release:")
    print(f"    pdgen release push")
    return 0


def cmd_release(args) -> int:
    repo = args.repo or releasemod.detect_repo()
    if args.action == "push":
        return releasemod.push(args.db, repo, args.tag)
    if args.action == "pull":
        return releasemod.pull(args.db, repo, args.tag)
    return releasemod.status(args.db, repo, args.tag)


# --- inspection -------------------------------------------------------------

def cmd_stats(args) -> int:
    db = dbmod.load(args.db)
    s = dbmod.stats(db, args.tld)
    print(f"  db        {args.db}")
    print(f"  updated   {db['updated_at']}")
    print(f"  names     {s['total']:,}")
    _rule(50)
    print("  tiers      " + "  ".join(f"{t}={s['by_tier'].get(t, 0):,}"
                                      for t, _ in dbmod.TIERS if s["by_tier"].get(t)))
    print("  syllables  " + "  ".join(f"{k}={v:,}" for k, v in sorted(s["syllables"].items())))
    print("  tlds       " + "  ".join(f".{k}={v:,}" for k, v in sorted(s["tlds"].items())))
    _rule(50)
    for key, label in (("by_status", "status"), ("by_confidence", "confidence"),
                       ("available_by_confidence", "free by conf"),
                       ("available_by_tier", "free by tier")):
        if s[key]:
            print(f"  {label:<14}" + "  ".join(f"{k}={v:,}" for k, v in sorted(s[key].items())))
    if s["dropping"]:
        print(f"  dropping      {s['dropping']:,}  (see `pdgen dropping`)")
    if s["with_meanings"]:
        print(f"  meanings      {s['with_meanings']:,} names mean something elsewhere")
    return 0


def cmd_score(args) -> int:
    for name in args.name:
        name = name.lower().split(".")[0]
        sc, bd = ph.score(name)
        print(f"\n  {name}   /{ph.to_ipa(name)}/   {sc}   tier {dbmod.tier_of(sc)}")
        print(f"    base {bd['base']}")
        for m in bd["modifiers"]:
            print(f"    {m['delta']:+6.1f}  {m['label']}")
        for d in ph.phoneme_detail(name):
            print(f"    {d['ch']}  {d['weight']:.2f}  {d['tier']:<8} {d['note']}")
    return 0


def cmd_screen(args) -> int:
    db = dbmod.load(args.db)
    terms = screenmod.load(args.blocklist)
    hits = [(n, screenmod.blocked(n, terms)) for n in list(db["names"])]
    hits = [(n, h) for n, h in hits if h]
    for n, h in hits[: args.show]:
        print(f"  {n}  matches '{h}'")
    if args.remove and hits:
        for n, _ in hits:
            del db["names"][n]
        dbmod.save(db, args.db)
        print(f"  removed {len(hits):,} names")
    elif not hits:
        print(f"  clean against {len(terms):,} blocked terms")
    else:
        print(f"  {len(hits):,} matches. Re-run with --remove to delete them.")
    return 0


def cmd_merge(args) -> int:
    """Fold a harvest exported from the website back into the database.

    The site can coin names that were never generated locally, so the patch
    carries them explicitly. Without this the whole point of exploring in the
    browser would be lost the moment you closed the tab.
    """
    db = dbmod.load(args.db)
    patch = json.loads(Path(args.patch).read_text(encoding="utf-8"))
    if patch.get("kind") != "sayable-verification-patch":
        print(f"  {args.patch} is not a Sayable harvest file", file=sys.stderr)
        return 2

    coined = 0
    for name, meta in (patch.get("coined") or {}).items():
        if name in db["names"]:
            continue
        sc, breakdown = ph.score(name)
        dbmod.add_candidate(db, {"name": name, "score": sc, "breakdown": breakdown})
        rec = db["names"][name]
        rec["origin"] = {"words": meta.get("origin", []),
                         "japanese": meta.get("japanese"), "via": "coined"}
        coined += 1

    applied = unknown = 0
    for key, res in (patch.get("checked") or {}).items():
        name, _, tld = key.partition(".")
        tld = tld or db.get("primary_tld", "com")
        if name not in db["names"]:
            unknown += 1
            continue
        dbmod.record_check(db, name, tld, res["status"], res.get("confidence", "rdap"),
                           res.get("source", "rdap:browser"), res.get("flags"),
                           force=args.force)
        applied += 1

    marked = 0
    for key in (patch.get("bookmarks") or {}):
        name = key.split(".")[0]
        rec = db["names"].get(name)
        if rec is not None and "bookmarked" not in rec.get("flags", []):
            rec.setdefault("flags", []).append("bookmarked")
            marked += 1

    dbmod.save(db, args.db)
    print(f"  {coined:,} coined names added")
    print(f"  {applied:,} check results applied, {unknown:,} skipped (not in db)")
    print(f"  {marked:,} names flagged as bookmarked")
    print(f"  saved -> {args.db}")
    if marked:
        print(f"  list them with: pdgen bookmarks")
    return 0


def cmd_bookmarks(args) -> int:
    rows = [(r["score"], n, r) for n, r in db_bookmarked(dbmod.load(args.db))]
    if not rows:
        print("  none. Star names on the website, then `pdgen merge` the export.")
        return 0
    rows.sort(reverse=True)
    print(f"  {len(rows):,} bookmarked names:\n")
    for score, name, rec in rows:
        t = rec["tlds"].get(args.tld, {})
        origin = rec.get("origin", {})
        via = f"  <- {' '.join(origin.get('words', []))}" if origin.get("words") else ""
        print(f"    {name}.{args.tld:<6} {score:>5.1f}  {t.get('status', 'unknown'):<10}"
              f"{t.get('confidence', '-'):<11}{via}")
    return 0


def db_bookmarked(db):
    return [(n, r) for n, r in db["names"].items() if "bookmarked" in r.get("flags", [])]


def cmd_query(args) -> int:
    """Structured lookup, built for agents and scripts rather than for reading.

    Everything else in this CLI prints tables for humans. This prints JSON, so
    an agent can ask a precise question and parse the answer without scraping.
    """
    # Loading a 21 MB database to answer one lookup takes seconds. The
    # published snapshot holds everything `query` reports and is ~130 KB, so
    # prefer it unless the caller asks for the full set or it is missing.
    snapshot = Path(args.snapshot)
    use_snapshot = args.source == "snapshot" or (
        args.source == "auto" and snapshot.exists() and not args.include_unchecked)
    if use_snapshot and snapshot.exists():
        db = _db_from_snapshot(snapshot)
        source_used = str(snapshot)
    else:
        db = dbmod.load(args.db)
        source_used = args.db
    tld = args.tld

    def describe(name: str) -> dict:
        rec = db["names"][name]
        t = rec["tlds"].get(tld, dbmod.blank_tld())
        sc, breakdown = ph.score(name)
        return {
            "name": name, "domain": f"{name}.{tld}",
            "score": rec["score"], "tier": dbmod.tier_of(rec["score"]),
            "syllables": ph.syllables(name), "length": len(name),
            "ipa": ph.to_ipa(name),
            "status": t["status"], "confidence": t["confidence"],
            "checked_at": t["checked_at"], "source": t["source"],
            "stale": dbmod.days_since(t["checked_at"]) > args.stale_after,
            "dropping": dbmod.is_dropping(t),
            "flags": sorted(set(t.get("flags", [])) | set(rec.get("flags", []))),
            "meanings": [m.get("lang") for m in rec.get("meanings", [])],
            "origin": rec.get("origin"),
            "alternates": {k: v["status"] for k, v in rec["tlds"].items() if k != tld},
            "score_breakdown": breakdown,
        }

    if args.name:
        results, missing = [], []
        for raw in args.name:
            n = raw.lower().split(".")[0]
            (results.append(describe(n)) if n in db["names"] else missing.append(n))
        payload = {"query": {"names": args.name}, "results": results, "not_in_db": missing}
        if missing:
            payload["hint"] = ("Names absent from the db were never generated. "
                               "Check one directly with: pdgen check --name NAME --stage rdap")
    else:
        names = list(db["names"])
        if args.prefix:
            names = [n for n in names if n.startswith(args.prefix)]
        if args.suffix:
            names = [n for n in names if n.endswith(args.suffix)]
        if args.contains:
            names = [n for n in names if args.contains in n]
        if args.syllables:
            names = [n for n in names if ph.syllables(n) in args.syllables]
        if args.tier:
            names = [n for n in names if dbmod.tier_of(db["names"][n]["score"]) in args.tier]
        names = [n for n in names if db["names"][n]["score"] >= args.min_score]
        if args.available:
            names = [n for n in names
                     if db["names"][n]["tlds"].get(tld, {}).get("status") == "available"]
        if args.min_confidence:
            floor = dbmod.CONFIDENCE_RANK[args.min_confidence]
            names = [n for n in names if dbmod.CONFIDENCE_RANK[
                db["names"][n]["tlds"].get(tld, dbmod.blank_tld())["confidence"]] >= floor]
        if args.dropping:
            names = [n for n in names
                     if dbmod.is_dropping(db["names"][n]["tlds"].get(tld, dbmod.blank_tld()))]
        names.sort(key=lambda n: -db["names"][n]["score"])
        total = len(names)
        names = names[: args.top]
        payload = {
            "query": {k: v for k, v in vars(args).items()
                      if k not in ("func", "command", "db") and v not in (None, False, [])},
            "matched": total, "returned": len(names),
            "results": [describe(n) for n in names],
        }

    payload["db"] = {"path": source_used, "updated_at": db["updated_at"],
                     "names": len(db["names"]), "tld": tld}
    payload["caveat"] = ("Only confidence 'rdap' or 'registrar' means the registry "
                         "answered. 'dns' is a lead and can miss registered-but-"
                         "undelegated names.")
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def _db_from_snapshot(path: Path) -> dict:
    """Rehydrate just enough of a database from the published snapshot."""
    snap = json.loads(path.read_text(encoding="utf-8"))
    fields = {name: i for i, name in enumerate(snap["fields"])}
    levels = snap.get("confidence_levels", dbmod.CONFIDENCE_LEVELS)
    statuses = snap.get("statuses", ["unknown", "available", "taken"])
    out = dbmod.empty()
    out["updated_at"] = snap["generated_at"]
    tld = snap.get("tld", "com")
    for row in snap["rows"]:
        name = row[fields["name"]]
        out["names"][name] = {
            "score": row[fields["score"]],
            "flags": row[fields["flags"]],
            "meanings": [{"lang": m} for m in row[fields["meanings"]]],
            "meaning_checked_at": None,
            "tlds": {tld: {
                "status": statuses[row[fields["status"]]],
                "confidence": levels[row[fields["confidence"]]],
                "checked_at": row[fields["checked_at"]] or None,
                "source": "snapshot",
                "flags": row[fields["flags"]],
            }, **{k: {"status": statuses[v], "confidence": "rdap",
                      "checked_at": None, "source": "snapshot", "flags": []}
                  for k, v in (row[fields["alternates"]] or {}).items()}},
        }
    return out


def cmd_dictionary(args) -> int:
    if args.action == "status":
        idx = Path(args.out) / "index.json"
        if not idx.exists():
            print(f"  no dictionary at {args.out}")
            print(f"  build it with: pdgen dictionary build")
            return 0
        m = json.loads(idx.read_text())
        print(f"  {m['words']:,} words across {len(m['shards'])} shards -> {args.out}")
        print(f"  largest shard {m['max_shard_bytes'] / 1024:.0f} KB")
        return 0

    print(f"  building pronunciation shards -> {args.out}")
    r = dictmod.build(args.out, source=args.source)
    print(f"  {r['words']:,} words, {r['shards']} shards, {r['total_kb'] / 1024:.1f} MB total")
    print(f"  median fetch {r['median_kb']:.1f} KB, largest {r['max_kb']:.0f} KB")
    print(f"\n  Commit these -- GitHub Pages serves them with CORS, releases do not.")
    return 0


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdgen",
        description="Generate, rank, check and publish pronounceable domain candidates.")
    p.add_argument("--db", default=DEFAULT_DB, help=f"JSON database (default {DEFAULT_DB})")
    sub = p.add_subparsers(dest="command", required=True)

    def add_rate_flags(sp, workers=24):
        sp.add_argument("--dns-rps", type=float, default=40.0)
        sp.add_argument("--rdap-rps", type=float, default=8.0)
        sp.add_argument("--workers", type=int, default=workers)

    # pass 1
    g = sub.add_parser("generate", help="pass 1: scored, screened candidates, no network")
    g.add_argument("--pattern", action="append", default=None,
                   help=f"C/V pattern, repeatable (default {' '.join(DEFAULT_PATTERNS)})")
    g.add_argument("--min-score", type=float, default=85.0)
    g.add_argument("--limit", type=int, default=None)
    g.add_argument("--sample", type=float, default=1.0, help="keep this fraction, 0..1")
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--extended", action="store_true", help="allow g r h v z")
    g.add_argument("--blocklist", action="append", default=None)
    g.add_argument("--yes", action="store_true", help="skip the large-space prompt")
    g.set_defaults(func=cmd_generate)

    pl = sub.add_parser("plan", help="estimate time and coverage before checking")
    pl.add_argument("--tld", default="com")
    pl.add_argument("--stage", choices=["zone", "dns", "rdap", "registrar"], default="rdap")
    pl.add_argument("--recheck-below", choices=dbmod.CONFIDENCE_LEVELS, default="generated")
    pl.add_argument("--include-stale", action="store_true")
    pl.add_argument("--stale-after", type=float, default=STALE_DAYS)
    pl.add_argument("--zone-file", default=None)
    pl.add_argument("--budget", action="append", default=None, help="e.g. 30m, 2h, 1d")
    add_rate_flags(pl)
    pl.set_defaults(func=cmd_plan)

    # pass 2
    c = sub.add_parser("check", help="pass 2: availability, best candidates first")
    c.add_argument("--tld", default="com")
    c.add_argument("--stage", choices=["zone", "dns", "rdap", "registrar"], default="rdap")
    c.add_argument("--top", type=int, default=None, help="only the best N in the queue")
    c.add_argument("--tier", action="append", default=None, help="S A B C D, repeatable")
    c.add_argument("--min-score", type=float, default=0.0)
    c.add_argument("--name", action="append", default=None)
    c.add_argument("--recheck-below", choices=dbmod.CONFIDENCE_LEVELS, default="generated")
    c.add_argument("--recheck-older-than", type=float, default=None, metavar="DAYS")
    c.add_argument("--force", action="store_true")
    c.add_argument("--zone-file", default=None)
    c.add_argument("--checkpoint", type=int, default=250)
    c.add_argument("--dry-run", action="store_true", help="show the estimate and stop")
    c.add_argument("--max-duration", type=planmod.parse_duration, default=None,
                   metavar="DURATION",
                   help="stop cleanly after this long, e.g. 50m. Essential in CI: "
                        "a job killed at the 6h wall loses everything unsaved.")
    add_rate_flags(c)
    c.add_argument("--force-lock", action="store_true",
                   help="ignore a lock held by another run (rarely correct)")
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("alternates", help="check other TLDs for names whose .com is taken")
    a.add_argument("--tld", action="append", default=None,
                   help=f"repeatable; known: {' '.join(checkmod.ALTERNATE_TLDS)}")
    a.add_argument("--primary", default="com")
    a.add_argument("--stage", choices=["dns", "rdap", "registrar"], default="rdap")
    a.add_argument("--top", type=int, default=200)
    a.add_argument("--min-score", type=float, default=90.0)
    a.add_argument("--force", action="store_true")
    a.add_argument("--checkpoint", type=int, default=100)
    add_rate_flags(a)
    a.add_argument("--force-lock", action="store_true",
                   help="ignore a lock held by another run (rarely correct)")
    a.set_defaults(func=cmd_alternates, zone_file=None, max_duration=None,
                   recheck_below="generated", recheck_older_than=None)

    m = sub.add_parser("meaning", help="what these strings mean in other languages")
    m.add_argument("--source", action="append", default=None,
                   choices=["wiktionary", "panlex"])
    m.add_argument("--top", type=int, default=300)
    m.add_argument("--min-score", type=float, default=90.0)
    m.add_argument("--tld", default="com")
    m.add_argument("--only-available", action="store_true")
    m.add_argument("--recheck", action="store_true")
    m.add_argument("--rps", type=float, default=4.0)
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--force-lock", action="store_true",
                   help="ignore a lock held by another run (rarely correct)")
    m.set_defaults(func=cmd_meaning)

    st = sub.add_parser("stale", help="find results past their freshness window")
    st.add_argument("--tld", default="com")
    st.add_argument("--older-than", type=float, default=STALE_DAYS, metavar="DAYS")
    st.set_defaults(func=cmd_stale)

    dr = sub.add_parser("dropping", help="taken names heading back to the pool")
    dr.add_argument("--top", type=int, default=40)
    dr.set_defaults(func=cmd_dropping)

    pu = sub.add_parser("publish", help="write the snapshot the website reads")
    pu.add_argument("--out", default=DEFAULT_OUT)
    pu.add_argument("--tld", default="com")
    pu.add_argument("--min-confidence", choices=dbmod.CONFIDENCE_LEVELS, default="dns")
    pu.add_argument("--min-score", type=float, default=0.0)
    pu.add_argument("--limit", type=int, default=5000)
    pu.add_argument("--include-taken", action="store_true")
    pu.add_argument("--fail-on-demo", action="store_true",
                    help="exit nonzero if the snapshot still holds seeded data (use in CI)")
    pu.add_argument("--min-rows", type=int, default=0, metavar="N",
                    help="refuse to write a snapshot with fewer than N names. "
                         "Guards against replacing a good snapshot with an empty "
                         "one after a failed `release pull` (use in CI)")
    pu.add_argument("--no-dropping", action="store_true",
                    help="omit taken-but-dropping names")
    pu.set_defaults(func=cmd_publish)

    rl = sub.add_parser("release", help="store the working db in a GitHub release")
    rl.add_argument("action", choices=["push", "pull", "status"], nargs="?", default="status")
    rl.add_argument("--repo", default=None, metavar="OWNER/NAME")
    rl.add_argument("--tag", default=releasemod.DEFAULT_TAG)
    rl.set_defaults(func=cmd_release)

    s = sub.add_parser("stats", help="summarise the db")
    s.add_argument("--tld", default="com")
    s.set_defaults(func=cmd_stats)

    sc = sub.add_parser("score", help="explain the score for one or more names")
    sc.add_argument("name", nargs="+")
    sc.set_defaults(func=cmd_score)

    sn = sub.add_parser("screen", help="re-run the blocklist over the db")
    sn.add_argument("--blocklist", action="append", default=None)
    sn.add_argument("--remove", action="store_true")
    sn.add_argument("--show", type=int, default=20)
    sn.set_defaults(func=cmd_screen)

    bk = sub.add_parser("bookmarks", help="names you starred on the website")
    bk.add_argument("--tld", default="com")
    bk.set_defaults(func=cmd_bookmarks)

    q = sub.add_parser("query", help="structured JSON lookup, for agents and scripts")
    q.add_argument("--name", action="append", default=None, help="exact name, repeatable")
    q.add_argument("--prefix", default=None)
    q.add_argument("--suffix", default=None)
    q.add_argument("--contains", default=None)
    q.add_argument("--syllables", type=int, action="append", default=None)
    q.add_argument("--tier", action="append", default=None)
    q.add_argument("--min-score", type=float, default=0.0)
    q.add_argument("--min-confidence", choices=dbmod.CONFIDENCE_LEVELS, default=None)
    q.add_argument("--available", action="store_true")
    q.add_argument("--dropping", action="store_true")
    q.add_argument("--tld", default="com")
    q.add_argument("--top", type=int, default=20)
    q.add_argument("--stale-after", type=float, default=STALE_DAYS)
    q.add_argument("--source", choices=["auto", "snapshot", "db"], default="auto",
                   help="auto prefers the small published snapshot (much faster)")
    q.add_argument("--snapshot", default=DEFAULT_OUT)
    q.add_argument("--include-unchecked", action="store_true",
                   help="search the full database, including never-checked names")
    q.add_argument("--pretty", action="store_true", default=True)
    q.add_argument("--compact", dest="pretty", action="store_false")
    q.set_defaults(func=cmd_query)

    dc = sub.add_parser("dictionary", help="build the browser pronunciation shards")
    dc.add_argument("action", choices=["build", "status"], nargs="?", default="status")
    dc.add_argument("--out", default="docs/data/cmudict")
    dc.add_argument("--source", default=None, help="local cmudict.dict instead of downloading")
    dc.set_defaults(func=cmd_dictionary)

    mg = sub.add_parser("merge", help="apply a harvest exported from the website")
    mg.add_argument("patch")
    mg.add_argument("--force", action="store_true")
    mg.set_defaults(func=cmd_merge)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "source", None) is None and args.command == "meaning":
        args.source = ["wiktionary"]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
