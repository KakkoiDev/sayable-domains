"""A cross-process lock for anything that talks to a registry.

The rate limiter in check.py is per-process, so two `pdgen check` runs started
in different terminals quietly double your actual request rate against the
registry. That is the easiest way to get throttled or banned, and nothing
warned about it.

This is a lock file next to the database holding the PID and what it is doing.
A stale lock (dead process) is reclaimed automatically, so a crashed run does
not leave the tool wedged.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def _alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)          # signal 0 tests existence without touching it
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # exists, owned by someone else
    except OSError:
        return False
    return True


def lock_path(db_path: str | Path) -> Path:
    p = Path(db_path)
    return p.parent / f".{p.name}.lock"


def read(db_path: str | Path) -> dict | None:
    lp = lock_path(db_path)
    if not lp.exists():
        return None
    try:
        return json.loads(lp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@contextmanager
def network_lock(db_path: str | Path, what: str, force: bool = False):
    """Hold the lock for the duration of a network-using command.

    Raises SystemExit with an explanation rather than a traceback if another
    live process already holds it.
    """
    lp = lock_path(db_path)
    existing = read(db_path)

    if existing and not force:
        pid = int(existing.get("pid", -1))
        if _alive(pid):
            age = time.time() - float(existing.get("started", 0))
            raise SystemExit(
                f"  another Sayable run is already using the network:\n"
                f"    pid {pid}, '{existing.get('what')}', started {age / 60:.0f} min ago\n\n"
                f"  Running two at once doubles your request rate against the registry,\n"
                f"  which is how you get throttled. Wait for it, or pass --force-lock if\n"
                f"  you are certain that process is gone."
            )
        print(f"  clearing a stale lock from pid {pid} (process is gone)")

    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(
        {"pid": os.getpid(), "what": what, "started": time.time()}), encoding="utf-8")
    try:
        yield
    finally:
        try:
            current = read(db_path)
            if current and int(current.get("pid", -1)) == os.getpid():
                lp.unlink(missing_ok=True)
        except OSError:
            pass
