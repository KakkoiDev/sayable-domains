// @ts-check
/**
 * Coin names from English words the way Japanese coins loanwords.
 *
 *   cloud             -> kuraudo  (Japanese)  -> kulaudo (safe)
 *   personal computer -> pasokon               (contraction)
 *
 * This is the *only* implementation. There is deliberately no Python twin:
 * coining is an interactive, handful-at-a-time activity, and results travel
 * back to the CLI through the export/merge patch rather than through a second
 * copy of these rules.
 *
 * Three stages:
 *
 *  1. English spelling -> rough phonemes. English orthography is chaotic and
 *     this is a heuristic ruleset, not a pronunciation dictionary. Good enough
 *     to coin names, wrong often enough that you should read the output.
 *
 *  2. Phonemes -> Japanese morae. Japanese has no consonant clusters and no
 *     coda except /N/, so loanwords take epenthetic vowels: /u/ by default,
 *     /o/ after t and d (hando, sutoraiku, hitto). That default is what
 *     produces the -mu, -ru, -su, -tsu endings that make a word sound
 *     Japanese. We emit the Japanese-default form and, on request, one variant
 *     per vowel, because the "wrong" vowel is often the better name.
 *
 *  3. Morae -> the safe universal alphabet. Japanese output contains r, g, h,
 *     z, w and y, none of which are safe, so a second pass maps them to the
 *     nearest safe sound. Both forms are kept: the Japanese one is what a
 *     Japanese speaker would say, the safe one is what everyone can say.
 */

/** Ordered longest-first; applied greedily left to right.
 *  Single-symbol phonemes: C=ch S=sh T=th J=dʒ N=ng Y=/aɪ/ W=/aʊ/ E=/eɪ/ O=/ɔɪ/
 *  @type {[string, string][]} */
const GRAPHEMES = [
  ["tion", "Son"], ["sion", "Zon"], ["ough", "o"], ["augh", "o"],
  ["tch", "C"], ["sch", "sk"], ["igh", "Y"],
  ["ch", "C"], ["sh", "S"], ["th", "T"], ["ph", "f"], ["ck", "k"],
  ["ng", "N"], ["qu", "kw"], ["wh", "w"], ["gh", ""], ["kn", "n"],
  ["wr", "r"], ["dg", "J"],
  ["ee", "i"], ["ea", "i"], ["oo", "u"], ["ou", "W"], ["ow", "W"],
  ["oa", "o"], ["ai", "E"], ["ay", "E"], ["ei", "E"], ["ey", "E"],
  ["ie", "i"], ["oi", "O"], ["oy", "O"], ["au", "o"], ["aw", "o"],
  ["ue", "u"], ["ui", "u"], ["eu", "yu"], ["ew", "yu"],
  // English r-coloured vowels become plain long vowels in Japanese loanwords:
  // personal -> paasonaru (pa-so-...), not perusonaru. Getting this wrong is
  // the difference between "pasokon" and "pelukomu".
  ["ar", "a"], ["er", "a"], ["ir", "a"], ["or", "o"], ["ur", "a"],
  ["a", "a"], ["e", "e"], ["i", "i"], ["o", "o"], ["u", "u"], ["y", "i"],
  ["b", "b"], ["c", "k"], ["d", "d"], ["f", "f"], ["g", "g"], ["h", "h"],
  ["j", "J"], ["k", "k"], ["l", "l"], ["m", "m"], ["n", "n"], ["p", "p"],
  ["r", "r"], ["s", "s"], ["t", "t"], ["v", "v"], ["w", "w"], ["x", "ks"],
  ["z", "z"],
];

const DIPHTHONGS = { Y: "ai", W: "au", E: "ei", O: "oi" };
const SOFTEN = { c: "s", g: "J" };           // soften before e, i, y
const VOWELS = new Set(["a", "e", "i", "o", "u"]);

/** Consonants Japanese lacks, mapped to what it substitutes. */
const JA_CONSONANT = {
  l: "r", v: "b", T: "s", D: "z", C: "ch", J: "j", S: "sh", Z: "j", N: "n",
};

/** Japanese inserts /u/ to break a cluster or close a syllable, except after
 *  t and d which take /o/. This one rule is most of what makes a loanword
 *  sound Japanese. */
const JA_EPENTHESIS = { t: "o", d: "o", ch: "i", j: "i" };
const JA_EPENTHESIS_DEFAULT = "u";

/** Allophonic shifts inside the syllabary. */
const JA_SYLLABLE = {
  si: "shi", ti: "chi", tu: "tsu", di: "ji", du: "zu", hu: "fu", zi: "ji",
  wu: "u", wi: "ui", we: "ue", wo: "o", yi: "i", ye: "ie",
};

/** Japanese r is a tap, closest to l among safe consonants. g/z/v devoice or
 *  merge. h has no safe equivalent and becomes k. Glides are dropped because
 *  y and w are banned spellings. */
const SAFE_CONSONANT = {
  r: "l", g: "k", z: "s", v: "b", h: "k", j: "d",
  ch: "t", sh: "s", ts: "t", y: "", w: "b", N: "n",
};

const SAFE_ALPHABET = new Set("bdfklmnpst".split("").concat("aeiou".split("")));

/** A run of adjacent vowels is one nucleus. Counting vowel *letters* would
 *  report "kulaudo" as four syllables when it is ku-lau-do, three.
 *  @param {string} s @returns {number} */
function countSyllables(s) {
  let n = 0, prev = false;
  for (const c of s) { const v = VOWELS.has(c); if (v && !prev) n += 1; prev = v; }
  return n;
}

/* ------------------------------------------------------------------ */

/** @param {string} word @returns {string} */
export function toPhonemes(word) {
  let w = (word || "").toLowerCase().replace(/[^a-z]/g, "");
  if (!w) return "";
  // Drop a silent final e ("code" -> kod, not kode).
  if (w.length > 3 && w.endsWith("e") && !VOWELS.has(w[w.length - 2])) {
    w = w.slice(0, -1);
  }
  let out = "", i = 0;
  outer: while (i < w.length) {
    for (const [src, dst] of GRAPHEMES) {
      if (!w.startsWith(src, i)) continue;
      const soft = SOFTEN[src];
      out += soft && "eiy".includes(w[i + 1] || "") ? soft : dst;
      i += src.length;
      continue outer;
    }
    i += 1;
  }
  return out;
}

/** @param {string} c @param {string} v @returns {string} */
const syllable = (c, v) => JA_SYLLABLE[c + v] || c + v;

/**
 * Phonemes -> Japanese morae (each CV, V, or n).
 * @param {string} phon
 * @param {string|null} epenthetic force one vowel instead of the JA default
 * @returns {string[]}
 */
export function toMorae(phon, epenthetic = null) {
  const expanded = [...phon].map((c) => DIPHTHONGS[c] || c).join("");
  const units = [...expanded].map((c) => (VOWELS.has(c) ? c : JA_CONSONANT[c] || c));

  const morae = [];
  let i = 0;
  while (i < units.length) {
    const u = units[i];
    if (VOWELS.has(u)) { morae.push(u); i += 1; continue; }
    const next = units[i + 1];
    if (next && VOWELS.has(next)) { morae.push(syllable(u, next)); i += 2; continue; }
    // Nasal assimilation: a nasal before another consonant becomes the moraic
    // coda ん. This is why "computer" is konpyuutaa, not komupyuutaa, and so
    // why the clipping is pasokon rather than pasokomu.
    if (u === "n" || u === "m") { morae.push("n"); i += 1; continue; }
    morae.push(syllable(u, epenthetic || JA_EPENTHESIS[u] || JA_EPENTHESIS_DEFAULT));
    i += 1;
  }
  return morae;
}

/**
 * Japanese romaji -> the safe alphabet, or null if it cannot be rescued.
 * @param {string} text @returns {string|null}
 */
export function toSafe(text) {
  let out = "", i = 0;
  while (i < text.length) {
    const two = text.slice(i, i + 2);
    if (SAFE_CONSONANT[two] !== undefined) { out += SAFE_CONSONANT[two]; i += 2; continue; }
    const c = text[i];
    out += VOWELS.has(c) ? c : (SAFE_CONSONANT[c] !== undefined ? SAFE_CONSONANT[c] : c);
    i += 1;
  }
  // Collapse doubles and the vowel hiatus left behind by dropped glides.
  let s = "";
  for (const c of out) if (s[s.length - 1] !== c) s += c;
  if (!s) return null;
  // A trailing -e is silenced by English readers; shift it to -a.
  if (s.endsWith("e")) s = s.slice(0, -1) + "a";
  if (VOWELS.has(s[0])) return null;               // keep names consonant-initial
  for (const c of s) if (!SAFE_ALPHABET.has(c)) return null;
  return isSayable(s) ? s : null;
}

/**
 * Universal phonotactics, slightly looser than strict CV.
 *
 * Open syllables are the safe default, but two things are near-universal and
 * excluding them throws away the best names: a coda /n/ (which Japanese itself
 * permits, and which every "-kon", "-san", "-min" name depends on) and a
 * two-vowel sequence (Italian aula, Spanish auto, Japanese kau). Three vowels
 * in a row, or any other consonant cluster, is where speakers start to differ.
 *
 * @param {string} s @returns {boolean}
 */
export function isSayable(s) {
  if (!s) return false;
  let vowelRun = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s[i], prev = s[i - 1];
    if (c === prev) return false;                  // no doubled letters
    if (VOWELS.has(c)) {
      if (++vowelRun > 2) return false;            // no triphthongs
      continue;
    }
    vowelRun = 0;
    // A consonant may follow another consonant only when that one is n.
    if (prev !== undefined && !VOWELS.has(prev) && prev !== "n") return false;
  }
  const last = s[s.length - 1];
  return VOWELS.has(last) || last === "n";         // codas: vowels, or n
}

/**
 * Japanese-style clippings. The productive pattern is the first two morae of
 * each element: paso(naru)+kon(pyuutaa) -> pasokon. For a single word we also
 * take straight truncations, which is how sumaho and terebi are formed.
 * @param {string[][]} wordMorae
 * @param {number} maxSyllables
 */
export function contractions(wordMorae, maxSyllables = 4) {
  const out = [];
  const add = (morae, kind) => {
    if (!morae.length) return;
    const form = morae.join("");
    const syl = countSyllables(form);
    if (syl >= 2 && syl <= maxSyllables) out.push({ form, kind });
  };
  if (wordMorae.length > 1) {
    add(wordMorae.flatMap((w) => w.slice(0, 2)), "clip-2");
    add(wordMorae.flatMap((w) => w.slice(0, 1)), "clip-1");
    add(wordMorae[0].slice(0, 2).concat(wordMorae.slice(1).flatMap((w) => w.slice(0, 2))), "clip-2-2");
  } else {
    const full = wordMorae[0] || [];
    for (const n of [2, 3, 4]) add(full.slice(0, n), `trunc-${n}`);
  }
  add(wordMorae.flat(), "full");
  return out;
}

/**
 * Turn English words into ranked, safe-alphabet candidates.
 *
 * `pronunciations` optionally supplies real CMUdict phonemes per word. Where a
 * word is present the dictionary wins; otherwise the spelling heuristic runs.
 *
 * The two disagree in an interesting way. CMUdict is phonetically accurate:
 * "personal" is /pasinal/, with a schwa in the middle. But Japanese renders
 * unstressed English vowels from the *spelling*, which is why パーソナル is
 * pa-so-na-ru and the clipping is pasokon rather than pasikan. So the
 * dictionary gives better novel names and the spelling route gives more
 * Japanese-faithful ones. Both are offered; neither is wrong.
 * Screening and scoring are injected so this file stays pure: the caller
 * supplies the same blocklist and scorer the rest of the site uses.
 *
 * @param {string[]} words
 * @param {{allVowels?: boolean, maxSyllables?: number,
 *          pronunciations?: Record<string,string>|null,
 *          isBlocked?: (s: string) => string|null,
 *          score?: (s: string) => number}} [opts]
 */
export function coin(words, opts = {}) {
  const {
    allVowels = true, maxSyllables = 4, pronunciations = null,
    isBlocked = () => null, score = () => 0,
  } = opts;
  const phonemesFor = (w) =>
    (pronunciations && pronunciations[w.toLowerCase()]) || toPhonemes(w);

  const clean = words.map((w) => w.trim()).filter(Boolean);
  if (!clean.length) return [];

  const vowelOptions = allVowels ? [null, "a", "e", "i", "o", "u"] : [null];
  /** @type {Map<string, any>} */
  const seen = new Map();

  for (const epenthetic of vowelOptions) {
    const perWord = clean.map((w) => toMorae(phonemesFor(w), epenthetic));
    if (!perWord.some((m) => m.length)) continue;

    for (const variant of contractions(perWord, maxSyllables)) {
      const safe = toSafe(variant.form);
      if (!safe || safe.length < 4) continue;
      if (isBlocked(safe)) continue;
      const sc = score(safe);
      if (sc <= 0) continue;

      const prev = seen.get(safe);
      if (prev) {
        prev.kinds = [...new Set([...prev.kinds, variant.kind])].sort();
        continue;
      }
      seen.set(safe, {
        name: safe,
        japanese: variant.form,
        viaDictionary: Boolean(pronunciations && clean.some(
          (w) => pronunciations[w.toLowerCase()])),
        score: sc,
        syllables: countSyllables(safe),
        length: safe.length,
        kinds: [variant.kind],
        epenthetic: epenthetic || "japanese default",
        origin: clean,
      });
    }
  }

  // Rank shortest first, then most pronounceable. Short and sayable beats
  // long and marginally more sayable.
  return [...seen.values()].sort(
    (a, b) => a.length - b.length || b.score - a.score || a.name.localeCompare(b.name));
}
