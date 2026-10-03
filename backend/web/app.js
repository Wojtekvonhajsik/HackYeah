"use strict";

// Kraków bez barier - aplikacja webowa (mobile-first). Bez frameworka i bez kroku budowania:
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
  } else if (!profile()) {
    store.set("returnTo", hash); // po wyborze profilu wracamy tam, dokąd szedł użytkownik
    view = "profile";
  } else if (hash.startsWith("#/miejsce/")) {
    view = "place";
    arg = decodeURIComponent(hash.slice("#/miejsce/".length));
  }
  for (const name of ["profile", "search", "place"]) $(`#view-${name}`).hidden = name !== view;
  $("#profile-chip-label").textContent = profileLabel();
  stopSpeaking();
  if (view === "profile") showProfile();
  if (view === "search") showSearch();
  if (view === "place") showPlace(arg);
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

async function onSearchSubmit(event) {
  event.preventDefault();
  const query = $("#search-input").value.trim();
  if (!query) return;
  const box = $("#search-results");
  box.innerHTML = loadingHtml("Szukam…");
  announce("Szukam…");
  try {
    const results = await api(`/places/search?q=${encodeURIComponent(query)}`);
    box.innerHTML = results.length
      ? `<h2 class="section-title">Wyniki</h2><ul class="place-list">${results.map(placeItem).join("")}</ul>`
      : `<p class="notice info">Nie znaleźliśmy „${esc(query)}” w Krakowie. Spróbuj innej nazwy.</p>`;
    announce(results.length ? `Znaleziono miejsc: ${results.length}.` : "Brak wyników.");
  } catch (error) {
    box.innerHTML = `<p class="notice warn" role="alert">${esc(error.message)}</p>`;
  }
}

function setupVoiceSearch() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return; // przeglądarka bez rozpoznawania mowy - przycisk zostaje ukryty
  const mic = $("#mic");
  mic.hidden = false;
  mic.addEventListener("click", () => {
    const recognition = new Recognition();
    recognition.lang = "pl-PL";
    recognition.interimResults = false;
    mic.dataset.listening = "true";
    announce("Słucham. Powiedz nazwę miejsca.");
    recognition.onresult = (event) => {
      $("#search-input").value = event.results[0][0].transcript;
      $("#search-form").requestSubmit();
    };
    recognition.onerror = () => announce("Nie udało się rozpoznać mowy. Wpisz nazwę miejsca.");
    recognition.onend = () => { mic.dataset.listening = "false"; };
    recognition.start();
  });
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

async function showPlace(id) {
  $("#place-title").textContent = "Sprawdzam miejsce…";
  $("#place-address").textContent = "";
  $("#place-body").innerHTML = loadingHtml(
    "Sprawdzam miejsce. Za pierwszym razem pobieramy dane z OpenStreetMap - może to potrwać do 30 sekund.",
  );
  announce("Sprawdzam miejsce…");
  try {
    const assessment = await fetchAssessment(id);
    const places = await api("/places");
    const place = places.find((p) => p.id === id) || { id, name: "Miejsce" };
    if (location.hash !== `#/miejsce/${encodeURIComponent(id)}`) return; // użytkownik zdążył przejść dalej
    current = { id, place, assessment };
    rememberRecent(place);
    renderPlace();
    announce(`${place.name}: ${SUMMARY[assessment.summary].title}.`);
    focusHeading("#place-title");
  } catch (error) {
    $("#place-title").textContent = "Nie udało się sprawdzić miejsca";
    $("#place-body").innerHTML = `
      <p class="notice warn" role="alert">${esc(error.message)}</p>
      <button class="btn primary" type="button" data-action="retry">Spróbuj ponownie</button>`;
  }
}

function confidenceWord(pct) {
  if (pct >= 60) return "wysoka";
  if (pct >= 30) return "średnia";
  return "niska";
}

function meter(pct, label) {
  if (pct === null || pct === undefined) {
    return `<div class="meter"><div class="meter-label"><span>${label}</span><span>brak danych</span></div></div>`;
  }
  return `<div class="meter">
    <div class="meter-label"><span>${label}</span><strong>${pct}% (${confidenceWord(pct)})</strong></div>
    <div class="meter-bar" aria-hidden="true"><div class="meter-fill" style="width:${Number(pct)}%"></div></div>
  </div>`;
}

function sourceLine(evidence) {
  const url = safeUrl(evidence.source.url);
  const name = esc(SOURCE[evidence.source.type] || evidence.source.type);
  const linked = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${name}</a>` : name;
  const sample = evidence.source.sample ? " (przykład)" : "";
  return `${linked}${sample}, ${fmtDate(evidence.source.observed_at)}`;
}

function evidenceItem(evidence) {
  const myVote = (store.get("votes") || {})[evidence.observation_id];
  const url = safeUrl(evidence.source.url);
  const verdict = VERDICT[evidence.verdict];
  return `<li class="evidence-item">
    <p><strong>${sourceLine(evidence)}</strong> · ${STATUS[evidence.status]}</p>
    <p>${evidence.verdict === "unknown" ? "" : `${verdict.label}: `}${esc(cap(evidence.reasons.join("; ")))}</p>
    ${evidence.note ? `<p class="muted">Opis: ${esc(evidence.note)}</p>` : ""}
    <p class="muted">${esc(evidence.source.name)}${evidence.source.license ? ` · licencja ${esc(evidence.source.license)}` : ""}${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener">zobacz źródło</a>` : ""}</p>
    <p class="muted">Potwierdzenia: ${evidence.confirmations} · Zaprzeczenia: ${evidence.denials}</p>
    <div class="vote" role="group" aria-label="Czy ta informacja się zgadza?">
      <button class="btn small" type="button" data-vote="confirm" data-obs="${esc(evidence.observation_id)}" aria-pressed="${myVote === "confirm"}">✓ Zgadza się</button>
      <button class="btn small" type="button" data-vote="deny" data-obs="${esc(evidence.observation_id)}" aria-pressed="${myVote === "deny"}">✕ Nie zgadza się</button>
    </div>
  </li>`;
}

function featureCard(feature) {
  const verdict = VERDICT[feature.verdict];
  const primary = feature.evidence.find((e) => e.observation_id === feature.primary_observation_id) || feature.evidence[0];
  return `<li class="card feature" id="f-${esc(feature.feature_id)}">
    <div class="feature-head">
      <span class="verdict-icon v-${feature.verdict}" aria-hidden="true">${verdict.symbol}</span>
      <h3>${esc(cap(feature.label))}</h3>
      <span class="badge v-${feature.verdict}">${verdict.label}</span>
    </div>
    <ul class="reasons">${feature.reasons.map((r) => `<li>${esc(cap(r))}</li>`).join("")}</ul>
    ${feature.type === "amenity" ? "" : meter(feature.confidence_pct, "Pewność")}
    ${feature.conflict ? `<p class="conflict">Źródła podają różne informacje - pokazujemy ostrożniejszą.</p>` : ""}
    <p class="source">Źródło: ${sourceLine(primary)} · <span class="status">${STATUS[feature.status]}</span></p>
    <details class="evidence" data-feature="${esc(feature.feature_id)}">
      <summary>Źródła (${feature.evidence.length}) i Twoja opinia</summary>
      <ul class="evidence-list">${feature.evidence.map(evidenceItem).join("")}</ul>
    </details>
  </li>`;
}

function renderPlace() {
  const { place, assessment: a } = current;
  const summary = SUMMARY[a.summary];
  const atPlace = a.features.filter((f) => f.scope === "place");
  const around = a.features.filter((f) => f.scope === "surroundings");
  $("#place-title").textContent = place.name;
  $("#place-address").textContent = shortAddress(place.address);
  $("#place-body").innerHTML = `
    ${a.contains_sample_data ? `<p class="notice sample"><strong>Dane przykładowe.</strong> Ta ocena zawiera dane demonstracyjne, które nie opisują rzeczywistego obiektu.</p>` : ""}
    ${a.warnings.map((w) => `<p class="notice warn" role="alert">${esc(w)}</p>`).join("")}

    <section class="card summary" data-summary="${esc(a.summary)}" aria-labelledby="summary-title">
      <h2 id="summary-title"><span class="verdict-icon v-${summary.verdict}" aria-hidden="true">${VERDICT[summary.verdict].symbol}</span>${esc(summary.title)}</h2>
      <p>${esc(a.summary_text)}</p>
      ${meter(a.summary_confidence_pct, "Pewność oceny")}
      <p class="meter-note">Pewność rośnie, gdy źródła się zgadzają, i spada, gdy dane są stare lub sprzeczne.</p>
      ${a.missing.length ? `<div class="notice info"><strong>Czego nie wiemy:</strong><ul>${a.missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : ""}
      <p class="meter-note">Dla profilu: ${esc(profileLabel())} · <a href="#/profil">zmień</a></p>
      <div class="summary-actions">
        <button class="btn small" type="button" data-action="speak" aria-pressed="false"><span aria-hidden="true">🔊</span> Odczytaj na głos</button>
        <button class="btn small" type="button" data-action="share">Udostępnij</button>
      </div>
    </section>

    <div class="map-wrap">
      <div id="map" role="img" aria-label="Mapa miejsca i okolicy. Wszystkie informacje z mapy są w liście poniżej."></div>
      <p class="map-note">Mapa to uzupełnienie - pełna informacja jest w liście poniżej.</p>
    </div>

    <h2 class="section-title">Miejsce i wejście</h2>
    ${atPlace.length
      ? `<ul class="features">${atPlace.map(featureCard).join("")}</ul>`
      : `<p class="notice info">Nie mamy informacji o samym miejscu i jego wejściu. Jeśli tu jesteś, potwierdź lub popraw informacje z okolicy.</p>`}

    ${around.length ? `
      <h2 class="section-title">W okolicy</h2>
      <p class="hint">Rzeczy w promieniu ok. 30 m - mogą istnieć inne drogi dojścia.</p>
      <details class="around card" ${atPlace.length ? "" : "open"}>
        <summary>Pokaż informacje z okolicy (${around.length})</summary>
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

async function vote(button) {
  const observationId = button.dataset.obs;
  const value = button.dataset.vote;
  const featureId = button.closest("details.evidence")?.dataset.feature;
  button.disabled = true;
  try {
    await api(`/observations/${encodeURIComponent(observationId)}/votes`, {
      method: "POST",
      body: JSON.stringify({ voter_id: voterId(), value }),
    });
    const votes = store.get("votes") || {};
    votes[observationId] = value;
    store.set("votes", votes);
    current.assessment = await fetchAssessment(current.id);
    renderPlace();
    // przywracamy miejsce, w którym był użytkownik
    const details = document.querySelector(`details.evidence[data-feature="${CSS.escape(featureId || "")}"]`);
    if (details) {
      details.open = true;
      details.closest("details.around")?.setAttribute("open", "");
      details.querySelector(`[data-obs="${CSS.escape(observationId)}"][data-vote="${value}"]`)?.focus();
    }
    announce("Dziękujemy! Zapisaliśmy Twoją opinię i przeliczyliśmy ocenę.");
  } catch (error) {
    button.disabled = false;
    announce(error.message);
    alert(error.message);
  }
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
    else if (button.dataset.action === "speak") speak(button);
    else if (button.dataset.action === "share") share();
    else if (button.dataset.action === "retry") route();
  });
  window.addEventListener("hashchange", route);
  setupVoiceSearch();
  loadPresets().catch(() => null).finally(route);
}

init();
