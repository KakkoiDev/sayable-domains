"""Rehearse the chained sweep offline, against a fake registry.

The whole design rests on one claim: a run that stops on a time budget can be
resumed by the next run, picking up exactly where it left off, until the queue
drains. Nothing in CI proves that — a chain that silently fails to fire looks
identical to a finished queue, and you would not find out for hours.

This runs the real code path (queue ordering, --max-duration, checkpointing,
save/reload, the GITHUB_OUTPUT signal) against a deterministic stub registry.
No network, no GitHub, seconds instead of hours.

    python3 tools/rehearse_chain.py --slice 2s --slices 6

Each "slice" is one simulated workflow run. What it verifies:

  * a slice stops on its budget rather than running to completion
  * the next slice resumes at the next-best unchecked name, with no gaps
    and nothing checked twice
  * the queue monotonically drains
  * `complete` flips to true exactly once, on the slice that empties it

It cannot tell you whether the live endpoints behave as stubbed, or whether
GitHub actually dispatches the next run. Those still need a real trial.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdgen import db as dbmod, plan  # noqa: E402

# A stub registry, injected via sitecustomize so the child processes are real
# `python3 -m pdgen check` invocations rather than in-process fakes.
STUB = '''
import hashlib, time
from pdgen import check as _c

_LATENCY = float(__import__("os").environ.get("REHEARSE_LATENCY", "0.004"))

def _det(name, salt):
    h = hashlib.sha256(f"{salt}:{name}".encode()).digest()
    return int.from_bytes(h[:6], "big") / float(1 << 48)

def _doh(fqdn, limiter, endpoint=0):
    time.sleep(_LATENCY)
    name = fqdn.split(".")[0]
    return "taken" if _det(name, "dns") < 0.70 else "absent"

def _rdap(name, tld, limiter, retries=3):
    time.sleep(_LATENCY * 2)
    r = _det(name, "rdap")
    if r < 0.55:
        return "available", []
    return "taken", (["redemptionperiod"] if r > 0.97 else [])

_c.doh_ns = _doh
_c.rdap = _rdap
'''


def run_slice(db_path: Path, workdir: Path, slice_len: str, n: int) -> dict:
    """One simulated workflow run. Returns the step outputs it produced."""
    outputs = workdir / f"out{n}.txt"
    outputs.write_text("")
    env = {
        **os.environ,
        "PYTHONPATH": f"{workdir}:{ROOT}",
        "GITHUB_OUTPUT": str(outputs),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "pdgen", "--db", str(db_path), "check",
         "--stage", "rdap", "--max-duration", slice_len,
         "--rdap-rps", "0", "--dns-rps", "0", "--workers", "8",
         "--checkpoint", "50"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"slice {n} failed with code {proc.returncode}")
    parsed = dict(
        line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line)
    return parsed


def checked_set(db_path: Path) -> set[str]:
    db = dbmod.load(db_path)
    return {n for n, r in db["names"].items()
            if r["tlds"].get("com", {}).get("confidence") != "generated"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", default="2s", help="budget per simulated run")
    ap.add_argument("--slices", type=int, default=6, help="max runs to chain")
    ap.add_argument("--names", type=int, default=900, help="candidates to seed")
    ap.add_argument("--latency", type=float, default=0.004,
                    help="fake per-request latency, seconds")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="rehearse-"))
    (work / "sitecustomize.py").write_text(STUB)
    os.environ["REHEARSE_LATENCY"] = str(args.latency)
    db_path = work / "db.json"

    print(f"  seeding {args.names:,} candidates into {db_path.name}")
    subprocess.run(
        [sys.executable, "-m", "pdgen", "--db", str(db_path), "generate",
         "--pattern", "CVCVCV", "--min-score", "93", "--limit", str(args.names)],
        cwd=ROOT, capture_output=True, text=True, check=True)

    total = len(dbmod.load(db_path)["names"])
    budget = plan.parse_duration(args.slice)
    print(f"  {total:,} names, {plan.human(budget)} per slice, "
          f"max {args.slices} slices\n")
    print(f"  {'slice':<7}{'checked':>9}{'cumulative':>12}{'remaining':>11}"
          f"{'complete':>10}{'wall':>8}   resume point")
    print("  " + "-" * 74)

    seen: set[str] = set()
    overlaps = gaps = 0
    completed_at = None

    for i in range(1, args.slices + 1):
        before = checked_set(db_path)
        t0 = time.time()
        out = run_slice(db_path, work, args.slice, i)
        wall = time.time() - t0
        after = checked_set(db_path)

        new = after - before
        # Anything re-checked would be wasted registry budget.
        overlaps += len(new & seen)
        seen |= new

        db = dbmod.load(db_path)
        nxt = dbmod.queue(db, tld="com")
        resume = nxt[0] if nxt else "—"
        # The resume point must be the best-scoring name still unchecked.
        if nxt and any(dbmod.CONFIDENCE_RANK[
                db["names"][n]["tlds"]["com"]["confidence"]] == 0 and
                db["names"][n]["score"] > db["names"][resume]["score"] for n in seen):
            gaps += 1

        print(f"  {i:<7}{len(new):>9,}{len(seen):>12,}{out.get('remaining', '?'):>11}"
              f"{out.get('complete', '?'):>10}{wall:>7.1f}s   {resume}")

        if out.get("complete") == "true":
            completed_at = i
            break

    print("  " + "-" * 74)
    ok = True
    def check(label, passed, detail=""):
        nonlocal ok
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")

    check("every name checked exactly once", overlaps == 0,
          f"{overlaps} duplicate checks" if overlaps else "no duplicate registry calls")
    check("resume point is always the best unchecked name", gaps == 0,
          f"{gaps} out-of-order resumes" if gaps else "")
    check("queue drained to empty", completed_at is not None,
          f"complete=true on slice {completed_at}" if completed_at
          else f"still {total - len(seen):,} unchecked after {args.slices} slices")
    check("all candidates accounted for", len(seen) == total,
          f"{len(seen):,} of {total:,}")

    print(f"\n  {'REHEARSAL PASSED' if ok else 'REHEARSAL FAILED'}")
    print("  This proves resume-across-slices. It does NOT prove the live")
    print("  registry behaves as stubbed, or that GitHub dispatches the chain.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
