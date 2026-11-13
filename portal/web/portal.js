/* Solarmaa Portal – spricht direkt mit Supabase (Auth + REST). Keine Fremdbibliotheken.
   Die Datenbank entscheidet über jede Zeile, wer sie sehen darf (Row Level Security). */
(() => {
  "use strict";
  const { url: URL_, key: KEY } = window.PORTAL;
  const $ = (id) => document.getElementById(id);
  const L = "de-CH";
  const nf0 = new Intl.NumberFormat(L, { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat(L, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtW = (w) => (w == null ? "–" : Math.abs(w) < 1000 ? `${nf0.format(Math.abs(w))} W` : `${nf1.format(Math.abs(w) / 1000)} kW`);
  const kwh = (wh) => (wh == null ? "–" : (Math.abs(wh) < 100000 ? nf1 : nf0).format(wh / 1000));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLS = "id,name,ort,kwp,letzter_kontakt,version,live,heute,status,stoerung,abregelung,alarme,fernwartung";
  const ONLINE_S = 300;

  /* ------------------------------------------------------------ Sitzung */
  let sess = null, admin = false, plants = [], current = null, timer = null;
  const store = {
    load() { try { return JSON.parse(localStorage.getItem("sm_portal") || "null"); } catch (e) { return null; } },
    save(s) { try { s ? localStorage.setItem("sm_portal", JSON.stringify(s)) : localStorage.removeItem("sm_portal"); } catch (e) { /* privat */ } },
  };
  class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

  async function raw(path, opt = {}, token) {
    const r = await fetch(URL_ + path, { ...opt, headers: { apikey: KEY, "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opt.headers || {}) } });
    let data = null;
    try { data = await r.json(); } catch (e) { /* leer */ }
    if (!r.ok) throw new ApiError(r.status, (data && (data.msg || data.message || data.error_description || data.error)) || `Fehler ${r.status}`);
    return data;
  }
  function setSess(d) {
    sess = d ? { access: d.access_token, refresh: d.refresh_token, exp: Date.now() + (d.expires_in || 3600) * 1000, email: d.user?.email || sess?.email } : null;
    store.save(sess);
  }
  async function token() {
    if (!sess) throw new ApiError(401, "Bitte anmelden");
    if (Date.now() > sess.exp - 60000) {
      try { setSess(await raw("/auth/v1/token?grant_type=refresh_token", { method: "POST", body: JSON.stringify({ refresh_token: sess.refresh }) })); }
      catch (e) { setSess(null); throw new ApiError(401, "Sitzung abgelaufen"); }
    }
    return sess.access;
  }
  const get = async (path) => raw("/rest/v1/" + path, {}, await token());
  const rpc = async (fn, args = {}) => raw("/rest/v1/rpc/" + fn, { method: "POST", body: JSON.stringify(args) }, await token());

  /* ------------------------------------------------------------ Ansichten */
  function show(view) {
    ["vLogin", "vNewPw", "vList", "vPlant"].forEach((v) => { $(v).hidden = v !== view; });
    const inApp = view === "vList" || view === "vPlant";
    $("logout").hidden = !inApp; $("who").hidden = !inApp;
    if (inApp) $("who").textContent = sess?.email || "";
    clearInterval(timer); clearInterval(fern.timer);
    if (view !== "vPlant") fernLeeren();
    if (view === "vList") timer = setInterval(() => loadList().catch(onErr), 30000);
    if (view === "vPlant") timer = setInterval(() => loadPlant(current, false).catch(onErr), 30000);
    window.scrollTo(0, 0);
  }
  function onErr(e) { if (e.status === 401) { setSess(null); show("vLogin"); } }
  const online = (p) => p.letzter_kontakt && (Date.now() - new Date(p.letzter_kontakt).getTime()) / 1000 < ONLINE_S;
  function statePill(p) {
    if (!p.letzter_kontakt) return '<span class="pill off">noch nie verbunden</span>';
    if (!online(p)) return `<span class="pill off">offline seit ${esc(new Date(p.letzter_kontakt).toLocaleString(L, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }))}</span>`;
    if (p.stoerung || p.status === "fehler") return '<span class="pill bad">Störung</span>';
    if (p.abregelung?.aktiv) return '<span class="pill cur">abgeregelt</span>';
    return '<span class="pill ok">online</span>';
  }

  async function loadList() {
    plants = await get(`anlagen?select=${COLS}&order=name`);
    const on = plants.filter(online), bad = on.filter((p) => p.stoerung || p.status === "fehler");
    const pv = on.reduce((a, p) => a + (p.live?.pv_w || 0), 0);
    $("summary").innerHTML = plants.length > 1 ? `<span class="pill">${plants.length} Anlagen</span><span class="pill ok">${on.length} online</span>${plants.length - on.length ? `<span class="pill off">${plants.length - on.length} offline</span>` : ""}${bad.length ? `<span class="pill bad">${bad.length} mit Störung</span>` : ""}<span class="pill">Produktion jetzt ${fmtW(pv)}</span>` : "";
    $("empty").hidden = plants.length > 0 || admin;
    $("plants").innerHTML = plants.map((p) => `<button class="plant" data-id="${esc(p.id)}">
        <span class="p-top"><span><span class="p-name">${esc(p.name)}</span><br><span class="muted small">${esc(p.ort)}${p.kwp ? ` · ${nf1.format(p.kwp)} kWp` : ""}</span></span>${statePill(p)}</span>
        <span class="p-val"><span class="pv"><b>${online(p) ? fmtW(p.live?.pv_w) : "–"}</b><span>Produktion</span></span><span><b>${kwh(p.heute?.pv_wh)}</b><span>kWh heute</span></span></span>
      </button>`).join("");
    $("plants").querySelectorAll(".plant").forEach((b) => b.addEventListener("click", () => openPlant(b.dataset.id)));
    if (!admin && plants.length === 1 && !current) openPlant(plants[0].id);
  }

  async function openPlant(id) { current = id; show("vPlant"); await loadPlant(id, true).catch(onErr); }

  async function loadPlant(id, first) {
    const rows = await get(`anlagen?select=${COLS}&id=eq.${encodeURIComponent(id)}`);
    const p = rows[0];
    if (!p) { current = null; show("vList"); return loadList(); }
    $("pAdmin").hidden = !admin;
    const l = online(p) ? p.live || {} : {}, h = p.heute || {};
    $("pName").textContent = p.name;
    $("pMeta").textContent = [p.ort, p.kwp ? `${nf1.format(p.kwp)} kWp` : "", p.version ? `Software ${p.version}` : ""].filter(Boolean).join(" · ");
    $("pState").outerHTML = statePill(p).replace('<span class="pill', '<span id="pState" class="pill');
    const alarme = (p.alarme || []).filter((a) => a && a.text);
    $("pAlarm").hidden = !(online(p) && alarme.length);
    $("pAlarm").textContent = alarme.map((a) => a.text).join(" · ");
    $("pCurtail").hidden = !(online(p) && p.abregelung?.aktiv);
    $("pCurtail").textContent = `Einspeisung abgeregelt: ${p.abregelung?.grund || "negativer Strompreis"}${p.abregelung?.modus === "simulation" ? " (Simulation)" : ""}`;
    const tile = (cls, label, val, sub) => `<div class="tile ${cls}"><span>${label}</span><b>${val}</b>${sub ? ` <small>${sub}</small>` : ""}</div>`;
    const netz = l.grid_w == null ? "Netz" : l.grid_w < -15 ? "Einspeisung" : l.grid_w > 15 ? "Netzbezug" : "Netz";
    const ev = (l.verbraucher || []).filter((v) => v.art === "wallbox");
    $("pNow").innerHTML = tile("pv", "Produktion", fmtW(l.pv_w)) + tile("home", "Verbrauch", fmtW(l.load_w)) + tile("grid", netz, fmtW(l.grid_w))
      + (l.soc != null || l.battery_w != null ? tile("bat", "Batterie", l.soc != null ? `${l.soc_geschaetzt ? "ca. " : ""}${nf0.format(l.soc)} %` : "–", l.battery_w == null ? "" : l.battery_w > 15 ? `entlädt ${fmtW(l.battery_w)}` : l.battery_w < -15 ? `lädt ${fmtW(l.battery_w)}` : "") : "")
      + (ev.length ? tile("", "Auto", fmtW(ev.reduce((a, v) => a + (v.w || 0), 0)), esc(ev[0].status || "")) : "");
    const eig = h.pv_wh > 50 ? Math.max(0, Math.min(100, (100 * (h.pv_wh - (h.export_wh || 0))) / h.pv_wh)) : null;
    const aut = h.load_wh > 50 ? Math.max(0, Math.min(100, (100 * (h.load_wh - (h.import_wh || 0))) / h.load_wh)) : null;
    $("pToday").innerHTML = tile("pv", "Produziert", kwh(h.pv_wh), "kWh") + tile("home", "Verbraucht", kwh(h.load_wh), "kWh") + tile("grid", "Eingespeist", kwh(h.export_wh), "kWh")
      + tile("grid", "Vom Netz", kwh(h.import_wh), "kWh") + tile("", "Eigenverbrauch", eig == null ? "–" : `${nf0.format(eig)} %`) + tile("", "Autarkie", aut == null ? "–" : `${nf0.format(aut)} %`);
    const t0 = new Date(); t0.setHours(0, 0, 0, 0);
    const d30 = new Date(t0); d30.setDate(d30.getDate() - 29);
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const [mw, tw] = await Promise.all([
      get(`messwerte?select=ts,pv_w,load_w&anlage_id=eq.${encodeURIComponent(id)}&ts=gte.${encodeURIComponent(t0.toISOString())}&order=ts`),
      get(`tageswerte?select=tag,pv_wh,load_wh&anlage_id=eq.${encodeURIComponent(id)}&tag=gte.${iso(d30)}&order=tag`),
    ]);
    drawDay(mw, t0.getTime());
    drawDays(tw, d30);
    $("pAdmin").hidden = !admin;
    $("pFern").hidden = !admin;
    if (admin && first) { loadMembers(id); loadFern(id, p).catch(onErr); }
    else if (admin) { fern.p = p; fernStatus(); }
  }

  /* ------------------------------------------------------------ Diagramme */
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, text) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; return n; };
  function niceMax(v) { if (v <= 0) return 1; const raw_ = v / 4, p = 10 ** Math.floor(Math.log10(raw_)); for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw_) return 4 * m * p; return 40 * p; }
  function frame(svg, max, unit) {
    svg.replaceChildren();
    const W = Math.max(280, svg.clientWidth || 600), H = 230, Lp = 42, R = 8, T = 22, B = 24;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    for (let i = 0; i <= 4; i++) {
      const y = H - B - ((H - B - T) * i) / 4, v = (max * i) / 4;
      svg.append(el("line", { x1: Lp, x2: W - R, y1: y, y2: y, class: "g" }));
      svg.append(el("text", { x: Lp - 6, y: y + 4, "text-anchor": "end" }, (max < 4 && v % 1 ? nf1 : nf0).format(v)));
    }
    svg.append(el("text", { x: 4, y: 11 }, unit));
    return { W, H, Lp, R, T, B };
  }
  function drawDay(rows, t0) {
    const svg = $("chDay");
    const max = niceMax(Math.max(0.5, ...rows.map((r) => Math.max(r.pv_w || 0, r.load_w || 0) / 1000)));
    const f = frame(svg, max, "kW"), span = 86400000;
    const x = (t) => f.Lp + ((t - t0) / span) * (f.W - f.Lp - f.R), y = (w) => f.H - f.B - (w / 1000 / max) * (f.H - f.B - f.T);
    for (let h = 0; h <= 24; h += 6) svg.append(el("text", { x: x(t0 + h * 3600000), y: f.H - 6, "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, `${String(h).padStart(2, "0")}:00`));
    if (!rows.length) { svg.append(el("text", { x: f.W / 2, y: f.H / 2, "text-anchor": "middle" }, "Heute noch keine Werte")); return; }
    const path = (k) => rows.map((r, i) => { const t = new Date(r.ts).getTime(); const gap = i && t - new Date(rows[i - 1].ts).getTime() > 900000; return `${!i || gap ? "M" : "L"}${x(t).toFixed(1)} ${y(r[k] || 0).toFixed(1)}`; }).join("");
    const first = new Date(rows[0].ts).getTime(), last = new Date(rows[rows.length - 1].ts).getTime();
    svg.append(el("path", { d: `${path("pv_w")}L${x(last).toFixed(1)} ${y(0)}L${x(first).toFixed(1)} ${y(0)}Z`, class: "pv-a" }));
    svg.append(el("path", { d: path("pv_w"), class: "pv-l" }));
    svg.append(el("path", { d: path("load_w"), class: "ho-l" }));
  }
  function drawDays(rows, start) {
    const svg = $("chDays");
    const by = Object.fromEntries(rows.map((r) => [r.tag, r]));
    const max = niceMax(Math.max(1, ...rows.map((r) => Math.max(r.pv_wh, r.load_wh) / 1000)));
    const f = frame(svg, max, "kWh"), sw = (f.W - f.Lp - f.R) / 30, bw = Math.max(2, Math.min(12, sw * 0.38));
    const y = (wh) => f.H - f.B - (wh / 1000 / max) * (f.H - f.B - f.T);
    for (let i = 0; i < 30; i++) {
      const d = new Date(start); d.setDate(d.getDate() + i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`, r = by[key], cx = f.Lp + sw * (i + 0.5);
      if (d.getDate() % 5 === 0) svg.append(el("text", { x: cx, y: f.H - 6, "text-anchor": "middle" }, String(d.getDate())));
      if (!r) continue;
      const g = el("g");
      g.append(el("rect", { x: cx - bw - 0.5, y: y(r.pv_wh), width: bw, height: Math.max(0, y(0) - y(r.pv_wh)), rx: 1.5, class: "b-pv" }));
      g.append(el("rect", { x: cx + 0.5, y: y(r.load_wh), width: bw, height: Math.max(0, y(0) - y(r.load_wh)), rx: 1.5, class: "b-ho" }));
      g.append(el("title", {}, `${d.toLocaleDateString(L)}: Produktion ${kwh(r.pv_wh)} kWh, Verbrauch ${kwh(r.load_wh)} kWh`));
      svg.append(g);
    }
  }

  /* ------------------------------------------------------------ Verwaltung (Admin) */
  async function loadMembers(id) {
    const m = await get(`mitglieder?select=email,user_id&anlage_id=eq.${encodeURIComponent(id)}&order=email`);
    $("members").innerHTML = m.map((x) => `<li><span>${esc(x.email)}</span><button class="btn ghost" data-mail="${esc(x.email)}">Entfernen</button></li>`).join("") || '<li class="muted">Noch kein Kunde zugeordnet</li>';
    $("members").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
      try { await rpc("mitglied_setzen", { p_anlage: id, p_email: b.dataset.mail, p_entfernen: true }); loadMembers(id); } catch (e) { showErr("memErr", e); }
    }));
  }
  function showErr(id, e) {
    const n = $(id);
    // Die Datenbank verlangt für heikle Aktionen eine Zwei-Faktor-Anmeldung: verständlich weiterleiten
    n.textContent = /Zwei-Faktor/i.test(e.message || "") ? "Dafür ist eine Zwei-Faktor-Anmeldung nötig. Bitte unten bei «Fernwartung» den Zwei-Faktor einrichten bzw. bestätigen und dann erneut versuchen." : e.message;
    n.hidden = false;
  }
  function showToken(name, tok) {
    $("tokenText").textContent = `[portal]\nurl = "${URL_}"\nschluessel = "${KEY}"\ntoken = "${tok}"\nfernwartung = true\n`;
    $("tokenBox").hidden = false; $("tokenBox").querySelector("h2").textContent = `Pi-Token für «${name}»`;
    $("tokenBox").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("addOpen").addEventListener("click", () => { $("fAdd").hidden = false; $("aName").focus(); });
  $("addCancel").addEventListener("click", () => { $("fAdd").hidden = true; });
  $("fAdd").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("addErr").hidden = true;
    const name = $("aName").value.trim();
    if (!name) return showErr("addErr", new Error("Bitte einen Namen eingeben"));
    try {
      const r = await rpc("anlage_anlegen", { p_name: name, p_ort: $("aOrt").value.trim(), p_kwp: parseFloat($("aKwp").value.replace(",", ".")) || 0 });
      $("fAdd").hidden = true; $("fAdd").reset(); showToken(name, r.token); loadList();
    } catch (e) { showErr("addErr", e); }
  });
  $("tokenCopy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("tokenText").textContent); $("tokenCopy").textContent = "Kopiert"; }
    catch (e) { const r = document.createRange(); r.selectNodeContents($("tokenText")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
  });
  $("tokenClose").addEventListener("click", () => { $("tokenBox").hidden = true; $("tokenText").textContent = ""; $("tokenCopy").textContent = "Kopieren"; });
  $("tokenNew").addEventListener("click", async () => {
    const b = $("tokenNew");
    if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Wirklich? Der alte Token wird ungültig."; return; }
    b.dataset.sure = ""; b.textContent = "Neuen Pi-Token erzeugen"; $("memErr").hidden = true;
    try { const r = await rpc("anlage_token_neu", { p_anlage: current }); const p = plants.find((x) => x.id === current); show("vList"); await loadList(); showToken(p?.name || "", r.token); }
    catch (e) { showErr("memErr", e); }
  });
  $("fMember").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("memErr").hidden = true;
    try { await rpc("mitglied_setzen", { p_anlage: current, p_email: $("mEmail").value.trim(), p_entfernen: false }); $("mEmail").value = ""; loadMembers(current); }
    catch (e) { showErr("memErr", e); }
  });

  /* ------------------------------------------------------------ Fernwartung (Admin mit Zwei-Faktor) */
  // Die Datenbank prüft jede Aktion selbst (Admin + Zwei-Faktor). Hier wird nur angezeigt und gesendet.
  const fern = { id: null, p: null, orig: null, faktor: null, timer: null };
  function aal() {
    try {
      const t = sess.access.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(t + "=".repeat((4 - (t.length % 4)) % 4))).aal || "";
    } catch (e) { return ""; }
  }
  const zeit = (t) => (t ? new Date(t).toLocaleString(L, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
  const sure = (b, frage, text) => { if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = frage; return false; } b.dataset.sure = ""; b.textContent = text; return true; };
  function mfaLeeren() {
    // Schlüssel und QR-Code der Zwei-Faktor-Einrichtung nie länger als nötig im Dokument lassen
    $("mfaSecret").textContent = ""; $("mfaQr").removeAttribute("src");
    $("mfaCode").value = ""; $("mfaEnrollCode").value = "";
    $("mfaEnroll").hidden = true; $("mfaErr").hidden = true;
    fern.faktor = null;
  }
  function fernLeeren() {
    clearInterval(fern.timer);
    fern.id = null; fern.p = null; fern.orig = null;
    mfaLeeren();
    $("fernKonfig").value = ""; $("fernKonfig").hidden = true; $("fernKonfigSenden").hidden = true;
    ["fernBefehle", "fernStand", "fernMsg", "fernErr", "fernHinweis"].forEach((id) => { $(id).textContent = ""; });
    $("fernMsg").hidden = true; $("fernErr").hidden = true;
    [["fernUpdate", "Update auslösen"], ["fernKonfigSenden", "Konfiguration senden"]].forEach(([id, text]) => { $(id).dataset.sure = ""; $(id).textContent = text; });
  }
  function fernStatus() {
    const p = fern.p, ok2 = aal() === "aal2";
    if (!p) return;
    const grund = !ok2 ? "Fernwartung ist erst nach der Zwei-Faktor-Bestätigung möglich."
      : !p.fernwartung ? "Fernwartung ist auf dieser Anlage ausgeschaltet. Einschalten nur vor Ort: in der Konfiguration des Pi unter [portal] die Zeile fernwartung = true ergänzen (ab Software 1.6.0)."
      : !online(p) ? "Die Anlage ist offline. Befehle sind erst wieder möglich, wenn sich der Pi meldet." : "";
    $("fernHinweis").textContent = grund; $("fernHinweis").hidden = !grund;
    ["fernUpdate", "fernKonfigLaden", "fernKonfigSenden"].forEach((id) => { $(id).disabled = !!grund; });
  }
  async function loadMfa() {
    const ok2 = aal() === "aal2";
    $("mfaOk").hidden = !ok2; $("fMfa").hidden = true; $("mfaNeu").hidden = true;
    if (ok2) return;
    const u = await raw("/auth/v1/user", {}, await token());
    fern.faktor = ((u && u.factors) || []).find((f) => (f.factor_type || "totp") === "totp" && f.status === "verified") || null;
    $("fMfa").hidden = !fern.faktor; $("mfaNeu").hidden = !!fern.faktor || !$("mfaEnroll").hidden;
  }
  async function mfaPruefen(faktorId, code) {
    if (!/^\d{6}$/.test(code)) throw new Error("Bitte den 6-stelligen Code eingeben");
    const tok = await token();
    const ch = await raw(`/auth/v1/factors/${encodeURIComponent(faktorId)}/challenge`, { method: "POST", body: "{}" }, tok);
    let d;
    try { d = await raw(`/auth/v1/factors/${encodeURIComponent(faktorId)}/verify`, { method: "POST", body: JSON.stringify({ challenge_id: ch.id, code }) }, tok); }
    catch (e) { throw e.status === 401 ? e : new Error("Code falsch oder abgelaufen. Bitte den aktuellen Code aus der App eingeben."); }
    if (!d || !d.access_token) throw new Error("Bestätigung fehlgeschlagen");
    setSess(d);
    mfaLeeren();
    await loadMfa(); fernStatus(); loadBefehle().catch(onErr);
  }
  $("fMfa").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("mfaErr").hidden = true;
    try { await mfaPruefen(fern.faktor.id, $("mfaCode").value.trim()); } catch (e) { $("mfaCode").value = ""; showErr("mfaErr", e); onErr(e); }
  });
  $("mfaSetup").addEventListener("click", async () => {
    $("mfaErr").hidden = true;
    try {
      const tok = await token();
      const u = await raw("/auth/v1/user", {}, tok);
      // angefangene, nie bestätigte Einrichtungen entfernen
      for (const f of ((u && u.factors) || []).filter((x) => x.status !== "verified")) await raw(`/auth/v1/factors/${encodeURIComponent(f.id)}`, { method: "DELETE" }, tok);
      const d = await raw("/auth/v1/factors", { method: "POST", body: JSON.stringify({ factor_type: "totp", friendly_name: "Solarmaa Portal" }) }, tok);
      const qr = (d.totp && d.totp.qr_code) || "";
      fern.faktor = { id: d.id, neu: true };
      $("mfaQr").src = qr.startsWith("data:") ? qr : "data:image/svg+xml;utf8," + encodeURIComponent(qr);
      $("mfaSecret").textContent = (d.totp && d.totp.secret) || "";
      $("mfaNeu").hidden = true; $("mfaEnroll").hidden = false; $("mfaEnrollCode").focus();
    } catch (e) { showErr("mfaErr", e); onErr(e); }
  });
  $("mfaEnroll").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("mfaErr").hidden = true;
    try { await mfaPruefen(fern.faktor.id, $("mfaEnrollCode").value.trim()); } catch (e) { $("mfaEnrollCode").value = ""; showErr("mfaErr", e); onErr(e); }
  });

  const ART = { update: "Update", konfig: "Konfiguration" };
  function befehlStand(b) {
    const alt = (t, min) => t && Date.now() - new Date(t).getTime() > min * 60000;
    if (b.status === "offen") return alt(b.erstellt, 60) ? ["off", "verfallen"] : ["cur", "wartet auf den Pi"];
    if (b.status === "gesendet") return alt(b.gesendet_am, 45) ? ["warn", "keine Quittung"] : ["cur", "abgeholt, läuft"];
    if (b.status === "erledigt") return ["ok", "erledigt"];
    if (b.status === "fehler") return ["bad", "fehler"];
    return ["off", "verfallen"];
  }
  async function loadBefehle() {
    const id = fern.id;
    if (!id) return;
    const rows = await get(`befehle?select=id,art,status,ergebnis,erstellt,erstellt_von,gesendet_am,erledigt_am&anlage_id=eq.${encodeURIComponent(id)}&order=erstellt.desc&limit=20`);
    if (fern.id !== id) return;
    // nutzlast wird nie angefordert (gesperrt); ergebnis kommt vom Pi und wird nur als Text eingesetzt
    $("fernBefehle").innerHTML = rows.map((b) => { const [cls, text] = befehlStand(b); return `<li><span class="cmd"><b>${esc(ART[b.art] || b.art)}</b><span class="pill ${cls}">${esc(text)}</span><br><span class="muted small">${esc(zeit(b.erstellt))}${b.erstellt_von ? ` · ${esc(b.erstellt_von)}` : ""}${b.erledigt_am ? ` · quittiert ${esc(zeit(b.erledigt_am))}` : ""}</span>${b.ergebnis ? `<span class="cmd-res">${esc(b.ergebnis)}</span>` : ""}</span></li>`; }).join("") || '<li class="muted">Noch kein Befehl gesendet</li>';
    clearInterval(fern.timer);
    const laeuft = rows.some((b) => { const s = befehlStand(b)[1]; return s === "wartet auf den Pi" || s === "abgeholt, läuft"; });
    if (laeuft) fern.timer = setInterval(() => loadBefehle().catch(onErr), 10000);
  }
  async function loadFern(id, p) {
    fernLeeren();
    fern.id = id; fern.p = p;
    fernStatus();
    await loadMfa(); fernStatus();
    await loadBefehle();
  }
  async function sha256(text) {
    const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
  }
  const fernMsg = (text) => { $("fernMsg").textContent = text; $("fernMsg").hidden = !text; };
  $("fernUpdate").addEventListener("click", async () => {
    const b = $("fernUpdate");
    if (!sure(b, "Wirklich? Der Dienst auf dem Pi startet neu.", "Update auslösen")) return;
    $("fernErr").hidden = true; fernMsg("");
    try { await rpc("befehl_anlegen", { p_anlage: fern.id, p_art: "update" }); fernMsg("Update angefordert. Der Pi holt den Befehl bei seiner nächsten Meldung ab (etwa eine Minute)."); await loadBefehle(); }
    catch (e) { showErr("fernErr", e); onErr(e); }
  });
  $("fernKonfigLaden").addEventListener("click", async () => {
    $("fernErr").hidden = true; fernMsg("");
    try {
      const id = fern.id, rows = await get(`anlagen_konfig?select=text,stand&anlage_id=eq.${encodeURIComponent(id)}`);
      if (fern.id !== id) return;
      const k = rows[0];
      fern.orig = k && k.text ? k.text : null;
      $("fernKonfig").value = fern.orig || ""; $("fernKonfig").hidden = !fern.orig; $("fernKonfigSenden").hidden = !fern.orig;
      $("fernStand").textContent = fern.orig ? `Stand ${zeit(k.stand)}` : "";
      if (!fern.orig) fernMsg("Der Pi hat keine Konfiguration übertragen (Fernwartung auf dem Pi aus, oder er hat sich seit dem Einschalten noch nicht gemeldet).");
    } catch (e) { showErr("fernErr", e); onErr(e); }
  });
  $("fernKonfigSenden").addEventListener("click", async () => {
    const b = $("fernKonfigSenden");
    if (!fern.orig) return;
    if (!sure(b, "Wirklich senden? Der Dienst auf dem Pi startet neu.", "Konfiguration senden")) return;
    $("fernErr").hidden = true; fernMsg("");
    try {
      // basis = Stand, der hier geladen wurde: Der Pi lehnt ab, wenn seine Datei inzwischen anders ist
      await rpc("befehl_anlegen", { p_anlage: fern.id, p_art: "konfig", p_nutzlast: { text: $("fernKonfig").value, basis: await sha256(fern.orig) } });
      fern.orig = null; $("fernKonfig").value = ""; $("fernKonfig").hidden = true; b.hidden = true; $("fernStand").textContent = "";
      fernMsg("Gesendet. Der Pi prüft die Konfiguration, speichert sie und startet neu. Das Ergebnis erscheint unten.");
      await loadBefehle();
    } catch (e) { showErr("fernErr", e); onErr(e); }
  });

  /* ------------------------------------------------------------ Anmeldung */
  async function enter() {
    admin = false;
    try { admin = (await rpc("ist_admin")) === true; } catch (e) { if (e.status === 401) throw e; }
    $("addOpen").hidden = !admin;
    current = null;
    show("vList");
    await loadList();
  }
  $("fLogin").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("loginErr").hidden = true; $("loginBtn").disabled = true;
    try {
      setSess(await raw("/auth/v1/token?grant_type=password", { method: "POST", body: JSON.stringify({ email: $("email").value.trim(), password: $("pw").value }) }));
      $("pw").value = "";
      await enter();
    } catch (e) { showErr("loginErr", e.status === 400 ? new Error("E-Mail oder Passwort falsch") : e); }
    finally { $("loginBtn").disabled = false; }
  });
  $("forgot").addEventListener("click", async () => {
    const mail = $("email").value.trim(), msg = $("forgotMsg");
    msg.hidden = false;
    if (!mail) { msg.textContent = "Bitte oben zuerst die E-Mail-Adresse eintragen."; return; }
    try { await raw("/auth/v1/recover", { method: "POST", body: JSON.stringify({ email: mail }) }); } catch (e) { /* absichtlich gleiche Meldung */ }
    msg.textContent = "Falls es ein Konto mit dieser Adresse gibt, ist eine E-Mail mit einem Link unterwegs.";
  });
  $("fNewPw").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("npwErr").hidden = true;
    const pw = $("npw").value;
    if (pw.length < 10) return showErr("npwErr", new Error("Mindestens 10 Zeichen"));
    try { await raw("/auth/v1/user", { method: "PUT", body: JSON.stringify({ password: pw }) }, await token()); $("npw").value = ""; await enter(); }
    catch (e) { showErr("npwErr", e); }
  });
  $("logout").addEventListener("click", async () => {
    try { await raw("/auth/v1/logout", { method: "POST" }, sess?.access); } catch (e) { /* egal */ }
    setSess(null); plants = []; current = null; admin = false;
    // nichts vom vorherigen Konto im Dokument stehen lassen
    ["plants", "members", "summary", "pNow", "pToday", "tokenText", "pName", "pMeta", "pAlarm", "pCurtail", "chDay", "chDays", "who", "memErr"].forEach((id) => { $(id).textContent = ""; });
    $("pAdmin").hidden = true; $("addOpen").hidden = true; $("tokenBox").hidden = true;
    fernLeeren(); $("pFern").hidden = true;
    show("vLogin");
  });
  $("back").addEventListener("click", () => { current = null; show("vList"); loadList().catch(onErr); });
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (current) loadPlant(current, false).catch(onErr); }, 200); });

  (async () => {
    // Link aus Einladungs- oder Passwort-E-Mail: #access_token=…&type=recovery|invite
    const hash = new URLSearchParams(location.hash.slice(1));
    if (hash.get("access_token") && ["recovery", "invite", "signup", "magiclink"].includes(hash.get("type") || "")) {
      setSess({ access_token: hash.get("access_token"), refresh_token: hash.get("refresh_token"), expires_in: Number(hash.get("expires_in")) || 3600 });
      history.replaceState(null, "", location.pathname);
      try { const u = await raw("/auth/v1/user", {}, sess.access); sess.email = u.email; store.save(sess); } catch (e) { /* weiter */ }
      if (["recovery", "invite"].includes(hash.get("type"))) return show("vNewPw");
    } else {
      sess = store.load();
    }
    if (!sess) return show("vLogin");
    try { await enter(); } catch (e) { setSess(null); show("vLogin"); }
  })();
})();
