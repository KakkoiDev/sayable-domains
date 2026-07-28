"""Keep the working database in a GitHub release instead of in git.

db.json is ~10 MB and churns on every check run, which is exactly the kind of
file git is worst at. A release asset is a better home: versioned, free, and
outside the clone.

Only Python ever reads it, so the CORS problem that blocks browsers from
fetching release assets does not apply here. The *published snapshot*
(docs/data/domains.json, ~130 KB) still has to be committed, because GitHub
Pages is what serves it to the browser and release assets cannot be fetched
cross-origin. See HANDOFF.md.

Uses the `gh` CLI if present -- it already holds your credentials and handles
the upload -- and falls back to the REST API with GITHUB_TOKEN.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ASSET = "db.json.gz"
PREVIOUS = "db-previous.json.gz"
DEFAULT_TAG = "db-latest"
API = "https://api.github.com"


def have_gh() -> bool:
    return shutil.which("gh") is not None


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def compress(src: Path, dest: Path) -> int:
    with open(src, "rb") as fi, gzip.open(dest, "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)
    return dest.stat().st_size


def decompress(src: Path, dest: Path) -> None:
    with gzip.open(src, "rb") as fi, open(dest, "wb") as fo:
        shutil.copyfileobj(fi, fo)


def push(db_path: str | Path, repo: str | None, tag: str = DEFAULT_TAG,
         notes: str = "") -> int:
    """Upload the database as a release asset, replacing any previous copy."""
    src = Path(db_path)
    if not src.exists():
        print(f"  {src} does not exist yet -- nothing to push")
        return 1
    if not have_gh():
        print("  the `gh` CLI is required for release uploads.")
        print("  install it from https://cli.github.com, then `gh auth login`.")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        # The outgoing archive gets a directory to itself. `gh release
        # download --dir X` writes the asset into X under its own name, so
        # pointing it at the directory holding the freshly compressed database
        # silently overwrites it, and the upload that follows sends the old
        # bytes straight back. That turned every chained sweep slice into a
        # no-op: four slices ran, ~25,000 names were checked, and the release
        # still held a database with zero checks in it.
        outgoing = Path(tmp) / "outgoing"
        outgoing.mkdir()
        gz = outgoing / ASSET
        size = compress(src, gz)
        if size < 1024:
            print(f"  refusing to push a {size} byte database -- that looks wrong")
            return 1
        print(f"  {src.name} {src.stat().st_size / 1e6:.1f} MB -> {ASSET} {size / 1e6:.1f} MB")

        repo_args = ["--repo", repo] if repo else []
        code, _ = _run(["gh", "release", "view", tag, *repo_args])
        if code != 0:
            print(f"  creating release {tag} ...")
            code, out = _run([
                "gh", "release", "create", tag, str(gz), *repo_args,
                "--title", "Working database (latest)",
                "--notes", notes or "Sayable candidate database. Rolling asset, "
                                    "overwritten by `pdgen release push`.",
            ])
            if code != 0:
                print(f"  failed: {out}")
                return code
        else:
            # Clobbering a rolling asset leaves no way back if a bad run wrote
            # a corrupt db. Keep exactly one previous copy alongside it.
            #
            # A separate directory from `outgoing`, and no path comparison: the
            # old guard was `(Path(tmp) / ASSET) != gz`, which compares a path
            # to itself and is therefore always false. It never kept a previous
            # copy, and it never stopped the download from clobbering the new
            # archive either, because by then the damage was done.
            incoming = Path(tmp) / "incoming"
            incoming.mkdir()
            code, _ = _run(["gh", "release", "download", tag, "--pattern", ASSET,
                            "--dir", str(incoming), "--clobber", *repo_args])
            fetched = incoming / ASSET
            if code == 0 and fetched.exists():
                prev = incoming / PREVIOUS
                shutil.move(str(fetched), str(prev))
                _run(["gh", "release", "upload", tag, str(prev), "--clobber", *repo_args])
                print(f"  previous database kept as {PREVIOUS}")
            print(f"  uploading to existing release {tag} ...")
            code, out = _run(["gh", "release", "upload", tag, str(gz),
                              "--clobber", *repo_args])
            if code != 0:
                print(f"  failed: {out}")
                return code

    print(f"  pushed -> release {tag}, asset {ASSET}")
    return 0


def pull(db_path: str | Path, repo: str | None, tag: str = DEFAULT_TAG) -> int:
    """Download and unpack the database from a release asset."""
    dest = Path(db_path)
    with tempfile.TemporaryDirectory() as tmp:
        gz = Path(tmp) / ASSET
        if have_gh():
            repo_args = ["--repo", repo] if repo else []
            code, out = _run(["gh", "release", "download", tag, "--pattern", ASSET,
                              "--dir", tmp, "--clobber", *repo_args])
            if code != 0:
                print(f"  failed: {out}")
                return code
        elif repo:
            url = f"https://github.com/{repo}/releases/download/{tag}/{ASSET}"
            print(f"  no `gh` CLI; downloading {url}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sayable"})
                with urllib.request.urlopen(req, timeout=120) as r, open(gz, "wb") as f:
                    shutil.copyfileobj(r, f)
            except urllib.error.HTTPError as e:
                print(f"  failed: HTTP {e.code}")
                return 1
        else:
            print("  need either the `gh` CLI or --repo OWNER/NAME")
            return 2

        if dest.exists():
            backup = dest.with_suffix(dest.suffix + ".bak")
            shutil.copy2(dest, backup)
            print(f"  existing db backed up to {backup.name}")
        decompress(gz, dest)

    print(f"  pulled -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return 0


def status(db_path: str | Path, repo: str | None, tag: str = DEFAULT_TAG) -> int:
    """Compare the local database against the release asset."""
    local = Path(db_path)
    if local.exists():
        import datetime
        mtime = datetime.datetime.fromtimestamp(local.stat().st_mtime)
        print(f"  local    {local} {local.stat().st_size / 1e6:.1f} MB, "
              f"modified {mtime:%Y-%m-%d %H:%M}")
    else:
        print(f"  local    {local} does not exist")

    if not have_gh():
        print("  remote   unknown (`gh` CLI not installed)")
        return 0
    repo_args = ["--repo", repo] if repo else []
    code, out = _run(["gh", "release", "view", tag, "--json",
                      "publishedAt,assets", *repo_args])
    if code != 0:
        print(f"  remote   no release tagged {tag} yet")
        return 0
    try:
        data = json.loads(out)
        asset = next((a for a in data.get("assets", []) if a["name"] == ASSET), None)
        if asset:
            print(f"  remote   {tag}/{ASSET} {asset.get('size', 0) / 1e6:.1f} MB, "
                  f"updated {asset.get('updatedAt', '?')[:16].replace('T', ' ')}")
        else:
            print(f"  remote   release {tag} exists but has no {ASSET}")
    except (json.JSONDecodeError, StopIteration):
        print(f"  remote   could not parse the release payload")
    return 0


def detect_repo() -> str | None:
    """Best-effort OWNER/NAME from the git remote."""
    code, out = _run(["git", "remote", "get-url", "origin"])
    if code != 0:
        return None
    url = out.strip()
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git")
    return None
