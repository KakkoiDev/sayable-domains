// @ts-check
/**
 * On-demand pronunciation lookup.
 *
 * CMUdict is 3.6 MB, so it is sharded by the first two letters of each word.
 * Typing "cloud native" fetches cl.json and na.json — under a kilobyte each in
 * the median case — instead of the whole dictionary. Shards are cached in
 * memory for the session.
 *
 * The shards are served from docs/, not from a GitHub release: release assets
 * cannot be fetched cross-origin. CI builds them with `pdgen dictionary build`.
 *
 * If the shards are absent the whole module degrades to null and the caller
 * falls back to the spelling heuristic, so the site works either way.
 */

const BASE = "data/cmudict";

/** @type {Map<string, Promise<Record<string,string>|null>>} */
const shards = new Map();
/** @type {{words: number, shards: string[]}|null} */
let manifest = null;
let manifestTried = false;

/** @returns {Promise<{words: number, shards: string[]}|null>} */
export async function load() {
  if (manifestTried) return manifest;
  manifestTried = true;
  try {
    const res = await fetch(`${BASE}/index.json`, { cache: "force-cache" });
    manifest = res.ok ? await res.json() : null;
  } catch {
    manifest = null;
  }
  return manifest;
}

export const available = () => manifest !== null;

/** @param {string} word */
function keyFor(word) {
  const w = word.toLowerCase().replace(/[^a-z]/g, "");
  return w.length >= 2 ? w.slice(0, 2) : (w || "_");
}

/** @param {string} key @returns {Promise<Record<string,string>|null>} */
function shard(key) {
  if (!shards.has(key)) {
    shards.set(key, fetch(`${BASE}/${key}.json`, { cache: "force-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null));
  }
  return shards.get(key);
}

/**
 * Phoneme string for one word, or null when the dictionary has no entry.
 * @param {string} word @returns {Promise<string|null>}
 */
export async function lookup(word) {
  if (!(await load())) return null;
  const w = word.toLowerCase().replace(/[^a-z]/g, "");
  if (!w) return null;
  const data = await shard(keyFor(w));
  return (data && data[w]) || null;
}

/**
 * Look several words up at once, returning a plain map so callers can stay
 * synchronous afterwards.
 * @param {string[]} words @returns {Promise<Record<string,string>>}
 */
export async function lookupAll(words) {
  const pairs = await Promise.all(
    words.map(async (w) => [w.toLowerCase(), await lookup(w)]));
  /** @type {Record<string,string>} */
  const out = {};
  for (const [w, p] of pairs) if (p) out[String(w)] = String(p);
  return out;
}
