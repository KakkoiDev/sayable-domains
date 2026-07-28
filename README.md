# Sayable

Find domains built only from sounds that appear in almost every language on
earth, rank them, check availability best-first, and publish the result as a
static site.

> **Read [HANDOFF.md](HANDOFF.md) first.** This is an MVP. Every network path in
> it was written against documentation and never executed. The offline half —
> generation, scoring, screening, the database, planning, the website — is
> thoroughly exercised. The online half is not.

Two halves:

- **`pdgen`** — a zero-dependency Python CLI. Generates candidates, scores them
  for cross-linguistic pronounceability, checks availability highest-score
  first, and looks up what the survivors mean in other languages.
- **`docs/`** — a static front page for GitHub Pages: ranked list, search,
  filters, and live re-verification against the registry.

```
  pass 1                pass 2                        refinements
┌──────────┐      ┌──────────────┐            ┌────────────────────────┐
│ generate │─────>│ plan → check │───────────>│ meaning · alternates   │
│ no       │      │ best         │            │ stale   · dropping     │
│ network  │      │ candidates   │            └────────────────────────┘
└──────────┘      │ first        │                        │
                  └──────────────┘                        v
                          │                          publish
                          v                               │
                  db.json ──> GitHub release      docs/data/domains.json
                  (gitignored)                    (committed, GH Pages)
                                                          │
                                            browser "full check" ──> merge
```

---

## Quickstart

Python 3.10+. No `pip install`; standard library only.

```bash
# Pass 1 — scored, obscenity-screened candidates. Nothing is checked yet.
python3 -m pdgen generate

# See what checking would cost before you start.
python3 -m pdgen plan --budget 1h --budget 8h

# Preview the site with fake-but-labelled data.
python3 tools/seed_demo.py --limit 9000
python3 -m pdgen publish
cd docs && python3 -m http.server 8000     # open localhost:8000

# When you're ready for real results.
python3 tools/seed_demo.py --clear
python3 -m pdgen check --stage rdap --top 5000 --rdap-rps 2
python3 -m pdgen publish --min-confidence rdap
```

---

## The two passes

### Pass 1 — `generate`

Pure computation. Expands C/V patterns over the safe alphabet, scores every
result, drops anything the obscenity filter catches, and writes the database.
No network, no waiting, no rate limits.

Default patterns are `CVCV`, `CVCVC`, `CVVCV`, `CVVC`, `CVCVCV` — two and three
syllables, including diphthong names like `kaido` and `naumi`. Vowel pairs are
scored by how consistently speakers resolve them: `ai au oi` are near-universal,
`ae ao eo` are hiatus that different languages syllabify differently, and three
vowels in a row is rejected.
Four-syllable names are a 6.25M-combination space, so sample it:

```bash
python3 -m pdgen generate --pattern CVCVCVCV --sample 0.02 --min-score 90
```

Candidates are sorted into **tiers** by score, and everything downstream works
through them in order:

| Tier | Score |
| --- | --- |
| S | 95+ |
| A | 92–95 |
| B | 89–92 |
| C | 85–89 |
| D | below 85 |

### `plan` — how long, and how far

The point of ranking before checking is that you will never check everything.
So the useful question is not "how long is the full sweep" but "what do I reach
in the time I have". `plan` answers both:

```
$ python3 -m pdgen plan --budget 30m
  survives DNS: 59% (measured from 9,000 checks)
  ------------------------------------------------------------------
  TIER    SCORE     NAMES  TO CHECK   RDAP+   FREE      TIME  CUMULATIVE
  S         95+     7,905     2,920   1,903  1,660        4m          4m
  A         92+    33,055    31,269     699    621       38m         42m
  B         89+    43,995    41,766     824    791       51m      1h 32m
  C         85+    28,565    28,565       0      0       35m      2h 07m

  What a time budget buys you:
      30m    24,550 names  down to score 92.6 (tier A)  23% of queue
```

The DNS survival rate is measured from your own history, so estimates sharpen
as you run. Add `--zone-file` or change `--rdap-rps` to see the effect.

### Pass 2 — `check`

Walks the queue **highest score first**, always. An interrupted run, or a budget
that only covers 10% of the space, will have spent itself on the best
candidates. Ctrl-C saves and exits cleanly; re-running resumes.

```bash
python3 -m pdgen check --stage rdap --tier S --tier A --rdap-rps 2
python3 -m pdgen check --dry-run --top 5000      # estimate only
```

---

## Confidence: not everything is checked to the same depth

A run can stop at any stage of the funnel, so the database mixes levels. Each
is tracked **per TLD** and shown on the site as a three-step indicator.

| Level | What it means | Trust it for |
| --- | --- | --- |
| `generated` | Scored only. Never checked. | Nothing |
| `dns` | Absent from the zone. **Misses registered-but-undelegated names.** | A shortlist |
| `rdap` | The registry itself said it isn't registered. | Almost everything |
| `registrar` | A registrar confirmed you can buy it, and at what price. | Buying |

Only `registrar` catches premium pricing and registry-reserved names.

**Everything goes stale.** Domains get registered every second.

```bash
python3 -m pdgen stale --older-than 21           # report
python3 -m pdgen check --recheck-older-than 21   # re-verify
```

The site shows each name's age, marks anything past three weeks in red, and
warns when the whole snapshot is old.

---

## Finding pearls

Six-letter CVCVCV is a big, mostly-free space that needs no cleverness. The
interesting output is elsewhere.

**Names about to drop.** RDAP reports lifecycle status, so a name in
`redemptionPeriod` is registered today and back in the pool within weeks. These
are kept in the published snapshot even though they are technically taken, and
flagged **dropping** on the site.

```bash
python3 -m pdgen dropping
```

**Two-syllable survivors.** Every 4-letter `.com` has been registered since
around 2015 and 5-letter is heavily mined, so any genuinely free two-syllable
name is an anomaly worth checking immediately. There are only ~23k of them —
sweep the whole class.

**Names that already mean something.**

```bash
python3 -m pdgen meaning --only-available --top 300
```

Looks each name up in Wiktionary (default; ~1.3M entries, hundreds of languages
on shared pages) or PanLex (`--source panlex`; ~20M expressions across ~9,000
varieties, experimental — see HANDOFF.md). A hit is not automatically bad:
"means *star* in Swahili" is the pearl, "means something crude in Tagalog" is
the landmine the blocklist missed. The tool reports; you judge. Hits are marked
**word** on the site.

**Other endings.** When a good name's `.com` is gone:

```bash
python3 -m pdgen alternates --tld net --tld org --tld co --top 200
```

Known TLDs: `net org co io app dev xyz ai me sh`. `.ai` and `.sh` have
unreliable RDAP coverage and are flagged at runtime.

---

## Why these letters

Alphabet: `b d f k l m n p s t` and `a e i o u`.

**Phoneme inventory.** These sounds appear in the overwhelming majority of the
world's languages. Excluded: `th` (globally rare), `v`/`z` (merge with `b`/`s`
for Spanish, Japanese, Korean speakers), `r` (a tap, trill, approximant or
uvular fricative depending on where you are), `h` (silent for French, Italian,
Spanish readers).

**Syllable structure.** Strict CV alternation, no codas, no clusters — this
matters more than the phoneme choice. Japanese, Mandarin, Hawaiian, Italian and
Spanish all break consonant clusters. Open syllables are the universal
denominator, which is why *Toyota*, *Nokia* and *Hakuna* travel intact.

**Orthographic determinism.** `c` reads as /k/, /s/, /tʃ/ or /θ/; `j` as /dʒ/,
/x/, /ʒ/ or /j/. Both banned, with `q w x y`. Trailing `-e` is banned because
English readers silence it — the *Nike* problem.

`--extended` unlocks `g r h v z`; the scorer prices each one in.

Score is a weighted mean of per-phoneme universality (consonants 0.62, vowels
0.38, scaled to 90) plus variety bonuses, minus penalties for repeated
syllables, a single vowel throughout, an `l`/`r` clash, and length. Length is
capped at −6: it is a marketing preference, not a pronunciation problem, so
four-syllable names stay competitive.

```bash
python3 -m pdgen score midako tesabu
```

---

## Storage: releases, not commits

`db.json` reaches ~21 MB and churns on every run — exactly what git is worst at.
It lives in a GitHub release instead, and is gitignored:

```bash
python3 -m pdgen release push      # gzip + upload (needs the `gh` CLI)
python3 -m pdgen release pull      # fetch on another machine
python3 -m pdgen release status    # compare local against remote
```

**The published snapshot still has to be committed.** GitHub release assets
cannot be fetched from a browser — the URL 302s to a blob host that sends no
CORS headers. GitHub Pages serves `docs/data/domains.json` instead, and it's
only ~375 KB. Details in HANDOFF.md.

---

## Coining names from English

The **Coin one from English** panel mimics how Japanese borrows English: force
the word into open syllables, insert /u/ (or /o/ after t and d — that default
is what produces the -mu, -ru, -su endings), then clip the first two morae of
each element. It reproduces real coinages from English input:

| Input | Output |
| --- | --- |
| `personal computer` | `pasokon` |
| `remote control` | `lemokon` (Japanese *rimokon*) |
| `cloud native` | `kina`, `kuna`, `kulanati` |

Japanese defaults to /u/, but **All vowels** also tries a, e, i, o — the
"wrong" vowel is often the better name. Output is screened against the same
blocklist the CLI uses, scored with the same constants, and ranked shortest
first.

This lives only in the browser. There is deliberately no `pdgen coin`: coining
is interactive and low-volume, so a second implementation would only drift.
Results travel back through the export/merge loop.

## The website

Three static files and one JSON. Ranked list with tier badges, instant search
(`^mi` starts with, `do$` ends with), filters for syllables, length, tier,
confidence, dropping-soon and already-a-word. Click any name for a panel with
its sounds explained one by one, the score breakdown, alternate TLDs, and
meanings.

The hero shuffles six names that are **registry-confirmed available** — press
for six more. Star anything to bookmark it; bookmarks and live-check results
are cached in your browser and survive a reload.

**Full check** re-queries the registry live (capped at 400, ~4/sec, cached 21
days). **Export harvest** downloads everything you learned as a patch:

```bash
python3 -m pdgen merge ~/Downloads/sayable-harvest-2026-07-28.json
python3 -m pdgen bookmarks
```

Coined names come across with their English origin intact, so an afternoon of
clicking around becomes reusable data. **Bookmarks CSV** exports a flat list.

## Automation: sweeping over days

A GitHub-hosted job is **terminated at 6 hours, and the termination is a
failure** — so one long job is the wrong shape. `sweep.yml` checks for up to **5 hours**, saves, and — if the queue is not
empty — **dispatches itself again**, chaining until everything is up to date.
Each slice resumes exactly where the last stopped, because the check queue is
ordered by score and that ordering is stable.

Self-dispatch uses the default `GITHUB_TOKEN`: `workflow_dispatch` is an
explicit exception to Actions' recursion guard. No PAT, no stored secret — it
just needs `permissions: actions: write`.

The arithmetic:

| | |
| --- | --- |
| Candidates | ~117,000 |
| Survive the DNS stage | ~30% → ~35,000 registry calls |
| At `--rdap-rps 2` | ~4.9 hours of RDAP time |
| At 5h/slice | a first full sweep is **about one slice** |
| Afterwards | the queue is mostly re-verification past 21 days |

```bash
gh workflow run sweep.yml -f duration=5h -f rdap_rps=2
```

Two guards on the chain, since a self-triggering workflow can burn a budget:
`chain_remaining` decrements each hop, and the chain only fires on success. A
failed slice stops everything and opens an issue.

**5 hours at 2 req/s is 36,000 requests in one session from one IP.** That is a
lot to ask of a registry. If you see 429s, lower `rdap_rps` to 1 before you
shorten the slice: a slow sweep finishes, a banned one does not.

**The scheduled run is deliberately gentler than a manual one.** A hand-run
slice is 5h at 2 req/s and may chain 12 times, because you are watching it.
The weekly cron gets 90m at 1 req/s and a chain cap of 2, which is roughly
5,400 registry calls per slice and at most ~16,000 in a week. That is enough to
keep the 21-day re-verification window fresh without pointing a standing
36k/day load at Verisign forever. The numbers live in one place: the `SLICE`,
`RPS` and `CHAIN` env vars on the `Validate the slice length` step. Because
`workflow_dispatch` inputs always carry their declared defaults, those
fallbacks only ever apply to a scheduled run.

Five things this gets right that are easy to get wrong:

- **`--max-duration 5h` stops the CLI before Actions kills it**, with
  `timeout-minutes: 350` as a backstop under the 6h wall. A job killed at the
  wall loses everything not yet pushed, and the runner disk is ephemeral.
- **Every step after the check runs `if: always()`.** Losing an hour of
  registry calls because a later step broke would be the worst failure here.
  The steps that *write* carry a second condition, `steps.check.conclusion !=
  'skipped'`, because `always()` on its own is a data-loss path: if something
  fails before the check, the runner never pulls a database, and `pdgen
  publish` will cheerfully write a valid **0-row** snapshot and exit 0.
  `--fail-on-demo` does not catch it, since an empty snapshot is not demo data.
  Committing that replaces the live site with nothing. A check that *failed* is
  fine to publish from; a check that never ran is not.
- **`concurrency` prevents overlapping slices.** The CLI's lock file is
  per-machine and cannot see another runner, so this is the only thing stopping
  two runs from doubling your real request rate.
- **Each run commits a marker file.** Scheduled workflows are silently disabled
  after 60 days of repository inactivity, and a sweep that finds nothing new
  would otherwise make no commit at all.
- **`--max-duration` above 5 is refused**, not clamped. Guessing at the 6h wall
  is how you lose a run.

Scheduled runs are commonly delayed 10–30 minutes and occasionally more. This
does not matter for a sweep; do not build anything time-critical on it.

If a scheduled run fails, GitHub does not email you. The workflow opens an
issue instead (one at a time, labelled `sweep-failure`). That lives in a
separate `notify` job with `needs: [sweep, deploy]`, so a failure in *either*
is reported. An earlier version had it as a step inside `sweep`, which meant a
Pages deploy failure notified nobody: the sweep job went green, the site
silently went stale, and the only trace was a red tick in the Actions tab.

### Rehearsing the chain

The design rests on one claim: a slice that stops on its budget resumes exactly
where it left off. Nothing in CI would catch a violation, because a chain that
fails to fire looks identical to a finished queue. So test it offline first:

```bash
python3 tools/rehearse_chain.py --slice 2s --slices 10 --names 4000 --latency 0.02
```

Real `pdgen check` subprocesses against a stub registry — real queue ordering,
real `--max-duration`, real save-and-reload, real `$GITHUB_OUTPUT` signal:

```
  slice    checked  cumulative  remaining  complete    wall   resume point
  1            477         477       3345     false    2.2s   kinama
  2            475         952       2870     false    2.2s   binema
  ...
  8            476       3,795         27     false    2.2s   kudomi
  9             27       3,822          0      true    0.2s   —

  [PASS] every name checked exactly once     no duplicate registry calls
  [PASS] resume point is always the best unchecked name
  [PASS] queue drained to empty              complete=true on slice 9
  [PASS] all candidates accounted for        3,822 of 3,822
```

A smaller version runs in CI. It cannot tell you the live registry behaves as
stubbed, or that GitHub dispatches the next run — those need a real trial.

### Then a real 5-minute trial

```bash
gh workflow run sweep.yml -f duration=5m -f rdap_rps=1 -f chain_remaining=3
```

Five-minute slices, one request per second, at most three chained runs. Watch
the Actions tab: you should see a second run appear on its own within a minute
of the first finishing. If it does not, the chain is not firing — check that
`actions: write` survived in `permissions`.

Slices take a duration string, so `5m`, `90m` and `5h` all work, validated by
the same parser `--max-duration` uses. Anything over 5h is refused.

## Continuous integration

Releases work as storage precisely because Actions can fetch release assets
even though browsers cannot.

`.github/workflows/check.yml` type-checks `docs/*.js` with
`tsc --noEmit` (JSDoc annotations, `checkJs: true`, no build step) and
smoke-tests the offline Python pipeline.

`.github/workflows/pages.yml` builds the dictionary shards and deploys `docs/`
on every push that touches it. The sweep deploys too, but only on a schedule or
a manual dispatch, so without this a change to the site would sit unpublished
until the next slice. Both share the `pages-deploy` concurrency group, because
two simultaneous Pages deployments are an error. The sweep's snapshot commit
carries `[skip ci]`, so it does not double-deploy.

## For agents

An agent pointed at the deployed URL can find everything on its own:

| Path | |
| --- | --- |
| `/llms.txt` | orientation, and the warning about confidence levels |
| `/skill.md` | the full operating guide |
| `/data/api.json` | machine-readable schema for every endpoint |
| `/data/domains.json` | the snapshot |
| `/data/cmudict/index.json` | dictionary shard manifest |

From the CLI, `query` emits JSON built for parsing rather than reading:

```bash
python3 -m pdgen query --name kaminu
python3 -m pdgen query --available --syllables 2 --min-confidence rdap --top 20
python3 -m pdgen query --dropping --compact
```

Every response carries the database timestamp, per-result `confidence` and
`stale`, and a `caveat` field restating that `dns` is a lead rather than an
answer — so an agent cannot easily report a lead as a confirmation.

## Pronunciation dictionary

Coining guesses English pronunciation from spelling by default. Build the
CMUdict shards for real pronunciations:

```bash
python3 -m pdgen dictionary build      # ~15s, 117k words, 468 shards
```

Sharded by first two letters, so a lookup fetches ~0.4 KB, not 3.6 MB. The
shards are gitignored — CI rebuilds them into the Pages artifact each deploy.
Toggle **Dictionary** in the coin panel to compare; see HANDOFF.md for why the
two modes disagree and which you want.

## Agent skill

`skill/` is a Claude skill that teaches an agent to drive this toolkit safely —
estimate before sweeping, never round a DNS result up to "available", never
commit `db.json`. Install `sayable.skill`, or point an agent at `skill/SKILL.md`.

If your browser blocks the request, the UI says so and gives you the CLI
command.

## Deploying

```bash
./scripts/bootstrap.sh my-repo-name
```

Creates the repo, pushes, enables Pages, builds a first snapshot and seeds the
database release — using your own `gh` credentials.

**Pages must be set to build from GitHub Actions, not from a branch.** The
dictionary shards are gitignored and built fresh into the Pages artifact on
each deploy; a branch-based deploy would ship a site with the Dictionary toggle
permanently dead. The bootstrap script sets this for you, or:
**Settings → Pages → Source: GitHub Actions**.

---

## Command reference

```
pdgen [--db db.json] <command>

generate    pass 1: scored, screened candidates. No network.
plan        estimate time and coverage before checking
check       pass 2: availability, best candidates first
alternates  other TLDs for names whose .com is taken
meaning     what these strings mean in other languages
stale       results past their freshness window
dropping    taken names heading back to the pool
publish     write the snapshot the website reads
release     push/pull/status the working db as a GitHub release
merge       apply a verification patch exported from the site
stats       counts by tier, syllable, status, confidence
score       explain a score
screen      re-run the blocklist over the db
```

Every subcommand takes `--help`.

---

## Tests

```bash
python3 -m unittest discover tests -v      # 35 tests, no network, no deps
```

They cover the response parsers — DoH, RDAP, Porkbun, zone files — against
recorded shapes, plus syllabification, the database, planning and ARPAbet
conversion. That is where the bugs are: a wrong assumption about a response
should fail here rather than silently mark a registered domain as available.

## Concurrency

Two `pdgen check` runs at once would double your real request rate against the
registry. A lock file next to the database prevents that, reclaims stale locks
from crashed runs automatically, and can be overridden with `--force-lock` if
you are certain.

## Screening

Randomly generated CV strings will eventually spell something unfortunate in a
language you don't speak. `pdgen/data/blocklist.txt` removed ~3,500 of 117,000
names on the default run, **but it is not sufficient**:

```bash
git clone https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words
python3 -m pdgen screen --blocklist List-of-*/en --blocklist List-of-*/es --remove
```

---

## Before you buy

The score is a hypothesis about pronounceability, not a fact. Say your shortlist
out loud to speakers of a few unrelated languages, then run a trademark search.
Availability and freedom to operate are different questions, and this tool only
answers the first one.
