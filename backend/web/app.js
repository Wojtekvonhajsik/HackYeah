"use strict";

// Dostępni.pl - aplikacja webowa (mobile-first). Bez frameworka i bez kroku budowania:
// serwowana przez backend pod /app/, API pod tym samym adresem.

// ---------- słowniki (tekst + symbol - znaczenie nie zależy od koloru) ----------

const VERDICT = {
  blocker: { label: "Przeszkoda", symbol: "✕" },
  uncertain: { label: "Do sprawdzenia", symbol: "?" },
  difficult: { label: "Utrudnienie", symbol: "!" },
  unknown: { label: "Brak danych", symbol: "–" },
  ok: { label: "Bez przeszkód", symbol: "✓" },
  amenity: { label: "Udogodnienie", symbol: "+" },
};

const SUMMARY = {
  barriers: { title: "Są przeszkody", verdict: "blocker" },
  difficulties: { title: "Są utrudnienia lub rzeczy do sprawdzenia", verdict: "difficult" },
  incomplete_data: { title: "Niepełne dane", verdict: "unknown" },
  // celowo nie "dostępne" - brak znanych przeszkód to nie dowód ich braku
  no_known_barriers: { title: "Brak znanych przeszkód", verdict: "ok" },
};

const STATUS = {
  confirmed: "potwierdzone",
  unverified: "niepotwierdzone",
  outdated: "nieaktualne",
  conflicting: "źródła się nie zgadzają",
};

const SOURCE = {
  official: "Dane urzędowe",
  owner: "Właściciel obiektu",
  verified_user: "Zweryfikowane zgłoszenia",
  osm: "OpenStreetMap",
  user_report: "Zgłoszenie użytkownika",
  ai_detection: "Analiza zdjęcia (AI)",
};

// Pola zgłoszeń - zgodne z walidacją w backendzie (classification/report_schema.py)
const SURFACES = [
  ["asphalt", "asfalt"], ["concrete", "beton"], ["paving_stones", "płyty / kostka betonowa"],
  ["sett", "kostka kamienna"], ["cobblestone", "bruk"], ["unhewn_cobblestone", "kocie łby"],
  ["gravel", "żwir"], ["fine_gravel", "drobny żwir"], ["compacted", "utwardzona ziemia"],
  ["grass", "trawa"], ["sand", "piasek"], ["dirt", "ziemia"], ["wood", "drewno"],
];
const KERBS = [["lowered", "obniżony"], ["flush", "równo z chodnikiem"], ["raised", "wysoki"]];

const FIELDS = {
  entrance: [
    { key: "width_cm", label: "Szerokość drzwi (cm)", kind: "number", min: 20, max: 1000 },
    { key: "threshold_cm", label: "Wysokość progu (cm, 0 = brak progu)", kind: "number", min: 0, max: 100 },
    { key: "automatic_door", label: "Drzwi automatyczne", kind: "bool" },
  ],
  steps: [
    { key: "count", label: "Liczba stopni", kind: "number", min: 0, max: 500 },
    { key: "ramp", label: "Jest podjazd", kind: "bool" },
    { key: "elevator", label: "Jest winda", kind: "bool" },
    { key: "handrail", label: "Jest poręcz", kind: "bool" },
  ],
  kerb: [
    { key: "kind", label: "Krawężnik", kind: "select", options: KERBS },
    { key: "height_cm", label: "Wysokość (cm)", kind: "number", min: 0, max: 100 },
  ],
  surface: [{ key: "value", label: "Nawierzchnia", kind: "select", options: SURFACES }],
  path_width: [{ key: "width_cm", label: "Szerokość przejścia (cm)", kind: "number", min: 0, max: 1000 }],
  ramp: [
    { key: "incline_pct", label: "Nachylenie (%)", kind: "number", min: 0, max: 100 },
    { key: "width_cm", label: "Szerokość (cm)", kind: "number", min: 0, max: 1000 },
  ],
  incline: [{ key: "incline_pct", label: "Nachylenie (%)", kind: "number", min: 0, max: 100 }],
  obstacle: [
    { key: "kind", label: "Co to za przeszkoda", kind: "text" },
    { key: "blocks_path", label: "Blokuje przejście", kind: "bool" },
    { key: "remaining_width_cm", label: "Wolne miejsce obok (cm)", kind: "number", min: 0, max: 1000 },
    { key: "temporary", label: "Tymczasowa (np. remont)", kind: "bool" },
  ],
  crossing: [
    { key: "tactile_paving", label: "Ścieżka dotykowa", kind: "bool" },
    { key: "sound_signals", label: "Sygnalizacja dźwiękowa", kind: "bool" },
    { key: "kerb", label: "Krawężnik na przejściu", kind: "select", options: KERBS },
  ],
  amenity: [{
    key: "kind", label: "Rodzaj", kind: "select",
    options: [["toilets_wheelchair", "toaleta dostępna dla wózków"], ["elevator", "winda"], ["bench", "ławka"]],
  }],
};

const TYPE_LABEL = {
  entrance: "Wejście", steps: "Schody", kerb: "Krawężnik", surface: "Nawierzchnia", path_width: "Szerokość przejścia",
  ramp: "Podjazd", incline: "Nachylenie", obstacle: "Przeszkoda", crossing: "Przejście dla pieszych",
  amenity: "Udogodnienie (toaleta, winda, ławka)",
};

// ---------- narzędzia ----------

const $ = (selector) => document.querySelector(selector);

const store = {
  get(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* tryb prywatny - działamy bez zapamiętywania */ }
  },
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const cap = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");

function fmtDate(iso) {
  if (!iso) return "data nieznana";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function shortAddress(address) {
  return (address || "").split(",").slice(1, 4).map((s) => s.trim()).filter(Boolean).join(", ");
}

function safeUrl(url) {
  return /^https?:\/\//.test(url || "") ? url : null;
}

function announce(message) {
  const el = $("#status");
  el.textContent = "";
  setTimeout(() => { el.textContent = message; }, 50);
}

function loadingHtml(text) {
  return `<div class="card loading"><span class="spinner" aria-hidden="true"></span><p>${esc(text)}</p></div>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try { body = await response.json(); } catch { /* odpowiedź bez JSON */ }
  if (!response.ok) {
    const detail = body && body.detail;
    if (typeof detail === "string") throw new Error(detail);
    if (Array.isArray(detail)) throw new Error(detail.map((d) => d.msg).join("; "));
    throw new Error(`Błąd serwera (${response.status}). Spróbuj ponownie.`);
  }
  return body;
}

let fieldSeq = 0;

function fieldsHtml(type) {
  return FIELDS[type].map((field) => {
    const id = `fld-${++fieldSeq}`;
    const label = `<label for="${id}">${esc(field.label)}</label>`;
    if (field.kind === "number") {
      return `${label}<input id="${id}" name="${field.key}" type="number" inputmode="decimal" min="${field.min}" max="${field.max}" step="any">`;
    }
    if (field.kind === "text") return `${label}<input id="${id}" name="${field.key}" type="text" maxlength="40">`;
    const options = field.kind === "bool" ? [["true", "tak"], ["false", "nie"]] : field.options;
    return `${label}<select id="${id}" name="${field.key}"><option value="">nie wiem</option>${
      options.map(([value, text]) => `<option value="${value}">${esc(text)}</option>`).join("")}</select>`;
  }).join("");
}

function readAttrs(container, type) {
  const attrs = {};
  for (const field of FIELDS[type]) {
    const el = container.querySelector(`[name="${field.key}"]`);
    if (!el || el.value.trim() === "") continue;
    if (field.kind === "number") attrs[field.key] = Number(el.value);
    else if (field.kind === "bool") attrs[field.key] = el.value === "true";
    else attrs[field.key] = el.value.trim();
  }
  return attrs;
}

function showFormError(form, message) {
  const error = form.querySelector(".form-error");
  error.textContent = message;
  error.hidden = false;
}

// ---------- profil (tylko na urządzeniu) ----------

let presets = null;

async function loadPresets() {
  if (!presets) presets = await api("/presets");
  return presets;
}

const profile = () => store.get("profile");

function profileLabel() {
  const p = profile();
  if (!p) return "Wybierz profil";
  const label = presets?.[p.preset]?.label || "Twój profil";
  return p.overrides ? `${label} (dostosowany)` : label;
}

function voterId() {
  let id = store.get("voter_id");
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    store.set("voter_id", id);
  }
  return id;
}

// ---------- nawigacja (#/, #/profil, #/miejsce/<id>) ----------

function route() {
  const hash = location.hash || "#/";
  let view = "search";
  let arg = null;
  if (hash.startsWith("#/profil")) {
    view = "profile";
  } else if (hash.startsWith("#/prywatnosc")) {
    view = "privacy"; // strony prawne dostępne bez wybierania profilu
  } else if (hash.startsWith("#/regulamin")) {
    view = "terms";
  } else if (hash.startsWith("#/wlasciciel/")) {
    view = "owner"; // właściciel nie musi wybierać profilu potrzeb
    arg = decodeURIComponent(hash.slice("#/wlasciciel/".length));
  } else if (!profile()) {
    store.set("returnTo", hash); // po wyborze profilu wracamy tam, dokąd szedł użytkownik
    view = "profile";
  } else if (hash.startsWith("#/asystent")) {
    view = "assistant";
  } else if (hash.startsWith("#/miejsce/")) {
    view = "place";
    arg = decodeURIComponent(hash.slice("#/miejsce/".length));
  }
  for (const name of ["profile", "search", "place", "owner", "assistant", "privacy", "terms"]) {
    $(`#view-${name}`).hidden = name !== view;
  }
  $("#profile-chip-label").textContent = profileLabel();
  stopSpeaking();
  if (view === "profile") showProfile();
  if (view === "search") showSearch();
  if (view === "place") showPlace(arg);
  if (view === "owner") showOwner(arg);
  if (view === "assistant") showAssistant();
  if (view === "privacy") focusHeading("#privacy-title");
  if (view === "terms") focusHeading("#terms-title");
}

function focusHeading(id) {
  const heading = $(id);
  heading.setAttribute("tabindex", "-1");
  heading.focus();
}

// ---------- widok: profil ----------

async function showProfile() {
  const list = $("#preset-list");
  try {
    await loadPresets();
  } catch (error) {
    list.innerHTML = `<p class="notice warn" role="alert">Nie udało się wczytać profili: ${esc(error.message)}</p>`;
    return;
  }
  const current = profile();
  list.innerHTML = Object.entries(presets).map(([key, p], i) => `
    <label class="preset">
      <input type="radio" name="preset" value="${esc(key)}" ${(current ? current.preset === key : i === 0) ? "checked" : ""} required>
      <div><strong>${esc(p.label)}</strong><span>${esc(p.description)}</span></div>
    </label>`).join("");
  const mobility = current?.overrides?.mobility || {};
  $("#tune-edge").value = mobility.max_edge_height_cm?.hard ?? "";
  $("#tune-width").value = mobility.min_width_cm?.hard ?? "";
  $("#tune-steps").value = mobility.max_step_count?.hard ?? "";
  updateTuneVisibility();
  focusHeading("#profile-title");
}

const selectedPreset = () => document.querySelector('input[name="preset"]:checked')?.value;

function updateTuneVisibility() {
  $("#tune").hidden = !presets?.[selectedPreset()]?.needs?.mobility; // progi ruchowe tylko dla profili ruchowych
}

function numberOrNull(selector) {
  const raw = $(selector).value.trim();
  return raw === "" || Number.isNaN(Number(raw)) ? null : Number(raw);
}

function onProfileSubmit(event) {
  event.preventDefault();
  const preset = selectedPreset();
  const base = presets[preset].needs.mobility;
  let overrides = null;
  if (base) {
    const mobility = {};
    const edge = numberOrNull("#tune-edge");
    const width = numberOrNull("#tune-width");
    const steps = numberOrNull("#tune-steps");
    // soft (komfort) nie może przekroczyć hard (granica) - API to sprawdza
    if (edge !== null) mobility.max_edge_height_cm = { soft: Math.min(base.max_edge_height_cm.soft, edge), hard: edge };
    if (width !== null) mobility.min_width_cm = { soft: Math.max(base.min_width_cm.soft, width), hard: width };
    if (steps !== null) mobility.max_step_count = { soft: Math.min(base.max_step_count.soft, steps), hard: steps };
    if (Object.keys(mobility).length) overrides = { mobility };
  }
  store.set("profile", { preset, overrides });
  announce("Zapisano profil.");
  const target = store.get("returnTo") || "#/";
  store.set("returnTo", null);
  if (location.hash === target) route(); else location.hash = target;
}

// ---------- widok: wyszukiwanie ----------

function placeItem(place) {
  const details = [shortAddress(place.address), place.sample ? "dane przykładowe" : ""].filter(Boolean).join(" · ");
  return `<li><a class="place-link" href="#/miejsce/${encodeURIComponent(place.id)}">
    <strong>${esc(place.name)}</strong>${details ? `<small>${esc(details)}</small>` : ""}</a></li>`;
}

// ---------- dane GUS ----------

let cityStats = null;
const fmtPct = (pct) => `${String(pct).replace(".", ",")}%`;

async function loadCityStats() {
  if (!cityStats) cityStats = await api("/stats/city");
  return cityStats;
}

async function showSearch() {
  const recent = store.get("recent") || [];
  const title = document.querySelector("#view-search .section-title");
  const list = $("#recent");
  if (recent.length) {
    title.textContent = "Ostatnio sprawdzane";
    list.innerHTML = recent.map(placeItem).join("");
  } else {
    // pierwsze uruchomienie: miejsca, które mają już dane (np. przygotowane do demo)
    title.textContent = "Przykładowe miejsca";
    try {
      const places = await api("/places");
      const ready = places.filter((p) => p.data_loaded && !p.sample).slice(0, 6);
      const demo = places.filter((p) => p.sample);
      list.innerHTML = [...ready, ...demo].map(placeItem).join("") || "<li class='muted'>Wyszukaj miejsce powyżej.</li>";
    } catch {
      list.innerHTML = "<li class='muted'>Wyszukaj miejsce powyżej.</li>";
    }
  }
}

// Pytanie zamiast nazwy miejsca ("gdzie zjem bez schodów") trafia od razu do asystenta.
// (\s|$) zamiast \b - w JS \b nie działa przy polskich znakach (np. "pokaż").
const QUESTION_START = /^(gdzie|jak|jaki|jaka|jakie|który|która|które|co|czy|polec\S*|szukam|chcę|chce|potrzebuj\S*|pokaż|znajdź)(\s|$)/i;

function looksLikeQuestion(query) {
  return query.endsWith("?") || QUESTION_START.test(query); // długie nazwy ("Teatr im. J. Słowackiego") zostają wyszukiwaniem
}

let pendingQuestion = null;

function askAssistant(question) {
  pendingQuestion = question;
  if (location.hash === "#/asystent") showAssistant(); else location.hash = "#/asystent";
}

async function onSearchSubmit(event) {
  event.preventDefault();
  const query = $("#search-input").value.trim();
  if (!query) return;
  if (looksLikeQuestion(query)) {
    askAssistant(query);
    return;
  }
  const box = $("#search-results");
  box.innerHTML = loadingHtml("Szukam…");
  announce("Szukam…");
  try {
    const results = await api(`/places/search?q=${encodeURIComponent(query)}`);
    box.innerHTML = results.length
      ? `<h2 class="section-title">Wyniki</h2><ul class="place-list">${results.map(placeItem).join("")}</ul>`
      : `<p class="notice info">Nie znaleźliśmy miejsca o nazwie „${esc(query)}” w Krakowie.</p>
         <button class="btn primary block" type="button" data-ask="${esc(query)}">Zapytaj asystenta: „${esc(query)}”</button>`;
    announce(results.length ? `Znaleziono miejsc: ${results.length}.` : "Brak wyników. Możesz zapytać asystenta.");
  } catch (error) {
    box.innerHTML = `<p class="notice warn" role="alert">${esc(error.message)}</p>`;
  }
}

// Wprowadzanie głosowe dla pola: mikrofon -> tekst w polu -> wysłanie formularza
function setupVoiceInput(micSelector, inputSelector, formSelector, prompt) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return; // przeglądarka bez rozpoznawania mowy - przycisk zostaje ukryty
  const mic = $(micSelector);
  mic.hidden = false;
  mic.addEventListener("click", () => {
    const recognition = new Recognition();
    recognition.lang = "pl-PL";
    recognition.interimResults = false;
    mic.dataset.listening = "true";
    announce(prompt);
    recognition.onresult = (event) => {
      $(inputSelector).value = event.results[0][0].transcript;
      $(formSelector).requestSubmit();
    };
    recognition.onerror = () => announce("Nie udało się rozpoznać mowy. Wpisz tekst.");
    recognition.onend = () => { mic.dataset.listening = "false"; };
    recognition.start();
  });
}

// ---------- widok: asystent ----------

function showAssistant() {
  focusHeading("#assistant-title");
  if (pendingQuestion) {
    $("#assistant-input").value = pendingQuestion;
    pendingQuestion = null;
    $("#assistant-form").requestSubmit();
  }
}

function assistantPlace(place, onClickAttr = "") {
  const summary = SUMMARY[place.summary] || SUMMARY.incomplete_data;
  const confidence = place.summary_confidence_pct !== null ? ` · pewność ${place.summary_confidence_pct}%` : "";
  return `<a class="place-link" href="#/miejsce/${encodeURIComponent(place.place_id)}" ${onClickAttr}>
    <strong><span class="verdict-icon v-${summary.verdict}" aria-hidden="true">${VERDICT[summary.verdict].symbol}</span> ${esc(place.name)}</strong>
    <small>${esc(place.summary_text)}${confidence}</small>
    ${place.partner && !place.sponsored
      ? `<small class="partner-note">Partner (płatna promocja w Dostępni.pl) - pozycja wynika z oceny, nie z opłaty</small>`
      : ""}
  </a>`;
}

async function onAssistantSubmit(event) {
  event.preventDefault();
  const question = $("#assistant-input").value.trim();
  if (question.length < 3) return;
  const box = $("#assistant-answer");
  box.innerHTML = loadingHtml("Asystent przegląda dane…");
  const p = profile();
  try {
    const a = await api("/assistant", {
      method: "POST",
      body: JSON.stringify({ question, preset: p.preset, overrides: p.overrides }),
    });
    const sponsored = a.sponsored;
    // Reklama jest NAD odpowiedzią - dlatego wprost piszemy, że jest tam za opłatą
    box.innerHTML = `
      ${sponsored ? `
      <aside class="card sponsored" aria-label="Sponsorowane - wyświetlane wyżej za opłatą">
        <p class="sponsored-label">Sponsorowane · wyświetlane wyżej za opłatą</p>
        <p><strong>${esc(sponsored.sponsor_name)}:</strong> ${esc(sponsored.tagline)}</p>
        ${assistantPlace(sponsored, `data-sponsorship="${esc(sponsored.sponsorship_id)}"`)}
        <details class="evidence"><summary>Dlaczego to widzę?</summary>
          <p class="hint">Obiekt wykupił promocję i potwierdził dane o dostępności kodem właściciela. Pokazujemy ją
          tylko osobom, dla których to miejsce nie ma znanych przeszkód. Opłata nie zmienia ocen ani kolejności poleceń poniżej.</p>
        </details>
      </aside>` : ""}
      <section class="card answer" aria-labelledby="answer-title">
        <h2 id="answer-title" class="answer-title">Polecane na podstawie danych</h2>
        <p class="hint">Kolejność wynika tylko z ocen dostępności - bez wpływu opłat.</p>
        <p>${esc(a.answer)}</p>
        ${a.places.length ? `<ul class="place-list">${a.places.map((pl) => `<li>${assistantPlace(pl)}</li>`).join("")}</ul>` : ""}
        <p class="source-note">${esc(a.engine.startsWith("reguły") ? "Odpowiedź bez modelu AI (reguły)." : `Model AI: ${a.engine}.`)} ${esc(a.disclosure)}</p>
      </section>`;
    speakText(a.answer); // przy włączonym czytaniu odpowiedź jest od razu odczytywana
  } catch (error) {
    box.innerHTML = `<p class="notice warn" role="alert">${esc(error.message)}</p>`;
  }
}

// ---------- widok: miejsce ----------

let map = null;
let current = { id: null, place: null, assessment: null };

function rememberRecent(place) {
  const recent = (store.get("recent") || []).filter((p) => p.id !== place.id);
  recent.unshift({ id: place.id, name: place.name, address: place.address, sample: place.sample });
  store.set("recent", recent.slice(0, 8));
}

async function fetchAssessment(id) {
  const p = profile();
  return api(`/places/${encodeURIComponent(id)}/assessment`, {
    method: "POST",
    body: JSON.stringify({ preset: p.preset, overrides: p.overrides }),
  });
}

let pollTimer = null;

async function showPlace(id) {
  clearTimeout(pollTimer);
  $("#place-title").textContent = "Sprawdzam miejsce…";
  $("#place-address").textContent = "";
  $("#place-body").innerHTML = loadingHtml("Sprawdzam miejsce…");
  announce("Sprawdzam miejsce…");
  try {
    const assessment = await fetchAssessment(id);
    const places = await api("/places");
    const place = places.find((p) => p.id === id) || { id, name: "Miejsce" };
    if (!isShowing(id)) return; // użytkownik zdążył przejść dalej
    current = { id, place, assessment };
    await loadCityStats().catch(() => null); // kontekst GUS dla hoteli - opcjonalny
    rememberRecent(place);
    renderPlace();
    announce(`${place.name}: ${SUMMARY[assessment.summary].title}.`);
    focusHeading("#place-title");
    pollWhilePending(id, 0);
  } catch (error) {
    $("#place-title").textContent = "Nie udało się sprawdzić miejsca";
    $("#place-body").innerHTML = `
      <p class="notice warn" role="alert">${esc(error.message)}</p>
      <button class="btn primary" type="button" data-action="retry">Spróbuj ponownie</button>`;
  }
}

const isShowing = (id) => location.hash === `#/miejsce/${encodeURIComponent(id)}`;

// Dane z OSM dociągają się w tle - pokazujemy ocenę od razu i odświeżamy, gdy dojdą
function pollWhilePending(id, attempt) {
  if (!current.assessment.pending_sources.length) return;
  if (attempt >= 15) return; // ok. 45 s - dalej backend zgłosi błąd źródła przy kolejnym wejściu
  pollTimer = setTimeout(async () => {
    if (!isShowing(id)) return;
    try {
      const assessment = await fetchAssessment(id);
      if (!isShowing(id)) return;
      current.assessment = assessment;
      if (!assessment.pending_sources.length) {
        renderPlace();
        const failed = assessment.warnings.some((w) => w.includes("OpenStreetMap"));
        announce(failed
          ? "Nie udało się pobrać danych z OpenStreetMap - pokazujemy dane zapisane wcześniej."
          : "Dociągnęliśmy dane z OpenStreetMap - ocena zaktualizowana.");
        return;
      }
    } catch { /* spróbujemy ponownie */ }
    pollWhilePending(id, attempt + 1);
  }, 3000);
}

const confidenceWord = (pct) => (pct >= 60 ? "wysoka" : pct >= 30 ? "średnia" : "niska");

function confidenceText(pct) {
  return pct === null || pct === undefined ? "pewność: brak danych" : `pewność ${pct}% (${confidenceWord(pct)})`;
}

function pluralInfo(n) {
  if (n === 1) return "informacja";
  const lastTwo = n % 100;
  return n % 10 >= 2 && n % 10 <= 4 && (lastTwo < 12 || lastTwo > 14) ? "informacje" : "informacji";
}

const usesAi = (feature) => feature.evidence.some((e) => e.source.type === "ai_detection");

function aiTag() {
  return `<span class="tag-ai" title="Informacja wykryta automatycznie przez AI na zdjęciu ulicy">AI<span class="visually-hidden"> - analiza zdjęcia</span></span>`;
}

function sourceLine(evidence) {
  const url = safeUrl(evidence.source.url);
  const name = esc(SOURCE[evidence.source.type] || evidence.source.type);
  const linked = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${name}</a>` : name;
  const sample = evidence.source.sample ? " (przykład)" : "";
  return `${linked}${sample}, ${fmtDate(evidence.source.observed_at)}`;
}

function voteControls(evidence, featureType) {
  const myVote = (store.get("votes") || {})[evidence.observation_id];
  const obs = esc(evidence.observation_id);
  if (myVote) {
    // po oddaniu głosu nie pokazujemy obu przycisków - żeby nie dało się przypadkiem zagłosować odwrotnie
    return `<p class="my-vote">Twój głos: <strong>${myVote === "confirm" ? "✓ zgadza się" : "✕ nie zgadza się"}</strong>
      · <button class="link-btn" type="button" data-action="change-vote" data-obs="${obs}">zmień</button></p>`;
  }
  return `<div class="vote" role="group" aria-label="Czy ta informacja się zgadza?">
      <button class="btn small" type="button" data-vote="confirm" data-obs="${obs}">✓ Zgadza się</button>
      <button class="btn small" type="button" data-vote="deny" data-obs="${obs}" data-type="${esc(featureType)}" aria-expanded="false">✕ Nie zgadza się</button>
    </div>`;
}

function evidenceItem(evidence, featureType) {
  const verdict = VERDICT[evidence.verdict];
  const isAi = evidence.source.type === "ai_detection";
  return `<li class="evidence-item">
    <p>${isAi ? aiTag() : ""}<strong>${sourceLine(evidence)}</strong> · ${STATUS[evidence.status]}</p>
    <p>${evidence.verdict === "unknown" ? "" : `${verdict.label}: `}${esc(cap(evidence.reasons.join("; ")))}</p>
    ${evidence.note ? `<p class="muted">„${esc(evidence.note)}”</p>` : ""}
    <p class="muted small">${esc(evidence.source.name)}${evidence.source.license ? ` · ${esc(evidence.source.license)}` : ""}
      · <span aria-label="potwierdzenia">✓ ${evidence.confirmations}</span> <span aria-label="zaprzeczenia">✕ ${evidence.denials}</span></p>
    ${voteControls(evidence, featureType)}
  </li>`;
}

function featureCard(feature) {
  const verdict = VERDICT[feature.verdict];
  const primary = feature.evidence.find((e) => e.observation_id === feature.primary_observation_id) || feature.evidence[0];
  const [mainReason, ...moreReasons] = feature.reasons;
  const meta = [
    feature.type === "amenity" ? null : confidenceText(feature.confidence_pct),
    sourceLine(primary),
    `<span class="status">${STATUS[feature.status]}</span>`,
  ].filter(Boolean).join(" · ");
  return `<li class="card feature" id="f-${esc(feature.feature_id)}">
    <div class="feature-head">
      <span class="verdict-icon v-${feature.verdict}" aria-hidden="true">${verdict.symbol}</span>
      <h3>${esc(cap(feature.label))}</h3>
      ${usesAi(feature) ? aiTag() : ""}
      <span class="badge v-${feature.verdict}">${verdict.label}</span>
    </div>
    <p class="lead-reason">${esc(cap(mainReason || ""))}</p>
    <p class="meta">${meta}</p>
    ${feature.conflict ? `<p class="conflict">Źródła się nie zgadzają - pokazujemy ostrożniejszą wersję.</p>` : ""}
    <details class="evidence" data-feature="${esc(feature.feature_id)}">
      <summary>Szczegóły i źródła (${feature.evidence.length})</summary>
      ${moreReasons.length ? `<ul class="reasons">${moreReasons.map((r) => `<li>${esc(cap(r))}</li>`).join("")}</ul>` : ""}
      <ul class="evidence-list">${feature.evidence.map((e) => evidenceItem(e, feature.type)).join("")}</ul>
    </details>
  </li>`;
}

function surroundingsSummary(around) {
  const notable = { blocker: "przeszkoda", uncertain: "do sprawdzenia", difficult: "utrudnienie" };
  const counts = new Map();
  for (const f of around) {
    const key = notable[f.verdict] ? `${f.label} (${notable[f.verdict]})` : f.verdict === "amenity" ? f.reasons[0] : null;
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts].map(([key, n]) => (n > 1 ? `${key} ×${n}` : key)).join(", ");
}

const LODGING_KINDS = ["tourism:hotel", "tourism:hostel", "tourism:guest_house", "tourism:motel", "tourism:apartment"];

function lodgingContext() {
  if (!LODGING_KINDS.includes(current.place.kind) || !cityStats) return "";
  const pick = (key) => cityStats.items.find((i) => i.key === key);
  const lines = ["lodging_elevator", "lodging_ramp", "lodging_auto_door"].map(pick).filter(Boolean);
  if (!lines.length) return "";
  return `<aside class="card gus-context" aria-label="Kontekst z danych GUS">
    <p><strong>Noclegi w mieście:</strong> ${lines.map((i) => `${esc(shortLodging(i.key))} ma ${fmtPct(i.share_pct)} obiektów`).join(", ")}
    (GUS, ${lines[0].year}).</p>
  </aside>`;
}

const shortLodging = (key) => ({ lodging_elevator: "windę", lodging_ramp: "pochylnię", lodging_auto_door: "drzwi automatyczne" })[key];

// Bezpieczeństwo - najwyższy priorytet, więc pierwsza karta miejsca
const SAFETY_LEVEL = {
  elevated: { badge: "Uwaga", symbol: "!", verdict: "uncertain" },
  typical: { badge: "Typowo", symbol: "i", verdict: "unknown" },
  lower: { badge: "Spokojniej", symbol: "✓", verdict: "ok" },
  unknown: { badge: "Brak danych", symbol: "–", verdict: "unknown" },
};

function safetyCard(safety) {
  if (!safety) return "";
  const level = SAFETY_LEVEL[safety.level];
  const snapshot = safety.city?.from_snapshot ? ` Dane GUS z kopii z ${fmtDate(safety.city.retrieved_at)}.` : "";
  return `<section class="card safety" data-level="${esc(safety.level)}" aria-labelledby="safety-title">
    <div class="feature-head">
      <span class="verdict-icon v-${level.verdict}" aria-hidden="true">${level.symbol}</span>
      <h2 id="safety-title">Bezpieczeństwo</h2>
      <span class="badge v-${level.verdict}">${level.badge}</span>
    </div>
    <p>${esc(safety.headline)}</p>
    ${safety.tips.length ? `<ul class="safety-tips">${safety.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    <details class="evidence">
      <summary>Dane o bezpieczeństwie</summary>
      <ul class="reasons">${safety.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
      <p class="source-note">${esc(safety.scope_note)}${esc(snapshot)} Licencja GUS: CC BY 4.0.</p>
    </details>
  </section>`;
}

function renderPlace() {
  const { place, assessment: a } = current;
  const summary = SUMMARY[a.summary];
  const atPlace = a.features.filter((f) => f.scope === "place");
  const around = a.features.filter((f) => f.scope === "surroundings");
  const pending = a.pending_sources.length > 0;
  const warnings = a.warnings.filter((w) => !w.startsWith("Pobieramy dane"));
  const aiCount = a.features.filter(usesAi).length;
  const aroundText = surroundingsSummary(around);
  $("#place-title").textContent = place.name;
  $("#place-address").textContent = shortAddress(place.address);
  $("#place-body").innerHTML = `
    ${a.contains_sample_data ? `<p class="notice sample"><strong>Dane przykładowe</strong> - nie opisują rzeczywistego obiektu.</p>` : ""}
    ${pending ? `<p class="notice info loading"><span class="spinner" aria-hidden="true"></span>Dociągamy dane z OpenStreetMap - ocena uzupełni się sama.</p>` : ""}
    ${warnings.map((w) => `<p class="notice warn" role="alert">${esc(w)}</p>`).join("")}

    ${a.sponsor_name ? `<p class="notice info partner-notice">Ten obiekt wykupił promocję w Dostępni.pl (${esc(a.sponsor_name)}). Ocena dostępności nie zależy od opłaty.</p>` : ""}
    ${safetyCard(a.safety)}

    <section class="card summary" data-summary="${esc(a.summary)}" aria-labelledby="summary-title">
      <h2 id="summary-title"><span class="verdict-icon v-${summary.verdict}" aria-hidden="true">${VERDICT[summary.verdict].symbol}</span>${esc(summary.title)}</h2>
      <ul class="chips">
        <li class="chip">${cap(confidenceText(a.summary_confidence_pct))}</li>
        <li class="chip">${aiCount ? `${aiTag()} analiza zdjęć AI: ${aiCount} ${pluralInfo(aiCount)}` : "bez analizy zdjęć AI"}</li>
      </ul>
      ${a.missing.length ? `<p><strong>Brakuje:</strong> ${esc(a.missing.join(", "))}</p>` : ""}
      ${aroundText ? `<p class="muted">W okolicy: ${esc(aroundText)}</p>` : ""}
      <div class="summary-actions">
        <button class="btn small" type="button" data-action="speak" aria-pressed="false"><span aria-hidden="true">🔊</span> Odczytaj</button>
        <button class="btn small" type="button" data-action="share">Udostępnij</button>
      </div>
    </section>

    <div class="map-wrap">
      <div id="map" role="img" aria-label="Mapa miejsca i okolicy. Wszystkie informacje z mapy są w liście poniżej."></div>
    </div>

    <h2 class="section-title">Miejsce i wejście</h2>
    ${atPlace.length
      ? `<ul class="features">${atPlace.map(featureCard).join("")}</ul>`
      : `<p class="notice info">Brak informacji o samym miejscu.</p>`}

    ${lodgingContext()}

    <details class="card report" ${a.missing.length && !pending ? "open" : ""}>
      <summary>Wiesz coś o tym miejscu? Uzupełnij</summary>
      <form id="report-form">
        <div class="form-grid">
          <label for="report-type">Czego dotyczy?</label>
          <select id="report-type" name="type">${Object.entries(TYPE_LABEL).map(([value, text]) => `<option value="${value}">${esc(text)}</option>`).join("")}</select>
        </div>
        <div id="report-fields" class="form-grid">${fieldsHtml("entrance")}</div>
        <p class="hint">Widoczne jako niepotwierdzone, dopóki inni nie potwierdzą.</p>
        <p class="form-error" role="alert" hidden></p>
        <button class="btn primary" type="submit">Wyślij</button>
      </form>
    </details>

    ${around.length ? `
      <details class="around card">
        <summary>W okolicy (${around.length}) - mogą istnieć inne drogi dojścia</summary>
        <ul class="features">${around.map(featureCard).join("")}</ul>
      </details>` : ""}
  `;
  renderMap();
}

function renderMap() {
  if (map) { map.remove(); map = null; }
  const { place, assessment } = current;
  if (!window.L || !place.location) return;
  map = L.map("map", { scrollWheelZoom: false }).setView([place.location.lat, place.location.lon], 18);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  L.marker([place.location.lat, place.location.lon], { title: place.name, alt: place.name }).addTo(map);
  for (const feature of assessment.features) {
    const verdict = VERDICT[feature.verdict];
    const icon = L.divIcon({
      className: "",
      html: `<span class="verdict-icon v-${feature.verdict}" style="border:2px solid currentColor">${verdict.symbol}</span>`,
      iconSize: [32, 32],
    });
    L.marker([feature.location.lat, feature.location.lon], { icon, title: `${cap(feature.label)}: ${verdict.label}` })
      .bindPopup(`<strong>${esc(cap(feature.label))}</strong><br>${verdict.label}<br>${esc(feature.reasons.join("; "))}`)
      .addTo(map);
  }
}

// ---------- głos, udostępnianie, głosowanie ----------

// Czytanie na głos przy wyborze opcji (profil, rodzaj zgłoszenia). Włącznik w nagłówku, domyślnie włączone.
const voiceOn = () => store.get("voice") !== false;

function updateVoiceToggle() {
  const button = $("#voice-toggle");
  button.setAttribute("aria-pressed", String(voiceOn()));
  button.querySelector(".voice-state").textContent = voiceOn() ? "wł." : "wył.";
}

function toggleVoice() {
  store.set("voice", !voiceOn());
  updateVoiceToggle();
  if (!voiceOn()) stopSpeaking();
  speakText("Czytanie na głos włączone.");
}

function speakText(text) {
  if (!voiceOn() || !("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "pl-PL";
  speechSynthesis.speak(utterance);
}

function stopSpeaking() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  const button = document.querySelector('[data-action="speak"]');
  if (button) button.setAttribute("aria-pressed", "false");
}

function speak(button) {
  if (!("speechSynthesis" in window)) {
    announce("Ta przeglądarka nie obsługuje czytania na głos.");
    return;
  }
  if (speechSynthesis.speaking) { stopSpeaking(); return; }
  const utterance = new SpeechSynthesisUtterance(`${current.place.name}. ${current.assessment.text}`);
  utterance.lang = "pl-PL";
  utterance.onend = () => button.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-pressed", "true");
  speechSynthesis.speak(utterance);
}

async function share() {
  const data = { title: current.place.name, text: current.assessment.summary_text, url: location.href };
  try {
    if (navigator.share) { await navigator.share(data); return; }
    await navigator.clipboard.writeText(location.href);
    announce("Skopiowano link do schowka.");
  } catch { /* użytkownik anulował */ }
}

function openCorrection(button) {
  const item = button.closest(".evidence-item");
  const existing = item.querySelector("form.correction");
  if (existing) {
    existing.remove();
    button.setAttribute("aria-expanded", "false");
    return;
  }
  const form = document.createElement("form");
  form.className = "correction";
  form.dataset.obs = button.dataset.obs;
  form.dataset.type = button.dataset.type;
  form.innerHTML = `
    <p><strong>Jak jest naprawdę?</strong> (opcjonalnie - pomoże innym)</p>
    <div class="form-grid">${fieldsHtml(button.dataset.type)}</div>
    <p class="form-error" role="alert" hidden></p>
    <div class="vote">
      <button class="btn primary small" type="submit">Wyślij z poprawką</button>
      <button class="btn small" type="button" data-action="deny-plain">Wyślij bez poprawki</button>
    </div>`;
  item.append(form);
  button.setAttribute("aria-expanded", "true");
  form.querySelector("select, input")?.focus();
}

async function vote(button) {
  if (button.dataset.vote === "deny") {
    openCorrection(button); // najpierw pytamy, jak jest naprawdę
    return;
  }
  await sendVote(button.dataset.obs, "confirm", null, button);
}

async function sendVote(observationId, value, correctionAttrs, button, form = null) {
  const featureId = button.closest("details.evidence")?.dataset.feature;
  button.disabled = true;
  try {
    await api(`/observations/${encodeURIComponent(observationId)}/votes`, {
      method: "POST",
      body: JSON.stringify({ voter_id: voterId(), value, correction_attrs: correctionAttrs }),
    });
    const votes = store.get("votes") || {};
    votes[observationId] = value;
    store.set("votes", votes);
    current.assessment = await fetchAssessment(current.id);
    renderPlace();
    reopenEvidence(featureId, `[data-action="change-vote"][data-obs="${CSS.escape(observationId)}"]`);
    announce(correctionAttrs
      ? "Dziękujemy! Dodaliśmy Twoją poprawkę i przeliczyliśmy ocenę."
      : "Dziękujemy! Zapisaliśmy Twoją opinię i przeliczyliśmy ocenę.");
  } catch (error) {
    button.disabled = false;
    if (form) {
      showFormError(form, error.message);
    } else {
      announce(error.message);
      alert(error.message);
    }
  }
}

// Po przerysowaniu karty wracamy do miejsca, w którym był użytkownik (rozwinięte źródła, fokus)
function reopenEvidence(featureId, focusSelector) {
  const details = document.querySelector(`details.evidence[data-feature="${CSS.escape(featureId || "")}"]`);
  if (!details) return;
  details.open = true;
  details.closest("details.around")?.setAttribute("open", "");
  details.querySelector(focusSelector)?.focus();
}

function changeVote(button) {
  // zmiana zdania: znów pokazujemy oba przyciski; nowy głos zastąpi poprzedni (jeden głos na urządzenie)
  const observationId = button.dataset.obs;
  const featureId = button.closest("details.evidence")?.dataset.feature;
  const votes = store.get("votes") || {};
  delete votes[observationId];
  store.set("votes", votes);
  renderPlace();
  reopenEvidence(featureId, `[data-vote="confirm"][data-obs="${CSS.escape(observationId)}"]`);
}

async function onCorrectionSubmit(form, plain) {
  const attrs = plain ? null : readAttrs(form, form.dataset.type);
  if (attrs && !Object.keys(attrs).length) {
    showFormError(form, "Uzupełnij co najmniej jedno pole albo wybierz „Wyślij bez poprawki”.");
    return;
  }
  const button = form.querySelector(plain ? '[data-action="deny-plain"]' : 'button[type="submit"]');
  await sendVote(form.dataset.obs, "deny", attrs, button, form);
}

async function onReportSubmit(form) {
  const type = form.elements.type.value;
  const attrs = readAttrs(form.querySelector("#report-fields"), type);
  if (!Object.keys(attrs).length) {
    showFormError(form, "Uzupełnij co najmniej jedno pole.");
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await api(`/places/${encodeURIComponent(current.id)}/reports`, {
      method: "POST",
      body: JSON.stringify({ type, attrs }),
    });
    current.assessment = await fetchAssessment(current.id);
    renderPlace();
    announce("Dziękujemy! Dodaliśmy Twoje zgłoszenie i przeliczyliśmy ocenę.");
    focusHeading("#summary-title");
  } catch (error) {
    button.disabled = false;
    showFormError(form, error.message);
  }
}

// ---------- widok: właściciel obiektu ----------

async function showOwner(placeId) {
  const form = $("#owner-form");
  form.reset(); // kod i dane z poprzedniego miejsca nie mogą przejść na kolejne
  form.hidden = false;
  form.dataset.place = placeId;
  $("#owner-done").hidden = true;
  $("#owner-error").hidden = true;
  $("#owner-entrance").innerHTML = fieldsHtml("entrance");
  $("#owner-steps").innerHTML = fieldsHtml("steps");
  $("#owner-place").textContent = "Wczytuję miejsce…";
  try {
    const place = (await api("/places")).find((p) => p.id === placeId);
    $("#owner-place").textContent = `Miejsce: ${place ? place.name : placeId}`;
  } catch {
    $("#owner-place").textContent = `Miejsce: ${placeId}`;
  }
  focusHeading("#owner-title");
}

async function onOwnerSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = $("#owner-error");
  const fail = (message) => {
    error.textContent = message;
    error.hidden = false;
  };
  error.hidden = true;
  const code = form.elements.code.value.trim();
  const reports = [];
  const entrance = readAttrs($("#owner-entrance"), "entrance");
  if (Object.keys(entrance).length) reports.push({ type: "entrance", attrs: entrance });
  const steps = readAttrs($("#owner-steps"), "steps");
  if (Object.keys(steps).length) reports.push({ type: "steps", attrs: steps });
  for (const box of form.querySelectorAll('input[name="amenity"]:checked')) {
    reports.push({ type: "amenity", attrs: { kind: box.value } });
  }
  if (!code) return fail("Podaj kod właściciela.");
  if (!reports.length) return fail("Uzupełnij co najmniej jedną informację.");
  const placeId = form.dataset.place;
  try {
    await api(`/places/${encodeURIComponent(placeId)}/owner-reports`, {
      method: "POST",
      headers: { "X-Owner-Code": code },
      body: JSON.stringify({ reports }),
    });
  } catch (e) {
    return fail(e.message);
  }
  form.hidden = true;
  const done = $("#owner-done");
  done.hidden = false;
  done.innerHTML = `
    <p class="notice info" role="status"><strong>Dziękujemy!</strong> Zapisaliśmy informacje o obiekcie (${reports.length}).
    Są widoczne jako potwierdzone dane od właściciela.</p>
    <a class="btn primary block" href="#/miejsce/${encodeURIComponent(placeId)}">Zobacz kartę miejsca</a>`;
  announce("Zapisano dane obiektu.");
}

// ---------- start ----------

function init() {
  $("#profile-form").addEventListener("submit", onProfileSubmit);
  $("#preset-list").addEventListener("change", updateTuneVisibility);
  $("#search-form").addEventListener("submit", onSearchSubmit);
  $("#place-body").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.vote) vote(button);
    else if (button.dataset.action === "deny-plain") onCorrectionSubmit(button.closest("form"), true);
    else if (button.dataset.action === "change-vote") changeVote(button);
    else if (button.dataset.action === "speak") speak(button);
    else if (button.dataset.action === "share") share();
    else if (button.dataset.action === "retry") route();
  });
  $("#place-body").addEventListener("submit", (event) => {
    event.preventDefault();
    if (event.target.id === "report-form") onReportSubmit(event.target);
    else if (event.target.classList.contains("correction")) onCorrectionSubmit(event.target, false);
  });
  $("#place-body").addEventListener("change", (event) => {
    if (event.target.id === "report-type") {
      $("#report-fields").innerHTML = fieldsHtml(event.target.value);
      speakText(TYPE_LABEL[event.target.value]);
    }
  });
  $("#preset-list").addEventListener("change", () => {
    const p = presets?.[selectedPreset()];
    if (p) speakText(`${p.label}. ${p.description}`);
  });
  $("#voice-toggle").addEventListener("click", toggleVoice);
  updateVoiceToggle();
  $("#owner-form").addEventListener("submit", onOwnerSubmit);
  window.addEventListener("hashchange", route);
  setupVoiceInput("#mic", "#search-input", "#search-form", "Słucham. Powiedz nazwę miejsca.");
  setupVoiceInput("#assistant-mic", "#assistant-input", "#assistant-form", "Słucham. Zadaj pytanie.");
  $("#assistant-form").addEventListener("submit", onAssistantSubmit);
  $("#search-results").addEventListener("click", (event) => {
    const ask = event.target.closest("[data-ask]");
    if (ask) askAssistant(ask.dataset.ask);
  });
  $("#view-assistant").addEventListener("click", (event) => {
    const example = event.target.closest("[data-question]");
    if (example) {
      $("#assistant-input").value = example.dataset.question;
      $("#assistant-form").requestSubmit();
    }
    const ad = event.target.closest("[data-sponsorship]");
    if (ad) fetch(`/sponsorships/${encodeURIComponent(ad.dataset.sponsorship)}/click`, { method: "POST" }).catch(() => null);
  });
  loadPresets().catch(() => null).finally(route);
}

init();
