---
name: sayable
description: Drive the Sayable domain-name toolkit (the `pdgen` CLI) to find, rank, and verify available domain names built from cross-linguistically pronounceable sounds. Use this skill whenever the user wants to find or check domain names, brainstorm a company/product/project name, check whether a domain is taken, hunt for expiring or dropping domains, coin a brandable name from English words, or work with a Sayable repo (db.json, docs/data/domains.json, pdgen). Trigger it even when the user only says something like "I need a name for my startup", "is example.com free?", "find me a short domain", or "what should I call this project" — naming and domain availability are exactly what this tool is for. Also use it when asked to interpret, refresh, or publish an existing Sayable database.
---

# Sayable

A toolkit for finding domain names that anyone on earth can pronounce. Two
halves: a zero-dependency Python CLI (`pdgen`) for bulk work, and a static site
for interactive exploration.

**Before doing anything else, read `HANDOFF.md` in the repo.** It lists which
parts are verified and which have never made a live network request. Do not
present unverified output as fact.

## The one rule that matters most

**Network checks cost real requests against registries that will throttle and
ban.** Never launch a large sweep without first estimating it and confirming
with the user.

```
Always:  pdgen plan  →  show the estimate  →  get agreement  →  pdgen check
Never:   pdgen check --top 50000   (unannounced)
```

Start at `--rdap-rps 2`. The default of 8 is optimistic and untested.

## Workflow

### 1. Orient

```bash
python3 -m pdgen stats          # what's in the database
python3 -m pdgen plan           # what checking would cost
```

If `stats` shows `demo=...` anywhere or `publish` warns about demo data, the
database is seeded with fake results. Run `python3 tools/seed_demo.py --clear`
before doing anything real, and tell the user you did.

### 2. Generate (free, offline)

```bash
python3 -m pdgen generate                                   # 2 and 3 syllables
python3 -m pdgen generate --pattern CVCVCVCV --sample 0.02  # 4 syllables
```

No network, no rate limits, a few seconds. Safe to run unprompted.

### 3. Estimate, then check

```bash
python3 -m pdgen plan --rdap-rps 2 --budget 30m --budget 4h
python3 -m pdgen check --stage rdap --top 2000 --rdap-rps 2
```

`check` always works highest-score-first, so a run that gets cut short still
spent itself on the best candidates. Ctrl-C saves cleanly; re-running resumes.

Use `--dry-run` to show an estimate without spending anything.

### 4. Refine

```bash
python3 -m pdgen dropping                        # names returning to the pool
python3 -m pdgen meaning --only-available --top 200
python3 -m pdgen alternates --tld net --tld org --top 100
python3 -m pdgen stale --older-than 21
```

### 5. Publish

```bash
python3 -m pdgen publish --min-confidence rdap   # writes docs/data/domains.json
python3 -m pdgen release push                    # db.json -> GitHub release
```

Commit `docs/data/domains.json`. Never commit `db.json` — it is gitignored and
belongs in a release.

## Reading results correctly

Confidence is tracked per name per TLD, and **it is not uniform**. Always state
which level a result came from.

| Level | Means | Say it like |
| --- | --- | --- |
| `generated` | Scored only, never checked | "not checked" |
| `dns` | Absent from the zone; misses registered-but-undelegated names | "a lead, not confirmed" |
| `rdap` | The registry itself answered | "registry-confirmed" |
| `registrar` | A registrar confirmed purchasability and price | "confirmed buyable" |

Two failure modes to avoid:

- Reporting a `dns`-level result as "available". It is a lead.
- Reporting a stale `rdap` result as current. Check `checked_at`; anything past
  ~21 days needs re-verification.

## Choosing what to run

| The user wants | Do this |
| --- | --- |
| A name for a project, no constraints | `generate`, then `plan`, then `check --tier S --top 500` |
| Short names specifically | `check --top N` filtered to 2 syllables; expect almost nothing free |
| Something brandable from a concept | Point them at the website's coin panel (see below) |
| To know if one domain is free | `check --name <name> --stage rdap --rdap-rps 1` |
| Expiring/dropping domains | `check --stage rdap` first, then `dropping` |
| A `.com` alternative | `alternates --tld net --tld org --tld co` |
| To refresh a stale dataset | `stale`, then `check --recheck-older-than 21` |
| To understand a score | `score <name>` — explains it phoneme by phoneme |

## Coining names from English words

This lives **only in the browser**, at the "Coin one from English" panel. There
is deliberately no CLI equivalent — coining is interactive and low-volume, and
results travel back via export.

It mimics how Japanese borrows English: force the word into open syllables,
insert /u/ (or /o/ after t and d), then clip the first two morae of each
element. `personal computer` → `pasokon`. `remote control` → `lemokon`.

If the user wants coined names in the database, the loop is:

1. They coin and star names on the site
2. **Export harvest** downloads a patch
3. `python3 -m pdgen merge <patch>.json` adds them with origins intact
4. `python3 -m pdgen bookmarks` lists them

## Long sweeps

Do not try to check a large space in one go, locally or in CI. A GitHub job is
killed at 6 hours and the kill is a failure. The right shape is bounded,
resumable slices:

```bash
python3 -m pdgen check --stage rdap --max-duration 50m --rdap-rps 2
```

The queue is score-ordered and stable, so re-running resumes exactly where the
last run stopped. `sweep.yml` does this on a schedule and chains itself until
the queue empties:

```bash
gh workflow run sweep.yml -f duration=5h -f rdap_rps=2
gh workflow run sweep.yml -f duration=5m -f rdap_rps=1 -f chain_remaining=3   # trial
```

Before trusting the chain, rehearse it offline — it takes five seconds and
needs no network:

```bash
python3 tools/rehearse_chain.py --slice 2s --slices 10 --names 4000 --latency 0.02
```

Never run two checks concurrently — it doubles the real request rate. The CLI
holds a lock file locally; CI uses a `concurrency` group.

## Things not to do

- Do not claim a name is available on `dns` evidence alone.
- Do not raise `--rdap-rps` above 8 to make a run finish faster.
- Do not commit `db.json`.
- Do not present the score as measured fact — it is a hypothesis about
  pronounceability that has never been tested on speakers.
- Do not skip the profanity screen when adding names by hand. Random CV strings
  spell unfortunate things in languages you do not speak.
- Do not tell the user a name is safe to buy. Availability is not a trademark
  clearance.

## Reference

- `references/commands.md` — every subcommand and flag
- `references/interpreting.md` — score model, tiers, what flags mean
- The repo's `HANDOFF.md` — verified vs unverified, and the known limitations
