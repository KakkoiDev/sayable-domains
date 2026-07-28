"""Build a pronunciation dictionary the browser can query one shard at a time.

The coining engine's weakest part is guessing English pronunciation from
spelling. CMUdict has 135,000 real pronunciations and fixes that -- but it is
3.6 MB, far too much to ship to a phone.

So it gets sharded by the first two letters of each word. Typing "cloud native"
fetches cl.json and na.json, about 8 KB each, instead of the whole thing.

The shards live in docs/, not in a release. Release assets cannot be fetched
cross-origin, so a browser cannot read them; GitHub Pages serves docs/ with
permissive CORS. CI does the downloading and sharding, which is the part that
genuinely benefits from running server-side.

ARPAbet is converted to the same phoneme symbols the heuristic emits, so the
rest of the pipeline is untouched -- the dictionary only replaces stage one.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.request
from pathlib import Path

CMUDICT_URL = "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict"
UA = "sayable/2.0 dictionary builder"

# ARPAbet -> the internal symbols used by nativize.js.
#   C=ch S=sh T=th D=dh J=dʒ Z=zh N=ng Y=/aɪ/ W=/aʊ/ E=/eɪ/ O=/ɔɪ/
# ER maps to "a" on purpose: English r-coloured vowels surface as long /aː/ in
# Japanese loanwords, which is what turns "personal" into pasonaru.
ARPABET = {
    "AA": "a", "AE": "a", "AH": "a", "AO": "o", "AW": "W", "AY": "Y",
    "EH": "e", "ER": "a", "EY": "E", "IH": "i", "IY": "i", "OW": "o",
    "OY": "O", "UH": "u", "UW": "u",
    "B": "b", "CH": "C", "D": "d", "DH": "D", "F": "f", "G": "g", "HH": "h",
    "JH": "J", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "N", "P": "p",
    "R": "r", "S": "s", "SH": "S", "T": "t", "TH": "T", "V": "v", "W": "w",
    "Y": "y", "Z": "z", "ZH": "Z",
}

STRESS = re.compile(r"\d")
VARIANT = re.compile(r"\(\d+\)$")


def convert(arpa: str) -> str:
    """'K L AW1 D' -> 'klWd'."""
    out = []
    for token in arpa.split():
        sym = ARPABET.get(STRESS.sub("", token).upper())
        if sym:
            out.append(sym)
    return "".join(out)


def shard_key(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    if len(w) >= 2:
        return w[:2]
    return w or "_"


def download(dest: Path) -> Path:
    print(f"  downloading {CMUDICT_URL}")
    req = urllib.request.Request(CMUDICT_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    print(f"  {dest.stat().st_size / 1e6:.1f} MB")
    return dest


def parse(path: Path) -> dict[str, str]:
    """CMUdict text -> {word: phoneme string}. First variant wins."""
    words: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        word = VARIANT.sub("", head).lower()
        if not word.isalpha() or word in words:
            continue        # keep only the primary pronunciation
        phon = convert(rest)
        if phon:
            words[word] = phon
    return words


def build(out_dir: str | Path, source: str | Path | None = None,
          min_len: int = 2) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    src = Path(source) if source else None
    tmp = None
    if src is None:
        tmp = out.parent / "cmudict.dict.tmp"
        src = download(tmp)

    words = {w: p for w, p in parse(src).items() if len(w) >= min_len}
    if tmp and tmp.exists():
        tmp.unlink()

    shards: dict[str, dict[str, str]] = {}
    for word, phon in words.items():
        shards.setdefault(shard_key(word), {})[word] = phon

    sizes = []
    for key, entries in shards.items():
        text = json.dumps(entries, separators=(",", ":"), sort_keys=True)
        (out / f"{key}.json").write_text(text, encoding="utf-8")
        sizes.append(len(text.encode()))

    manifest = {
        "source": CMUDICT_URL,
        "words": len(words),
        "shards": sorted(shards),
        "shard_by": "first two letters of the word, lowercased",
        "symbols": "internal phoneme symbols, see docs/nativize.js",
        "max_shard_bytes": max(sizes) if sizes else 0,
    }
    (out / "index.json").write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True), encoding="utf-8")

    return {
        "words": len(words),
        "shards": len(shards),
        "total_kb": sum(sizes) / 1024,
        "median_kb": sorted(sizes)[len(sizes) // 2] / 1024 if sizes else 0,
        "max_kb": max(sizes) / 1024 if sizes else 0,
    }
