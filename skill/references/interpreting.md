# Interpreting Sayable output

## Tiers

Score bands used for ordering everywhere — the check queue, the CLI tables, and
the site's badges.

| Tier | Score |
| --- | --- |
| S | 95+ |
| A | 92–95 |
| B | 89–92 |
| C | 85–89 |
| D | below 85 |

## The score

0–100. A weighted mean of per-phoneme cross-linguistic universality
(consonants 0.62, vowels 0.38, scaled to 90), plus:

| Adjustment | |
| --- | --- |
| vowel variety | up to +3 |
| consonant variety | up to +3 |
| short (5 letters) | +3 |
| long (>6 letters) | −1.5/letter, capped at −6 |
| repeated syllable | −10 |
| single vowel throughout | −6 |
| `l` and `r` together | −12 |
| a consonant 3+ times | −5 |
| trailing `-e`, or `c j q w x y` | −100 (effectively banned) |

Length is capped deliberately: it is a marketing preference, not a
pronunciation problem, so four-syllable names stay competitive.

**The model has never been validated against real speakers.** Present it as a
ranked hypothesis.

## The alphabet

Safe: `b d f k l m n p s t` + `a e i o u`.

Excluded and why: `th` is globally rare; `v` and `z` merge with `b`/`s` for
Spanish, Japanese and Korean speakers; `r` is a tap, trill, approximant or
uvular fricative depending on region; `h` is silent for French, Italian and
Spanish readers; `c j q w x y` are read differently by readers of different
orthographies; trailing `-e` is silenced by English readers.

Structure is strict CV alternation with two allowances: a coda `n` (universal,
and what every `-kon`/`-san` name depends on) and two-vowel sequences.

## Flags

| Flag | Meaning |
| --- | --- |
| `redemptionperiod`, `pendingdelete` | registered now, returning to the pool within weeks — the pearls |
| `clienthold`, `serverhold` | suspended, not necessarily dropping |
| `premium` | registry-priced above standard; `registrar` stage only |
| `price:N` | registrar's quoted price |
| `bookmarked` | starred on the website and merged back |

## Confidence, restated

Never round `dns` up to "available". A `.com` can be registered with no
nameservers, and DNS cannot see it. Only `rdap` and `registrar` are answers.

## Sources

| Source | Meaning |
| --- | --- |
| `zone` | local CZDS zone file |
| `doh` | DNS over HTTPS |
| `rdap:com` etc. | registry RDAP |
| `registrar:porkbun` | registrar API |
| `rdap:browser` | live check from the website, merged in |

## What "fully validated" means on the site

The hero shuffles only names where confidence is `rdap` or better **and** status
is available. Everything else is a lead.
