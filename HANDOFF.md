# Handoff

This is an MVP built from a chat draft. It runs end to end, but **every network
path in it is unverified** — the environment it was written in blocked outbound
requests to all the relevant hosts. Read this before trusting any output.

---

## 1. What is actually verified

Exercised, repeatedly, with real data:

- Candidate generation, scoring, and the obscenity screen (113,520 names, ~5s)
- The score model and its explanations (`pdgen score`)
- Database read/write/migrate, atomic saves, resume after Ctrl-C
- Schema 1 → 2 migration
- Queue ordering, tier bucketing, staleness detection, `dropping`
- Time and coverage estimation (`pdgen plan`), including self-calibration
- Snapshot publishing and the full front end, against seeded data
- The verification-patch round trip (`export` in browser → `pdgen merge`)
- Cold run from the unpacked zip

## 2. What is NOT verified — read this list

Every one of these was written against documentation and never executed.

| Area | Risk | What to do |
| --- | --- | --- |
| **DNS-over-HTTPS** (`dns.google`, `cloudflare-dns.com`) | Response parsing assumes the standard `Status` / `Answer` JSON shape. | Run `pdgen check --stage dns --top 20` and eyeball the results. |
| **RDAP** (`rdap.verisign.com`, `rdap.org`) | Parsing is now unit-tested against recorded shapes, but nothing proves the live endpoints match those shapes. Note one deliberate behaviour: a 200 whose body will not parse returns `error`, not `taken` — the tool does not guess. | Call the parser directly (see §5). `check --name google` does **not** work: it filters `--name` to names already in the db, and `google` is unreachable in every generation pattern. |
| **Porkbun registrar API** | Request shape and the `avail` / `premium` / `price` response keys are from memory, not from a live call. | Test with two known domains before trusting a run. |
| **PanLex** (`api.panlex.org/v2/expr`) | **Most likely thing to be broken.** The v2 request body was inferred from docs. Failures are swallowed silently, so a broken call looks like "no meanings found". | Verify manually with curl before believing a clean result. Use `--source wiktionary` until you have. |
| **Wiktionary** | The "top-level sections are languages" heuristic will pick up the occasional non-language section. | Spot-check a few pages. |
| **`gh release` upload** | ~~Shells out to the `gh` CLI. Untested.~~ **Was broken, now fixed and covered by tests.** `push` compressed the new database to `<tmp>/db.json.gz`, then ran `gh release download --dir <tmp> --clobber`, which wrote the *existing* asset over it, and uploaded that. The guard meant to prevent it, `(Path(tmp) / ASSET) != gz`, compares a path to itself and is always false, so it also never wrote the `db-previous.json.gz` rollback copy. Every chained sweep slice therefore discarded the previous slice's work. Observed 2026-07-28: four slices checked ~25,000 names and the stored database still reported zero checks. | `pdgen release push` then `pdgen release status`. `TestReleasePush` in `tests/test_parsers.py` fails if this regresses. |
| **Browser live verification** | RFC 7480 says RDAP servers should send permissive CORS headers. Whether Verisign and rdap.org actually do, in practice, from a `github.io` origin, is unconfirmed. | Click **Check** on one row. If it fails the UI tells you and falls back to the CLI. |
| **Coining engine** (`docs/nativize.js`) | English G2P is a heuristic ruleset, not a pronunciation dictionary. It reproduces `pasokon` and `lemokon` correctly, but it will mangle irregular spellings. | Read the Japanese column in the coin panel; it shows what the engine thinks the word sounds like. |
| **Browser scorer** (`docs/score.js`) | Re-implements the Python scoring arithmetic. `verifyAgainstFixtures` re-derives six published scores at load and warns in the console on drift — but only for those six. | If you change `phonetics.py`, re-run `pdgen publish` and watch the console. |
| **Pages source** | Must be **GitHub Actions**, not "deploy from a branch". The dictionary shards are gitignored and only exist inside the Actions artifact, so a branch deploy silently ships a site whose Dictionary toggle never works. `scripts/bootstrap.sh` sets this. | After the first deploy, open the coin panel and confirm the Dictionary chip is not disabled. |
| **`scripts/bootstrap.sh`** | Never executed. The Pages API call has two fallbacks but has not been run against a real account. | If it fails, set Settings → Pages → Source: GitHub Actions by hand. |
| **`sweep.yml` chaining** | The *resume* half is now proven offline by `tools/rehearse_chain.py` (9 slices, zero duplicate checks, queue drains, `complete` flips once) and runs in CI. The *dispatch* half is still unproven: a chain that fails to fire looks identical to a completed queue. | `gh workflow run sweep.yml -f duration=5m -f rdap_rps=1 -f chain_remaining=3`, then watch for a second run appearing on its own. |
| **`sweep.yml`** | Never executed. The release pull/push steps depend on `gh` behaving inside Actions. | Trigger it manually with a short slice before letting the schedule run: `gh workflow run sweep.yml -f duration=5m -f rdap_rps=1 -f chain_remaining=3`. There is no `top` input. |
| **Zone file parser** | Tested only on synthetic input, never on a real 20 GB CZDS file. | Check the loaded label count looks sane. |
| **Rate limits** | `--rdap-rps 8` is a guess. Registries throttle aggressively and will ban. | **Start at `--rdap-rps 2` and watch for 429s.** |

## 3. The GitHub Releases decision, and why the snapshot is still committed

You asked to keep the result JSON out of git. That works for the working
database but not for the site's data file, because of a hard platform limit:

**GitHub release assets cannot be fetched from a browser.** The download URL is
a 302 to a blob host; CORS preflights do not follow redirects, and the final
asset carries no `Access-Control-Allow-Origin`. There is no way around this
without a proxy.

So the split is:

| File | Size | Where it lives | Why |
| --- | --- | --- | --- |
| `db.json` | ~21 MB, churns constantly | **GitHub release** (`pdgen release push`) | Only Python reads it. No CORS involved. Gitignored. |
| `docs/data/domains.json` | ~375 KB, changes on publish | **Committed** | GitHub Pages must serve it to the browser. |

That still keeps the bulk dict data out of git, which was the goal. If you truly
want the snapshot out of the repo too, you need a CORS-capable host — Cloudflare
R2, a gist, or jsDelivr pointed at a tagged commit.

## 3b. Why there is no `pdgen coin`

The transliteration engine exists once, in `docs/nativize.js`, and has no Python
twin. That is deliberate: an earlier draft had both, sharing rule tables via
JSON, and that is exactly the kind of duplication that drifts silently.

Coining is interactive and low-volume — you try a few words and look at what
comes out. You never sweep 100,000 coined names. So it belongs in the browser,
and results travel back through `pdgen merge`, which already existed. The CLI
keeps its job (bulk sweeps, rate limiting, a 21 MB database) and stays
zero-dependency Python with no build step.

The same reasoning is why the CLI was not rewritten in TypeScript. Type safety
on the web side comes from `// @ts-check` plus JSDoc, verified in CI by
`tsc --noEmit` against `tsconfig.json`, with no build step and `docs/` still
served as raw files.

The one place duplication remains is scoring: the browser must score names it
just coined. The constants all ship from Python in the snapshot, only the
arithmetic is repeated, and fixtures catch drift.

## 3c. The dictionary, and why it is not in a release

Putting CMUdict in a GitHub release and querying it live from the browser does
not work, for the same reason the database snapshot has to stay committed:
**release assets cannot be fetched cross-origin.**

What does work is splitting the two roles. CI *builds* the dictionary (it can
download from anywhere), and GitHub Pages *serves* it. It is sharded by the
first two letters of each word, so "cloud native" fetches `cl.json` and
`na.json` — a median of 0.4 KB each — rather than 3.6 MB.

The shards are gitignored and rebuilt into the Pages artifact on every deploy,
so 468 files never enter your history. For a local preview:

```bash
python3 -m pdgen dictionary build      # ~15s, writes docs/data/cmudict/
```

**A real finding: the dictionary is more accurate and less Japanese-faithful.**
CMUdict says "personal" is /pasinal/ — a schwa in the middle, which is correct
English. But Japanese renders unstressed English vowels from the *spelling*,
which is why パーソナル is pa-so-na-ru and the clipping is **pasokon**, not
pasikan. So:

| Mode | `personal computer` |
| --- | --- |
| Spelling heuristic | `pasokon` — matches the real Japanese loanword |
| CMUdict | `pasikan` — phonetically accurate English |

Neither is wrong. The site exposes both via the **Dictionary** toggle. If you
want Japanese-flavoured coinages, turn it off; if you want phonetically sound
novel names, leave it on. Reconciling them properly means mapping unstressed
schwa back to the spelled vowel, which is a real piece of work and is not done.

## 3d. Vowel runs

An early version counted vowel *letters* as syllables, which was correct only
for strict CV. Once diphthongs entered through the coining engine it silently
broke: `kulaudo` reported four syllables when it is ku-lau-do, three. Every
syllable filter, the `--syllables` flag and the site's chips inherited the
error, and the repeated-syllable penalty was chopping names into fixed
two-letter pairs, which puts the boundaries in the wrong place for anything
with a diphthong or a coda.

Now there is a real syllabifier — onset consonants, vowel run, optional coda
`n` — shared by both implementations and covered by tests. Vowel pairs are
scored by how consistently speakers resolve them:

| | |
| --- | --- |
| `ai au oi` | 1.00 — near-universal falling diphthongs |
| `ei ou` | 0.95 |
| `ia io ua ue ui iu` | 0.85–0.88 — rising, read as glide plus vowel |
| `ie uo` | 0.80–0.82 |
| `ae ao ea eo oa oe eu` | 0.62–0.70 — hiatus; Spanish and Italian readers break these into two syllables and English readers often do not, so the name changes shape depending on who says it |
| three in a row | −25 and flagged `triphthong` |

`CVVCV` and `CVVC` are now default generation patterns, so diphthong names
(`kaido`, `naumi`, `taiko`) are in the candidate space at all — previously they
could not be expressed.

## 3e. Project pages and the llms.txt convention

The site is served from `you.github.io/<repo>/`, not from a domain root. Two
consequences:

1. **Every path in the discovery files is relative.** An absolute
   `/data/domains.json` resolves to the account root and 404s. This was wrong
   in the first cut and is fixed; if you add new links, keep them relative.
2. **`llms.txt` is conventionally fetched from the site root.** An agent that
   guesses `you.github.io/llms.txt` will miss it. An agent given the full
   project URL is fine. If root-level discovery matters to you, either point a
   custom domain at the repo (add a `CNAME` file in `docs/`) or name the repo
   `<username>.github.io`, which serves at the account root.

`docs/data/api.json` is regenerated by `pdgen publish`. It used to be a
hand-written file, which meant it silently drifted from the snapshot it
described.

## 3f. Why the sweep is chunked

Hosted Actions jobs are terminated at 6 hours and the termination is a failure.
An earlier version pushed the database to the release as the *final* step,
which meant a job hitting the wall lost the whole run — the on-disk checkpoints
live on an ephemeral runner. Fixed three ways: `--max-duration` stops the CLI
first, `timeout-minutes: 350` sits above the 5h budget and under the 6h wall,
and every step after the check is `if: always()`.

**`always()` on its own was too broad, and the hole was a data-loss one.** The
steps that write now also require `steps.check.conclusion != 'skipped'`. If a
step before the check fails, `check` is skipped and the runner has no database
at all. `pdgen publish` does not object to that: it writes a structurally valid
snapshot with `published: 0` and exits 0, and `--fail-on-demo` does not fire
because an empty snapshot is not demo data. The commit step would then push
that over the live one and the site would serve nothing, silently. Publishing
from a check that *failed* is intentional and still happens; publishing from a
check that never ran is the bug. Worth knowing if you call `pdgen publish` by
hand after a failed `release pull`: the CLI has the same sharp edge.

Two platform behaviours worth knowing:

- **Scheduled workflows are disabled after 60 days of repository inactivity**,
  silently, and re-enabling is manual. The sweep commits `.github/last-sweep`
  every run so there is always default-branch activity.
- **Scheduled runs are delayed 10–30 minutes routinely**, sometimes longer.
  Fine for a sweep. Do not build anything time-sensitive on the schedule.

**Correction to an earlier version of this document:** I previously wrote that
self-dispatch needs a PAT. That is out of date. GitHub carved out an exception
in September 2022 — `workflow_dispatch` and `repository_dispatch` *always*
create workflow runs, even from `GITHUB_TOKEN`, because they are explicit calls
unlikely to loop by accident. The workflow now chains itself with the default
token and `permissions: actions: write`. No secret required.

Two guards on the chain, because an unbounded self-triggering workflow is a
good way to burn a budget:

- `chain_remaining` decrements each hop, so a stuck state costs a bounded
  number of slices rather than running forever. A manual dispatch starts at 12,
  the weekly cron at 2. The cron is also 90m at 1 req/s rather than 5h at 2, so
  its worst case is roughly 16,000 registry calls in a week, not 70,000. The
  three numbers live on the `Validate the slice length` step's env block, and
  because `workflow_dispatch` inputs always carry their declared defaults,
  those fallbacks only ever apply to a scheduled run.
- The chain only fires on `success()`. If a slice fails, nothing re-dispatches
  and an issue is opened instead. The issue comes from a separate `notify` job
  with `needs: [sweep, deploy]`, so a Pages deploy failure is reported too.
  When it was a step inside `sweep`, a deploy failure was completely silent:
  observed 2026-07-28, when `deploy-pages` hit "Multiple artifacts named
  github-pages" and nothing was raised.

## 4. Known design limitations

- **The score is not empirically validated.** The phoneme weights and penalties
  are informed by cross-linguistic phonology, but nobody has tested whether
  high-scoring names are actually easier to say. Treat it as a ranked
  hypothesis. Say your shortlist out loud to speakers of unrelated languages.
- **The bundled blocklist is a starting point.** It removed ~3,500 of 117,000
  names, but it only covers what I could enumerate. Add the LDNOOBW multilingual
  list before publishing anything.
- **No trademark check.** Availability and freedom to operate are different
  questions; this only answers the first.
- ~~The rate limiter is per-process.~~ **Fixed.** A lock file next to the
  database now blocks a second network run, reclaims stale locks from crashed
  processes, and can be overridden with `--force-lock`.
- **No incremental snapshot loading.** The site fetches all ~376 KB of
  `domains.json` on every page load. Fine now; add ETag handling or a
  versioned filename if the snapshot grows past a megabyte.
- ~~`query` reads the whole database.~~ **Fixed.** It now reads the published
  snapshot by default (0.10s vs 1.29s); pass `--source db` or
  `--include-unchecked` to search everything.
- **The 4-syllable space is sampled, not swept.** `CVCVCVCV` is 6.25M
  combinations. The default run samples 2%. Raise `--sample` if you want more.
- **Meanings have no glosses.** Wiktionary tells you *that* a string is a word in
  Basque, not what it means. You get a link. Adding glosses means parsing
  wikitext, which is a real project.
- **`--stage registrar` only supports Porkbun.** Others need a new function in
  `check.py`; the interface is one function returning `(status, flags)`.
- **`db.json` is loaded whole into memory.** Fine at 113k names (~2s, ~400 MB).
  Past ~1M it will hurt, and `queue()` re-sorts the entire db on each call.
- ~~Nothing tests the network parsers.~~ **Fixed.** `tests/test_parsers.py` has
  35 tests covering DoH, RDAP, Porkbun, zone files, syllabification, the
  database, planning and ARPAbet conversion, using recorded response shapes and
  no network. CI runs them. The orchestration around the parsers is still
  untested, and no test can prove the live endpoints behave as recorded.
- **The coin panel's G2P is spelling-based.** A pronunciation dictionary
  (CMUdict) would be far more accurate but is 3.5 MB — too heavy to ship to a
  browser. Words with irregular spelling will come out wrong.
- **Browser live checks are capped at 400** and cached for 21 days in
  localStorage. Clearing site data loses the harvest, so export regularly.

## 5. First thirty minutes on your machine

```bash
# 0. Clear the fake data. It is seeded, labelled, and must not be trusted.
python3 tools/seed_demo.py --clear

# 1. Prove the network paths work before spending hours on them.
#
#    Call the parsers directly. An earlier version of this document said to use
#    `check --stage rdap --name google`; that cannot work. cmd_check filters
#    --name down to names already in the db, and `google` is unreachable in
#    every generation pattern (g is not in the alphabet, gl is a cluster, and a
#    trailing e is banned). The command prints "nothing to check" and exits 0 --
#    a pass that proves nothing.
python3 -c "from pdgen.check import rdap, RateLimiter as R; print(rdap('google','com',R(1)))"
#    -> must print ('taken', []). If it says available, RDAP parsing is broken.
python3 -c "from pdgen.check import rdap, RateLimiter as R; print(rdap('kaminubadolomi','com',R(1)))"
#    -> must print ('available', []). Proves a 404 is read as free, not as an error.
python3 -c "from pdgen.check import doh_ns, RateLimiter as R; print(doh_ns('google.com',R(1)))"
#    -> must print taken.

python3 -m pdgen check --stage rdap --top 20 --rdap-rps 2
python3 -m pdgen stats

# 2. Size the real run before starting it.
python3 -m pdgen plan --rdap-rps 2 --budget 1h --budget 8h

# 3. Two-syllable names first. Only ~23k of them, and any hit is a pearl.
python3 -m pdgen check --top 23000 --min-score 90 --rdap-rps 2
python3 -m pdgen dropping        # short names in redemption are the real prize

# 4. Meaning pass on whatever came back free.
python3 -m pdgen meaning --only-available --top 200 --source wiktionary

# 5. Alternates for the good names whose .com is gone.
python3 -m pdgen alternates --tld net --tld org --tld co --top 100

# 6. Publish and ship.
python3 -m pdgen publish --min-confidence rdap
python3 -m pdgen release push
git add docs/ && git commit -m "first real snapshot" && git push
```

## 6. Where the value probably is

Not in six-letter CVCVCV — that space is enormous and mostly free, so it needs
no cleverness to mine. The interesting output is:

1. **`pdgen dropping`.** Short, high-scoring names in redemption. They are
   registered today and return to the pool within weeks. This tool finds them;
   it does not register them, so pair it with a backorder service.
2. **Two-syllable survivors.** Any 4- or 5-letter CV name that is genuinely free
   is an anomaly worth investigating immediately.
3. **The meaning pass.** A name that already means "star" or "river" in a real
   language is worth more than a random string with a slightly higher score.

## 7. Things worth building next

- Glosses in the meaning pass (parse Wiktionary wikitext, or fix PanLex)
- A watchlist: re-check a handful of dropping names daily and alert
- Real tests, especially for the response parsers in `check.py`
- Pronunciation validation with actual speakers, to calibrate the score
- Backorder integration, so a drop can be acted on rather than just observed
