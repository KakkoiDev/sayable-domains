"""Phoneme inventories, cross-linguistic universality weights, and scoring.

The weights below approximate how widely a sound appears across the world's
languages (PHOIBLE-style segment frequencies), adjusted for how reliably the
*letter* maps to that sound for readers of different orthographies.

The default alphabet is deliberately tiny. Every letter in it is read the same
way by speakers of English, Spanish, Portuguese, Italian, Indonesian, Swahili,
Turkish, Japanese romaji and Mandarin pinyin.
"""

from __future__ import annotations

# --- Consonants -------------------------------------------------------------
# value = universality score 0..1
CONSONANTS: dict[str, float] = {
    "m": 1.00,
    "n": 1.00,
    "k": 1.00,
    "t": 1.00,
    "s": 0.98,
    "b": 0.95,
    "p": 0.92,
    "d": 0.90,
    "l": 0.88,
    "f": 0.72,
}

# Available with --extended-alphabet. Each carries a real cost, listed in NOTES.
CONSONANTS_EXTENDED: dict[str, float] = {
    "g": 0.80,
    "r": 0.55,
    "h": 0.50,
    "v": 0.45,
    "z": 0.45,
}

# --- Vowels -----------------------------------------------------------------
VOWELS: dict[str, float] = {
    "a": 1.00,
    "i": 1.00,
    "u": 0.98,
    "o": 0.92,
    "e": 0.90,
}

# Letters that are never safe: they are read as different sounds depending on
# the reader's first language, so the name loses its identity in transit.
BANNED_LETTERS = set("cjqwxy")

# English readers silence a trailing -e (the "Nike problem").
BANNED_FINAL = set("e")

NOTES: dict[str, str] = {
    "m": "Present in essentially every spoken language.",
    "n": "Present in essentially every spoken language.",
    "k": "Near-universal voiceless stop.",
    "t": "Near-universal voiceless stop.",
    "s": "Near-universal; a handful of Pacific languages lack it.",
    "b": "Very widespread. Devoiced to /p/ by some Mandarin speakers.",
    "p": "Absent from Modern Standard Arabic; often realised as /b/.",
    "d": "Widespread. Merges toward /t/ for some Mandarin speakers.",
    "l": "Widespread, but conflated with /r/ by Japanese and Korean speakers.",
    "f": "Absent from Korean and many Pacific and Australian languages.",
    "g": "Unstable across Arabic dialects (/g/, /dʒ/, /ɡ/ vary by region).",
    "r": "Realised very differently by region: tap, trill, approximant, uvular.",
    "h": "Silent for French, Italian and most Spanish readers.",
    "v": "Merges with /b/ for Spanish and Korean speakers.",
    "z": "Absent from Spanish (Peninsular) and Mandarin; merges with /s/.",
    "a": "The most universal vowel. Stable across every major orthography.",
    "i": "One of the three quantal vowels; stable everywhere.",
    "u": "One of the three quantal vowels. French readers may say /y/.",
    "o": "Widespread, though quality drifts between /o/ and /ɔ/.",
    "e": "Widespread, though quality drifts between /e/ and /ɛ/.",
}

IPA: dict[str, str] = {
    "a": "a", "e": "e", "i": "i", "o": "o", "u": "u",
    "b": "b", "d": "d", "f": "f", "g": "ɡ", "h": "h",
    "k": "k", "l": "l", "m": "m", "n": "n", "p": "p",
    "r": "ɾ", "s": "s", "t": "t", "v": "v", "z": "z",
}


def tier(letter: str) -> str:
    """Coarse band used to colour the phoneme strip in the web UI."""
    w = weight(letter)
    if w >= 0.95:
        return "core"
    if w >= 0.85:
        return "solid"
    if w >= 0.70:
        return "caution"
    return "risky"


def weight(letter: str) -> float:
    if letter in VOWELS:
        return VOWELS[letter]
    if letter in CONSONANTS:
        return CONSONANTS[letter]
    return CONSONANTS_EXTENDED.get(letter, 0.30)


def is_vowel(letter: str) -> bool:
    return letter in VOWELS


def to_ipa(name: str) -> str:
    return "".join(IPA.get(ch, ch) for ch in name)


# Vowel sequences, weighted by how consistently speakers of different
# languages resolve them into a single nucleus.
#
# Falling diphthongs in /i/ and /u/ are near-universal. Rising sequences are
# common in Romance languages and read as glide-plus-vowel. The rest are
# hiatus: Spanish and Italian readers break them into two syllables, English
# readers often do not, so the name changes shape depending on who says it.
VOWEL_RUNS: dict[str, float] = {
    "ai": 1.00, "au": 1.00, "oi": 1.00,
    "ei": 0.95, "ou": 0.95,
    "ia": 0.88, "io": 0.88, "iu": 0.85, "ua": 0.88, "ue": 0.85, "ui": 0.85,
    "ie": 0.82, "uo": 0.80,
    "ae": 0.65, "ao": 0.65, "ea": 0.65, "eo": 0.62, "oa": 0.65, "oe": 0.62,
    "eu": 0.70,
}

# The only coda the safe alphabet permits. Universal, and what every -kon,
# -san, -min name depends on.
CODA = "n"


def vowel_runs(name: str) -> list[str]:
    """Maximal runs of adjacent vowels, e.g. 'kulaudo' -> ['u', 'au', 'o']."""
    runs, current = [], ""
    for ch in name:
        if is_vowel(ch):
            current += ch
        elif current:
            runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def syllables(name: str) -> int:
    """Count syllable nuclei.

    A run of adjacent vowels is one nucleus, not several: 'kulaudo' is
    ku-lau-do, three syllables, even though it contains four vowel letters.
    Counting letters instead of runs was silently inflating every syllable
    figure for any name containing a diphthong.
    """
    return len(vowel_runs(name))


def segment(name: str) -> list[str]:
    """Split into syllables: onset consonants + vowel run + optional coda n.

    Needed because the repeated-syllable penalty used to chop the name into
    fixed two-letter pairs, which is only correct for strict CV and gives
    nonsense for anything with a diphthong or a coda.
    """
    out: list[str] = []
    onset = ""
    i = 0
    while i < len(name):
        if not is_vowel(name[i]):
            onset += name[i]
            i += 1
            continue
        run = ""
        while i < len(name) and is_vowel(name[i]):
            run += name[i]
            i += 1
        syl = onset + run
        onset = ""
        # A following n closes this syllable rather than opening the next one,
        # unless a vowel follows it.
        if i < len(name) and name[i] == CODA and (
                i + 1 >= len(name) or not is_vowel(name[i + 1])):
            syl += CODA
            i += 1
        out.append(syl)
    if onset:
        if out:
            out[-1] += onset
        else:
            out.append(onset)
    return out


def score(name: str) -> tuple[float, dict]:
    """Return (score 0..100, breakdown).

    Breakdown is carried into the JSON db so the web UI can explain a rank
    instead of just asserting one.
    """
    cons = [c for c in name if not is_vowel(c)]
    vows = [v for v in name if is_vowel(v)]
    runs = vowel_runs(name)
    if not cons or not vows:
        return 0.0, {"base": 0.0, "modifiers": [], "flags": ["degenerate"]}

    c_mean = sum(weight(c) for c in cons) / len(cons)
    v_mean = sum(weight(v) for v in vows) / len(vows)
    # 90 rather than 100 so the bonuses below have somewhere to go: a perfect
    # 5-letter name lands near 99 and nothing clamps, which keeps the top of
    # the ranking meaningfully ordered.
    base = 90 * (0.62 * c_mean + 0.38 * v_mean)

    mods: list[dict] = []
    flags: list[str] = []

    def add(label: str, delta: float, flag: str | None = None) -> None:
        if delta:
            mods.append({"label": label, "delta": round(delta, 1)})
        if flag:
            flags.append(flag)

    # Reward variety: repeated sounds make a name mushy and hard to recall.
    # Measured over nuclei, so 'kaido' is not scored as if it had two separate
    # vowel slots where a listener hears one.
    if len(runs) > 1:
        add("vowel variety", 3 * (len(set(runs)) - 1) / (len(runs) - 1))
    if len(cons) > 1:
        add("consonant variety", 3 * (len(set(cons)) - 1) / (len(cons) - 1))

    # Adjacent identical syllables ("bebeko") read as babble. Uses real
    # segmentation rather than fixed letter pairs, so diphthongs and codas
    # do not throw the boundaries off.
    sylls = segment(name)
    if any(a == b for a, b in zip(sylls, sylls[1:])):
        add("repeated syllable", -10, "repeated-syllable")

    # Vowel sequences: reward the universal diphthongs, penalise hiatus that
    # different languages syllabify differently, and reject triphthongs.
    for run in vowel_runs(name):
        if len(run) == 1:
            continue
        if len(run) > 2:
            add(f"three vowels in a row ('{run}')", -25, "triphthong")
            continue
        quality = VOWEL_RUNS.get(run, 0.5)
        add(f"vowel pair '{run}'", (quality - 1.0) * 14,
            None if quality >= 0.8 else "awkward-hiatus")

    if len(set(runs)) == 1 and len(runs) > 1:
        add("single vowel throughout", -6, "monovocalic")

    if "l" in name and "r" in name:
        add("l + r in one name", -12, "liquid-clash")

    for c in set(cons):
        if cons.count(c) >= 3:
            add(f"'{c}' repeated {cons.count(c)}x", -5)

    # Length is a marketing preference, not a pronunciation problem, so the
    # penalty is gentle and capped. Four-syllable names stay competitive --
    # Mitsubishi and Panasonic are not hard to say. Filter by --pattern or the
    # syllable control on the site if you want short specifically.
    if len(name) == 5:
        add("short (5 letters)", 3)
    elif len(name) > 6:
        add(f"long ({len(name)} letters)", max(-6, -1.5 * (len(name) - 6)))

    if name[-1] in BANNED_FINAL:
        add("silent final -e risk", -100, "final-e")

    for ch in set(name):
        if ch in BANNED_LETTERS:
            add(f"ambiguous letter '{ch}'", -100, "ambiguous-letter")

    total = base + sum(m["delta"] for m in mods)
    total = max(0.0, min(100.0, total))
    return round(total, 1), {
        "base": round(base, 1),
        "modifiers": mods,
        "flags": flags,
    }


def phoneme_detail(name: str) -> list[dict]:
    """Per-letter data for the phoneme strip in the UI."""
    return [
        {
            "ch": ch,
            "ipa": IPA.get(ch, ch),
            "tier": tier(ch),
            "weight": round(weight(ch), 2),
            "note": NOTES.get(ch, ""),
        }
        for ch in name
    ]
