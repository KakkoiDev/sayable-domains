"""The availability funnel. Standard library only -- no pip install required.

    zone file (optional, free, instant)
        v
    DNS over HTTPS  -- kills the majority that are obviously taken
        v
    RDAP at the registry  -- authoritative on registration
        v
    registrar API (optional)  -- authoritative on whether you can buy it

Each stage is more expensive and more trustworthy than the last, so a run can
legitimately stop at any of them. Whichever stage a name reached is written to
the db as its confidence and surfaced in the UI.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = ("sayable/2.0 (+https://github.com/KakkoiDev/sayable-domains) "
      "domain availability research")

DOH_ENDPOINTS = [
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
]

# .com goes direct to Verisign; everything else routes through the IANA
# bootstrap mirror at rdap.org, which 302s to the right registry.
RDAP_DIRECT = {"com": "https://rdap.verisign.com/com/v1/domain/"}
RDAP_BOOTSTRAP = "https://rdap.org/domain/"

# Alternates worth trying when the .com is gone. `rdap` records whether the
# registry actually answers RDAP -- several popular ccTLDs still do not.
ALTERNATE_TLDS = {
    "net":  {"rdap": True,  "note": "gTLD, Verisign. Closest substitute for .com."},
    "org":  {"rdap": True,  "note": "gTLD, PIR. Strong for non-profit and community."},
    "co":   {"rdap": True,  "note": "ccTLD (Colombia), sold as a .com alternative."},
    "io":   {"rdap": True,  "note": "ccTLD. Popular with developer tools; pricey."},
    "app":  {"rdap": True,  "note": "gTLD, Google. HTTPS enforced by preload."},
    "dev":  {"rdap": True,  "note": "gTLD, Google. HTTPS enforced by preload."},
    "xyz":  {"rdap": True,  "note": "gTLD. Cheap and plentiful; some spam stigma."},
    "ai":   {"rdap": False, "note": "ccTLD (Anguilla). RDAP coverage unreliable."},
    "me":   {"rdap": True,  "note": "ccTLD (Montenegro), used for personal sites."},
    "sh":   {"rdap": False, "note": "ccTLD (St Helena). RDAP coverage unreliable."},
}

# Registry statuses that mean "taken now, but dropping".
LIFECYCLE = {"redemptionperiod", "pendingdelete", "clienthold", "serverhold"}


class RateLimiter:
    """Token bucket. Registries will throttle or ban you; be a good citizen."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            sleep_for = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if sleep_for:
            time.sleep(sleep_for)


def _get(url: str, accept: str = "application/json", timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout)


# --- Stage 1: zone file -----------------------------------------------------

def load_zone(path: str | Path, tld: str = "com") -> set[str]:
    """Load registered second-level labels from an ICANN CZDS zone file.

    Apply at czds.icann.org. This turns the whole DNS stage into a local set
    lookup, which is faster and far kinder to public resolvers.
    """
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    suffix = f".{tld}"
    names: set[str] = set()
    with opener(p, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line[0] in ";$":
                continue
            label = line.split("\t", 1)[0].split(" ", 1)[0].strip().lower()
            if label.endswith("."):
                label = label[:-1]
            if label.endswith(suffix):
                names.add(label[: -len(suffix)])
    return names


# --- Stage 2: DNS over HTTPS ------------------------------------------------

def doh_ns(fqdn: str, limiter: RateLimiter, endpoint: int = 0) -> str:
    """Return 'taken', 'absent', or 'error'.

    NXDOMAIN means the name is not delegated, a strong hint it is unregistered.
    NOERROR with NS records means it is definitely registered. Registered-but-
    undelegated names look 'absent' here, which is exactly why RDAP follows.
    """
    base = DOH_ENDPOINTS[endpoint % len(DOH_ENDPOINTS)]
    qs = urllib.parse.urlencode({"name": fqdn, "type": "NS"})
    limiter.wait()
    try:
        with _get(f"{base}?{qs}", accept="application/dns-json") as resp:
            data = json.load(resp)
    except Exception:
        return "error"
    status = data.get("Status")
    if status == 3:
        return "absent"
    if status == 0:
        return "taken" if data.get("Answer") else "absent"
    return "error"


# --- Stage 3: RDAP ----------------------------------------------------------

def rdap(name: str, tld: str, limiter: RateLimiter, retries: int = 3) -> tuple[str, list[str]]:
    """Return (status, flags). status is 'available', 'taken' or 'error'."""
    base = RDAP_DIRECT.get(tld, RDAP_BOOTSTRAP)
    url = base + urllib.parse.quote(f"{name}.{tld}")
    for attempt in range(retries):
        limiter.wait()
        try:
            with _get(url, accept="application/rdap+json") as resp:
                body = json.load(resp)
            statuses = [str(s).replace(" ", "").lower() for s in body.get("status", [])]
            return "taken", sorted(set(statuses) & LIFECYCLE)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "available", []
            if e.code in (429, 503):
                delay = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                time.sleep(min(delay, 60))
                continue
            # 501/400 from the bootstrap usually means this TLD has no RDAP.
            return "error", []
        except Exception:
            time.sleep(2 ** attempt)
    return "error", []


# --- Stage 4: registrar (optional) ------------------------------------------

def porkbun(name: str, tld: str, api_key: str, secret: str,
            limiter: RateLimiter) -> tuple[str, list[str]]:
    """Authoritative on purchasability: catches premium pricing and reserved names."""
    limiter.wait()
    payload = json.dumps({"apikey": api_key, "secretapikey": secret}).encode()
    req = urllib.request.Request(
        f"https://api.porkbun.com/api/json/v3/domain/checkDomain/{name}.{tld}",
        data=payload,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.load(resp)
    except Exception:
        return "error", []
    if body.get("status") != "SUCCESS":
        return "error", []
    r = body.get("response", {})
    truthy = ("yes", "true", "1")
    flags = []
    if str(r.get("premium", "")).lower() in truthy:
        flags.append("premium")
    if r.get("price"):
        flags.append(f"price:{r['price']}")
    return ("available" if str(r.get("avail", "")).lower() in truthy else "taken"), flags


# --- Orchestration ----------------------------------------------------------

def run(
    names: Iterable[str],
    tld: str = "com",
    stage: str = "rdap",
    zone: set[str] | None = None,
    dns_rps: float = 40.0,
    rdap_rps: float = 8.0,
    workers: int = 24,
    registrar: tuple[str, str] | None = None,
    on_result: Callable[[str, str, str, str, str, list[str]], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Push names through the funnel, reporting each result as it lands.

    on_result(name, tld, status, confidence, source, flags) runs on worker
    threads, so the caller must keep it cheap and thread-safe.
    """
    names = list(names)
    total = len(names)
    dns_limiter = RateLimiter(dns_rps)
    rdap_limiter = RateLimiter(rdap_rps)
    done = 0
    lock = threading.Lock()

    def emit(name, status, confidence, source, flags):
        nonlocal done
        with lock:
            done += 1
            if on_result:
                on_result(name, tld, status, confidence, source, flags)
            if on_progress:
                on_progress(done, total)

    def one(name: str, idx: int) -> None:
        if should_stop and should_stop():
            return
        # Stage 1 -- local zone file.
        if zone is not None:
            if name in zone:
                emit(name, "taken", "dns", "zone", [])
                return
            if stage == "zone":
                emit(name, "available", "dns", "zone", [])
                return
        # Stage 2 -- DNS.
        elif stage in ("dns", "rdap", "registrar"):
            verdict = doh_ns(f"{name}.{tld}", dns_limiter, idx)
            if verdict == "taken":
                emit(name, "taken", "dns", "doh", [])
                return
            if verdict == "error":
                emit(name, "unknown", "generated", "doh:error", [])
                return
            if stage == "dns":
                emit(name, "available", "dns", "doh", [])
                return

        # Stage 3 -- RDAP.
        status, flags = rdap(name, tld, rdap_limiter)
        if status == "error":
            emit(name, "unknown", "dns", "rdap:error", [])
            return
        if status == "taken" or stage != "registrar" or not registrar:
            emit(name, status, "rdap", f"rdap:{tld}", flags)
            return

        # Stage 4 -- registrar.
        r_status, r_flags = porkbun(name, tld, registrar[0], registrar[1], rdap_limiter)
        if r_status == "error":
            emit(name, status, "rdap", f"rdap:{tld}", flags)
        else:
            emit(name, r_status, "registrar", "registrar:porkbun", flags + r_flags)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, n, i) for i, n in enumerate(names)]
        for f in as_completed(futures):
            f.result()
