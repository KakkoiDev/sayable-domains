// @ts-check
/* Sayable — front page logic. ES modules, no build step; served straight off
   GitHub Pages.

   Three things happen here. The hero shuffles through names that are fully
   validated, so the first thing you see is something you could actually buy.
   The coin panel turns English words into Japanese-style candidates and checks
   them live. The catalogue below is the full ranked snapshot.

   Everything the browser learns — live checks, bookmarks — is cached locally
   and exports as a patch the CLI can merge. */

import { coin } from "./nativize.js";
import { score as scoreName, verifyAgainstFixtures, blocked } from "./score.js";
import { checks, bookmarks, exportPatch, exportBookmarksCsv, ageDays, onStorageProblem } from "./store.js";
import * as dict from "./dict.js";

const DATA_URL = "data/domains.json";
const STALE_DAYS = 21;
const PAGE = 80;
const LIVE_CONCURRENCY = 3;
const LIVE_MIN_INTERVAL = 220;
const LIVE_CAP = 400;
const SHUFFLE_COUNT = 6;

const F = {
  NAME: 0, SCORE: 1, TIER: 2, SYL: 3, LEN: 4, CONF: 5,
  STATUS: 6, CHECKED: 7, FLAGS: 8, MODS: 9, MEANINGS: 10, ALTS: 11,
};

/* Typed DOM accessors. getElementById returns HTMLElement, which has no
   .value or .disabled, so narrow at the boundary instead of casting to any
   at every call site -- that way tsc still catches genuine misuse. */
/** @param {string} id @returns {HTMLElement} */
const el = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
/** @param {string} id @returns {HTMLInputElement} */
const input = (id) => /** @type {HTMLInputElement} */ (document.getElementById(id));
/** @param {string} id @returns {HTMLButtonElement} */
const btn = (id) => /** @type {HTMLButtonElement} */ (document.getElementById(id));
/** Nearest matching ancestor of the event target, typed so `.dataset` works.
 *  @param {Event} e @param {string} sel @returns {HTMLElement|null} */
const closest = (e, sel) => {
  const t = /** @type {Element|null} */ (e.target);
  return t ? /** @type {HTMLElement|null} */ (t.closest(sel)) : null;
};

/** An error the browser refused to send at all, rather than one the registry
 *  answered. Distinguishing them is what lets us tell the user it is CORS.
 *  @extends Error */
class BlockedError extends Error {
  constructor() { super("blocked"); this.cors = true; }
}
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  data: null, phonemes: {}, scoring: null, blocklist: [],
  confLevels: ["generated", "dns", "rdap", "registrar"],
  statuses: ["unknown", "available", "taken"],
  droppingFlags: ["redemptionperiod", "pendingdelete"],
  rows: [], view: [], rendered: 0,
  query: "", syllables: new Set(), lengths: new Set(), tiers: new Set(),
  minConf: 0, show: { dropping: false, meaning: false, bookmarked: false },
  sort: "score", coined: [], cancelLive: false, pronunciations: null,
  // Bookmarked coinages, rebuilt from localStorage, in snapshot row shape.
  // They are not in `rows`, which stays exactly what the snapshot published.
  saved: [],
};

/** Everything the catalogue can show: the snapshot plus your saved coinages. */
const allRows = () => (state.saved.length ? state.rows.concat(state.saved) : state.rows);

const fmtAge = (iso) => {
  const d = ageDays(iso);
  if (d === Infinity) return "never";
  if (d < 1) return "today";
  if (d < 2) return "yesterday";
  if (d < 45) return `${Math.floor(d)}d ago`;
  return `${Math.floor(d / 30)}mo ago`;
};

const isVowel = (ch) => "aeiou".includes(ch);
const domainOf = (name) => `${name}.${state.data.tld}`;
const isDropping = (row) => (row[F.FLAGS] || []).some((f) => state.droppingFlags.includes(f));
const hasMeaning = (row) => (row[F.MEANINGS] || []).length > 0;

function stripHTML(name) {
  return [...name].map((ch) => {
    const p = state.phonemes[ch] || { tier: "risky", ipa: ch };
    return `<span class="ph${isVowel(ch) ? " is-vowel" : ""}" data-tier="${p.tier}" title="/${p.ipa}/">${ch}</span>`;
  }).join("");
}

/** A cached live check always supersedes the snapshot. */
function effective(row) {
  const live = checks.get(domainOf(row[F.NAME]));
  if (!live) return { conf: row[F.CONF], status: row[F.STATUS], checked: row[F.CHECKED], live: false };
  return {
    conf: state.confLevels.indexOf(live.confidence),
    status: state.statuses.indexOf(live.status),
    checked: live.checked_at, live: true,
  };
}

/** Fully validated means the registry itself confirmed it, and it was free. */
const fullyValidated = (row) => {
  const e = effective(row);
  return e.conf >= 2 && e.status === 1;
};

/* --- hero shuffle ------------------------------------------------------- */

function shuffle() {
  const pool = state.rows.filter(fullyValidated);
  const box = el("shuffle");
  if (!pool.length) {
    box.innerHTML = `<p class="shuffle-empty">Nothing in this snapshot is registry-confirmed yet.
      Run <code>pdgen check --stage rdap</code> and publish again.</p>`;
    btn("btn-shuffle").disabled = true;
    return;
  }
  const picks = [];
  const taken = new Set();
  while (picks.length < Math.min(SHUFFLE_COUNT, pool.length)) {
    const i = Math.floor(Math.random() * pool.length);
    if (taken.has(i)) continue;
    taken.add(i);
    picks.push(pool[i]);
  }
  box.innerHTML = picks.map((r) => {
    const d = domainOf(r[F.NAME]);
    return `<button class="pick" data-name="${r[F.NAME]}">
      <span class="pick-name">${r[F.NAME]}<span class="tld">.${state.data.tld}</span></span>
      <span class="pick-strip">${stripHTML(r[F.NAME])}</span>
      <span class="pick-meta">${r[F.SCORE].toFixed(1)} · tier ${r[F.TIER]} · ${r[F.SYL]} syl</span>
      <span class="star${bookmarks.has(d) ? " is-on" : ""}" data-bookmark="${r[F.NAME]}"
            role="button" tabindex="0" aria-label="Bookmark ${d}">&#9733;</span>
    </button>`;
  }).join("");
  el("shuffle-note").textContent =
    `${pool.length.toLocaleString()} names are registry-confirmed available. Showing ${picks.length}.`;
}

/* --- coining ------------------------------------------------------------ */

async function runCoin() {
  const raw = input("coin-input").value.trim();
  if (!raw) { state.coined = []; renderCoined(); return; }
  const words = raw.split(/[\s,\-_/]+/).filter(Boolean).slice(0, 6);

  // Dictionary lookups are one small shard per word, so this is fast, but it
  // is still a fetch -- only do it when the user asked for that mode.
  const useDict = el("coin-dict").classList.contains("is-on");
  state.pronunciations = useDict ? await dict.lookupAll(words) : null;

  state.coined = coin(words, {
    pronunciations: state.pronunciations,
    allVowels: el("coin-allvowels").classList.contains("is-on"),
    maxSyllables: 4,
    isBlocked: (s) => blocked(s, state.blocklist),
    score: (s) => scoreName(s, state.phonemes, state.scoring).score,
  });
  renderCoined();
}

function renderCoined() {
  const box = el("coin-results");
  const meta = el("coin-meta");
  if (!state.coined.length) {
    box.innerHTML = "";
    meta.textContent = input("coin-input").value.trim()
      ? "Nothing survived the safe alphabet and the obscenity filter. Try different words."
      : "";
    btn("btn-coincheck").disabled = true;
    return;
  }
  const viaDict = state.coined.filter((c) => c.viaDictionary).length;
  meta.textContent = `${state.coined.length} candidates, shortest first. ` +
    (viaDict ? "Real pronunciations from CMUdict. " : "Pronunciation guessed from spelling. ") +
    `Nothing here has been checked yet.`;
  btn("btn-coincheck").disabled = false;

  box.innerHTML = state.coined.map((c) => {
    const d = domainOf(c.name);
    const cached = checks.get(d);
    const verdict = cached
      ? `<span class="coin-verdict ${cached.status === "available" ? "free" : "gone"}">${cached.status}</span>`
      : `<button class="rowaction" data-check="${c.name}">Check</button>`;
    return `<div class="coin-row" data-name="${c.name}">
      <span class="name">${c.name}<span class="tld">.${state.data.tld}</span></span>
      <span class="strip">${stripHTML(c.name)}</span>
      <span class="coin-ja" title="Japanese form before the safe-alphabet pass">${esc(c.japanese)}</span>
      <span class="coin-kind" title="${esc(c.kinds.join(', '))} · epenthetic vowel: ${esc(c.epenthetic)}">${esc(c.kinds[0])}</span>
      <span class="score">${c.score.toFixed(1)}</span>
      ${verdict}
      <span class="star${bookmarks.has(d) ? " is-on" : ""}" data-bookmark="${c.name}"
            role="button" tabindex="0" aria-label="Bookmark ${d}">&#9733;</span>
    </div>`;
  }).join("");
}

/* --- saved coinages ------------------------------------------------------ */

/**
 * A coined name has no snapshot row, so bookmarking one used to put it
 * somewhere the UI could not show it: the harvest counter said "1 bookmarked"
 * while the Bookmarked filter said "0 of 115 names", and after a reload the
 * name existed only in localStorage and the export files. Rebuilding a row for
 * each saved coinage puts it back in the catalogue, where it was asked for.
 *
 * The row is synthesised, not published, so it is flagged `coined` and tagged
 * in the list. Confidence starts at `generated` and status at `unknown`;
 * `effective()` will lift both if a live check was cached against the domain.
 * @param {string} domain @param {Record<string, any>} meta
 */
function savedRow(domain, meta) {
  const name = domain.split(".")[0];
  const score = typeof meta.score === "number"
    ? meta.score
    : scoreName(name, state.phonemes, state.scoring).score;
  const floors = state.data.tier_floors || {};
  const tier = Object.keys(floors)
    .sort((a, b) => floors[b] - floors[a])
    .find((t) => score >= floors[t]) || "D";
  const row = [];
  row[F.NAME] = name;
  row[F.SCORE] = score;
  row[F.TIER] = tier;
  row[F.SYL] = meta.syllables ?? [...name].filter(isVowel).length;
  row[F.LEN] = name.length;
  row[F.CONF] = 0;
  row[F.STATUS] = 0;
  row[F.CHECKED] = "";
  row[F.FLAGS] = ["coined"];
  row[F.MODS] = scoreName(name, state.phonemes, state.scoring).modifiers;
  row[F.MEANINGS] = [];
  row[F.ALTS] = {};
  return row;
}

/** Rebuild `state.saved` from storage. Cheap, and the source of truth is
 *  localStorage, so this can safely run after any bookmark change. */
function rebuildSaved() {
  if (!state.scoring) { state.saved = []; return; }
  const known = new Set(state.rows.map((r) => r[F.NAME]));
  state.saved = Object.entries(bookmarks.all())
    .filter(([domain, meta]) => meta && meta.coined && !known.has(domain.split(".")[0]))
    .map(([domain, meta]) => savedRow(domain, meta))
    .sort((a, b) => b[F.SCORE] - a[F.SCORE]);
}

const isCoined = (row) => (row[F.FLAGS] || []).includes("coined");

/* --- catalogue ---------------------------------------------------------- */

function matcher(q) {
  q = q.trim().toLowerCase().replace(/\.\w+$/, "");
  if (!q) return () => true;
  if (q.startsWith("^")) { const s = q.slice(1); return (n) => n.startsWith(s); }
  if (q.endsWith("$")) { const s = q.slice(0, -1); return (n) => n.endsWith(s); }
  return (n) => n.includes(q);
}

function applyFilters() {
  const match = matcher(state.query);
  state.view = allRows().filter((r) => {
    if (!match(r[F.NAME])) return false;
    if (state.syllables.size && !state.syllables.has(r[F.SYL])) return false;
    if (state.lengths.size && !state.lengths.has(r[F.LEN])) return false;
    if (state.tiers.size && !state.tiers.has(r[F.TIER])) return false;
    if (effective(r).conf < state.minConf) return false;
    if (state.show.dropping && !isDropping(r)) return false;
    if (state.show.meaning && !hasMeaning(r)) return false;
    if (state.show.bookmarked && !bookmarks.has(domainOf(r[F.NAME]))) return false;
    return true;
  });

  const s = state.sort;
  state.view.sort((a, b) => {
    if (s === "alpha") return a[F.NAME].localeCompare(b[F.NAME]);
    if (s === "length") return a[F.LEN] - b[F.LEN] || b[F.SCORE] - a[F.SCORE];
    if (s === "checked") return ageDays(effective(a).checked) - ageDays(effective(b).checked);
    return b[F.SCORE] - a[F.SCORE] || a[F.NAME].localeCompare(b[F.NAME]);
  });

  state.rendered = 0;
  el("rows").innerHTML = "";
  renderMore();
  updateCounts();
}

function rowHTML(row, rank) {
  const name = row[F.NAME];
  const d = domainOf(name);
  const e = effective(row);
  const stale = ageDays(e.checked) > STALE_DAYS;
  const dropping = isDropping(row);

  const steps = state.confLevels.slice(1).map((_, i) => {
    const on = e.conf >= i + 1;
    return `<span class="step${on ? (e.live ? " is-live" : " is-on") : ""}"></span>`;
  }).join("");

  const tags = [];
  if (isCoined(row)) {
    tags.push(`<span class="tag tag-coined" title="You coined this in the browser. It is not part of the published snapshot and has never been checked unless you checked it.">coined</span>`);
  }
  if (dropping) tags.push(`<span class="tag tag-drop">dropping</span>`);
  if (hasMeaning(row)) tags.push(`<span class="tag tag-mean" title="${esc(row[F.MEANINGS].join(', '))}">word</span>`);
  if ((row[F.FLAGS] || []).includes("premium")) tags.push(`<span class="tag">premium</span>`);
  const alts = Object.entries(row[F.ALTS] || {}).filter(([, st]) => st === 1).map(([t]) => `.${t}`);
  if (alts.length) tags.push(`<span class="tag tag-alt">${alts.join(" ")} free</span>`);

  return `<div class="row${dropping ? " is-dropping" : ""}" role="listitem" tabindex="0" data-name="${name}">
    <span class="rank">${String(rank).padStart(3, "0")}</span>
    <span class="name"><span class="tierbadge" data-tier="${row[F.TIER]}">${row[F.TIER]}</span>${name}<span class="tld">.${state.data.tld}</span>${tags.join("")}</span>
    <span class="strip">${stripHTML(name)}</span>
    <span class="score">${row[F.SCORE].toFixed(1)}</span>
    <span class="verified">
      <span class="steps" title="verified to: ${state.confLevels[e.conf]}">${steps}</span>
      <span class="verified-text${stale ? " stale" : ""}">${fmtAge(e.checked)}</span>
    </span>
    <span class="rowend">
      <button class="rowaction" data-check="${name}">Check</button>
      <span class="star${bookmarks.has(d) ? " is-on" : ""}" data-bookmark="${name}"
            role="button" tabindex="0" aria-label="Bookmark ${d}">&#9733;</span>
    </span>
  </div>`;
}

function refreshRow(name) {
  const node = document.querySelector(`.row[data-name="${name}"]`);
  if (node) {
    const row = allRows().find((r) => r[F.NAME] === name);
    if (!row) return;
    const rank = Number(node.querySelector(".rank").textContent);
    const tmp = document.createElement("div");
    tmp.innerHTML = rowHTML(row, rank);
    node.replaceWith(tmp.firstElementChild);
  }
  if (state.coined.some((c) => c.name === name)) renderCoined();
}

function renderMore() {
  const slice = state.view.slice(state.rendered, state.rendered + PAGE);
  if (!slice.length) return;
  el("rows").insertAdjacentHTML("beforeend",
    slice.map((r, i) => rowHTML(r, state.rendered + i + 1)).join(""));
  state.rendered += slice.length;
}

function updateCounts() {
  const n = state.view.length;
  const total = allRows().length;
  const drop = state.view.filter(isDropping).length;
  const coined = state.view.filter(isCoined).length;
  let text = n === total ? `${total.toLocaleString()} names`
    : `${n.toLocaleString()} of ${total.toLocaleString()} names`;
  if (drop) text += ` · ${drop} dropping soon`;
  // Named separately from the snapshot count in the masthead, so the extra
  // rows are never mistaken for published, checked data.
  if (coined) text += ` · ${coined} coined by you`;
  el("resultcount").textContent = text;

  btn("btn-fullcheck").disabled = n === 0;
  btn("btn-fullcheck").textContent =
    n > 0 && n <= LIVE_CAP ? `Full check these ${n}` : "Full check these names";

  const empty = el("empty");
  empty.hidden = n !== 0;
  if (n === 0) {
    empty.innerHTML = state.query
      ? `Nothing matches <code>${esc(state.query)}</code>. Try fewer letters, or drop the <code>^</code>/<code>$</code> anchor.`
      : "No names match these filters. Clear one to widen the search.";
  }
  updateHarvest();
}

function updateHarvest() {
  const b = bookmarks.count(), c = checks.count();
  el("harvest-count").textContent =
    b || c ? `${b} bookmarked · ${c} checked` : "nothing harvested yet";
  btn("btn-export").disabled = !(b || c);
  btn("btn-export-csv").disabled = !b;
}

/* --- detail panel ------------------------------------------------------- */

function openPanel(name) {
  const row = allRows().find((r) => r[F.NAME] === name);
  const coined = state.coined.find((c) => c.name === name);
  if (!row && !coined) return;
  const tld = state.data.tld;
  const d = domainOf(name);
  const ipa = [...name].map((c) => (state.phonemes[c] || {}).ipa || c).join("");
  const live = checks.get(d);

  const sc = row ? row[F.SCORE] : coined.score;
  const mods = row ? row[F.MODS] : scoreName(name, state.phonemes, state.scoring).modifiers;
  const e = row ? effective(row) : {
    conf: live ? 2 : 0, status: live ? state.statuses.indexOf(live.status) : 0,
    checked: live ? live.checked_at : null, live: Boolean(live),
  };

  const phList = [...name].map((ch) => {
    const p = state.phonemes[ch] || {};
    return `<li><span class="glyph" style="color:var(--tier-${p.tier})">${ch}</span>
      <span class="w">${(p.w ?? 0).toFixed(2)}</span>
      <span class="note">${esc(p.note || "Not part of the safe alphabet.")}</span></li>`;
  }).join("");

  const modList = (mods || []).map(([label, delta]) =>
    `<li><span>${esc(label)}</span><span class="${delta >= 0 ? "plus" : "minus"}">${delta >= 0 ? "+" : ""}${delta}</span></li>`
  ).join("") || `<li><span>no adjustments</span><span></span></li>`;

  // After a reload `state.coined` is empty, but a saved coinage still has its
  // English and Japanese in the bookmark. Pattern and vowel are not persisted,
  // so those two rows only appear while the coin panel still holds the result.
  const savedMeta = (!coined && row && isCoined(row)) ? bookmarks.all()[d] : null;
  const origin = coined || savedMeta;
  const originBlock = origin ? `
    <h3>Coined from</h3>
    <dl class="kv">
      <dt>English</dt><dd>${esc((origin.origin || []).join(" "))}</dd>
      <dt>Japanese</dt><dd class="mono">${esc(origin.japanese || "")}</dd>
      ${coined ? `<dt>Pattern</dt><dd>${esc(coined.kinds.join(", "))}</dd>
      <dt>Vowel</dt><dd>${esc(coined.epenthetic)}</dd>` : ""}
    </dl>
    <p class="panel-note dim">You coined this. It is not in the published
      snapshot, so nothing here has been checked unless you checked it.</p>` : "";

  const meanings = row ? (row[F.MEANINGS] || []) : [];
  const meaningBlock = meanings.length ? `
    <h3>Already a word in</h3>
    <p class="panel-note">${meanings.map(esc).join(" · ")}</p>
    <p class="panel-note dim">A hit can be a pearl or a landmine —
      <a href="https://en.wiktionary.org/wiki/${encodeURIComponent(name)}"
         target="_blank" rel="noopener noreferrer">look it up</a>.</p>` : "";

  const alts = row ? Object.entries(row[F.ALTS] || {}) : [];
  const altBlock = alts.length ? `
    <h3>Other endings</h3>
    <ul class="altlist">${alts.map(([t, st]) =>
      `<li><span class="mono">.${t}</span><span class="${st === 1 ? "free" : "gone"}">${state.statuses[st]}</span></li>`
    ).join("")}</ul>` : "";

  const dropBlock = row && isDropping(row) ? `
    <div class="checkresult bad">Registered today, but in redemption or pending delete —
      it returns to the pool within weeks. This tool does not register anything.</div>` : "";

  el("panel-body").innerHTML = `
    <p class="panel-name">${name}<span class="tld">.${tld}</span></p>
    <p class="panel-ipa">/${ipa}/</p>
    ${dropBlock}
    <dl class="kv">
      <dt>Score</dt><dd>${sc.toFixed(1)} / 100${row ? ` &nbsp;<span class="tierbadge" data-tier="${row[F.TIER]}">${row[F.TIER]}</span>` : ""}</dd>
      <dt>Shape</dt><dd>${[...name].filter(isVowel).length} syllables, ${name.length} letters</dd>
      <dt>Status</dt><dd>${state.statuses[e.status]}</dd>
      <dt>Verified to</dt><dd>${state.confLevels[e.conf]}${e.live ? " (live, cached here)" : ""}</dd>
      <dt>Checked</dt><dd>${fmtAge(e.checked)}</dd>
    </dl>
    ${originBlock}${meaningBlock}${altBlock}
    <h3>Sound by sound</h3>
    <ul class="phlist">${phList}</ul>
    <h3>Score adjustments</h3>
    <ul class="mods">${modList}</ul>
    <div class="panel-actions">
      <button class="btn" data-check="${name}">Verify with registry now</button>
      <button class="btn btn-quiet" data-bookmark="${name}">${bookmarks.has(d) ? "Remove bookmark" : "Bookmark"}</button>
      <a class="btn btn-quiet" target="_blank" rel="noopener noreferrer"
         href="https://porkbun.com/checkout/search?q=${name}.${tld}">Price it</a>
    </div>
    <div id="panel-check"></div>`;

  el("panel").hidden = false;
  el("scrim").hidden = false;
  btn("panel-close").focus();
}

const closePanel = () => { el("panel").hidden = true; el("scrim").hidden = true; };

/* --- live verification --------------------------------------------------
   RDAP servers are required to send permissive CORS headers (RFC 7480), so
   the browser can query the registry directly. When that fails we say so and
   hand over the command that does work. Results are cached for three weeks. */

let lastRequest = 0;

async function throttle() {
  const wait = Math.max(0, lastRequest + LIVE_MIN_INTERVAL - Date.now());
  lastRequest = Date.now() + wait;
  if (wait) await new Promise((r) => setTimeout(r, wait));
}

async function verify(name) {
  const d = domainOf(name);
  const cached = checks.get(d);
  if (cached) return { status: cached.status, flags: cached.flags, cached: true };

  await throttle();
  const base = state.data.rdap_endpoint || "https://rdap.org/domain/";
  let res;
  try {
    res = await fetch(base + d, { headers: { Accept: "application/rdap+json" } });
  } catch {
    throw new BlockedError();
  }
  let result;
  if (res.status === 404) result = { status: "available", flags: [] };
  else if (res.ok) {
    let body = {};
    try { body = await res.json(); } catch { /* status alone is enough */ }
    const st = (body.status || []).map((s) => String(s).replace(/\s/g, "").toLowerCase());
    result = { status: "taken", flags: st.filter((s) => state.droppingFlags.includes(s)) };
  } else if (res.status === 429) throw new Error("rate limited by the registry");
  else throw new Error(`registry returned ${res.status}`);

  checks.put(d, result);
  return result;
}

async function checkOne(name, button) {
  if (button) { button.disabled = true; button.textContent = "..."; }
  const out = el("panel-check");
  try {
    const result = await verify(name);
    if (out) {
      out.innerHTML = `<div class="checkresult ${result.status === "available" ? "ok" : "bad"}">
        Registry says: ${result.status}${result.flags.length ? ` (${result.flags.join(", ")})` : ""}${result.cached ? " — from cache" : ""}.
        Cached here; use “Export harvest” to merge it into your local db.</div>`;
    }
    refreshRow(name);
    updateCounts();
  } catch (e) {
    if (out) {
      out.innerHTML = `<div class="checkresult bad">${e instanceof BlockedError
        ? `Your browser blocked the registry request (CORS, an extension, or a proxy). Run <code>pdgen check --name ${esc(name)}</code> locally instead.`
        : `Could not verify: ${esc(e.message)}`}</div>`;
    }
    if (button) { button.disabled = false; button.textContent = "Check"; }
    return;
  }
  if (button) { button.disabled = false; button.textContent = "Check"; }
}

async function checkMany(names) {
  state.cancelLive = false;
  el("progress").hidden = false;
  let done = 0, failed = 0, corsHit = false;

  const tick = () => {
    el("progress-fill").style.width = `${(done / names.length) * 100}%`;
    el("progress-text").textContent =
      `Verifying with the registry — ${done} of ${names.length}${failed ? `, ${failed} failed` : ""}`;
  };
  tick();

  const queue = names.slice();
  const worker = async () => {
    while (queue.length && !state.cancelLive && !corsHit) {
      const name = queue.shift();
      try { await verify(name); }
      catch (e) { failed++; if (e instanceof BlockedError) corsHit = true; }
      done++; tick();
    }
  };
  await Promise.all(Array.from({ length: LIVE_CONCURRENCY }, worker));
  el("progress").hidden = true;

  if (corsHit) {
    showNotice("Live verification is blocked in this browser",
      `The registry request did not complete — usually CORS, an extension, or a corporate proxy.
       Run <code>pdgen check --stage rdap</code> locally, then <code>pdgen publish</code>.`, true);
  }
  applyFilters();
  renderCoined();
  shuffle();
}

/* --- notices ------------------------------------------------------------ */

function showNotice(title, body, alarm) {
  const n = el("notice");
  n.hidden = false;
  n.classList.toggle("is-alarm", Boolean(alarm));
  n.innerHTML = `<strong>${title}</strong><p>${body}</p>`;
}

function provenanceNotice() {
  const d = state.data;
  if (d.demo) {
    return showNotice("This is sample data, not real lookups",
      `The snapshot was seeded with <code>tools/seed_demo.py</code> so the site could be previewed.
       Run <code>python3 tools/seed_demo.py --clear</code>, then
       <code>python3 -m pdgen check --stage rdap</code> and <code>python3 -m pdgen publish</code>.`, true);
  }
  const age = ageDays(d.generated_at);
  const parts = [`Snapshot taken ${fmtAge(d.generated_at)}.`];
  const weak = ((d.db_stats || {}).available_by_confidence || {}).dns || 0;
  if (weak) {
    parts.push(`${weak.toLocaleString()} of these were only checked against DNS, which misses
      registered-but-undelegated names. Use the <em>Verified at least to</em> control to hide them.`);
  }
  // No CLI command here. A visitor has no clone to run it in, so it was
  // maintainer instruction on a public page. It lives in the README, in
  // skill.md and in llms.txt, which is where someone who can act on it looks.
  showNotice(age > STALE_DAYS ? "This snapshot is getting old" : "How current this is",
    parts.join(" "), age > STALE_DAYS);
}

/* --- controls ----------------------------------------------------------- */

function buildChips(id, values, set) {
  const box = el(id);
  box.innerHTML = values.map((v) => `<button class="chip" data-v="${v}">${v}</button>`).join("");
  box.addEventListener("click", (ev) => {
    const b = closest(ev, ".chip");
    if (!b) return;
    const raw = b.dataset.v;
    const v = /^\d+$/.test(raw) ? Number(raw) : raw;
    if (set.has(v)) set.delete(v); else set.add(v);
    b.classList.toggle("is-on");
    applyFilters();
  });
}

function buildConfidenceChips() {
  const box = el("f-confidence");
  const labels = { generated: "Any", dns: "DNS", rdap: "Registry", registrar: "Registrar" };
  const titles = ["Show everything in the snapshot", "DNS only — a lead, not a confirmation",
    "Confirmed by the registry over RDAP", "Confirmed purchasable, including premium pricing"];
  box.innerHTML = state.confLevels.map((lvl, i) =>
    `<button class="chip${i === 0 ? " is-on" : ""}" data-i="${i}" title="${titles[i]}">${labels[lvl]}</button>`).join("");
  box.addEventListener("click", (ev) => {
    const b = closest(ev, ".chip");
    if (!b) return;
    state.minConf = Number(b.dataset.i);
    [...box.children].forEach((c) => c.classList.toggle("is-on", c === b));
    applyFilters();
  });
}

function toggleBookmark(name) {
  const published = state.rows.find((r) => r[F.NAME] === name);
  const saved = state.saved.find((r) => r[F.NAME] === name);
  const coined = state.coined.find((c) => c.name === name);
  bookmarks.toggle(domainOf(name), {
    score: published ? published[F.SCORE] : coined ? coined.score : saved ? saved[F.SCORE] : null,
    syllables: published ? published[F.SYL] : coined ? coined.syllables : saved ? saved[F.SYL] : null,
    // A saved coinage keeps its provenance when re-bookmarked after a reload,
    // when `state.coined` is empty and only the synthetic row remains.
    coined: Boolean(!published && (coined || saved)),
    origin: coined ? coined.origin : undefined,
    japanese: coined ? coined.japanese : undefined,
  });

  // A coinage entering or leaving the bookmarks changes what the catalogue
  // contains, not just how a star looks, so the list has to be rebuilt. For an
  // ordinary snapshot row nothing structural changed, and re-filtering would
  // throw away the reader's scroll position for no reason.
  const before = state.saved.length;
  rebuildSaved();
  if (state.saved.length !== before || state.show.bookmarked) {
    applyFilters();
  } else {
    refreshRow(name);
  }
  document.querySelectorAll(`[data-bookmark="${name}"]`)
    .forEach((n) => n.classList.toggle("is-on", bookmarks.has(domainOf(name))));
  shuffle();
  updateHarvest();
}

function delegate(container, handler) {
  container.addEventListener("click", handler);
  container.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(e); }
  });
}

function wire() {
  let t;
  input("q").addEventListener("input", (e) => {
    clearTimeout(t);
    const v = /** @type {HTMLInputElement} */ (e.target).value;
    t = setTimeout(() => { state.query = v; applyFilters(); }, 110);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA"].includes((document.activeElement || document.body).tagName)) {
      e.preventDefault(); input("q").focus(); input("q").select();
    }
    if (e.key === "Escape") closePanel();
  });

  const rowHandler = (e) => {
    const star = closest(e, "[data-bookmark]");
    if (star) { e.stopPropagation(); toggleBookmark(star.dataset.bookmark); return; }
    const btn = closest(e, "[data-check]");
    if (btn) { e.stopPropagation(); checkOne(btn.dataset.check, btn); return; }
    const item = closest(e, ".row, .coin-row, .pick");
    if (item) openPanel(item.dataset.name);
  };
  delegate(el("rows"), rowHandler);
  delegate(el("coin-results"), rowHandler);
  delegate(el("shuffle"), rowHandler);
  delegate(el("panel-body"), rowHandler);

  btn("panel-close").addEventListener("click", closePanel);
  el("scrim").addEventListener("click", closePanel);
  btn("btn-shuffle").addEventListener("click", shuffle);

  btn("btn-coin").addEventListener("click", runCoin);
  input("coin-input").addEventListener("keydown", (e) => { if (e.key === "Enter") runCoin(); });
  for (const id of ["coin-allvowels", "coin-dict"]) {
    el(id).addEventListener("click", (e) => {
      /** @type {Element} */ (e.currentTarget).classList.toggle("is-on");
      if (state.coined.length) runCoin();
    });
  }
  btn("btn-coincheck").addEventListener("click", () =>
    checkMany(state.coined.slice(0, LIVE_CAP).map((c) => c.name)));

  el("f-show").addEventListener("click", (e) => {
    const b = closest(e, ".chip");
    if (!b) return;
    const k = b.dataset.show;
    state.show[k] = !state.show[k];
    b.classList.toggle("is-on", state.show[k]);
    applyFilters();
  });

  el("f-sort").addEventListener("click", (e) => {
    const b = closest(e, ".chip");
    if (!b) return;
    state.sort = b.dataset.sort;
    [...el("f-sort").children].forEach((c) => c.classList.toggle("is-on", c === b));
    applyFilters();
  });

  btn("btn-fullcheck").addEventListener("click", () => {
    const names = state.view.map((r) => r[F.NAME]);
    if (names.length > LIVE_CAP && !confirm(
      `That is ${names.length.toLocaleString()} registry lookups at about 4 per second.\n\n` +
      `Only the first ${LIVE_CAP} will run from the browser. For the full set:\n` +
      `  python3 -m pdgen check --stage rdap\n\nContinue with ${LIVE_CAP}?`)) return;
    checkMany(names.slice(0, LIVE_CAP));
  });

  btn("btn-cancel").addEventListener("click", () => { state.cancelLive = true; });
  btn("btn-export").addEventListener("click", exportPatch);
  btn("btn-export-csv").addEventListener("click", exportBookmarksCsv);

  btn("btn-reset").addEventListener("click", () => {
    state.query = ""; input("q").value = "";
    state.syllables.clear(); state.lengths.clear(); state.tiers.clear();
    state.minConf = 0; state.sort = "score";
    state.show = { dropping: false, meaning: false, bookmarked: false };
    document.querySelectorAll("#f-syllables .chip, #f-length .chip, #f-tier .chip, #f-show .chip")
      .forEach((c) => c.classList.remove("is-on"));
    [...el("f-confidence").children].forEach((c, i) => c.classList.toggle("is-on", i === 0));
    [...el("f-sort").children].forEach((c, i) => c.classList.toggle("is-on", i === 0));
    applyFilters();
  });

  new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) renderMore();
  }, { rootMargin: "600px" }).observe(el("sentinel"));
}

/* --- boot --------------------------------------------------------------- */

async function boot() {
  let d;
  try {
    const res = await fetch(DATA_URL, { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    d = await res.json();
  } catch {
    el("resultcount").textContent = "";
    el("empty").hidden = false;
    el("empty").innerHTML =
      `No snapshot found at <code>${DATA_URL}</code>. Build one with
       <code>python3 -m pdgen generate</code>, <code>python3 -m pdgen check</code>,
       then <code>python3 -m pdgen publish</code>.`;
    el("masthead-meta").textContent = "no data";
    return;
  }

  state.data = d;
  state.rows = d.rows || [];
  state.phonemes = d.phonemes || {};
  state.scoring = d.scoring || null;
  state.blocklist = d.blocklist || [];
  state.confLevels = d.confidence_levels || state.confLevels;
  state.statuses = d.statuses || state.statuses;
  state.droppingFlags = d.dropping_flags || state.droppingFlags;

  if (state.scoring) verifyAgainstFixtures(state.phonemes, state.scoring);
  else el("coin-section").hidden = true;

  const meta = el("masthead-meta");
  meta.querySelector('[data-slot="count"]').textContent =
    `${state.rows.length.toLocaleString()} names · .${d.tld}`;
  meta.querySelector('[data-slot="date"]').textContent =
    `updated ${String(d.generated_at).slice(0, 10)}`;

  const uniq = (i) => [...new Set(state.rows.map((r) => r[i]))].sort((a, b) => a - b);
  buildChips("f-syllables", uniq(F.SYL), state.syllables);
  buildChips("f-length", uniq(F.LEN), state.lengths);
  buildChips("f-tier", (d.tiers || []).filter((tr) => state.rows.some((r) => r[F.TIER] === tr)), state.tiers);
  buildConfidenceChips();

  onStorageProblem((msg) => showNotice("Local storage is under pressure", msg, true));
  const pruned = checks.prune();
  if (pruned) console.info(`[sayable] pruned ${pruned} expired cached checks`);

  // Absent shards are fine -- coining falls back to the spelling heuristic.
  dict.load().then((m) => {
    const toggle = el("coin-dict");
    if (m) {
      toggle.title = `${m.words.toLocaleString()} real pronunciations, fetched one small shard at a time`;
    } else {
      toggle.classList.remove("is-on");
      toggle.setAttribute("disabled", "true");
      toggle.title = "No dictionary published. Run: pdgen dictionary build";
    }
  });

  // Before the first applyFilters, or saved coinages would be missing from the
  // catalogue until the next bookmark toggle.
  rebuildSaved();

  provenanceNotice();
  wire();
  shuffle();
  applyFilters();
}

boot();
