// @ts-check
/**
 * Local persistence: bookmarks, the live-check cache, and export.
 *
 * Everything the browser learns is worth keeping. A live registry check costs
 * a request and goes stale in weeks, so results are cached with a timestamp
 * and reused until they age out. Bookmarks and cached checks both export as a
 * patch that `pdgen merge` folds into the local database, which is what turns
 * an afternoon of clicking around into harvested data.
 *
 * localStorage is unavailable in some embedded contexts, so every accessor
 * falls back to memory rather than letting the page die.
 */

const KEYS = {
  checks: "sayable.checks.v3",
  bookmarks: "sayable.bookmarks.v3",
};

const CACHE_TTL_DAYS = 21;

/** @type {Record<string, any>} */
const memory = {};

/** @param {string} key @returns {Record<string, any>} */
function read(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : (memory[key] || {});
  } catch {
    return memory[key] || {};
  }
}

/** @type {((msg: string) => void)|null} */
let quotaHandler = null;
/** @param {(msg: string) => void} fn */
export function onStorageProblem(fn) { quotaHandler = fn; }

/**
 * Writing used to swallow every failure, which meant a full localStorage quota
 * lost the harvest without saying anything. Now we evict the oldest cached
 * checks and retry, and if it still will not fit we tell the user to export.
 * @param {string} key @param {Record<string, any>} value
 */
function write(key, value) {
  memory[key] = value;
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return;
  } catch (e) {
    const quota = e instanceof DOMException &&
      (e.name === "QuotaExceededError" || e.code === 22);
    if (!quota) return;
  }
  // Drop the oldest half of the check cache. Bookmarks are never evicted --
  // they are deliberate choices, and losing one is worse than a re-lookup.
  try {
    const all = JSON.parse(localStorage.getItem(KEYS.checks) || "{}");
    const entries = Object.entries(all)
      .sort((a, b) => Date.parse(a[1].checked_at || 0) - Date.parse(b[1].checked_at || 0));
    const keep = Object.fromEntries(entries.slice(Math.floor(entries.length / 2)));
    localStorage.setItem(KEYS.checks, JSON.stringify(keep));
    localStorage.setItem(key, JSON.stringify(value));
    if (quotaHandler) {
      quotaHandler(`Local storage was full, so the oldest ${entries.length - Object.keys(keep).length}
        cached checks were dropped. Bookmarks were kept. Export your harvest to be safe.`);
    }
  } catch {
    if (quotaHandler) {
      quotaHandler(`Local storage is full and could not be cleared. Nothing new is
        being saved — export your harvest now, then clear site data.`);
    }
  }
}

export const ageDays = (iso) => {
  const t = Date.parse(iso || "");
  return isNaN(t) ? Infinity : (Date.now() - t) / 86400000;
};

/* --- live-check cache --------------------------------------------------- */

export const checks = {
  all: () => read(KEYS.checks),
  /** @param {string} domain */
  get(domain) {
    const hit = read(KEYS.checks)[domain];
    if (!hit) return null;
    return ageDays(hit.checked_at) > CACHE_TTL_DAYS ? null : hit;
  },
  /** @param {string} domain @param {{status: string, flags: string[]}} result */
  put(domain, result) {
    const all = read(KEYS.checks);
    all[domain] = {
      status: result.status,
      confidence: "rdap",
      source: "rdap:browser",
      flags: result.flags || [],
      checked_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    };
    write(KEYS.checks, all);
    return all[domain];
  },
  count: () => Object.keys(read(KEYS.checks)).length,
  clear: () => write(KEYS.checks, {}),
  /** Stale entries were ignored on read but never removed, so the cache grew
   *  without bound. Called once at boot. @returns {number} evicted */
  prune() {
    const all = read(KEYS.checks);
    let n = 0;
    for (const [k, v] of Object.entries(all)) {
      if (ageDays(v.checked_at) > CACHE_TTL_DAYS) { delete all[k]; n += 1; }
    }
    if (n) write(KEYS.checks, all);
    return n;
  },
};

/* --- bookmarks ----------------------------------------------------------- */

export const bookmarks = {
  all: () => read(KEYS.bookmarks),
  has: (domain) => Boolean(read(KEYS.bookmarks)[domain]),
  list() {
    return Object.entries(read(KEYS.bookmarks))
      .map(([domain, v]) => ({ domain, ...v }))
      .sort((a, b) => (b.score || 0) - (a.score || 0));
  },
  /** @param {string} domain @param {object} meta */
  toggle(domain, meta = {}) {
    const all = read(KEYS.bookmarks);
    if (all[domain]) {
      delete all[domain];
      write(KEYS.bookmarks, all);
      return false;
    }
    all[domain] = { ...meta, saved_at: new Date().toISOString() };
    write(KEYS.bookmarks, all);
    return true;
  },
  count: () => Object.keys(read(KEYS.bookmarks)).length,
  clear: () => write(KEYS.bookmarks, {}),
};

/* --- export -------------------------------------------------------------- */

/**
 * A patch `pdgen merge` understands. Bookmarked names that were coined in the
 * browser are included under `coined` so the CLI can add them to the database
 * — otherwise a merge would drop them for being unknown names.
 */
export function buildPatch() {
  const coined = {};
  for (const [domain, meta] of Object.entries(bookmarks.all())) {
    if (!meta || !meta.coined) continue;
    const name = domain.split(".")[0];
    coined[name] = {
      score: meta.score,
      origin: meta.origin || [],
      japanese: meta.japanese || null,
    };
  }
  return {
    schema: 2,
    kind: "sayable-verification-patch",
    exported_at: new Date().toISOString(),
    checked: checks.all(),
    bookmarks: bookmarks.all(),
    coined,
  };
}

/** @param {object} payload @param {string} filename */
export function download(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function exportPatch() {
  download(buildPatch(), `sayable-harvest-${new Date().toISOString().slice(0, 10)}.json`);
}

export function exportBookmarksCsv() {
  const rows = [["domain", "score", "syllables", "status", "origin", "japanese", "saved_at"]];
  for (const b of bookmarks.list()) {
    rows.push([
      b.domain, b.score ?? "", b.syllables ?? "",
      (checks.get(b.domain) || {}).status || b.status || "unknown",
      (b.origin || []).join(" "), b.japanese || "", b.saved_at || "",
    ]);
  }
  const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `sayable-bookmarks-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
