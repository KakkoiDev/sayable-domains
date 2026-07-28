"""Tests for the parsing logic — the part that had no coverage at all.

Every real bug in this project will be in how a response is interpreted, not in
the orchestration around it. These tests exercise the parsers against recorded
response shapes so a wrong assumption fails here rather than silently marking a
registered domain as available.

    python3 -m unittest discover tests -v

No network, no dependencies.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdgen import check, db as dbmod, dictionary, generate, phonetics as ph, plan  # noqa: E402
from pdgen import release as releasemod  # noqa: E402


class FakeResponse(io.BytesIO):
    """Enough of an http.client.HTTPResponse for json.load and `with`."""

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def http_error(code, headers=None):
    return urllib.error.HTTPError("http://x", code, "", headers or {}, None)


class NoLimit(check.RateLimiter):
    def __init__(self):
        super().__init__(0)


# --- DNS over HTTPS ---------------------------------------------------------

class TestDoH(unittest.TestCase):
    def respond(self, payload):
        check._get = lambda *a, **k: FakeResponse(payload)

    def tearDown(self):
        import importlib
        importlib.reload(check)

    def test_nxdomain_means_absent(self):
        self.respond({"Status": 3})
        self.assertEqual(check.doh_ns("x.com", NoLimit()), "absent")

    def test_noerror_with_ns_means_taken(self):
        self.respond({"Status": 0, "Answer": [{"type": 2, "data": "ns1.example.com."}]})
        self.assertEqual(check.doh_ns("x.com", NoLimit()), "taken")

    def test_noerror_without_answer_is_absent_not_taken(self):
        # A registered-but-undelegated name looks like this. Calling it "taken"
        # would silently discard real candidates; RDAP has to decide.
        self.respond({"Status": 0})
        self.assertEqual(check.doh_ns("x.com", NoLimit()), "absent")

    def test_servfail_is_an_error_not_a_verdict(self):
        self.respond({"Status": 2})
        self.assertEqual(check.doh_ns("x.com", NoLimit()), "error")

    def test_transport_failure_is_an_error(self):
        def boom(*a, **k):
            raise OSError("connection reset")
        check._get = boom
        self.assertEqual(check.doh_ns("x.com", NoLimit()), "error")


# --- RDAP -------------------------------------------------------------------

class TestRdap(unittest.TestCase):
    def tearDown(self):
        import importlib
        importlib.reload(check)

    def test_404_means_available(self):
        def raise404(*a, **k):
            raise http_error(404)
        check._get = raise404
        self.assertEqual(check.rdap("x", "com", NoLimit()), ("available", []))

    def test_200_means_taken(self):
        check._get = lambda *a, **k: FakeResponse({"objectClassName": "domain"})
        self.assertEqual(check.rdap("x", "com", NoLimit()), ("taken", []))

    def test_lifecycle_statuses_are_surfaced(self):
        check._get = lambda *a, **k: FakeResponse(
            {"status": ["client transfer prohibited", "redemption period"]})
        status, flags = check.rdap("x", "com", NoLimit())
        self.assertEqual(status, "taken")
        self.assertEqual(flags, ["redemptionperiod"])   # spaces stripped, lowercased

    def test_unparseable_body_still_counts_as_taken(self):
        # A 200 is a 200. Failing to read the JSON must not flip the verdict.
        class Garbage(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *e): return False
        check._get = lambda *a, **k: Garbage(b"<html>oops</html>")
        status, _ = check.rdap("x", "com", NoLimit(), retries=1)
        self.assertEqual(status, "error")   # documented: we do not guess

    def test_501_from_bootstrap_is_error_not_available(self):
        # Some ccTLD registries have no RDAP. Treating that as "available"
        # would be the worst possible failure mode.
        def raise501(*a, **k):
            raise http_error(501)
        check._get = raise501
        self.assertEqual(check.rdap("x", "ai", NoLimit()), ("error", []))

    def test_uses_the_direct_endpoint_for_com(self):
        seen = {}

        def capture(url, **k):
            seen["url"] = url
            raise http_error(404)
        check._get = capture
        check.rdap("x", "com", NoLimit())
        self.assertIn("rdap.verisign.com", seen["url"])
        check.rdap("x", "org", NoLimit())
        self.assertIn("rdap.org", seen["url"])


# --- registrar --------------------------------------------------------------

class TestPorkbun(unittest.TestCase):
    def call(self, payload):
        import urllib.request
        urllib.request.urlopen = lambda *a, **k: FakeResponse(payload)
        return check.porkbun("x", "com", "k", "s", NoLimit())

    def tearDown(self):
        import importlib
        importlib.reload(check)

    def test_available(self):
        status, flags = self.call({"status": "SUCCESS", "response": {"avail": "yes"}})
        self.assertEqual(status, "available")

    def test_premium_and_price_are_flagged(self):
        status, flags = self.call({"status": "SUCCESS", "response": {
            "avail": "yes", "premium": "yes", "price": "2400.00"}})
        self.assertEqual(status, "available")
        self.assertIn("premium", flags)
        self.assertIn("price:2400.00", flags)

    def test_api_error_is_not_a_verdict(self):
        status, _ = self.call({"status": "ERROR", "message": "bad key"})
        self.assertEqual(status, "error")


# --- zone file --------------------------------------------------------------

class TestZoneFile(unittest.TestCase):
    ZONE = (
        "; comment line\n"
        "$TTL 900\n"
        "example.com.\t900\tin\tns\tns1.example.net.\n"
        "example.com.\t900\tin\tns\tns2.example.net.\n"
        "other.com. 900 in ns ns1.host.net.\n"
        "notcom.org. 900 in ns ns1.host.net.\n"
    )

    def test_parses_labels_and_dedupes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(self.ZONE)
        names = check.load_zone(f.name, "com")
        self.assertEqual(names, {"example", "other"})

    def test_reads_gzip(self):
        with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as f:
            pass
        with gzip.open(f.name, "wt") as g:
            g.write(self.ZONE)
        self.assertEqual(check.load_zone(f.name, "com"), {"example", "other"})


# --- phonetics: the vowel-run rules that were wrong -------------------------

class TestSyllables(unittest.TestCase):
    def test_vowel_run_is_one_nucleus(self):
        # Counting vowel letters reported kulaudo as 4. It is ku-lau-do.
        self.assertEqual(ph.syllables("kulaudo"), 3)
        self.assertEqual(ph.syllables("kaido"), 2)
        self.assertEqual(ph.syllables("midako"), 3)

    def test_coda_attaches_to_its_own_syllable(self):
        self.assertEqual(ph.segment("kamin"), ["ka", "min"])
        self.assertEqual(ph.segment("pasokon"), ["pa", "so", "kon"])
        self.assertEqual(ph.segment("kanban"), ["kan", "ban"])
        self.assertEqual(ph.segment("fon"), ["fon"])

    def test_n_before_a_vowel_is_an_onset(self):
        self.assertEqual(ph.segment("kanu"), ["ka", "nu"])

    def test_triphthong_is_penalised_and_flagged(self):
        _, breakdown = ph.score("kuraui")
        self.assertIn("triphthong", breakdown["flags"])

    def test_awkward_hiatus_scores_below_a_clean_diphthong(self):
        self.assertGreater(ph.score("kaido")[0], ph.score("baeko")[0])

    def test_repeated_syllable_detected_with_real_segmentation(self):
        self.assertIn("repeated-syllable", ph.score("bebeko")[1]["flags"])
        self.assertNotIn("repeated-syllable", ph.score("kulaudo")[1]["flags"])

    def test_banned_spellings_score_zero(self):
        for name in ("fluvix", "nike", "cakedo"):
            self.assertEqual(ph.score(name)[0], 0.0, name)


class TestGenerate(unittest.TestCase):
    def test_rejects_doubled_letters_and_final_e(self):
        self.assertFalse(generate.viable("babbo"))
        self.assertFalse(generate.viable("midake"))
        self.assertTrue(generate.viable("midako"))

    def test_diphthong_patterns_produce_names(self):
        names = [c["name"] for c in generate.candidates(["CVVCV"], min_score=80, limit=50)]
        self.assertTrue(names)
        self.assertTrue(any(ph.syllables(n) == 2 for n in names))


# --- database ---------------------------------------------------------------

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "db.json"

    def test_round_trip(self):
        d = dbmod.empty()
        dbmod.add_candidate(d, {"name": "midako", "score": 93.2, "breakdown": {"flags": []}})
        dbmod.save(d, self.tmp)
        self.assertEqual(list(dbmod.load(self.tmp)["names"]), ["midako"])

    def test_confidence_never_downgrades_silently(self):
        d = dbmod.empty()
        dbmod.add_candidate(d, {"name": "x", "score": 90, "breakdown": {"flags": []}})
        dbmod.record_check(d, "x", "com", "available", "rdap", "rdap:com")
        dbmod.record_check(d, "x", "com", "taken", "dns", "doh")
        self.assertEqual(d["names"]["x"]["tlds"]["com"]["confidence"], "rdap")
        self.assertEqual(d["names"]["x"]["tlds"]["com"]["status"], "available")

    def test_force_allows_a_downgrade(self):
        d = dbmod.empty()
        dbmod.add_candidate(d, {"name": "x", "score": 90, "breakdown": {"flags": []}})
        dbmod.record_check(d, "x", "com", "available", "rdap", "rdap:com")
        dbmod.record_check(d, "x", "com", "taken", "dns", "doh", force=True)
        self.assertEqual(d["names"]["x"]["tlds"]["com"]["confidence"], "dns")

    def test_schema_1_migrates(self):
        self.tmp.write_text(json.dumps({
            "schema": 1, "tld": "com", "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "x", "domains": {"old": {
                "score": 91.0, "status": "available", "confidence": "rdap",
                "checked_at": "2026-01-01T00:00:00+00:00", "source": "rdap", "flags": []}}}))
        d = dbmod.load(self.tmp)
        self.assertEqual(d["schema"], 2)
        self.assertEqual(d["names"]["old"]["tlds"]["com"]["confidence"], "rdap")

    def test_queue_is_ordered_by_score(self):
        d = dbmod.empty()
        for n, sc in [("low", 86.0), ("high", 98.0), ("mid", 92.0)]:
            dbmod.add_candidate(d, {"name": n, "score": sc, "breakdown": {"flags": []}})
        self.assertEqual(dbmod.queue(d), ["high", "mid", "low"])


class TestPlan(unittest.TestCase):
    def test_duration_parsing(self):
        self.assertEqual(plan.parse_duration("90s"), 90)
        self.assertEqual(plan.parse_duration("2h"), 7200)
        self.assertEqual(plan.parse_duration("1h30m"), 5400)
        with self.assertRaises(ValueError):
            plan.parse_duration("soon")

    def test_zone_file_removes_the_dns_leg(self):
        with_dns = plan.seconds_for(1000, "rdap", 0.3, 40, 8, has_zone=False)
        with_zone = plan.seconds_for(1000, "rdap", 0.3, 40, 8, has_zone=True)
        self.assertLessEqual(with_zone, with_dns)


class TestDictionary(unittest.TestCase):
    def test_arpabet_conversion_strips_stress(self):
        self.assertEqual(dictionary.convert("K L AW1 D"), "klWd")
        self.assertEqual(dictionary.convert("N EY1 T IH0 V"), "nEtiv")

    def test_r_coloured_vowel_becomes_long_a(self):
        # ER -> a is what makes "personal" pasonaru rather than perusonaru.
        self.assertEqual(dictionary.convert("P ER1 S AH0 N AH0 L"), "pasanal")

    def test_shard_key(self):
        self.assertEqual(dictionary.shard_key("cloud"), "cl")
        self.assertEqual(dictionary.shard_key("a"), "a")


class TestReleasePush(unittest.TestCase):
    """`release push` is the only thing that carries a sweep slice's work to
    the next one. It used to compress the new database and then let
    `gh release download` overwrite it at the same path, so the upload sent the
    old bytes back and the release never advanced. Four chained slices checked
    roughly 25,000 names and the stored database still had zero checks."""

    @staticmethod
    def _db(n, marker):
        """A database big enough to clear push's own small-file guard."""
        return json.dumps({"names": {
            f"kaminu{i}": {"score": 90 + i % 97 / 7, "marker": marker, "tlds": {
                "com": {"confidence": "rdap", "status": "available",
                        "checked_at": f"2026-07-{i % 28 + 1:02d}"}}}
            for i in range(400)}}).encode()

    def _push(self, db_bytes, remote_bytes, stale_readback=False):
        """Run push against a stub `gh`. Returns (code, uploaded, calls).

        `stale_readback` makes the release report the size it held *before* the
        upload, which is what a silently-ineffective push looks like.
        """
        uploaded = {}
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            if cmd[1:3] == ["release", "view"]:
                if "--json" not in cmd:
                    return (0, "")                   # the release already exists
                held = remote_bytes if stale_readback else uploaded.get(releasemod.ASSET, b"")
                return (0, json.dumps({"assets": [
                    {"name": releasemod.ASSET, "size": len(held)}]}))
            if cmd[1:3] == ["release", "download"]:
                dest = Path(cmd[cmd.index("--dir") + 1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / releasemod.ASSET).write_bytes(remote_bytes)
                return (0, "")
            if cmd[1:3] == ["release", "upload"]:
                path = Path(cmd[4])
                uploaded[path.name] = path.read_bytes()
                return (0, "")
            raise AssertionError(f"unexpected gh call: {cmd}")

        original_run, original_which = releasemod._run, releasemod.shutil.which
        releasemod._run = fake_run
        releasemod.shutil.which = lambda _: "/usr/bin/gh"
        try:
            with tempfile.TemporaryDirectory() as work:
                local = Path(work) / "db.json"
                local.write_bytes(db_bytes)
                code = releasemod.push(local, "owner/repo")
        finally:
            releasemod._run, releasemod.shutil.which = original_run, original_which
        return code, uploaded, calls

    def test_uploads_the_new_database_not_the_one_it_just_downloaded(self):
        new = self._db(400, "this-slice")
        old = gzip.compress(self._db(400, "already-on-the-release"))

        code, uploaded, _ = self._push(new, old)

        self.assertEqual(code, 0)
        self.assertIn(releasemod.ASSET, uploaded)
        with gzip.open(io.BytesIO(uploaded[releasemod.ASSET]), "rb") as fh:
            self.assertEqual(fh.read(), new,
                             "push uploaded the previous release asset instead of "
                             "the local database, so the slice's work is lost")

    def test_keeps_exactly_one_previous_copy(self):
        new = self._db(400, "this-slice")
        old = gzip.compress(self._db(400, "already-on-the-release"))

        _, uploaded, _ = self._push(new, old)

        self.assertIn(releasemod.PREVIOUS, uploaded,
                      "the rollback copy was never uploaded, so a corrupt push "
                      "would leave no way back")
        self.assertEqual(uploaded[releasemod.PREVIOUS], old)

    def test_fails_when_the_release_did_not_actually_change(self):
        """The readback is the only thing that can tell an upload from a no-op.
        Without it, gh's exit code alone reported success for four slices."""
        new = self._db(400, "this-slice")
        old = gzip.compress(self._db(400, "already-on-the-release") + b"padding")

        code, _, _ = self._push(new, old, stale_readback=True)

        self.assertNotEqual(code, 0,
                            "push reported success while the release still held "
                            "the old asset")


class TestPublishGuards(unittest.TestCase):
    """`publish` refusals have to run before the write. They used to run after,
    so --fail-on-demo returned 3 having already replaced the live snapshot with
    the data it was refusing to publish."""

    def _publish(self, db, out, **flags):
        import argparse
        from pdgen import cli
        defaults = dict(
            db="unused", out=str(out), tld="com", min_confidence="rdap",
            min_score=0.0, limit=5000, include_taken=False, no_dropping=False,
            fail_on_demo=False, min_rows=0)
        args = argparse.Namespace(**{**defaults, **flags})
        original = dbmod.load
        dbmod.load = lambda _: db
        try:
            return cli.cmd_publish(args)
        finally:
            dbmod.load = original

    def test_empty_database_does_not_overwrite_a_good_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "domains.json"
            good = json.dumps({"published": 5000, "rows": ["real data"]})
            out.write_text(good)

            code = self._publish(dbmod.empty(), out, min_rows=1)

            self.assertEqual(code, 3)
            self.assertEqual(out.read_text(), good,
                             "publish overwrote the live snapshot with an empty "
                             "one before refusing")

    def test_min_rows_defaults_to_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "domains.json"
            self.assertEqual(self._publish(dbmod.empty(), out), 0)
            self.assertEqual(json.loads(out.read_text())["published"], 0)


if __name__ == "__main__":
    unittest.main()
