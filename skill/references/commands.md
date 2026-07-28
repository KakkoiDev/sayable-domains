# pdgen command reference

All commands take `--db PATH` (default `db.json`) and `--help`.

## generate — pass 1, offline

| Flag | Default | |
| --- | --- | --- |
| `--pattern` | `CVCV CVCVC CVCVCV` | C/V pattern, repeatable |
| `--min-score` | `85` | discard below this |
| `--sample` | `1.0` | keep this fraction of the space |
| `--seed` | — | reproducible sampling |
| `--limit` | — | stop after N candidates |
| `--extended` | off | allow `g r h v z` (lowers universality) |
| `--blocklist` | — | extra blocklist file, repeatable |
| `--yes` | off | skip the large-space prompt |

`CVCVCVCV` is 6.25M combinations — always pair it with `--sample`.

## plan — estimate before spending

| Flag | Default |
| --- | --- |
| `--stage` | `rdap` |
| `--tld` | `com` |
| `--recheck-below` | `generated` |
| `--include-stale` / `--stale-after` | off / 21 |
| `--zone-file` | — |
| `--budget` | `15m 1h 4h 12h`, repeatable |
| `--dns-rps` / `--rdap-rps` / `--workers` | 40 / 8 / 24 |

Prints a per-tier table with time-to-clear, plus what each time budget reaches.
The DNS survival rate is measured from the database's own history.

## check — pass 2, highest score first

| Flag | Default | |
| --- | --- | --- |
| `--stage` | `rdap` | `zone` · `dns` · `rdap` · `registrar` |
| `--tld` | `com` | |
| `--top` | — | best N in the queue |
| `--tier` | — | `S A B C D`, repeatable |
| `--min-score` | `0` | |
| `--name` | — | one specific name, repeatable |
| `--recheck-below` | `generated` | re-check at or below this confidence |
| `--recheck-older-than` | — | days |
| `--zone-file` | — | ICANN CZDS file (`.txt` or `.gz`) |
| `--force` | off | allow downgrading a stronger result |
| `--checkpoint` | `250` | save every N results |
| `--dry-run` | off | estimate and stop |

`--stage registrar` needs `PORKBUN_API_KEY` and `PORKBUN_SECRET_KEY`.

## alternates — other TLDs for taken names

`--tld` (repeatable, default `net org co`), `--primary` (default `com`),
`--top` (200), `--min-score` (90), `--stage`.

Known TLDs: `net org co io app dev xyz ai me sh`. `.ai` and `.sh` have
unreliable RDAP and are warned about at runtime.

## meaning — cross-language lookup

`--source wiktionary|panlex` (repeatable, default wiktionary), `--top` (300),
`--min-score` (90), `--only-available`, `--recheck`, `--rps` (4), `--dry-run`.

Single-threaded on purpose — these are courtesy APIs run by nonprofits.
PanLex is experimental and swallows errors; a clean result may mean the call
failed. See HANDOFF.md.

## stale / dropping

```
pdgen stale --older-than 21     # freshness buckets + what to re-verify
pdgen dropping --top 40         # names in redemption or pending delete
```

## publish

`--out` (`docs/data/domains.json`), `--tld`, `--min-confidence` (`dns`),
`--min-score`, `--limit` (5000), `--include-taken`, `--no-dropping`.

Ships the rows plus the phoneme table, scoring constants, blocklist, and score
fixtures the browser needs. Commit the output.

## release

```
pdgen release push | pull | status   [--repo OWNER/NAME] [--tag db-latest]
```

Gzips `db.json` to a release asset. Needs the `gh` CLI. CI uses this — Actions
can fetch release assets, browsers cannot.

## merge / bookmarks

```
pdgen merge harvest.json     # adds coined names, applies checks, flags bookmarks
pdgen bookmarks              # list starred names with their origins
```

## stats / score / screen

```
pdgen stats --tld com
pdgen score midako tesabu    # phoneme-by-phoneme explanation
pdgen screen --remove --blocklist path/to/list
```
