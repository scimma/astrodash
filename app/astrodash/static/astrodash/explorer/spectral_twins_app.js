

function daepWavelengthLabel() {
  const selected = document.getElementById('modelMode')?.value
    || new URLSearchParams(window.location.search).get('dataset')
    || 'rest_frame';

  if (selected === 'observer_frame') {
    return 'Observed wavelength [Å]';
  }
  if (selected === 'multi_object_rest') {
    return 'Wavelength [Å] (mixed frames)';
  }
  return 'Rest-frame wavelength [Å]';
}


(function () {
"use strict";

const CONFIG = JSON.parse(document.getElementById("twins-config").textContent);
// The bundle id busts browser caches when a new bundle is published. It does
// not select a bundle on the server, so a page left open across a release is
// caught by the spectra file's ETag instead (see prefetchSpectrum).
const META_URL = CONFIG.metaUrl + "?v=" + encodeURIComponent(CONFIG.bundleId);
const SPEC_URL = CONFIG.spectraUrl + "?v=" + encodeURIComponent(CONFIG.bundleId);

let DATA = null;
let TYPE_COLORS = null;
let TYPE_SYMBOLS = null;
let SN_TO_IDX = null;
let SORTED_SNS = null;
const specCache = new Map(); // idx -> Promise<{flux, recon, mask}>
let SPEC_ETAG = null; // ETag of the spectra file this page started with
let activeUpdateToken = 0;
let CURRENT_SELECTION = null;
let CURRENT_METRIC_ENTRIES = null;
let CURRENT_COLOR_FIELD = "canonical_type";
let latentBaseTraces = null;
const UPLOADED_TARGETS = new Map(); // transient page-memory only, never persisted
let NEXT_UPLOAD_ID = 0;
let UPLOAD_SOURCE_FILES = []; // Keep uploaded files across model switches.
let UPLOAD_CURRENT_FILES = []; // Files from the latest selection.
let UPLOAD_BUSY = false;
let LAST_UPLOAD_LATENT_KEY = null;

const $ = id => document.getElementById(id);

// Authoritative observation-time lookup, not model phase. Filled from the
// catalog file at startup; stays empty when there is none.
let LITERATURE_CATALOG = {};
// How long the page waits for the catalog after the metadata has loaded.
const LITERATURE_CATALOG_WAIT_MS = 10000;
let LITERATURE_ROWS = null;
function literatureKey(sn, date) {
  const name = String(sn || '').toLowerCase().replace(/[^a-z0-9]/g,'');
  const timestamp = String(date || '').trim().replace('T',' ').slice(0,19);
  return name + '|' + timestamp;
}
function buildLiteratureRows() {
  LITERATURE_ROWS = new Array(DATA.N);
  for (let i=0; i<DATA.N; i++) {
    LITERATURE_ROWS[i] = LITERATURE_CATALOG[literatureKey(DATA.sn[i], DATA.obs_date[i])] || null;
  }
}
function literatureAt(i) { return LITERATURE_ROWS && LITERATURE_ROWS[i] || null; }
function literaturePhase(i) {
  const row = literatureAt(i);
  return row && row[0] !== null && row[0] !== "" && Number.isFinite(Number(row[0])) ? Number(row[0]) : null;
}
function literaturePhaseHtml(i) {
  const phase = literaturePhase(i);
  if (phase === null) return '<td class="literature-phase-muted">—</td>';
  const label = (phase > 0 ? '+' : '') + phase.toFixed(2);
  return '<td class="mono">' + escapeHtml(label) + '</td>';
}
function peakBandHtml(i) {
  const row = literatureAt(i);
  if (!row) return '<td class="literature-phase-muted">—</td>';
  const band = row[5] == null || String(row[5]).trim() === '' ? '—' : String(row[5]);
  const source = String(row[1] || '');
  const citation = String(row[2] || '');
  const doi = String(row[3] || '');
  const sourceURL = /^https?:\/\//i.test(source) ? source : '';
  const doiURL = /^10\.\d{4,9}\//.test(doi) ? 'https://doi.org/' + encodeURI(doi) : '';
  const paperURL = /^https?:\/\/(?:arxiv\.org\/(?:abs|pdf)\/|[^/]*\.(?:aanda\.org|iop\.org|springer\.com|nature\.com|science\.org)\/)/i.test(sourceURL) ? sourceURL : '';
  const url = doiURL || paperURL;
  const link = url ? '<a class="peak-band-paper" href="' + escapeHtml(url) +
    '" target="_blank" rel="noopener noreferrer" title="' +
    escapeHtml(citation || 'Open cited paper') + '" aria-label="Open peak-date paper">↗</a>' : '';
  return '<td class="peak-band-cell"><span>' + escapeHtml(band) + '</span>' + link + '</td>';
}
function literatureFilterActive() { return $('filterLiteraturePhase').checked; }
function literaturePass(i) {
  if (!literatureFilterActive()) return true;
  const phase = literaturePhase(i);
  if (phase === null) return $('litPhaseShowMissing').checked;
  return phase >= Number($('litPhaseMin').value) && phase <= Number($('litPhaseMax').value);
}
function literatureReadout() {
  const lo = Number($('litPhaseMin').value), hi = Number($('litPhaseMax').value);
  const fmt = x => (x > 0 ? '+' : '') + x;
  $('litPhaseReadout').textContent = fmt(lo) + ' to ' + fmt(hi) + ' d';
}


function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
function fmtNum(x, ndp) {
  if (x === null || x === undefined) return "";
  const v = Number(x);
  return Number.isFinite(v) ? v.toFixed(ndp) : "";
}
function uniqSorted(arr) { return Array.from(new Set(arr)).sort((a, b) => a.localeCompare(b)); }
function snKey(sn) { return String(sn || "").trim().toLowerCase(); }
/* BEE_SAMESN_REPAIR_20261117 */
// Comparison only: normalized SN identity, including uploaded filenames with dates.
function reviewedSNIdentity(v) {
  const raw=String(v??'').trim().toLowerCase();
  const match=raw.match(/^(?:sn[\s._-]*)?((?:19|20)\d{2}[a-z]+)(?=$|[^a-z0-9])/);
  if(match) return match[1];
  return raw.replace(/[^a-z0-9]/g,'');
}
// Keep the original WISeREP raw_type as provenance, but use the reviewed Ia type
// consistently in the viewer and its canonical/broad labels.
function enforceBeeReviewedType() {
  if(!DATA||!Array.isArray(DATA.sn)) return 0;
  let n=0;
  for(let i=0;i<DATA.sn.length;i++) {
    if(reviewedSNIdentity(DATA.sn[i])!=='2023bee') continue;
    for(const [field,val] of [['obj_type','SN Ia'],['display_type','SNIa'],
                              ['canonical_type','SNIa'],['broad_type','SNIa']]) {
      if(Array.isArray(DATA[field])) DATA[field][i]=val;
    }
    n++;
  }
  return n;
}

// UI label cleanup only. Original archive/model labels remain in DATA.
function displaySNType(value) {
  const s = String(value == null ? '' : value).trim();
  if (s === 'SN-I') return 'SN I';
  if (/^SLSNI(?:I|R)?$/.test(s)) return 'SLSN-' + s.slice(4);
  if (s.startsWith('SNI')) return 'SN ' + s.slice(2);
  return s;
}
function normType(t) { const s = displaySNType(t); return s ? s : "Unknown"; }

function colorValuesForField(field) {
  const arr = DATA && Array.isArray(DATA[field]) ? DATA[field] : null;
  if (arr && arr.length === DATA.N) return arr.map(normType);
  return (DATA.obj_type || []).map(normType);
}
function colorKey(field, label) { return field + "::" + normType(label); }

const DISTINCT_TYPE_COLORS = [
  "#0066FF", "#E60000", "#00A651", "#8A00D4", "#FF8C00", "#00AFC1",
  "#FF1493", "#6B3E26", "#7CB518", "#2E5AAC", "#FFD000", "#D81159",
  "#008C8C", "#6F2DA8", "#00843D", "#F25C9B", "#A0522D", "#4169E1",
  "#9B870C", "#007A78", "#FF4F00", "#5D3FD3", "#39A900", "#B0006D",
  "#4B0082", "#D2691E", "#00A99D", "#0057B8", "#84BD00", "#FF66B3",
  "#5A6772", "#F4A300", "#00C878", "#A23DB6", "#2B7A9B", "#B8860B",
  "#EF553B", "#17BECF", "#7D3C98", "#1B8A3B", "#FF7F50", "#3F7F7F",
  "#C44BC5", "#C23B22", "#6757C8", "#2FA66A", "#D94F70", "#596B17",
  "#00BFA5", "#8B008B", "#1F77B4", "#BC6C25", "#00CC44", "#AA336A",
  "#3366CC", "#E07A5F", "#118AB2", "#9C6644", "#43AA8B", "#F72585",
  "#577590", "#F8961E", "#7209B7", "#277DA1"
];
const DISTINCT_TYPE_SYMBOLS = [
  "circle", "square", "diamond", "triangle-up", "triangle-down",
  "triangle-left", "triangle-right", "pentagon", "hexagon", "cross", "x", "star"
];

function uniqueOpaqueColor(i) {
  if (i < DISTINCT_TYPE_COLORS.length) return DISTINCT_TYPE_COLORS[i];
  const hue = (i * 137.50776405003785) % 360;
  const sat = 82 - (i % 3) * 8;
  const light = 42 + (i % 4) * 7;
  return `hsl(${hue.toFixed(3)}, ${sat}%, ${light}%)`;
}

function typeKeys() {
  const keys = [];
  for (const field of ["raw_type", "canonical_type", "broad_type"]) {
    for (const label of uniqSorted(colorValuesForField(field))) keys.push(colorKey(field, label));
  }
  return keys;
}
function buildGlobalTypeColorMap() {
  const cmap = {};
  typeKeys().forEach((key, i) => { cmap[key] = uniqueOpaqueColor(i); });
  return cmap;
}
function buildGlobalTypeSymbolMap() {
  const smap = {};
  typeKeys().forEach((key, i) => { smap[key] = DISTINCT_TYPE_SYMBOLS[i % DISTINCT_TYPE_SYMBOLS.length]; });
  return smap;
}
function colorFor(field, label) { return TYPE_COLORS[colorKey(field, label)] || "#000000"; }
function symbolFor(field, label) { return TYPE_SYMBOLS[colorKey(field, label)] || "circle"; }
function colorFieldLabel(field) {
  return {raw_type: "Raw type", canonical_type: "Canonical type", broad_type: "Broad type"}[field] || field;
}

// BEGIN CASE-INSENSITIVE SUPERNOVA GROUPING (2026-11-16)
// Map raw SN spellings to a single display name without modifying DATA.sn,
// spectrum IDs, latents, observation dates or any source metadata.
let SN_CANONICAL_BY_KEY = new Map();
function canonicalSNName(rawName) {
  const raw = String(rawName || "").trim();
  return SN_CANONICAL_BY_KEY.get(snKey(raw)) || raw;
}
function buildSnToIdx() {
  const groups = new Map();
  for (let i = 0; i < DATA.N; i++) {
    const raw = String(DATA.sn[i] || "").trim();
    const key = snKey(raw);
    if (!groups.has(key)) groups.set(key, {names: new Set(), indices: []});
    groups.get(key).names.add(raw);
    groups.get(key).indices.push(i);
  }
  SN_CANONICAL_BY_KEY = new Map();
  const map = Object.create(null);
  for (const [key, group] of groups) {
    const names = [...group.names];
    // Prefer the standardized capitalized ASASSN catalog prefix.
    const canonical = /^asassn-/i.test(names[0])
      ? "ASASSN-" + names[0].slice(7)
      : names[0];
    SN_CANONICAL_BY_KEY.set(key, canonical);
    map[canonical] = group.indices;
  }
  return map;
}
// END CASE-INSENSITIVE SUPERNOVA GROUPING

// Compact reference-spectrum tooltip. Use reviewed observer-frame literature
// phase exclusively; never fall back to the model-derived phase.
function reviewedSpectrumHover(i) {
  const value = (arr) => (arr && arr[i] != null && String(arr[i]).trim() !== '')
    ? String(arr[i]).trim() : '—';
  const number = (arr, places) => {
    const n = arr && arr[i] != null && arr[i] !== '' ? Number(arr[i]) : NaN;
    return Number.isFinite(n) ? n.toFixed(places) : '—';
  };
  const phase = literaturePhase(i);
  const phaseLabel = phase == null ? '—' : (phase > 0 ? '+' : '') + phase.toFixed(2) + ' d';
  const escape = (s) => String(s).replace(/&/g,'&amp;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
  return [
    'SN: ' + value(DATA.sn),
    'Redshift: ' + number(DATA.z, 6),
    'Phase from Max: ' + phaseLabel,
    'Raw Type: ' + value(DATA.raw_type),
    'Canonical Type: ' + value(DATA.canonical_type),
    'Broad Type: ' + value(DATA.broad_type),
    'Observation date: ' + value(DATA.obs_date),
    'Instrument: ' + value(DATA.instrument),
    'Source: ' + value(DATA.source_dataset),
    'Spectrum ID: ' + value(DATA.spec_id)
  ].map(escape).join('<br>');
}

function buildBaseLatentTraces() {
  const groups = {};
  const values = colorValuesForField(CURRENT_COLOR_FIELD);
  for (let i = 0; i < DATA.N; i++) {
    if (!literaturePass(i)) continue;
    const t = normType(values[i]);
    if (!(t in groups)) groups[t] = {x: [], y: [], text: [], customdata: []};
    groups[t].x.push(DATA.Y[i][0]);
    groups[t].y.push(DATA.Y[i][1]);
    groups[t].text.push(reviewedSpectrumHover(i));
    groups[t].customdata.push(i);
  }
  return Object.keys(groups).sort((a, b) => a.localeCompare(b)).map(t => ({
    type: "scattergl", mode: "markers",
    x: groups[t].x, y: groups[t].y, text: groups[t].text, customdata: groups[t].customdata,
    hoverinfo: "text", name: t,
    marker: {
      size: 7, color: colorFor(CURRENT_COLOR_FIELD, t), symbol: symbolFor(CURRENT_COLOR_FIELD, t),
      opacity: 0.95, line: {width: 0.45, color: "#222"}
    }
  }));
}

function arrayValue(arr, i, fallback) {
  if (!arr || arr[i] === null || arr[i] === undefined) return fallback || "";
  return String(arr[i]);
}
function sourceAt(i) { return arrayValue(DATA.source_dataset, i, ""); }
function instAt(i) { return arrayValue(DATA.instrument, i, ""); }
function dateAt(i) { return arrayValue(DATA.obs_date, i, ""); }

function obsLabelForIdx(i) {
  const dstr = DATA.jd_str[i] || "";
  const src = sourceAt(i);
  const inst = instAt(i);
  return (dstr ? dstr + "  " : "") +
    "jd=" + (fmtNum(DATA.jd[i], 3) || "NA") +
    "  phase from max=" + (literaturePhase(i) === null ? "—" : fmtNum(literaturePhase(i), 2) + " d") +
    "  z=" + (fmtNum(DATA.z[i], 6) || "NA") +
    "  " + (DATA.obj_type[i] || "") +
    (src ? "  " + src : "") + (inst ? "  " + inst : "") +
    "  idx=" + i;
}

function sortObsIdxList(idxs) {
  return idxs.map(i => ({
    i,
    jd: (DATA.jd[i] == null) ? Infinity : Number(DATA.jd[i]),
    phase: (DATA.phase[i] == null) ? Infinity : Number(DATA.phase[i]),
  })).sort((a, b) => (a.jd - b.jd) || (a.phase - b.phase) || (a.i - b.i)).map(o => o.i);
}

function getNeighborList(i, excludeSameSN, uniqueTwinSN) {
  const keyI = reviewedSNIdentity(DATA.sn[i]);
  const nbrs = DATA.nbrs[i];
  const dists = DATA.dists[i];
  const out = [];
  const seenSN = new Set();
  for (let t = 0; t < nbrs.length; t++) {
    const j = nbrs[t];
    if (j === i) continue;
    if (!literaturePass(j)) continue;
    const keyJ = reviewedSNIdentity(DATA.sn[j]);
    if (excludeSameSN && keyJ === keyI) continue;
    if (uniqueTwinSN) {
      if (seenSN.has(keyJ)) continue;
      seenSN.add(keyJ);
    }
    out.push({j, d: dists[t]});
  }
  return out;
}

// Recurrence is measured across DISTINCT EPOCHS of the selected target SN.
// Multiple spectra of one twin object within a single target list count once.
// A target observation with a missing JD/date is counted as its own epoch.
function epochKey(i) {
  const jd = DATA.jd && DATA.jd[i];
  if (jd !== null && jd !== undefined && Number.isFinite(Number(jd))) {
    // One date is one epoch; protect against repeated reductions of that epoch.
    // JD values (not MJD) supplied by this explorer use the integer calendar-day bin.
    const x = Number(jd);
    return "jd-day:" + Math.floor(x + 0.5);
  }
  const dt = (DATA.obs_date && DATA.obs_date[i]) || (DATA.jd_str && DATA.jd_str[i]) || "";
  if (dt) return "date:" + String(dt).trim().toLowerCase();
  return "unknown-observation:" + i;
}

function recurrenceForSN(targetSN, topK) {
  const targets = SN_TO_IDX[targetSN] || [];
  const targetKey = snKey(targetSN);
  const allEpochs = new Set(targets.map(epochKey));
  const byTwin = new Map();
  for (const ti of targets) {
    const foundThisObs = new Set();
    let rank = 0;
    for (const neighbor of getNeighborList(ti, true, true)) {
      const tj = neighbor.j;
      const twinName = DATA.sn[tj] || "";
      const key = snKey(twinName);
      if (!key || key === targetKey || foundThisObs.has(key)) continue;
      foundThisObs.add(key);
      rank++;
      if (rank > topK) break;
      if (!byTwin.has(key)) byTwin.set(key, {name:twinName, epochs:new Map(), observations:new Set(), matches:[]});
      const entry = byTwin.get(key);
      entry.observations.add(ti);
      const epoch = epochKey(ti);
      const match = {targetIdx:ti,twinIdx:tj,rank,distance:neighbor.d,similarity:1-neighbor.d,epoch};
      const prev = entry.epochs.get(epoch);
      if (!prev || match.rank < prev.rank || (match.rank===prev.rank && match.distance < prev.distance)) entry.epochs.set(epoch, match);
      entry.matches.push(match);
    }
  }
  const rows = [...byTwin.values()].map(r => {
    const bestByEpoch = [...r.epochs.values()].sort((a,b)=>a.distance-b.distance);
    return {...r, nEpochs:r.epochs.size, nObs:r.observations.size,
      fraction: allEpochs.size ? r.epochs.size/allEpochs.size : 0,
      meanSimilarity:bestByEpoch.reduce((a,m)=>a+m.similarity,0)/Math.max(1,bestByEpoch.length),
      bestSimilarity:bestByEpoch.length ? bestByEpoch[0].similarity : null,
      bestMatches:bestByEpoch};
  });
  rows.sort((a,b)=>b.nEpochs-a.nEpochs || b.meanSimilarity-a.meanSimilarity || a.name.localeCompare(b.name));
  return {rows, totalEpochs:allEpochs.size, totalObservations:targets.length};
}

let CURRENT_RECURRENCE = null;
let REC_PAIR_SN = null;
let REC_PAIR_EPOCH = null;
let REC_PAIR_TOKEN = 0;

function recDate(i) {
  return (DATA.jd_str && DATA.jd_str[i]) || dateAt(i) || ("idx="+i);
}

function renderRecurrence(targetIdx) {
  const targetSN = canonicalSNName(DATA.sn[targetIdx]);
  const topK = clamp(parseInt(document.getElementById("recurrenceTopK").value)||5,1,Math.max(1,DATA.K-1));
  const minimum = Math.max(2,parseInt(document.getElementById("recurrenceMinEpochs").value)||2);
  document.getElementById("recurrenceTopK").value = topK;
  document.getElementById("recurrenceMinEpochs").value = minimum;
  const rec = recurrenceForSN(targetSN,topK);
  const shown = rec.rows.filter(r=>r.nEpochs>=minimum);
  if (!CURRENT_RECURRENCE || CURRENT_RECURRENCE.targetSN !== targetSN) {
    REC_PAIR_SN = null; REC_PAIR_EPOCH = null;
  }
  CURRENT_RECURRENCE = {targetSN,topK,minimum,rec,shown};
  document.getElementById("recurrenceSummary").textContent =
    `${targetSN}: ${rec.totalEpochs} distinct observed days (${rec.totalObservations} spectra). ` +
    `A twin is counted once per target day if it appears among the top ${topK} distinct twin objects. ` +
    `Select an object below to plot its matching spectrum with the target at each epoch.`;
  const box = document.getElementById("recurrenceTable");
  if (!shown.length) {
    box.innerHTML = '<div class="small">No recurrent objects at these settings. Try a larger top-k or lower minimum epochs.</div>';
    REC_PAIR_SN = null;
    clearRecurrencePair("No recurrent twin selected");
    return new Set();
  }
  if (!shown.some(r=>snKey(r.name)===REC_PAIR_SN)) {
    REC_PAIR_SN = snKey(shown[0].name);
    REC_PAIR_EPOCH = null;
  }
  // The lower section summarizes one selected object pair, not every candidate.
  let html = '<div class="twins-row" style="margin:8px 0;">' +
    '<label for="recurrentObjectSelect">Select recurrent twin:</label>' +
    '<select id="recurrentObjectSelect" style="min-width:220px;max-width:100%;">' +
    shown.map(r => `<option value="${escapeHtml(snKey(r.name))}" ${snKey(r.name)===REC_PAIR_SN?'selected':''}>${escapeHtml(r.name)} — ${r.nEpochs} dates</option>`).join('') +
    '</select></div>' +
    '<table class="neighbor-table"><thead><tr><th>Selected twin</th><th>Days matched</th><th>Recurrence rate</th><th>Mean cosine similarity</th><th>Best cosine similarity</th></tr></thead><tbody>';
  const r = shown.find(r => snKey(r.name) === REC_PAIR_SN);
  if (r) {
    html += `<tr class="recSelected"><td><strong>${escapeHtml(r.name)}</strong></td>` +
      `<td>${r.nEpochs} of ${rec.totalEpochs}</td><td>${(100*r.fraction).toFixed(1)}%</td>` +
      `<td class="mono">${fmtNum(r.meanSimilarity,5)}</td><td class="mono">${fmtNum(r.bestSimilarity,5)}</td></tr>`;
  }
  html += '</tbody></table>';
  box.innerHTML = html;
  const recurrentObjectSelect = box.querySelector('#recurrentObjectSelect');
  recurrentObjectSelect.addEventListener('change', () => {
    REC_PAIR_SN = recurrentObjectSelect.value;
    REC_PAIR_EPOCH = null;
    renderRecurrence(targetIdx);
    // If already in recurrence metadata mode, keep its selected-pair row in sync.
    if ($('recurrentOnly').checked) updateAll(targetIdx);
  });
  populateRecurrenceEpochs();
  return new Set(shown.map(r=>snKey(r.name)));
}

function clearRecurrencePair(msg) {
  document.getElementById('recurrencePairTitle').textContent = msg;
  document.getElementById('recurrencePairEpoch').innerHTML = '';
  document.getElementById('recurrencePairCounter').textContent = '';
  document.getElementById('recurrencePairPrev').disabled = true;
  document.getElementById('recurrencePairNext').disabled = true;
  document.getElementById('recurrencePairDetails').textContent = '';
  Plotly.react('recurrencePairPlot',[],{annotations:[{text:msg,xref:'paper',yref:'paper',x:.5,y:.5,showarrow:false}],xaxis:{visible:false},yaxis:{visible:false}}, {responsive:true});
}

// All spectrum-level matches, including multiple target spectra on the same day.
// The object's recurrence fraction is STILL calculated from distinct epochKeys.
function sortedRecurrenceMatches(object) {
  return [...object.matches].sort((a,b) => {
    const jdA = DATA.jd[a.targetIdx], jdB = DATA.jd[b.targetIdx];
    const aNum = jdA === null || jdA === undefined ? Infinity : Number(jdA);
    const bNum = jdB === null || jdB === undefined ? Infinity : Number(jdB);
    return aNum - bNum || a.targetIdx - b.targetIdx || a.twinIdx - b.twinIdx;
  });
}
function recurrenceMatchId(m) { return String(m.targetIdx) + ':' + String(m.twinIdx); }
function recurrenceObsLabel(m) {
  const targetInst = instAt(m.targetIdx) || 'Unknown instrument';
  const twinInst = instAt(m.twinIdx) || 'Unknown instrument';

  const targetId = DATA.spec_id?.[m.targetIdx];
  const twinId = DATA.spec_id?.[m.twinIdx];

  const targetLabel = targetInst +
    (targetId != null && String(targetId).trim() ? ' #' + targetId : '');

  const twinLabel = twinInst +
    (twinId != null && String(twinId).trim() ? ' #' + twinId : '');

  return `${recDate(m.targetIdx)} · ${targetLabel}` +
    ` → ${recDate(m.twinIdx)} · ${twinLabel}` +
    ` · cosine ${fmtNum(m.similarity,4)}`;
}
function updateRecurrenceNavigation() {
  const sel = $('recurrencePairEpoch');
  const index = sel.selectedIndex;
  const count = sel.options.length;
  $('recurrencePairPrev').disabled = index <= 0;
  $('recurrencePairNext').disabled = index < 0 || index >= count-1;
  $('recurrencePairCounter').textContent = count ? `Observation ${index+1} of ${count}` : 'No matched observations';
}
function selectRecurrenceMatch(id) {
  const sel = $('recurrencePairEpoch');
  if (!Array.from(sel.options).some(o => o.value === id)) return;
  REC_PAIR_EPOCH = id; // Historical variable name; now identifies a unique spectrum pair.
  sel.value = id;
  updateRecurrenceNavigation();
  $('tbl').querySelectorAll('.pair-date-row').forEach(row =>
    row.classList.toggle('selected-epoch-row', row.dataset.epoch === id));
  plotRecurrencePair();
}
function stepRecurrenceMatch(delta) {
  const sel = $('recurrencePairEpoch');
  const next = sel.selectedIndex + delta;
  if (next < 0 || next >= sel.options.length) return;
  selectRecurrenceMatch(sel.options[next].value);
}

function populateRecurrenceEpochs() {
  if (!CURRENT_RECURRENCE) return;
  const r = CURRENT_RECURRENCE.shown.find(x => snKey(x.name) === REC_PAIR_SN);
  if (!r) { clearRecurrencePair('Select a recurrent twin'); updateRecurrenceNavigation(); return; }
  const matches = sortedRecurrenceMatches(r);
  const sel = $('recurrencePairEpoch');
  if (!matches.some(m => recurrenceMatchId(m) === REC_PAIR_EPOCH)) {
    REC_PAIR_EPOCH = matches.length ? recurrenceMatchId(matches[0]) : null;
  }
  sel.innerHTML = '';
  matches.forEach(m => {
    const opt = document.createElement('option');
    opt.value = recurrenceMatchId(m);
    opt.textContent = recurrenceObsLabel(m);
    sel.appendChild(opt);
  });
  if (REC_PAIR_EPOCH !== null) sel.value = REC_PAIR_EPOCH;
  updateRecurrenceNavigation();
  if (matches.length) plotRecurrencePair();
  else clearRecurrencePair('No matched observations');
}

async function plotRecurrencePair() {
  if (!CURRENT_RECURRENCE) return;
  const r=CURRENT_RECURRENCE.shown.find(x=>snKey(x.name)===REC_PAIR_SN);
  if (!r) return;
  const match=r.matches.find(m=>recurrenceMatchId(m)===REC_PAIR_EPOCH);
  if (!match) return;
  const myToken=++REC_PAIR_TOKEN;
  const targetSN=CURRENT_RECURRENCE.targetSN;
  document.getElementById('recurrencePairTitle').textContent=`${targetSN} vs ${r.name} — matched in ${r.nEpochs}/${CURRENT_RECURRENCE.rec.totalEpochs} target days`;
  document.getElementById('recurrencePairDetails').textContent='Loading focused spectral pair…';
  try {
    const [a,b]=await Promise.all([prefetchSpectrum(match.targetIdx),prefetchSpectrum(match.twinIdx)]);
    if(myToken!==REC_PAIR_TOKEN) return;
    const stats=computeSpectralRMSE(a,b);
    const traces=[
      spectraTrace(a,`${targetSN} | ${recDate(match.targetIdx)} | ${instAt(match.targetIdx)||'instrument NA'} | target`,null),
      spectraTrace(b,`${r.name} | ${recDate(match.twinIdx)} | ${instAt(match.twinIdx)||'instrument NA'} | twin`,'dash')
    ];
    traces[0].line={color:'#13294B',width:2.2};
    traces[1].line={color:'#2986D2',width:1.8,dash:'dash'};
    Plotly.react('recurrencePairPlot',traces,{
      title:'Matched spectra only — normalized flux',autosize:true,
      margin:{l:60,r:20,t:50,b:125},
      xaxis:{title:daepWavelengthLabel()},yaxis:{title:'Normalized flux'},
      legend:{orientation:'h',x:0,y:-0.3,yanchor:'top'}
    },{responsive:true});
    document.getElementById('recurrencePairDetails').textContent=
      `Target ${recDate(match.targetIdx)} → matched ${r.name} spectrum ${recDate(match.twinIdx)}; `+
      `cosine similarity ${fmtNum(match.similarity,6)}; `+
      `spectral RMSE ${stats.rmse===null?'NA':fmtNum(stats.rmse,5)} (${stats.nCommon} shared valid bins).`;
  } catch(err) {
    if(myToken===REC_PAIR_TOKEN) document.getElementById('recurrencePairDetails').textContent='Failed to fetch pair: '+String(err);
  }
}

// Export wavelength + target/twin flux arrays for EVERY matched observation of the
// selected recurrent twin, not only the currently plotted pair. Each spectrum
// pair is recoverable by pair_id, including same-night observations.
function recurrenceCsvCell(value) {
  const str = value === null || value === undefined ? '' : String(value);
  // Quote all fields and escape embedded quotes; guard against CSV formula injection.
  const safe = /^[=+@\t\r]/.test(str) ? "'" + str : str;
  return '"' + safe.replace(/"/g,'""') + '"';
}
function triggerCsvDownload(csv, filename) {
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
// Summary rows cover ALL recurrent objects; spectrum rows cover the selected twin.
// The row_type field distinguishes summary from wavelength samples.
async function exportUnifiedRecurrenceCSV() {
  const current = CURRENT_RECURRENCE;
  const selected = current && current.shown.find(r => snKey(r.name) === REC_PAIR_SN);
  if (!current || !selected) {
    alert('Select a recurrent twin first.');
    return;
  }
  const button = $('downloadRecurrence');
  if (button.disabled) return;
  const oldText = button.textContent;
  button.disabled = true;
  const columns = [
    'row_type','target_sn','twin_sn','target_epoch_count','total_target_epochs',
    'recurrence_rate','mean_cosine_similarity','best_cosine_similarity',
    'pair_id','target_spec_id','twin_spec_id','target_idx','twin_idx',
    'target_date','twin_date','target_instrument','twin_instrument',
    'cosine_similarity','spectral_rmse','common_valid_bins',
    'wavelength_angstrom','target_flux_normalized','twin_flux_normalized',
    'target_valid','twin_valid'
  ];
  const lines = [columns.map(recurrenceCsvCell).join(',')];
  const pushRow = values => lines.push(columns.map(key => recurrenceCsvCell(values[key])).join(','));
  try {
    // Preserve the complete recurrence summary without downloading every object's spectra.
    for (const object of current.shown) {
      pushRow({
        row_type:'summary',target_sn:current.targetSN,twin_sn:object.name,
        target_epoch_count:object.nEpochs,
        total_target_epochs:current.rec.totalEpochs,
        recurrence_rate:object.fraction,
        mean_cosine_similarity:object.meanSimilarity,
        best_cosine_similarity:object.bestSimilarity
      });
    }
    const matches=sortedRecurrenceMatches(selected);
    for (let start=0;start<matches.length;start+=6) {
      const batch=matches.slice(start,start+6);
      button.textContent=`Preparing spectra ${Math.min(start+batch.length,matches.length)}/${matches.length}…`;
      const loaded=await Promise.all(batch.map(async m=>{
        const [a,b]=await Promise.all([prefetchSpectrum(m.targetIdx),prefetchSpectrum(m.twinIdx)]);
        return {m,a,b,stats:computeSpectralRMSE(a,b)};
      }));
      for (const {m,a,b,stats} of loaded) {
        const meta={
          row_type:'spectrum',target_sn:current.targetSN,twin_sn:selected.name,
          target_epoch_count:selected.nEpochs,
          total_target_epochs:current.rec.totalEpochs,
          recurrence_rate:selected.fraction,
          mean_cosine_similarity:selected.meanSimilarity,
          best_cosine_similarity:selected.bestSimilarity,
          pair_id:`${m.targetIdx}:${m.twinIdx}`,
          target_spec_id:DATA.spec_id[m.targetIdx],
          twin_spec_id:DATA.spec_id[m.twinIdx],
          target_idx:m.targetIdx,twin_idx:m.twinIdx,
          target_date:recDate(m.targetIdx),twin_date:recDate(m.twinIdx),
          target_instrument:instAt(m.targetIdx),twin_instrument:instAt(m.twinIdx),
          cosine_similarity:m.similarity,
          spectral_rmse:stats.rmse===null?'':stats.rmse,
          common_valid_bins:stats.nCommon
        };
        for(let k=0;k<DATA.wave.length;k++) {
          const aValid=Boolean(a.mask[k]), bValid=Boolean(b.mask[k]);
          pushRow({...meta,
            wavelength_angstrom:DATA.wave[k],
            target_flux_normalized:aValid?a.flux[k]:'',
            twin_flux_normalized:bValid?b.flux[k]:'',
            target_valid:aValid?1:0,twin_valid:bValid?1:0
          });
        }
      }
    }
    const filename='recurrent_twins_'+safeFilenamePart(current.targetSN)+
      '_selected_'+safeFilenamePart(selected.name)+'.csv';
    triggerCsvDownload(lines.join('\n')+'\n',filename);
  } catch(err) {
    console.error('Unified recurrence export failed:',err);
    alert('Could not export recurrence CSV: '+String(err));
  } finally {
    button.disabled=false;
    button.textContent=oldText;
  }
}

function computeSpectralRMSE(targetData, twinData) {
  let ss = 0.0, n = 0, maxAbs = 0.0;
  const W = Math.min(targetData.flux.length, twinData.flux.length);
  for (let t = 0; t < W; t++) {
    if (!targetData.mask[t] || !twinData.mask[t]) continue;
    const a = Number(targetData.flux[t]);
    const b = Number(twinData.flux[t]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    const d = a - b;
    ss += d * d;
    maxAbs = Math.max(maxAbs, Math.abs(d));
    n += 1;
  }
  return {
    rmse: n > 0 ? Math.sqrt(ss / n) : null,
    nCommon: n,
    maxAbs: n > 0 ? maxAbs : null,
    commonFraction: W > 0 ? n / W : 0,
  };
}

function spectraTrace(data, name, dashStyle, wavelengthOverride) {
  const w = wavelengthOverride || DATA.wave, f = data.flux, m = data.mask;
  if (w.length !== f.length || f.length !== m.length) {
    throw new Error('Plot wavelength/flux/mask lengths do not match');
  }
  const x = [], y = [];
  for (let t = 0; t < w.length; t++) {
    if (m[t]) { x.push(w[t]); y.push(f[t]); }
  }
  const tr = {x, y, mode: "lines", name};
  if (dashStyle) tr.line = {dash: dashStyle};
  return tr;
}

function reconTrace(data, name) {
  const w = DATA.wave, r = data.recon, m = data.mask;
  const x = [], y = [];
  for (let t = 0; t < w.length; t++) {
    if (m[t]) { x.push(w[t]); y.push(r[t]); }
  }
  return {x, y, mode: "lines", name, line: {width: 1, color: "rgba(80,80,80,0.6)"}, visible: "legendonly"};
}

// Dedicated upload search. Never changes the reference target search query.
function updateUploadOnlySearch(selectFirst=false) {
  const input=$('uploadOnlySearch');
  const sel=$('uploadOnlySelect');
  if(!input||!sel)return;
  const q=String(input.value||'').trim().toLowerCase();
  const entries=[...UPLOADED_TARGETS.values()].flat();
  const old=sel.value;
  sel.replaceChildren();
  const matches=entries.filter(t=>[t.object_name,t.filename,t.key].some(v=>String(v||'').toLowerCase().includes(q)));
  if(!matches.length){const opt=document.createElement('option');opt.value='';opt.textContent=entries.length?'No matching uploads':'No uploaded spectra';sel.appendChild(opt);return;}
  for(const t of matches){const opt=document.createElement('option');opt.value=t.id;opt.textContent=(t.object_name||t.key||'Uploaded')+' | '+(t.filename||'');sel.appendChild(opt);}
  if(!selectFirst&&matches.some(t=>t.id===old))sel.value=old;
  if(selectFirst) selectUploadedOnlyObservation(sel.value);
}
function selectUploadedOnlyObservation(id){
  if(!id)return;
  const target=[...UPLOADED_TARGETS.values()].flat().find(t=>t.id===id);
  if(!target)return;
  ensureTargetOption(target.key);
  if(!Array.from($('snSelect').options).some(o=>o.value===target.key))addOption($('snSelect'),target.key,targetLabel(target.key));
  $('targetSelect').value=target.key;
  $('snSelect').value=target.key;
  populateObsDropdown(target.key);
  $('obsSelect').value=id;
  updateAll(id);
}

function normalizeSearch(s) { return String(s || "").trim().toLowerCase(); }

const searchBlobCache = [];
function searchBlobForIdx(i) {
  if (searchBlobCache[i] === undefined) {
    searchBlobCache[i] = normalizeSearch([
      i, DATA.sn[i], DATA.spec_id[i], DATA.obj_type[i], sourceAt(i), instAt(i),
      DATA.jd[i], DATA.jd_str[i], dateAt(i), DATA.phase[i], DATA.z[i], DATA.hover[i]
    ].join(" "));
  }
  return searchBlobCache[i];
}

function targetLabel(sn) {
  if (UPLOADED_TARGETS.has(sn)) return sn + " (" + UPLOADED_TARGETS.get(sn).length + " obs; uploaded)";
  const idxs = SN_TO_IDX[sn] || [];
  if (!idxs.length) return sn || "(blank target)";
  const first = idxs[0];
  const type = DATA.obj_type[first] || "";
  const src = sourceAt(first);
  let label = (sn || "(blank target)") + "  (" + (idxs.length === 1 ? "1 obs" : idxs.length + " obs") + ")";
  if (type) label += "  " + type;
  if (src) label += "  " + src;
  return label;
}

function getMatchedTargets(query, limit) {
  const q = normalizeSearch(query);
  const snList = [...SORTED_SNS, ...UPLOADED_TARGETS.keys()];
  const matches = [];
  let exactIdxSN = null;
  if (/^\d+$/.test(q)) {
    const idx = parseInt(q, 10);
    if (idx >= 0 && idx < DATA.N) exactIdxSN = DATA.sn[idx] || "";
  }
  for (const sn of snList) {
    const snNorm = normalizeSearch(sn);
    const idxs = SN_TO_IDX[sn] || [];
    let matched = (q === "") || snNorm.includes(q);
    if (!matched) matched = idxs.some(i => searchBlobForIdx(i).includes(q));
    if (!matched) continue;
    let score = 3;
    if (exactIdxSN !== null && snKey(sn) === snKey(exactIdxSN)) score = -1;
    else if (q !== "" && snNorm === q) score = 0;
    else if (q !== "" && snNorm.startsWith(q)) score = 1;
    else if (q !== "" && snNorm.includes(q)) score = 2;
    matches.push({sn, score});
  }
  matches.sort((a, b) => (a.score - b.score) || a.sn.localeCompare(b.sn));
  return matches.slice(0, limit || 500);
}

function addOption(sel, value, text) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = text;
  sel.appendChild(opt);
}

function populateTargetDropdown(matches) {
  const sel = $("targetSelect");
  sel.innerHTML = "";
  if (!matches.length) { addOption(sel, "", "No matches"); return; }
  matches.forEach(m => addOption(sel, m.sn, targetLabel(m.sn)));
}

function runTargetSearch(selectFirst) {
  const q = $("targetSearch").value;
  const matches = getMatchedTargets(q, 500);
  populateTargetDropdown(matches);
  const targetSel = $("targetSelect");
  const obsSel = $("obsSelect");
  if (matches.length > 0) {
    if (selectFirst || !targetSel.value) targetSel.value = matches[0].sn;
    $("snSelect").value = targetSel.value;
    populateObsDropdown(targetSel.value);
    if (obsSel.value !== "") updateAll(obsSel.value);
  } else {
    obsSel.innerHTML = "";
    $("status").textContent = "No target matches for search: " + q;
  }
}

function escapeHtml(x) {
  return String(x === null || x === undefined ? "" : x)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}


// A recurrence count describes an OBJECT; similarity/RMSE describe a SPECTRUM PAIR.
// Keep them separate. Each target date displays its actual target spectrum and
// its actual matched spectrum with independent dates and metadata.
let selectedRecurrenceTableToken = 0;
async function renderSelectedRecurrencePairsTable() {
  const el = $("tbl");
  const token = ++selectedRecurrenceTableToken;
  const current = CURRENT_RECURRENCE;
  const object = current && current.shown.find(r => snKey(r.name) === REC_PAIR_SN);
  if (!current || !object) {
    el.innerHTML = '<div class="small-note">No recurrent twin selected.</div>';
    return;
  }
  const matches = sortedRecurrenceMatches(object).filter(m => literaturePass(m.twinIdx));
  const isActive = () => token === selectedRecurrenceTableToken &&
    $("recurrentOnly").checked && CURRENT_RECURRENCE === current &&
    REC_PAIR_SN === snKey(object.name);
  el.innerHTML = '<div class="table-title">Matched spectra: ' +
    escapeHtml(current.targetSN) + ' ↔ ' + escapeHtml(object.name) + '</div>' +
    '<div class="small-note">Loading RMSE for all ' + matches.length +
    ' matched observing dates…</div>';

  // Small batches avoid flooding the browser with simultaneous range requests.
  const entries = [];
  for (let offset=0; offset<matches.length; offset+=8) {
    if (!isActive()) return;
    const batch = matches.slice(offset, offset+8);
    const resolved = await Promise.all(batch.map(async m => {
      try {
        const [target, twin] = await Promise.all([
          prefetchSpectrum(m.targetIdx), prefetchSpectrum(m.twinIdx)
        ]);
        return {m, stats: computeSpectralRMSE(target, twin), error: null};
      } catch(e) {
        return {m, stats: {rmse:null,nCommon:0}, error:String(e)};
      }
    }));
    entries.push(...resolved);
  }
  if (!isActive()) return;

  const cell = (value, mono=false) => '<td' + (mono ? ' class="mono"' : '') +
    '>' + escapeHtml(value === null || value === undefined ? '' : value) + '</td>';
  const date = idx => (DATA.jd_str && DATA.jd_str[idx]) || dateAt(idx) || '—';
  const row = (entry, isTwin) => {
    const {m,stats} = entry;
    const idx = isTwin ? m.twinIdx : m.targetIdx;
    const chosen = recurrenceMatchId(m) === REC_PAIR_EPOCH;
    const role = isTwin ? 'Twin' : 'Target';
    const label = chosen ? ' selected-epoch-row' : '';
    // Similarity and spectral RMSE are measured between the two spectra;
    // display them on the twin row, never as a self-comparison for the target.
    return '<tr class="pair-date-row' + label + '" data-epoch="' +
      escapeHtml(recurrenceMatchId(m)) + '" tabindex="0" title="Select this matching target observation">' +
      cell(role) + cell(DATA.sn[idx]) +
      cell(isTwin ? fmtNum(m.similarity,6) : '—',true) +
      cell(isTwin ? fmtNum(m.distance,6) : '—',true) +
      cell(isTwin ? (stats.rmse === null ? 'NA' : fmtNum(stats.rmse,6)) : '—',true) +
      cell(isTwin ? stats.nCommon : '—',true) +
      cell(fmtNum(DATA.z[idx],6),true) + cell(DATA.obj_type[idx] || '') +
      cell(date(idx)) + cell(instAt(idx)) + cell(sourceAt(idx)) + literaturePhaseHtml(idx) + peakBandHtml(idx) + '</tr>';
  };
  let html = '<div class="table-title">Matched spectra: ' +
    escapeHtml(current.targetSN) + ' ↔ ' + escapeHtml(object.name) + '</div>' +
    '<div class="table-subtitle">' + matches.length + ' matched target observations across ' + object.nEpochs + ' distinct dates. ' +
    'Each matched observation has two rows: the actual target spectrum and its matched twin spectrum. ' +
    'Date, z, type, instrument and source belong to the SN on that row. ' +
    'Cosine and RMSE on the twin row compare that exact pair, using mutually valid bins. ' +
    'Click either row to select and plot that observation.</div>' +
    '<table class="neighbor-table"><thead><tr>' +
    '<th>Role</th><th>SN</th><th>Cosine Similarity</th><th>Cosine Distance</th>' +
    '<th>RMSE</th><th>Common bins</th><th>z</th><th>Type</th><th>Date</th>' +
    '<th>Instrument</th><th>Source</th><th>Phase from Max (d)</th><th>Peak Band</th></tr></thead><tbody>';
  for (const entry of entries) {
    html += row(entry,false) + row(entry,true);
  }
  html += '</tbody></table>';
  el.innerHTML = html;
  el.querySelectorAll('.peak-band-paper').forEach(a => a.addEventListener('click', ev => ev.stopPropagation()));
  el.querySelectorAll('tr[data-epoch]').forEach(tr => {
    const selectDate = () => {
      selectRecurrenceMatch(tr.dataset.epoch);
    };
    tr.addEventListener('click',selectDate);
    tr.addEventListener('keydown',ev => {
      if (ev.key === 'Enter') {ev.preventDefault();selectDate();}
    });
  });

  // Use the SAME observation pairs as the table for the metric panel.
  const cosineMode = $('metricMode').value === 'cosine';
  const valid = entries.filter(o => cosineMode ? Number.isFinite(o.m.similarity) :
    Number.isFinite(o.stats.rmse));
  const values = valid.map(o => cosineMode ? o.m.similarity : o.stats.rmse);
  Plotly.react('rmse',[{
    type:'bar',
    x:valid.map((o,i)=>i+1), y:values,
    text:values.map(x=>x.toFixed(5)), textposition:'auto',
    marker:{color:'#13294B'},
    customdata:valid.map(o=>[date(o.m.targetIdx),date(o.m.twinIdx),instAt(o.m.targetIdx),instAt(o.m.twinIdx),DATA.spec_id[o.m.targetIdx]]),
    hovertemplate:'Target: %{customdata[0]} / %{customdata[2]}<br>Twin: %{customdata[1]} / %{customdata[3]}' +
      '<br>Value: %{y:.6f}<extra></extra>'
  }],{
    title:(cosineMode ? 'Latent cosine similarity' : 'Spectral RMSE') +
      ' — ' + current.targetSN + ' vs ' + object.name,
    autosize:true,margin:{l:60,r:15,t:55,b:140},
    xaxis:{title:'Matched target observation (chronological)',dtick:1,tickmode:'array',
      tickvals:valid.map((o,i)=>i+1),ticktext:valid.map(o=>date(o.m.targetIdx)+' / '+(instAt(o.m.targetIdx)||'NA')),tickangle:-45},
    yaxis:{title:cosineMode?'Cosine similarity':'RMSE (normalized flux)',
      ...(cosineMode ? {} : {rangemode:'tozero'})}
  },{responsive:true});
}

function renderNeighborTable(targetIdx, neighborList, rmseByIdx, targetData, recurrenceMode=false) {
  // This mode displays actual dated spectrum PAIRS.
  if (recurrenceMode) {
    renderSelectedRecurrencePairsTable();
    return;
  }

  const el = $("tbl");
  const targetValid = targetData ? Array.from(targetData.mask).reduce((a,v)=>a+(v?1:0),0) : 0;
  const cell = (v, mono=false) => '<td' + (mono ? ' class="mono"' : '') + '>' + escapeHtml(v) + '</td>';
  const heading = recurrenceMode
    ? 'Recurrent Spectroscopic Twins — Metadata'
    : 'Target + Spectroscopic Twin Spectra';
  const note = recurrenceMode
    ? 'Each row is a recurrent twin object, represented by its highest-similarity match across all observing dates of the selected SN. Cosine metrics and RMSE belong to that matched observation pair, not necessarily the currently selected spectrum. Days matched counts unique target observing dates. Select a row to make its twin spectrum the new target.'
    : 'Cosine similarity = (z_target · z_twin) / (||z_target|| × ||z_twin||), where z denotes the DAEP latent representation. RMSE compares normalized fluxes over mutually valid wavelength bins. Click a twin row, or focus it and press Enter, to make it the new target.';
  let html = '<div class="table-title">' + heading + '</div>' +
    '<div class="table-subtitle">' + note + '</div>' +
    '<table class="neighbor-table"><thead><tr>' +
    (recurrenceMode ? '<th>Object</th>' : '<th>Rank</th>') +
    '<th>SN</th><th>Cosine Similarity</th><th>Cosine Distance</th>' +
    '<th>RMSE</th><th>Common bins</th><th>z</th><th>Type</th><th>Date</th><th>Instrument</th><th>Source</th><th>Phase from Max (d)</th><th>Peak Band</th>' +
    (recurrenceMode ? '<th>Days matched</th><th>Matched target date</th>' : '') +
    '</tr></thead><tbody>';
  const orderedRow = (idx, rankHtml, sim, dist, rmse, nCommon, cls="", extra="") =>
    '<tr class="' + cls + '" data-idx="' + idx + '" tabindex="0">' +
    '<td>' + rankHtml + '</td>' + cell(DATA.sn[idx]) +
    cell(sim,true) + cell(dist,true) + cell(rmse,true) + cell(nCommon,true) +
    cell(fmtNum(DATA.z[idx],6),true) + cell(DATA.obj_type[idx] || "") +
    cell(DATA.jd_str[idx] || dateAt(idx) || "") + cell(instAt(idx)) + cell(sourceAt(idx)) + literaturePhaseHtml(idx) + peakBandHtml(idx) +
    extra + '</tr>';
  if (!recurrenceMode) {
    html += orderedRow(targetIdx, '<span class="rank-pill target-pill">Target</span>',
      '1.000000', '0.000000', '0.000000', targetValid, 'target-row');
  }
  if (!neighborList.length) {
    html += '<tr><td colspan="' + (recurrenceMode ? 15 : 13) + '" class="muted">' +
      (recurrenceMode ? 'No recurrent twins at these settings. Try a smaller minimum observing-date count or larger recurrence top-k.'
                      : 'No neighbors to display with the current filters.') + '</td></tr>';
  } else {
    neighborList.forEach((o,r) => {
      const stats = rmseByIdx.get(o.j) || {rmse:null,nCommon:0};
      const extra = recurrenceMode
        ? cell(o.matchedDays + ' / ' + o.matchedTotal, true) +
          cell(DATA.jd_str[o.matchedTargetIdx] || dateAt(o.matchedTargetIdx) || '')
        : '';
      html += orderedRow(o.j, recurrenceMode
        ? (snKey(DATA.sn[o.j]) === REC_PAIR_SN ? '<strong>Selected</strong>' : '—')
        : '<span class="rank-pill">'+(r+1)+'</span>',
        fmtNum(1-o.d,6),fmtNum(o.d,6),fmtNum(stats.rmse,6)||'NA',stats.nCommon,'',extra);
    });
  }
  html += '</tbody></table>';
  el.innerHTML = html;
  el.querySelectorAll('.peak-band-paper').forEach(a => a.addEventListener('click', ev => ev.stopPropagation()));
  el.querySelectorAll('tr[data-idx]').forEach(tr => {
    const go=()=>updateAll(parseInt(tr.dataset.idx));
    tr.addEventListener('click',go);
    tr.addEventListener('keydown',evt=>{if(evt.key==='Enter'){evt.preventDefault();go();}});
  });
}

function populateSnDropdown(snList) {
  const sel = $("snSelect");
  sel.innerHTML = "";
  snList.forEach(sn => addOption(sel, sn, targetLabel(sn)));
}

function ensureTargetOption(sn) {
  const targetSel = $("targetSelect");
  if (!Array.from(targetSel.options).some(opt => opt.value === sn)) addOption(targetSel, sn, targetLabel(sn));
}

function setDropdownsFromIdx(i) {
  const sn = canonicalSNName(DATA.sn[i]);
  ensureTargetOption(sn);
  $("targetSelect").value = sn;
  $("snSelect").value = sn;
  populateObsDropdown(sn);
  $("obsSelect").value = String(i);
}

function populateObsDropdown(sn) {
  const sel = $("obsSelect");
  sel.innerHTML = "";
  if (UPLOADED_TARGETS.has(sn)) {
    for (const obj of UPLOADED_TARGETS.get(sn)) addOption(sel, obj.id, obj.filename);
    if (sel.options.length) sel.value = sel.options[0].value;
    return;
  }
  sortObsIdxList(SN_TO_IDX[sn] || []).forEach(i => addOption(sel, String(i), obsLabelForIdx(i)));
  if (sel.options.length > 0) sel.value = sel.options[0].value;
}

function latentLayout() {
  const note = document.getElementById("latentColorNote");
  if (note) {
    note.textContent =
      "Colored by: " + colorFieldLabel(CURRENT_COLOR_FIELD) +
      (literatureFilterActive() ? " · Literature Phase Filter" : "");
  }
  return {
    title: "Spectral Latent Space",
    autosize: true,
    margin: {l: 45, r: 10, t: 40, b: 115},
    xaxis: {title: "UMAP X"},
    yaxis: {title: "UMAP Y"},
    legend: {orientation: "h", x: 0, xanchor: "left", y: -0.35, yanchor: "top"},
    uirevision: "latent-zoom-lock"
  };
}

function initLatentPlot() {
  latentBaseTraces = buildBaseLatentTraces();
  Plotly.newPlot("latent", latentBaseTraces, latentLayout(), {responsive: true});
  $("latent").on("plotly_click", evt => {
    if (evt && evt.points.length > 0) {
      const idx = evt.points[0].customdata;
      if (idx !== undefined && idx !== null) updateAll(idx);
    }
  });
}

function updateLatentHighlight(i, list, nLatent) {
  const twins = list.slice(0, clamp(nLatent, 1, list.length)).map(o => o.j);
  const overlayTraces = [
    {
      type: "scattergl", mode: "markers",
      x: twins.map(j => DATA.Y[j][0]), y: twins.map(j => DATA.Y[j][1]),
      text: twins.map(j => reviewedSpectrumHover(j)), hoverinfo: "text", name: "top twins",
      marker: {size: 12, symbol: "circle", color: "#FFD400", opacity: 1, line: {width: 2.5, color: "#111111"}}
    },
    {
      type: "scattergl", mode: "markers",
      x: [DATA.Y[i][0]], y: [DATA.Y[i][1]], text: [reviewedSpectrumHover(i)], hoverinfo: "text", name: "target",
      marker: {size: 14, symbol: "star", color: "#000000", opacity: 1, line: {width: 1, color: "#ffffff"}}
    }
  ];
  Plotly.react("latent", latentBaseTraces.concat(overlayTraces), latentLayout(), {responsive: true});
}

function resizePlots(ids) {
  ids.forEach(id => { const el = $(id); if (el && el.data) Plotly.Plots.resize(el); });
}

function toggleExpand(cardId) {
  const card = $(cardId);
  if (!card) return;
  const wasExpanded = card.classList.contains("expanded");
  document.querySelectorAll("#twins-app .panel-card.expanded").forEach(c => {
    c.classList.remove("expanded");
    const b = c.querySelector(".expandBtn");
    if (b) b.textContent = "⛶ Expand";
  });
  document.body.classList.remove("twins-no-scroll");
  if (!wasExpanded) {
    card.classList.add("expanded");
    document.body.classList.add("twins-no-scroll");
    const btn = card.querySelector(".expandBtn");
    if (btn) btn.textContent = "✕ Restore";
  }
  setTimeout(() => resizePlots(["latent", "spec", "rmse", "recurrencePairPlot"]), 60);
}

function setLayoutMode(mode) {
  const grid = $("dashboardGrid");
  grid.classList.remove("layout-stack", "layout-2x2");
  grid.classList.add(mode === "2x2" ? "layout-2x2" : "layout-stack");
  setTimeout(() => resizePlots(["latent", "spec", "rmse"]), 60);
}

function metaForDownload(i) {
  return {
    idx: i,
    sn: DATA.sn[i],
    spec_id: DATA.spec_id[i],
    obj_type: DATA.obj_type[i],
    raw_type: DATA.raw_type[i],
    canonical_type: DATA.canonical_type[i],
    broad_type: DATA.broad_type[i],
    source_dataset: sourceAt(i),
    instrument: instAt(i),
    obs_date: dateAt(i),
    jd: DATA.jd[i],
    phase: DATA.phase[i],
    literature_phase: literaturePhase(i),
    literature_phase_source: literatureAt(i) ? literatureAt(i)[1] : null,
    literature_phase_citation: literatureAt(i) ? literatureAt(i)[2] : null,
    literature_phase_doi: literatureAt(i) ? literatureAt(i)[3] : null,
    peak_mjd: literatureAt(i) ? literatureAt(i)[4] : null,
    peak_band: literatureAt(i) ? literatureAt(i)[5] : null,
    z: DATA.z[i],
    umap: DATA.Y[i],
  };
}

function safeFilenamePart(s) {
  return String(s || "target").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "target";
}

function downloadSelectedOutputs() {
  if (!CURRENT_SELECTION) { alert("Select a target first."); return; }
  const withArrays = o => Object.assign({}, o, {
    flux: Array.from(o.flux),
    mask: Array.from(o.mask, v => Boolean(v)),
  });
  const payload = {
    created_at: new Date().toISOString(),
    bundle_id: CONFIG.bundleId,
    note: "Target plus currently overlaid twins. RMSE uses common valid normalized-flux bins.",
    wave_angstrom: Array.from(DATA.wave || []),
    target: withArrays(CURRENT_SELECTION.target),
    twins: CURRENT_SELECTION.twins.map(withArrays),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "twins_selection_" + safeFilenamePart(CURRENT_SELECTION.target.meta.sn) +
    "_idx" + CURRENT_SELECTION.target.meta.idx + ".json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderRMSEPanel(targetIdx, twinEntries) {
  CURRENT_METRIC_ENTRIES = {targetIdx, twinEntries};
  const cosineMode = $("metricMode").value === "cosine";
  const good = twinEntries.filter(o => cosineMode ? Number.isFinite(o.distance) : (o.stats && Number.isFinite(o.stats.rmse)));
  const metricLabel = cosineMode ? "Latent Cosine Similarity" : "Spectral RMSE";
  if (!good.length) {
    Plotly.react("rmse", [], {
      title: metricLabel, autosize: true, margin: {l: 65, r: 15, t: 55, b: 140},
      annotations: [{text: "No valid twin metric values", xref: "paper", yref: "paper", x: 0.5, y: 0.5, showarrow: false}],
      xaxis: {visible: false}, yaxis: {visible: false}
    }, {responsive: true});
    return;
  }
  const labels = good.map(o => "#" + (o.rank + 1) + " " + (DATA.sn[o.j] || ("idx=" + o.j)));
  const vals = good.map(o => cosineMode ? 1 - o.distance : o.stats.rmse);
  const hover = good.map(o =>
    "SN=" + (DATA.sn[o.j] || "") + "<br>idx=" + o.j +
    "<br>Cosine similarity=" + (1 - o.distance).toFixed(6) +
    "<br>Latent distance=" + Number(o.distance).toFixed(6) +
    "<br>RMSE=" + (o.stats && Number.isFinite(o.stats.rmse) ? o.stats.rmse.toFixed(6) : "NA") +
    "<br>common bins=" + (o.stats ? o.stats.nCommon : 0));
  Plotly.react("rmse", [{
    type: "bar", x: labels, y: vals, text: vals.map(v => v.toFixed(5)), textposition: "auto",
    hovertext: hover, hoverinfo: "text", marker: {color: "#13294B"}
  }], {
    title: metricLabel + " — " + (DATA.sn[targetIdx] || "Target"),
    autosize: true, margin: {l: 65, r: 15, t: 55, b: 140},
    xaxis: {title: "Displayed twins", tickangle: -35},
    yaxis: Object.assign({title: cosineMode ? "Cosine similarity (latent)" : "RMSE (normalized flux)"},
                         cosineMode ? {} : {rangemode: "tozero"})
  }, {responsive: true});
}

// Fetch one spectrum's row from the spectra file. The promise is cached so
// concurrent requests for the same row share one request; a failed fetch is
// evicted so the next click retries it.
function prefetchSpectrum(i) {
  if (specCache.has(i)) return specCache.get(i);
  const W = DATA.W;
  const ROW = 9 * W; // flux (4W) + recon (4W) + mask (W) bytes
  const offset = i * ROW;
  const p = (async () => {
    const controller = new AbortController();
    const r = await fetch(SPEC_URL, {
      headers: {Range: `bytes=${offset}-${offset + ROW - 1}`},
      signal: controller.signal,
    });
    if (r.status !== 206) {
      // Without range support the server would stream the whole 233 MB file.
      controller.abort();
      throw new Error("spectra request returned " + r.status + " instead of a byte range");
    }
    const total = parseInt(((r.headers.get("Content-Range") || "").split("/")[1]) || "", 10);
    if (total !== DATA.N * ROW) {
      controller.abort();
      throw new Error("the spectra file does not match the loaded metadata");
    }
    // A release with the same N and W passes the size check, so a changed
    // file identity is the only sign its rows no longer match this metadata.
    const etag = r.headers.get("ETag");
    if (SPEC_ETAG === null) SPEC_ETAG = etag;
    else if (etag !== SPEC_ETAG) {
      controller.abort();
      throw new Error("the spectral twins data was updated since this page loaded");
    }
    const buf = await r.arrayBuffer();
    return {
      flux: new Float32Array(buf, 0, W),
      recon: new Float32Array(buf, 4 * W, W),
      mask: new Uint8Array(buf, 8 * W, W),
    };
  })();
  specCache.set(i, p);
  p.catch(() => specCache.delete(i));
  return p;
}

function twinLabel(prefix, j) {
  return prefix + " " + (DATA.sn[j] || "") +
    " | " + (DATA.jd_str[j] || DATA.obs_date[j] || "date NA") +
    " | " + (instAt(j) || "instrument NA") +
    " | " + (DATA.obj_type[j] || "type NA") +
    " | z=" + (fmtNum(DATA.z[j], 6) || "NA");
}

async function updateAll(i) {
  if (String(i).startsWith('upload:')) return renderUploadedSpectrum(String(i));
  LAST_UPLOAD_LATENT_KEY=null;
  i = clamp(parseInt(i), 0, DATA.N - 1);
  setDropdownsFromIdx(i);

  const excludeSameSN = $("excludeSameSN").checked;
  const uniqueTwinSN = $("uniqueTwinSN").checked;
  const kShow = clamp(parseInt($("kInput").value) || DATA.k_show_default, 2, DATA.K);
  $("kInput").value = kShow;
  const nLatent = parseInt($("latentN").value);
  const nSpec = parseInt($("specN").value);
  $("latentNVal").textContent = String(nLatent);
  $("specNVal").textContent = String(nSpec);

  const recurrentKeys = renderRecurrence(i);
  const onlyRecurrent = $("recurrentOnly").checked;
  // The plots continue to compare the SELECTED observation against its actual
  // nearest neighbors. The table switches to the across-epoch recurrence set.
  const nbrList = getNeighborList(i, excludeSameSN, uniqueTwinSN);
  const plotNeighbors = onlyRecurrent
    ? nbrList.filter(o => recurrentKeys.has(snKey(DATA.sn[o.j])))
    : nbrList;
  updateLatentHighlight(i, plotNeighbors, nLatent);
  // Show representative best match per
  // recurrent object, even when it isn't a neighbor of the current observation.
  const recurrenceRows = onlyRecurrent && CURRENT_RECURRENCE
    ? CURRENT_RECURRENCE.shown.map(r => {
        const selected = snKey(r.name) === REC_PAIR_SN;
        const m = (selected && r.matches.find(x => recurrenceMatchId(x) === REC_PAIR_EPOCH)) || r.bestMatches[0];
        return {j:m.twinIdx, d:m.distance, matchedTargetIdx:m.targetIdx,
                matchedDays:r.nEpochs, matchedTotal:CURRENT_RECURRENCE.rec.totalEpochs};
      })
    : [];
  // Put the selected recurrent twin at the top of the metadata table.
  // Preserve the original order of all other recurrent twins.
  const orderedRecurrenceRows = [...recurrenceRows].sort((a, b) => {
    const aSelected = snKey(DATA.sn[a.j]) === REC_PAIR_SN;
    const bSelected = snKey(DATA.sn[b.j]) === REC_PAIR_SN;
    return Number(bSelected) - Number(aSelected);
  });

  const tableList = (
    onlyRecurrent ? orderedRecurrenceRows : nbrList
  ).slice(0, kShow);
  const overlayList = plotNeighbors.slice(0, clamp(nSpec, 1, plotNeighbors.length));

  activeUpdateToken += 1;
  const myToken = activeUpdateToken;
  const statusEl = $("status");
  statusEl.textContent = "Loading target and twin spectra…";

  const fetchIdx = Array.from(new Set([
    i,
    ...tableList.map(o => o.j),
    ...tableList.filter(o => o.matchedTargetIdx !== undefined).map(o => o.matchedTargetIdx),
    ...overlayList.map(o => o.j)
  ]));
  const results = await Promise.allSettled(fetchIdx.map(prefetchSpectrum));
  if (myToken !== activeUpdateToken) return;

  if (results[0].status === "rejected") {
    statusEl.textContent = "Could not load the target spectrum (" + results[0].reason.message +
      "). If the data was just updated, reload the page.";
    return;
  }
  const dataByIdx = new Map();
  results.forEach((r, k) => { if (r.status === "fulfilled") dataByIdx.set(fetchIdx[k], r.value); });
  const targetData = dataByIdx.get(i);

  const rmseByIdx = new Map();
  tableList.forEach(o => {
    const td = dataByIdx.get(o.j);
    if (td) {
      // Recurrence matches can be from a different epoch. Compare the paired
      // target spectrum, NOT the currently selected target observation.
      const pairedTarget = o.matchedTargetIdx === undefined
        ? targetData : dataByIdx.get(o.matchedTargetIdx);
      if (pairedTarget) rmseByIdx.set(o.j, computeSpectralRMSE(pairedTarget, td));
    }
  });
  const goodTwins = overlayList
    .map((o, rank) => ({j: o.j, distance: o.d, rank, data: dataByIdx.get(o.j), stats: rmseByIdx.get(o.j)}))
    .filter(o => o.data);

  const specData = [
    spectraTrace(targetData, twinLabel("target", i), null),
  ];
  goodTwins.forEach(({j, data, rank}) => {
    specData.push(spectraTrace(data, twinLabel("twin#" + (rank + 1), j), rank === 0 ? "dot" : "dash"));
  });
  // Only observed/processed spectra are plotted; reconstruction arrays remain in the bundle.
  Plotly.react("spec", specData, {
    title: "Target and Twin Spectra",
    autosize: true,
    margin: {l: 55, r: 15, t: 50, b: 150},
    xaxis: {title: daepWavelengthLabel()},
    yaxis: {title: "Normalized flux"},
    legend: {orientation: "h", x: 0, xanchor: "left", y: -0.35, yanchor: "top"}
  }, {responsive: true});

  renderRMSEPanel(i, goodTwins);
  renderNeighborTable(i, tableList, rmseByIdx, targetData, onlyRecurrent);

  CURRENT_SELECTION = {
    // Typed arrays are kept as-is; the download converts them.
    target: {meta: metaForDownload(i), flux: targetData.flux, mask: targetData.mask},
    twins: goodTwins.map(o => ({
      rank: o.rank + 1,
      latent_distance: o.distance,
      spectral_rmse: o.stats ? o.stats.rmse : null,
      latent_cosine_similarity: 1 - o.distance,
      n_common_bins: o.stats ? o.stats.nCommon : 0,
      common_fraction: o.stats ? o.stats.commonFraction : 0,
      meta: metaForDownload(o.j),
      flux: o.data.flux,
      mask: o.data.mask,
    })),
  };

  const failedTwins = tableList.filter(o => !dataByIdx.has(o.j)).length;
  const best = goodTwins.length > 0 ? goodTwins[0] : null;
  let statusText =
    "Selected: SN=" + (DATA.sn[i] || "") + "  SpecID=" + (DATA.spec_id[i] || "") +
    "  date=" + (DATA.jd_str[i] || DATA.obs_date[i] || "") + "  jd=" + fmtNum(DATA.jd[i], 3) +
    "  phase from max=" + (literaturePhase(i) === null ? "—" : fmtNum(literaturePhase(i), 2) + " d") + "  z=" + fmtNum(DATA.z[i], 6) +
    "  type=" + (DATA.obj_type[i] || "") + "  idx=" + i;
  if (best) {
    statusText += " | Best twin: SN=" + (DATA.sn[best.j] || "") + "  SpecID=" + (DATA.spec_id[best.j] || "") +
      "  date=" + (DATA.jd_str[best.j] || DATA.obs_date[best.j] || "") +
      "  phase from max=" + (literaturePhase(best.j) === null ? "—" : fmtNum(literaturePhase(best.j), 2) + " d") + "  z=" + fmtNum(DATA.z[best.j], 6) +
      "  idx=" + best.j + "  distance=" + Number(best.distance).toFixed(6) +
      (best.stats && Number.isFinite(best.stats.rmse) ? "  RMSE=" + best.stats.rmse.toFixed(6) : "");
  }
  if (failedTwins > 0) {
    statusText += "  |  " + failedTwins + " twin" + (failedTwins === 1 ? "" : "s") + " skipped due to a fetch error";
  }
  statusEl.textContent = statusText;
}

// Each upload and ZIP member receives independent metadata; nothing is retained on refresh.
let UPLOAD_PREVIEW=[];
const SWITCH_UPLOAD_FLAG = 'astrodash-twins-explicit-model-switch';
const SWITCH_UPLOAD_DB = 'astrodash-twins-switch-files-v1';
function uploadSwitchDB() {
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open(SWITCH_UPLOAD_DB,1);
    request.onupgradeneeded=()=>{
      if(!request.result.objectStoreNames.contains('files'))request.result.createObjectStore('files');
    };
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(request.error||new Error('Cannot open upload cache'));
  });
}
async function uploadSwitchStore(files) {
  const db=await uploadSwitchDB();
  try {
    await new Promise((resolve,reject)=>{
      const tx=db.transaction('files','readwrite');
      tx.objectStore('files').put(Array.from(files),'pending');
      tx.oncomplete=resolve;
      tx.onerror=()=>reject(tx.error||new Error('Could not save uploaded files'));
      tx.onabort=()=>reject(tx.error||new Error('Upload file preservation was interrupted'));
    });
  } finally {db.close();}
}
async function uploadSwitchTake() {
  const db=await uploadSwitchDB();
  try {
    return await new Promise((resolve,reject)=>{
      const tx=db.transaction('files','readwrite');
      const store=tx.objectStore('files');
      const req=store.get('pending');
      let files=[];
      req.onsuccess=()=>{files=req.result||[];store.delete('pending');};
      tx.oncomplete=()=>resolve(files);
      tx.onerror=()=>reject(tx.error||new Error('Could not restore uploaded files'));
      tx.onabort=()=>reject(tx.error||new Error('Upload restoration interrupted'));
    });
  } finally {db.close();}
}
async function restoreUploadsAfterModelSwitch(input,status,readPreview) {
  const deliberate=sessionStorage.getItem(SWITCH_UPLOAD_FLAG)==='1';
  sessionStorage.removeItem(SWITCH_UPLOAD_FLAG);
  const files=await uploadSwitchTake();
  if(!deliberate||!files.length)return;
  const dt=new DataTransfer();
  for(const file of files)dt.items.add(file);
  input.files=dt.files;
  status.textContent='Re-encoding retained uploads for the selected model…';
  await readPreview();
}
function uploadRecommendedMode(row) {
  const frame=row.input_frame, hasZ=row.redshift!==null && row.redshift!==undefined;
  if (frame==='rest') return hasZ?'rest_frame':null;
  if (frame==='observer') return hasZ?'rest_frame':'observer_frame';
  return null;
}
function uploadScientificEligibility() {
  const hasFiles = UPLOAD_PREVIEW.some(row=>row.status==='parsed');
  const supported = ['rest_frame','observer_frame','multi_object_rest'].includes($('modelMode').value);
  $('uploadMatchNow').disabled = !(hasFiles && supported) || UPLOAD_BUSY;
  return hasFiles && supported;
}
function initUploadedSpectra() {
  const form=$('twinsUploadForm'), input=$('twinsUploadFiles'),panel=$('uploadMetadataPanel');
  if (!form||!input)return;
  input.value='';
  let generation=0;
  const box=$('uploadFileMetadata'),status=$('twinsUploadStatus'),results=$('twinsUploadResults');
  async function buildRows(rows) {
    box.replaceChildren();
    const mode=$('modelMode').value;
    const note=document.createElement('p');
    note.className='small-note';
    note.textContent=mode==='rest_frame'
      ? 'Rest-frame: make sure your uploaded wavelengths are already de-redshifted. MW and air/vacuum corrections are not applied to uploads.'
      : 'MW and air/vacuum corrections are not applied to uploaded spectra.';
    box.appendChild(note);
    for (const row of rows) {
      const line=document.createElement('div');
      line.style.cssText='padding:5px 0;border-bottom:1px solid #ddd';
      line.textContent=(row.object_name||row.filename)+' — '+row.filename +
        (row.status==='parsed'?'': ' — rejected: '+row.reason);
      box.appendChild(line);
    }
    if(mode==='multi_object_rest') {
      const message=document.createElement('div');
      message.textContent='Multi-Object uses rest-frame wavelengths; upload spectra that are already de-redshifted.';
      box.appendChild(message);
    }
    uploadScientificEligibility();
    if(uploadScientificEligibility()) await processUploads();
  }
  let uploadRun=0;
  let uploadController=null;
  async function readPreview() {
    const own=++generation;
    // Ignore outdated upload results.
    ++uploadRun;
    if(uploadController){uploadController.abort();uploadController=null;}
    UPLOAD_BUSY=false;
    UPLOAD_PREVIEW=[];box.replaceChildren();results.replaceChildren();
    panel.style.display=input.files.length?'':'none';
    if(!input.files.length)return;
    UPLOAD_CURRENT_FILES=Array.from(input.files);
    const saved=new Map(UPLOAD_SOURCE_FILES.map(f=>[f.name+'|'+f.size+'|'+f.lastModified,f]));
    for(const f of UPLOAD_CURRENT_FILES)saved.set(f.name+'|'+f.size+'|'+f.lastModified,f);
    UPLOAD_SOURCE_FILES=[...saved.values()];
    status.textContent='Reading spectrum metadata…';
    const fd=new FormData(form);
    try {
      const resp=await fetch(window.ASTRODASH_TWINS_ENDPOINTS.preflight,{method:'POST',body:fd,credentials:'same-origin'});
      const body=await resp.json();
      if(!resp.ok)throw new Error(body.error||'Could not read uploads');
      if(own!==generation)return;
      UPLOAD_PREVIEW=body.results||[];
      status.textContent='';await buildRows(UPLOAD_PREVIEW);
    }catch(e){if(own===generation)status.textContent='Upload inspection failed: '+(e.message||e);}
  }
  async function processUploads() {
    if(UPLOAD_BUSY||!uploadScientificEligibility())return;
    const files=UPLOAD_CURRENT_FILES.slice();
    if(!files.length){status.textContent='Choose at least one spectrum first.';return;}
    const currentRun=++uploadRun;
    UPLOAD_BUSY=true;
    uploadScientificEligibility();
    const controller=new AbortController();
    uploadController=controller;
    status.textContent='Encoding spectra and searching reference twins…';
    $('uploadMatchNow').disabled=true;results.replaceChildren();
    const overrides={}; // No per-file correction questions are required.
    const payload=new FormData(form);
    // Use saved files when the input is empty.
    payload.delete('spectra');
    for(const file of files)payload.append('spectra',file,file.name);
    payload.set('dataset',$('modelMode').value);
    payload.set('metadata_overrides',JSON.stringify(overrides));
    try {
      const reply=await fetch(form.action,{method:'POST',body:payload,credentials:'same-origin',signal:controller.signal});
      const body=await reply.json();
      if(!reply.ok)throw new Error(body.error||'Matching failed');
      if(currentRun!==uploadRun)return;
      let count=0;
      for(const result of body.results||[]) {
        if(result.status!=='matched') {
          const p=document.createElement('div');p.textContent=result.filename+': '+(result.reason||'Not matched');results.appendChild(p);continue;
        }
        const name=result.object_name||result.filename.replace(/\.[^.]+$/,'');
        const key='Uploaded: '+name;
        // Replace existing matches for the same file.
        for(const [oldKey,oldRows] of UPLOADED_TARGETS){
          const kept=oldRows.filter(o=>o.filename!==result.filename);
          if(kept.length)UPLOADED_TARGETS.set(oldKey,kept);
          else UPLOADED_TARGETS.delete(oldKey);
        }
        const obs={...result,id:'upload:'+(NEXT_UPLOAD_ID++),key};
        if(!UPLOADED_TARGETS.has(key))UPLOADED_TARGETS.set(key,[]);
        UPLOADED_TARGETS.get(key).push(obs);count++;
      }
      if(count) {
        populateSnDropdown([...SORTED_SNS,...UPLOADED_TARGETS.keys()]);
        updateUploadOnlySearch(false);
        const latest=[...UPLOADED_TARGETS.keys()].at(-1);ensureTargetOption(latest);
        $('targetSelect').value=latest;$('snSelect').value=latest;
        populateObsDropdown(latest);updateAll($('obsSelect').value);
        updateUploadOnlySearch(false);
        const chosen=$('obsSelect').value; if(chosen)$('uploadOnlySelect').value=chosen;
        status.textContent=count+' uploaded observation'+(count===1?'':'s')+' matched.';
      } else status.textContent='No spectra matched. See file-specific errors.';
    } catch(e){
      if(currentRun===uploadRun&&e.name!=='AbortError')status.textContent='Upload failed: '+(e.message||e);
    } finally {
      if(currentRun===uploadRun){
        UPLOAD_BUSY=false;uploadController=null;uploadScientificEligibility();
        // Keep files for future matches and model switches.
        panel.style.display='';
      }
    }
  }
  input.addEventListener('change',readPreview);
  $('uploadMatchNow').addEventListener('click',processUploads);
  $('modelMode').addEventListener('change',uploadScientificEligibility);
  // Plot updates are handled by the shared controls.
  restoreUploadsAfterModelSwitch(input, status, readPreview).catch(e=>{status.textContent='Upload restore failed: '+e.message;});
}
/* UPLOAD_FILTERED_TWINS_V1 */
function uploadedTargetLegendLabel(target) {
  const filename=String(target.filename||'');
  const name=String(target.object_name||filename.replace(/\.[^.]+$/,'')||'Unknown');
  const match=filename.match(/(?:^|[_\s])((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?=[_T\s.\-]|$)/);
  const date=target.obs_date||(match?match[2]+'/'+match[3]+'/'+match[1].slice(2):'date NA');
  const instrument=target.instrument||'instrument NA';
  const type=target.raw_type||target.obj_type||'type NA';
  const z=target.redshift===null||target.redshift===undefined||target.redshift===''?null:Number(target.redshift);
  return 'uploaded '+name+' | '+date+' | '+instrument+' | '+type+' | z='+(z!==null&&Number.isFinite(z)?z.toFixed(6):'NA');
}
function uploadTwinCandidates(target) {
  const unique=$('uniqueTwinSN').checked;
  const exclude=$('excludeSameSN').checked;
  const seen=new Set(), out=[];
  const own=reviewedSNIdentity(target.object_name||target.filename||'');
  for(const item of target.twins||[]) {
    const i=Number(item.reference_index);
    if(!Number.isInteger(i)||i<0||i>=DATA.N)continue;
    const sn=reviewedSNIdentity(DATA.sn[i]);
    if(exclude && sn===own)continue;
    if(!literaturePass(i))continue;
    if(unique && seen.has(sn))continue;
    seen.add(sn);
    out.push(item);
  }
  return out;
}
async function renderUploadedSpectrum(id) {
  const target=[...UPLOADED_TARGETS.values()].flat().find(x=>x.id===id);
  if(!target)return;
  const token=++activeUpdateToken;
  CURRENT_SELECTION=null;
  CURRENT_METRIC_ENTRIES=null;
  $('targetSelect').value=target.key;
  $('snSelect').value=target.key;
  if($('obsSelect').value!==id){populateObsDropdown(target.key);$('obsSelect').value=id;}
  const eligible=uploadTwinCandidates(target);
  const available=(target.twins||[]).length;
  const maxCandidates=Math.max(1,eligible.length);
  const k=clamp(parseInt($('kInput').value,10)||10,1,maxCandidates);
  const nOverlay=clamp(parseInt($('specN').value,10)||5,1,maxCandidates);
  const nLatent=clamp(parseInt($('latentN').value,10)||10,1,maxCandidates);
  $('specNVal').textContent=String(nOverlay);
  $('latentNVal').textContent=String(nLatent);
  // Independent table, spectral overlay, and UMAP-highlight counts.
  const chosen=eligible.slice(0,Math.max(k,nOverlay,nLatent));
  const arrivals=await Promise.allSettled(chosen.map(x=>prefetchSpectrum(x.reference_index)));
  if(token!==activeUpdateToken)return;
  const loaded=new Map();
  chosen.forEach((o,j)=>{if(arrivals[j].status==='fulfilled')loaded.set(o.reference_index,arrivals[j].value);});
  const mine={flux:target.flux,mask:target.mask};
  if(!mine.flux||!mine.mask||mine.flux.length!==1320||mine.mask.length!==1320) {
    $('status').textContent='Uploaded input must have the 1320-bin DAEP training grid; upload again.';
    return;
  }
  const uploadWave=Array.from({length:1320},(_,i)=>3202.5+5*i);
  // Explicit upload coordinates, never the 660-bin reference-display grid.
  const trace={x:[],y:[],mode:'lines',name:uploadedTargetLegendLabel(target)};
  for(let i=0;i<1320;i++)if(mine.mask[i]&&Number.isFinite(Number(mine.flux[i]))){
    trace.x.push(uploadWave[i]);trace.y.push(Number(mine.flux[i]));
  }
  trace.hovertemplate='Uploaded: '+escapeHtml(target.filename)+'<br>%{x:.1f} Å, %{y:.3f}<extra></extra>';
  const overlay=eligible.slice(0,nOverlay).filter(o=>loaded.has(o.reference_index));
  const lines=[trace];
  overlay.forEach((o,j)=>lines.push(spectraTrace(loaded.get(o.reference_index),twinLabel('twin#'+(j+1),o.reference_index))));
  Plotly.react('spec',lines,{
    title:'Uploaded Spectrum & Nearest Neighbors',autosize:true,
    margin:{l:70,r:20,t:50,b:155},
    legend:{orientation:'h',x:0,y:-0.22,yanchor:'top',font:{size:11}},
    xaxis:{title:daepWavelengthLabel()},yaxis:{title:'Normalized flux'}
  },{responsive:true});
  // Match each reference wavelength to the original uploaded 5-Angstrom bin center.
  const onRef={flux:[],mask:[]};
  for(const w of DATA.wave){
    const j=Math.round((Number(w)-3202.5)/5);
    const aligned=j>=0&&j<1320&&Math.abs(uploadWave[j]-Number(w))<0.01;
    onRef.flux.push(aligned?Number(mine.flux[j]):0);
    onRef.mask.push(Boolean(aligned&&mine.mask[j]));
  }
  const stats=new Map();
  for(const o of chosen)if(loaded.has(o.reference_index))
    stats.set(o.reference_index,computeSpectralRMSE(onRef,loaded.get(o.reference_index)));
  const tableEntries=eligible.slice(0,k);
  const cosineMode=$('metricMode').value==='cosine';
  const plotted=tableEntries.filter(o=>stats.has(o.reference_index));
  Plotly.react('rmse',[{type:'bar',
    x:plotted.map((o,j)=>'Twin '+(j+1)+' '+DATA.sn[o.reference_index]),
    y:plotted.map(o=>cosineMode?Number(o.cosine_similarity):stats.get(o.reference_index).rmse)
  }],{title:cosineMode?'1024D cosine similarity':'Spectral RMSE',autosize:true,
      margin:{l:70,r:10,t:45,b:110},xaxis:{tickangle:-30}}, {responsive:true});
  const table=$('tbl');table.replaceChildren();
  const heading=document.createElement('p');
  heading.textContent='Uploaded target: '+target.filename+' — '+available+' candidate neighbors before filters';
  table.appendChild(heading);
  const body=document.createElement('table');body.className='neighbor-table';
  body.innerHTML='<thead><tr><th>Rank</th><th>SN</th><th>Cosine similarity</th><th>RMSE</th><th>Common bins</th><th>z</th><th>Type</th><th>Date</th><th>Instrument</th><th>Source</th><th>Phase from Max (d)</th><th>Peak Band</th></tr></thead>';
  const tbody=document.createElement('tbody');
  tableEntries.forEach((o,j)=>{
    const i=o.reference_index, stat=stats.get(i);
    const tr=document.createElement('tr');tr.tabIndex=0;tr.style.cursor='pointer';
    const v=[j+1,DATA.sn[i],Number(o.cosine_similarity).toFixed(6),
      stat&&stat.rmse!==null?stat.rmse.toFixed(6):'—',stat?stat.nCommon:'—',
      fmtNum(DATA.z[i],6),DATA.obj_type[i]||'',DATA.jd_str[i]||dateAt(i)||'',instAt(i),sourceAt(i)];
    for(const item of v){const td=document.createElement('td');td.textContent=String(item);tr.appendChild(td);}
    // Reuse authoritative reviewed-catalog phase and paper citation cells.
    tr.insertAdjacentHTML('beforeend',literaturePhaseHtml(i)+peakBandHtml(i));
    tr.querySelectorAll('.peak-band-paper').forEach(a=>a.addEventListener('click',ev=>ev.stopPropagation()));
    tr.addEventListener('click',()=>updateAll(i));
    tr.addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();updateAll(i);}});
    tbody.appendChild(tr);
  });
  body.appendChild(tbody);table.appendChild(body);
  if(!tableEntries.length){const p=document.createElement('p');p.textContent='No uploaded twins pass the selected filters.';table.appendChild(p);}
  // Highlight filtered references independently of table and overlay counts.
  const highlighted=eligible.slice(0,nLatent);
  const y=highlighted.map(o=>DATA.Y[o.reference_index]);
  const highlightKey=String(CURRENT_COLOR_FIELD)+'|'+highlighted.map(o=>o.reference_index).join(',');
  if(highlightKey!==LAST_UPLOAD_LATENT_KEY){
    LAST_UPLOAD_LATENT_KEY=highlightKey;
    Plotly.react('latent',[...latentBaseTraces,{type:'scattergl',mode:'markers',name:'Uploaded target twins',
      x:y.map(p=>p[0]),y:y.map(p=>p[1]),marker:{size:11,symbol:'circle-open',line:{width:2}}}],
      latentLayout(),{responsive:true});
  }
  const loadedOverlay=overlay.length;
  $('status').textContent='Uploaded '+target.filename+' — '+eligible.length+' of '+available+
    ' candidates pass filters; '+loadedOverlay+' spectra displayed; '+tableEntries.length+
    ' table rows; '+highlighted.length+' UMAP highlights. Uploaded target UMAP coordinate unavailable.';
}

function init() {
  enforceBeeReviewedType();
  TYPE_COLORS = buildGlobalTypeColorMap();
  TYPE_SYMBOLS = buildGlobalTypeSymbolMap();
  SN_TO_IDX = buildSnToIdx();
  buildLiteratureRows();
  SORTED_SNS = uniqSorted(Object.keys(SN_TO_IDX));

  const colorBy = $("colorBy");
  CURRENT_COLOR_FIELD = ["raw_type", "canonical_type", "broad_type"].includes(DATA.type_col_used)
    ? DATA.type_col_used : "canonical_type";
  colorBy.value = CURRENT_COLOR_FIELD;

  $("excludeSameSN").checked = DATA.exclude_same_sn_default;
  $("uniqueTwinSN").checked = false;
  $("kInput").max = DATA.K;
  $("kInput").value = DATA.k_show_default;
  $("latentN").max = Math.max(1, Math.min(50, DATA.K - 1));
  $("specN").max = Math.max(1, Math.min(25, DATA.K - 1));

  const targetSel = $("targetSelect");
  const snSel = $("snSelect");
  const obsSel = $("obsSelect");
  const modelMode = $("modelMode");
  const refresh = () => { if (obsSel.value) updateAll(obsSel.value); };

  setLayoutMode("2x2");
  document.querySelectorAll("#twins-app .expandBtn").forEach(btn => {
    btn.addEventListener("click", () => toggleExpand(btn.dataset.card));
  });
  document.addEventListener("keydown", evt => {
    if (evt.key === "Escape") {
      const card = document.querySelector("#twins-app .panel-card.expanded");
      if (card) toggleExpand(card.id);
    }
  });
  modelMode.addEventListener("change", async () => {
    // Load the new dataset separately. Preserve the *original file bytes*, not a latent
    // vector or an earlier model's match rankings.
    const url=new URL(window.location.href);
    url.searchParams.set('dataset',modelMode.value);
    const retained=UPLOAD_SOURCE_FILES.length?UPLOAD_SOURCE_FILES:Array.from($('twinsUploadFiles').files||[]);
    if(retained.length){
      try {
        await uploadSwitchStore(retained);
        sessionStorage.setItem(SWITCH_UPLOAD_FLAG,'1');
      } catch(e) {
        $('status').textContent='Cannot retain uploaded spectra across model switch: '+e.message;
        return;
      }
    }
    window.location.assign(url.toString());
  });
  $("downloadSelected").addEventListener("click", downloadSelectedOutputs);
  $("downloadRecurrence").addEventListener("click", exportUnifiedRecurrenceCSV);
  $("recurrenceModeButton").addEventListener("click", () => {
    const enabled = !$("recurrentOnly").checked;
    $("recurrentOnly").checked = enabled;

    const button = $("recurrenceModeButton");

    button.textContent = enabled
      ? "Return to nearest neighbors"
      : "Update page to recurrent twins";

    button.setAttribute("aria-pressed", String(enabled));

    if ($("obsSelect").value) {
      updateAll($("obsSelect").value);
    }
  });

  $("recurrencePairEpoch").addEventListener("change", evt => {
    selectRecurrenceMatch(evt.target.value);
  });
  $("recurrencePairPrev").addEventListener("click", () => stepRecurrenceMatch(-1));
  $("recurrencePairNext").addEventListener("click", () => stepRecurrenceMatch(1));
  ["recurrenceTopK", "recurrenceMinEpochs"].forEach(id =>
    $(id).addEventListener("change", () => { if ($("obsSelect").value) updateAll($("obsSelect").value); })
  );
  $("metricMode").addEventListener("change", () => {
    if(String($('obsSelect').value).startsWith('upload:')) {renderUploadedSpectrum($('obsSelect').value);return;}
    if ($('recurrentOnly').checked) {
      renderSelectedRecurrencePairsTable();
    } else if (CURRENT_METRIC_ENTRIES) {
      renderRMSEPanel(CURRENT_METRIC_ENTRIES.targetIdx, CURRENT_METRIC_ENTRIES.twinEntries);
    }
  });

  const snList = SORTED_SNS;
  populateSnDropdown(snList);
  populateTargetDropdown(getMatchedTargets("", 500));
  if (snList.length > 0) {
    // Default to SN 2023mjq when present in this model's dataset.
    const preferredSN = snList.find(
      sn => reviewedSNIdentity(sn) === "2023mjq"
    );

    const initialSN = preferredSN || snList[0];

    snSel.value = initialSN;
    ensureTargetOption(initialSN);
    targetSel.value = initialSN;
    populateObsDropdown(initialSN);
  }

  $("uploadOnlySearchButton").addEventListener("click",()=>updateUploadOnlySearch(true));
  $("uploadOnlySearch").addEventListener("keydown",ev=>{if(ev.key==="Enter"){ev.preventDefault();updateUploadOnlySearch(true);}});
  $("uploadOnlySearch").addEventListener("input",()=>updateUploadOnlySearch(false));
  $("uploadOnlySelect").addEventListener("change",ev=>selectUploadedOnlyObservation(ev.target.value));
  $("targetSearchButton").addEventListener("click", () => runTargetSearch(true));
  $("targetSearch").addEventListener("keydown", evt => {
    if (evt.key === "Enter") { evt.preventDefault(); runTargetSearch(true); }
  });
  targetSel.addEventListener("change", () => {
    if (targetSel.value) {
      snSel.value = targetSel.value;
      populateObsDropdown(targetSel.value);
      refresh();
    }
  });
  snSel.addEventListener("change", () => {
    ensureTargetOption(snSel.value);
    targetSel.value = snSel.value;
    populateObsDropdown(snSel.value);
    refresh();
  });
  obsSel.addEventListener("change", refresh);
  // Shared Literature Phase checkbox and handles; no model/UMAP regeneration.
  const refreshLiterature = () => {
    LAST_UPLOAD_LATENT_KEY=null;
    literatureReadout();
    latentBaseTraces = buildBaseLatentTraces();
    refresh();
  };
  $('filterLiteraturePhase').addEventListener('change', () => {
    $('literaturePhaseControls').hidden = !literatureFilterActive();
    refreshLiterature();
  });
  $('litPhaseShowMissing').addEventListener('change', refreshLiterature);
  for (const id of ['litPhaseMin','litPhaseMax']) {
    $(id).addEventListener('input', () => {
      const lo = $('litPhaseMin'), hi = $('litPhaseMax');
      if (Number(lo.value) > Number(hi.value)) {
        if (id === 'litPhaseMin') hi.value = lo.value;
        else lo.value = hi.value;
      }
      refreshLiterature();
    });
  }
  literatureReadout();
  ["excludeSameSN", "uniqueTwinSN", "kInput"].forEach(id => $(id).addEventListener("change", refresh));
  // Dragging a slider fires many input events; render at most once per frame.
  let sliderFramePending = false;
  const refreshOnNextFrame = () => {
    if (sliderFramePending) return;
    sliderFramePending = true;
    requestAnimationFrame(() => { sliderFramePending = false; refresh(); });
  };
  ["latentN", "specN"].forEach(id => $(id).addEventListener("input", refreshOnNextFrame));
  colorBy.addEventListener("change", () => {
    LAST_UPLOAD_LATENT_KEY=null;
    CURRENT_COLOR_FIELD = colorBy.value;
    latentBaseTraces = buildBaseLatentTraces();
    if (obsSel.value) updateAll(obsSel.value);
    else Plotly.react("latent", latentBaseTraces, latentLayout(), {responsive: true});
  });

  const resizeObserver = new ResizeObserver(() => resizePlots(["latent", "spec", "rmse"]));
  document.querySelectorAll("#twins-app .panel-card").forEach(card => resizeObserver.observe(card));

  initLatentPlot();
  initUploadedSpectra();
  refresh();
}

// Download the metadata while showing progress. The body arrives decompressed,
// so progress is measured against the uncompressed size from the descriptor.
async function loadMetadata() {
  const r = await fetch(META_URL);
  if (!r.ok) throw new Error("metadata request returned " + r.status);
  if (!r.body || !r.body.getReader) return r.json();
  // Decode as it streams so the 73 MB text is held once, not as chunks plus a copy.
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  const parts = [];
  let received = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    parts.push(decoder.decode(value, {stream: true}));
    received += value.length;
    const pct = Math.min(100, Math.round(100 * received / CONFIG.metaBytes));
    $("loadingBar").style.width = pct + "%";
    $("loadingBar").setAttribute("aria-valuenow", String(pct));
  }
  parts.push(decoder.decode());
  return JSON.parse(parts.join(""));
}

// The literature-phase catalog is optional: without it every phase cell reads
// N/A, so any failure to load it yields an empty catalog rather than an error.
// Aborting through the signal counts as a failure.
async function loadLiteratureCatalog(signal) {
  try {
    const catalogURL = new URL(window.ASTRODASH_TWINS_ENDPOINTS.literature, window.location.href);
    catalogURL.searchParams.set('reviewed_schema', 'peak_band_v2_20261116');
    // Avoid a cached four-field response after the six-field catalog is updated.
    catalogURL.searchParams.set('fresh', String(Date.now()));
    const r = await fetch(catalogURL.toString(), {signal, cache: 'no-store'});
    if (!r.ok) return {};
    const catalog = await r.json();
    if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) return {};
    const sample = Object.values(catalog).find(v => Array.isArray(v));
    if (sample && sample.length < 6) {
      console.error('Literature catalog is out of date: expecting [phase, source, citation, doi, peak_mjd, peak_band], received:', sample);
      const notice = document.createElement('div');
      notice.className = 'small-note';
      notice.style.color = '#a33';
      notice.textContent = 'The literature endpoint returned an invalid phase catalog. Reviewed phase values have not been changed.';
      const parent = $('controls');
      if (parent) parent.prepend(notice);
    }
    return catalog;
  } catch (e) {
    return {};
  }
}

function checkMetadata() {
  if (DATA.N !== CONFIG.N || DATA.W !== CONFIG.W || !Array.isArray(DATA.wave) || DATA.wave.length !== DATA.W) {
    throw new Error("the metadata does not match the bundle descriptor");
  }
}

function showLoadFailure(message) {
  $("loadingPanel").hidden = false;
  $("controls").hidden = true;
  $("dashboardGrid").hidden = true;
  $("recurrenceSection").hidden = true;
  $("loadingText").textContent = message;
  $("loadingBar").parentElement.hidden = true;
}

async function boot() {
  if (typeof Plotly === "undefined") {
    showLoadFailure("The plotting library could not be loaded, so the explorer cannot start. Check that cdn.plot.ly is not blocked, then reload.");
    return;
  }
  // Start the catalog download alongside the metadata so it adds no wait.
  const catalogAbort = new AbortController();
  const catalogLoad = loadLiteratureCatalog(catalogAbort.signal);
  try {
    DATA = await loadMetadata();
    enforceBeeReviewedType();
    checkMetadata();
  } catch (e) {
    showLoadFailure("Could not load the spectral twins data (" + e.message + "). Try reloading the page.");
    return;
  }
  // A catalog request that stalls must not hold the explorer once its data has
  // loaded, so it gets a bounded wait from here and is then abandoned.
  const catalogDeadline = setTimeout(() => catalogAbort.abort(), LITERATURE_CATALOG_WAIT_MS);
  LITERATURE_CATALOG = await catalogLoad;
  clearTimeout(catalogDeadline);
  $("loadingPanel").hidden = true;
  $("controls").hidden = false;
  $("dashboardGrid").hidden = false;
  $("recurrenceSection").hidden = false;
  try {
    init();
  } catch (e) {
    showLoadFailure("The explorer failed to start (" + e.message + "). Try reloading the page.");
  }
}

boot();
})();
