// @ts-check
/**
 * Scoring for names coined in the browser.
 *
 * Every number here comes from `scoring` in the published snapshot, which the
 * Python scorer emits. Only the arithmetic lives in this file, and
 * `verifyAgainstFixtures` re-derives a handful of known scores at load time so
 * that if the two ever drift apart you find out in the console rather than
 * shipping quietly wrong rankings.
 */

const VOWELS = new Set(["a", "e", "i", "o", "u"]);

/** Maximal runs of adjacent vowels: 'kulaudo' -> ['u','au','o'].
 *  @param {string} name @returns {string[]} */
export function vowelRuns(name) {
  const runs = [];
  let cur = "";
  for (const ch of name) {
    if (VOWELS.has(ch)) cur += ch;
    else if (cur) { runs.push(cur); cur = ""; }
  }
  if (cur) runs.push(cur);
  return runs;
}

/** A run of adjacent vowels is one nucleus: 'kulaudo' is ku-lau-do, three
 *  syllables, not four. @param {string} name @returns {number} */
export const syllables = (name) => vowelRuns(name).length;

/** Onset consonants + vowel run + optional coda n.
 *  @param {string} name @param {string} coda @returns {string[]} */
export function segment(name, coda = "n") {
  const out = [];
  let onset = "", i = 0;
  while (i < name.length) {
    if (!VOWELS.has(name[i])) { onset += name[i]; i += 1; continue; }
    let run = "";
    while (i < name.length && VOWELS.has(name[i])) { run += name[i]; i += 1; }
    let syl = onset + run;
    onset = "";
    if (name[i] === coda && !VOWELS.has(name[i + 1] || "")) { syl += coda; i += 1; }
    out.push(syl);
  }
  if (onset) { if (out.length) out[out.length - 1] += onset; else out.push(onset); }
  return out;
}

/** @typedef {{ipa: string, tier: string, w: number, note: string}} Phoneme */

/**
 * @param {string} name
 * @param {Record<string, Phoneme>} phonemes
 * @param {any} spec
 * @returns {{score: number, modifiers: [string, number][]}}
 */
export function score(name, phonemes, spec) {
  const weight = (c) => (phonemes[c] ? phonemes[c].w : 0.3);
  const cons = [...name].filter((c) => !VOWELS.has(c));
  const vows = [...name].filter((c) => VOWELS.has(c));
  if (!cons.length || !vows.length) return { score: 0, modifiers: [] };

  const runs = vowelRuns(name);
  const mean = (a) => a.reduce((s, c) => s + weight(c), 0) / a.length;
  const base = spec.base_scale *
    (spec.cons_weight * mean(cons) + spec.vowel_weight * mean(vows));

  /** @type {[string, number][]} */
  const mods = [];
  const add = (label, delta) => { if (delta) mods.push([label, Math.round(delta * 10) / 10]); };

  const variety = (arr) =>
    arr.length > 1 ? spec.variety_bonus * (new Set(arr).size - 1) / (arr.length - 1) : 0;
  add("vowel variety", variety(runs));
  add("consonant variety", variety(cons));

  const sylls = segment(name, spec.coda || "n");
  if (sylls.some((s, i) => i > 0 && s === sylls[i - 1])) {
    add("repeated syllable", spec.repeated_syllable);
  }
  if (new Set(runs).size === 1 && runs.length > 1) {
    add("single vowel throughout", spec.monovocalic);
  }

  // Vowel sequences: reward universal diphthongs, penalise hiatus that
  // different languages syllabify differently, reject triphthongs.
  for (const run of runs) {
    if (run.length === 1) continue;
    if (run.length > 2) { add(`three vowels in a row ('${run}')`, spec.triphthong); continue; }
    const q = (spec.vowel_runs || {})[run] ?? spec.vowel_run_default;
    add(`vowel pair '${run}'`, (q - 1) * spec.vowel_run_scale);
  }
  if (name.includes("l") && name.includes("r")) add("l + r in one name", spec.liquid_clash);

  for (const c of new Set(cons)) {
    const n = cons.filter((x) => x === c).length;
    if (n >= 3) add(`'${c}' repeated ${n}x`, spec.triple_consonant);
  }

  if (name.length === 5) {
    add("short (5 letters)", spec.short_bonus);
  } else if (name.length > spec.length_penalty_from) {
    add(`long (${name.length} letters)`, Math.max(
      spec.length_penalty_cap,
      spec.length_penalty_per_letter * (name.length - spec.length_penalty_from)));
  }

  if (spec.banned_final.includes(name[name.length - 1])) add("silent final -e risk", -100);
  for (const c of new Set(name)) {
    if (spec.banned_letters.includes(c)) add(`ambiguous letter '${c}'`, -100);
  }

  const total = Math.max(0, Math.min(100, base + mods.reduce((s, m) => s + m[1], 0)));
  return { score: Math.round(total * 10) / 10, modifiers: mods };
}

/**
 * Re-derive the published fixtures. Returns the mismatches, empty when the two
 * implementations agree.
 * @param {Record<string, Phoneme>} phonemes @param {any} spec
 */
export function verifyAgainstFixtures(phonemes, spec) {
  const bad = [];
  for (const [name, expected] of Object.entries(spec.fixtures || {})) {
    const got = score(name, phonemes, spec).score;
    if (Math.abs(got - Number(expected)) > 0.05) bad.push({ name, expected, got });
  }
  if (bad.length) {
    console.warn(
      "[sayable] browser scorer disagrees with the Python scorer — coined-name " +
      "rankings may be wrong. Re-run `pdgen publish` after changing phonetics.py.",
      bad);
  }
  return bad;
}

/** Substring screen against the blocklist shipped in the snapshot.
 * @param {string} name @param {string[]} terms @returns {string|null} */
export function blocked(name, terms) {
  for (const t of terms) if (name.includes(t)) return t;
  return null;
}
