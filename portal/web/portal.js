/* Solarmaa Portal – spricht direkt mit Supabase (Auth + REST). Keine Fremdbibliotheken.
   Die Datenbank entscheidet über jede Zeile, wer sie sehen darf (Row Level Security). */
(() => {
  "use strict";
  const { url: URL_, key: KEY } = window.PORTAL;
  const $ = (id) => document.getElementById(id);
  // Ausgangszustand der Seite (hidden/disabled/Beschriftung): Abmelden stellt genau diesen wieder her
  const AUSGANG = [...document.querySelectorAll("[id]")].map((n) => [n, n.hidden, n.disabled, n.tagName === "BUTTON" ? n.textContent : null]);
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
  // marke: zählt jeden Ansichts- und Anlagenwechsel. anm: zählt jedes Abmelden. Jede Fortsetzung nach
  // einem await prüft, ob sie noch zur angezeigten Ansicht bzw. Anmeldung gehört, und bricht sonst ab.
  // So kann eine verspätete Antwort nie die falsche Anlage füllen oder nach dem Abmelden etwas anzeigen.
  let marke = 0, anm = 0;
  // anlage: die Anlage, deren Daten gerade angezeigt werden (von loadPlant bestätigt). Verwaltung und
  // Fernwartung handeln nur für diese Anlage; bis sie bestätigt ist, ist die Ansicht leer und gesperrt.
  let anlage = null;
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
  let sessNr = 0, erneuerung = null;
  function setSess(d) {
    sessNr++;
    sess = d ? { access: d.access_token, refresh: d.refresh_token, exp: Date.now() + (d.expires_in || 3600) * 1000, email: d.user?.email || sess?.email } : null;
    store.save(sess);
  }
  // Aufruf gehört zu einer früheren Anmeldung (dazwischen abgemeldet): nicht senden, still abbrechen
  const veraltet = () => Object.assign(new ApiError(0, "abgebrochen"), { veraltet: true });
  async function token() {
    if (!sess) throw new ApiError(401, "Bitte anmelden");
    const eintritt = anm;
    if (Date.now() > sess.exp - 60000) {
      // Nur eine Erneuerung gleichzeitig; alle Aufrufer warten auf dieselbe. Wurde die Sitzung inzwischen
      // ersetzt (Abmelden, Zwei-Faktor bestätigt), wird das Ergebnis verworfen: Eine ältere Erneuerung
      // darf eine aal2-Sitzung nie mit aal1 überschreiben und eine abgemeldete nie wiederbeleben.
      if (!erneuerung) {
        const nr = sessNr, a = anm;
        erneuerung = (async () => {
          let d;
          try { d = await raw("/auth/v1/token?grant_type=refresh_token", { method: "POST", body: JSON.stringify({ refresh_token: sess.refresh }) }); }
          catch (e) { if (nr === sessNr && a === anm) setSess(null); throw new ApiError(401, "Sitzung abgelaufen"); }
          if (nr === sessNr && a === anm && sess) setSess(d);
        })().finally(() => { erneuerung = null; });
      }
      try { await erneuerung; }
      catch (e) { if (eintritt !== anm) throw veraltet(); throw e; }
      // Inzwischen abgemeldet (und evtl. jemand anders angemeldet): nie mit der neuen Sitzung senden
      if (eintritt !== anm) throw veraltet();
      if (!sess) throw new ApiError(401, "Bitte anmelden");
    }
    return sess.access;
  }
  const get = async (path) => raw("/rest/v1/" + path, {}, await token());
  const rpc = async (fn, args = {}) => raw("/rest/v1/rpc/" + fn, { method: "POST", body: JSON.stringify(args) }, await token());

  /* ------------------------------------------------------------ Ansichten */
  function show(view) {
    marke++; sureReset(); anlageLeeren();
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
  function onErr(e) { if (e.status === 401 && !e.veraltet) abmeldenLokal(); }
  const online = (p) => p.letzter_kontakt && (Date.now() - new Date(p.letzter_kontakt).getTime()) / 1000 < ONLINE_S;
  function statePill(p) {
    if (!p.letzter_kontakt) return '<span class="pill off">noch nie verbunden</span>';
    if (!online(p)) return `<span class="pill off">offline seit ${esc(new Date(p.letzter_kontakt).toLocaleString(L, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }))}</span>`;
    if (p.stoerung || p.status === "fehler") return '<span class="pill bad">Störung</span>';
    if (p.abregelung?.aktiv) return '<span class="pill cur">abgeregelt</span>';
    return '<span class="pill ok">online</span>';
  }

  async function loadList() {
    const m = marke, neu = await get(`anlagen?select=${COLS}&order=name`);
    if (m !== marke) return;
    plants = neu;
    $("listErr").hidden = true;
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

  function anlageLeeren() {
    anlage = null;
    ["pName", "pMeta", "pNow", "pToday", "chDay", "chDays", "members", "memErr", "pAlarm", "pCurtail"].forEach((id) => { $(id).textContent = ""; });
    $("pState").textContent = ""; $("pState").className = "pill";
    ["pAlarm", "pCurtail", "pAdmin", "pFern", "memErr"].forEach((id) => { $(id).hidden = true; });
    $("mEmail").value = "";
    dokLeeren();
  }
  async function openPlant(id) { current = id; show("vPlant"); await loadPlant(id, true).catch(onErr); }

  async function loadPlant(id, first) {
    const m = marke, rows = await get(`anlagen?select=${COLS}&id=eq.${encodeURIComponent(id)}`);
    if (m !== marke || current !== id) return;         // inzwischen andere Ansicht, andere Anlage oder abgemeldet
    const p = rows[0];
    if (!p) { current = null; show("vList"); return loadList(); }
    anlage = id;
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
    if (m !== marke || current !== id) return;
    drawDay(mw, t0.getTime());
    drawDays(tw, d30);
    $("pAdmin").hidden = !admin;
    $("pFern").hidden = !admin;
    if (first) loadDoku(id).catch((e) => dokFehler(m, id, e));
    if (admin && first) { loadMembers(id).catch((e) => fehlerBei(m, "memErr", e)); loadFern(id, p).catch(onErr); }
    else if (admin && fernId() === id) { fern.p = p; fernStatus(); }
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
    const mk = marke, m = await get(`mitglieder?select=email,user_id&anlage_id=eq.${encodeURIComponent(id)}&order=email`);
    if (mk !== marke || anlage !== id) return;
    $("members").innerHTML = m.map((x) => `<li><span>${esc(x.email)}</span><button class="btn ghost" data-mail="${esc(x.email)}" data-anlage="${esc(id)}">Entfernen</button></li>`).join("") || '<li class="muted">Noch kein Kunde zugeordnet</li>';
    $("members").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
      const mk2 = marke;
      if (b.dataset.anlage !== anlage || anlage !== id) return;      // Knopf gehört nicht (mehr) zur angezeigten Anlage
      try { await rpc("mitglied_setzen", { p_anlage: id, p_email: b.dataset.mail, p_entfernen: true }); if (mk2 === marke) loadMembers(id).catch((e) => fehlerBei(mk2, "memErr", e)); } catch (e) { fehlerBei(mk2, "memErr", e); }
    }));
  }
  // Fehler einer Hintergrundabfrage: 401 meldet ab, sonst Meldung, sofern die Ansicht noch dieselbe ist
  function fehlerBei(m, ziel, e) { onErr(e); if (e.status !== 401 && !e.veraltet && m === marke) showErr(ziel, e); }
  function showErr(id, e) {
    if (e && e.veraltet) return;
    const n = $(id);
    // Die Datenbank verlangt für heikle Aktionen eine Zwei-Faktor-Anmeldung: verständlich weiterleiten
    n.textContent = /Zwei-Faktor/i.test(e.message || "") ? "Dafür ist eine Zwei-Faktor-Anmeldung nötig. Bitte unten bei «Fernwartung» den Zwei-Faktor einrichten bzw. bestätigen und dann erneut versuchen." : e.message;
    n.hidden = false;
  }
  const listeFehler = (a) => (e) => {
    onErr(e);
    if (e.status === 401 || e.veraltet || a !== anm) return;
    showErr("listErr", new Error("Die Liste der Anlagen konnte nicht neu geladen werden und zeigt eventuell einen alten Stand. Der Token oben ist gültig."));
  };
  function showToken(name, tok) {
    $("tokenText").textContent = `[portal]\nurl = "${URL_}"\nschluessel = "${KEY}"\ntoken = "${tok}"\n# Fernwartung durch Solarmaa (auf false setzen, wenn nicht vereinbart)\nfernwartung = true\n`;
    $("tokenBox").hidden = false; $("tokenBox").querySelector("h2").textContent = `Pi-Token für «${name}»`;
    $("tokenBox").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("addOpen").addEventListener("click", () => { $("fAdd").hidden = false; $("aName").focus(); });
  $("addCancel").addEventListener("click", () => { $("fAdd").hidden = true; });
  $("fAdd").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("addErr").hidden = true;
    const name = $("aName").value.trim();
    if (!name) return showErr("addErr", new Error("Bitte einen Namen eingeben"));
    const a = anm;
    try {
      const r = await rpc("anlage_anlegen", { p_name: name, p_ort: $("aOrt").value.trim(), p_kwp: parseFloat($("aKwp").value.replace(",", ".")) || 0 });
      if (a !== anm || !sess) return;                    // inzwischen abgemeldet: Token nicht mehr anzeigen
      current = null; if ($("vList").hidden) show("vList");
      $("fAdd").hidden = true; $("fAdd").reset(); showToken(name, r.token);
      loadList().catch(listeFehler(a));                  // scheitert die Liste, bleibt der Token trotzdem stehen
    } catch (e) { if (a === anm) showErr("addErr", e); onErr(e); }
  });
  $("tokenCopy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("tokenText").textContent); $("tokenCopy").textContent = "Kopiert"; }
    catch (e) { const r = document.createRange(); r.selectNodeContents($("tokenText")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
  });
  $("tokenClose").addEventListener("click", () => { $("tokenBox").hidden = true; $("tokenText").textContent = ""; $("tokenCopy").textContent = "Kopieren"; });
  $("tokenNew").addEventListener("click", async () => {
    const id = anlage;
    if (!id || !sure("tokenNew", "Wirklich? Der alte Token wird ungültig.")) return;
    $("memErr").hidden = true;
    const a = anm, m = marke, name = plants.find((x) => x.id === id)?.name || $("pName").textContent;
    let r;
    try { r = await rpc("anlage_token_neu", { p_anlage: id }); }
    catch (e) { fehlerBei(m, "memErr", e); return; }
    // Abgemeldet: Token nie mehr ins Dokument schreiben. Sonst sofort zeigen, auch wenn inzwischen eine
    // andere Ansicht offen ist: Der alte Token gilt nicht mehr, ohne diesen wäre der Pi ausgesperrt.
    if (a !== anm || !sess) return;
    current = null; show("vList"); showToken(name, r.token);
    loadList().catch(listeFehler(a));                    // scheitert die Liste, bleibt der Token trotzdem stehen
  });
  $("fMember").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("memErr").hidden = true;
    const id = anlage, m = marke;
    if (!id) return;                                     // Anlage noch nicht geladen: nichts senden
    try { await rpc("mitglied_setzen", { p_anlage: id, p_email: $("mEmail").value.trim(), p_entfernen: false }); if (m !== marke) return; $("mEmail").value = ""; loadMembers(id).catch((e) => fehlerBei(m, "memErr", e)); }
    catch (e) { fehlerBei(m, "memErr", e); }
  });

  /* ------------------------------------------------------------ Fernwartung (Admin mit Zwei-Faktor) */
  // Die Datenbank prüft jede Aktion selbst (Admin + Zwei-Faktor). Hier wird nur angezeigt und gesendet.
  // fern.id ist die einzige Quelle für die Anlage der Fernwartung. Gesetzt wird sie nur in loadFern(),
  // nachdem loadPlant() bestätigt hat, dass diese Anlage angezeigt wird; fern.origId hält fest, zu
  // welcher Anlage die geladene Konfiguration gehört.
  const fern = { id: null, p: null, orig: null, origId: null, faktor: null, timer: null };
  const fernId = () => (fern.id && fern.id === anlage && anlage === current ? fern.id : null);
  function aal() {
    try {
      const t = sess.access.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(t + "=".repeat((4 - (t.length % 4)) % 4))).aal || "";
    } catch (e) { return ""; }
  }
  const zeit = (t) => (t ? new Date(t).toLocaleString(L, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
  function mfaLeeren() {
    // Schlüssel und QR-Code der Zwei-Faktor-Einrichtung nie länger als nötig im Dokument lassen
    $("mfaSecret").textContent = ""; $("mfaQr").removeAttribute("src");
    $("mfaCode").value = ""; $("mfaEnrollCode").value = "";
    $("mfaEnroll").hidden = true; $("mfaErr").hidden = true;
    fern.faktor = null;
  }
  function fernLeeren() {
    clearInterval(fern.timer);
    fern.id = null; fern.p = null; fern.orig = null; fern.origId = null;
    mfaLeeren();
    $("fernKonfig").value = ""; $("fernKonfig").hidden = true; $("fernKonfigSenden").hidden = true;
    ["fernBefehle", "fernStand", "fernMsg", "fernErr", "fernHinweis"].forEach((id) => { $(id).textContent = ""; });
    ["fernMsg", "fernErr", "fernHinweis", "mfaOk", "fMfa", "mfaNeu"].forEach((id) => { $(id).hidden = true; });
    sureReset();
    // gesperrt, bis loadFern() die Anlage kennt: Sonst ginge ein Klick ohne Anlage hinaus
    ["fernUpdate", "fernKonfigLaden", "fernKonfigSenden"].forEach((id) => { $(id).disabled = true; });
  }
  function fernStatus() {
    const p = fern.p, ok2 = aal() === "aal2";
    if (!p || !fern.id) return;
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
    const m = marke, u = await raw("/auth/v1/user", {}, await token());
    if (m !== marke) return;
    fern.faktor = ((u && u.factors) || []).find((f) => (f.factor_type || "totp") === "totp" && f.status === "verified") || null;
    $("fMfa").hidden = !fern.faktor; $("mfaNeu").hidden = !!fern.faktor || !$("mfaEnroll").hidden;
  }
  async function mfaPruefen(faktorId, code) {
    if (!/^\d{6}$/.test(code)) throw new Error("Bitte den 6-stelligen Code eingeben");
    const m = marke, a = anm, tok = await token();
    const ch = await raw(`/auth/v1/factors/${encodeURIComponent(faktorId)}/challenge`, { method: "POST", body: "{}" }, tok);
    if (a !== anm) return;
    let d;
    try { d = await raw(`/auth/v1/factors/${encodeURIComponent(faktorId)}/verify`, { method: "POST", body: JSON.stringify({ challenge_id: ch.id, code }) }, tok); }
    catch (e) { throw e.status === 401 ? e : new Error("Code falsch oder abgelaufen. Bitte den aktuellen Code aus der App eingeben."); }
    if (a !== anm) return;                               // inzwischen abgemeldet: Sitzung nicht wiederbeleben
    if (!d || !d.access_token) throw new Error("Bestätigung fehlgeschlagen");
    setSess(d);
    if (m !== marke) return;
    mfaLeeren();
    await loadMfa();
    if (m !== marke) return;
    fernStatus(); loadBefehle().catch(onErr);
    dokListe().catch((e) => dokFehler(m, dokId(), e));        // jetzt auch interne Dokumente
  }
  $("fMfa").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("mfaErr").hidden = true;
    const m = marke;
    if (!fern.faktor) return;
    try { await mfaPruefen(fern.faktor.id, $("mfaCode").value.trim()); } catch (e) { if (m === marke) { $("mfaCode").value = ""; showErr("mfaErr", e); } onErr(e); }
  });
  $("mfaSetup").addEventListener("click", async () => {
    $("mfaErr").hidden = true;
    const m = marke;
    try {
      const tok = await token();
      const u = await raw("/auth/v1/user", {}, tok);
      // angefangene, nie bestätigte Einrichtungen entfernen
      for (const f of ((u && u.factors) || []).filter((x) => x.status !== "verified")) await raw(`/auth/v1/factors/${encodeURIComponent(f.id)}`, { method: "DELETE" }, tok);
      const d = await raw("/auth/v1/factors", { method: "POST", body: JSON.stringify({ factor_type: "totp", friendly_name: "Solarmaa Portal" }) }, tok);
      if (m !== marke) return;                           // Ansicht gewechselt oder abgemeldet: Schlüssel nicht mehr anzeigen
      const qr = (d.totp && d.totp.qr_code) || "";
      fern.faktor = { id: d.id, neu: true };
      $("mfaQr").src = qr.startsWith("data:") ? qr : "data:image/svg+xml;utf8," + encodeURIComponent(qr);
      $("mfaSecret").textContent = (d.totp && d.totp.secret) || "";
      $("mfaNeu").hidden = true; $("mfaEnroll").hidden = false; $("mfaEnrollCode").focus();
    } catch (e) { if (m === marke) showErr("mfaErr", e); onErr(e); }
  });
  $("mfaEnroll").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("mfaErr").hidden = true;
    const m = marke;
    if (!fern.faktor) return;
    try { await mfaPruefen(fern.faktor.id, $("mfaEnrollCode").value.trim()); } catch (e) { if (m === marke) { $("mfaEnrollCode").value = ""; showErr("mfaErr", e); } onErr(e); }
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
    const id = fernId(), m = marke;
    if (!id) return;
    const rows = await get(`befehle?select=id,art,status,ergebnis,erstellt,erstellt_von,gesendet_am,erledigt_am&anlage_id=eq.${encodeURIComponent(id)}&order=erstellt.desc&limit=20`);
    if (m !== marke || fernId() !== id) return;
    // nutzlast wird nie angefordert (gesperrt); ergebnis kommt vom Pi und wird nur als Text eingesetzt
    $("fernBefehle").innerHTML = rows.map((b) => { const [cls, text] = befehlStand(b); return `<li><span class="cmd"><b>${esc(ART[b.art] || b.art)}</b><span class="pill ${cls}">${esc(text)}</span><br><span class="muted small">${esc(zeit(b.erstellt))}${b.erstellt_von ? ` · ${esc(b.erstellt_von)}` : ""}${b.erledigt_am ? ` · quittiert ${esc(zeit(b.erledigt_am))}` : ""}</span>${b.ergebnis ? `<span class="cmd-res">${esc(b.ergebnis)}</span>` : ""}</span></li>`; }).join("") || '<li class="muted">Noch kein Befehl gesendet</li>';
    clearInterval(fern.timer);
    const laeuft = rows.some((b) => { const s = befehlStand(b)[1]; return s === "wartet auf den Pi" || s === "abgeholt, läuft"; });
    if (laeuft) fern.timer = setInterval(() => loadBefehle().catch(onErr), 10000);
  }
  async function loadFern(id, p) {
    const m = marke;
    fernLeeren();
    if (anlage !== id || p.id !== id) return;
    fern.id = id; fern.p = p;
    fernStatus();
    await loadMfa();
    if (m !== marke || fernId() !== id) return;
    fernStatus();
    await loadBefehle();
  }
  async function sha256(text) {
    const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
  }
  const fernMsg = (text) => { $("fernMsg").textContent = text; $("fernMsg").hidden = !text; };
  $("fernUpdate").addEventListener("click", async () => {
    const id = fernId(), m = marke;
    if (!id) return;
    if (!sure("fernUpdate", "Wirklich? Der Dienst auf dem Pi startet neu.")) return;
    $("fernErr").hidden = true; fernMsg("");
    try { await rpc("befehl_anlegen", { p_anlage: id, p_art: "update" }); if (m !== marke) return; fernMsg("Update angefordert. Der Pi holt den Befehl bei seiner nächsten Meldung ab (etwa eine Minute)."); await loadBefehle(); }
    catch (e) { if (m === marke) showErr("fernErr", e); onErr(e); }
  });
  $("fernKonfigLaden").addEventListener("click", async () => {
    const id = fernId(), m = marke;
    if (!id) return;
    $("fernErr").hidden = true; fernMsg("");
    try {
      const rows = await get(`anlagen_konfig?select=text,stand&anlage_id=eq.${encodeURIComponent(id)}`);
      if (m !== marke || fernId() !== id) return;
      const k = rows[0];
      fern.orig = k && k.text ? k.text : null; fern.origId = fern.orig ? id : null;
      $("fernKonfig").value = fern.orig || ""; $("fernKonfig").hidden = !fern.orig; $("fernKonfigSenden").hidden = !fern.orig;
      $("fernStand").textContent = fern.orig ? `Stand ${zeit(k.stand)}` : "";
      if (!fern.orig) fernMsg("Der Pi hat keine Konfiguration übertragen (Fernwartung auf dem Pi aus, oder er hat sich seit dem Einschalten noch nicht gemeldet).");
    } catch (e) { if (m === marke) showErr("fernErr", e); onErr(e); }
  });
  $("fernKonfigSenden").addEventListener("click", async () => {
    const b = $("fernKonfigSenden");
    const id = fernId(), m = marke, orig = fern.orig;
    if (!id || !orig || fern.origId !== id) return;      // nur die Konfiguration, die für genau diese Anlage geladen wurde
    if (!sure("fernKonfigSenden", "Wirklich senden? Der Dienst auf dem Pi startet neu.")) return;
    $("fernErr").hidden = true; fernMsg("");
    try {
      // basis = Stand, der hier geladen wurde: Der Pi lehnt ab, wenn seine Datei inzwischen anders ist
      const text = $("fernKonfig").value, basis = await sha256(orig);
      if (m !== marke || fernId() !== id) return;
      await rpc("befehl_anlegen", { p_anlage: id, p_art: "konfig", p_nutzlast: { text, basis } });
      if (m !== marke) return;
      fern.orig = null; fern.origId = null; $("fernKonfig").value = ""; $("fernKonfig").hidden = true; b.hidden = true; $("fernStand").textContent = "";
      fernMsg("Gesendet. Der Pi prüft die Konfiguration, speichert sie und startet neu. Das Ergebnis erscheint unten.");
      await loadBefehle();
    } catch (e) { if (m === marke) showErr("fernErr", e); onErr(e); }
  });

  /* ------------------------------------------------------------ Anlagedoku (E8) */
  // Wer was sieht und darf, entscheiden die Datenbank und die Regeln auf dem Storage-Bucket. Wie bei der
  // Fernwartung ist dok.id die einzige Quelle für die Anlage: gesetzt nur in loadDoku(), nachdem
  // loadPlant() die Anlage bestätigt hat. Jede Fortsetzung nach einem await prüft marke und dokId().
  // dok.abbruch bricht das Laden einer Datei ab, dok.hoch das Hochladen; dok.url ist die Blob-Adresse
  // der geladenen Datei und wird beim Schliessen, Wechseln und Abmelden freigegeben.
  const TYPEN = { "application/pdf": { endung: "pdf", name: "PDF" }, "image/jpeg": { endung: "jpg", name: "JPEG" }, "image/png": { endung: "png", name: "PNG" }, "image/webp": { endung: "webp", name: "WebP" } };
  const KATEGORIEN = { schema: "Schema", datenblatt: "Datenblatt", foto: "Foto", protokoll: "Protokoll", sonstiges: "Sonstiges" };
  const DOK_MAX = 20 * 1024 * 1024;
  const DOK_COLS = "id,anlage_id,pfad,titel,kategorie,dateiname,typ,groesse,sichtbar_kunde,status,erstellt";
  const dok = { id: null, liste: [], sel: null, blob: null, url: null, abbruch: null, hoch: null };
  const dokId = () => (dok.id && dok.id === anlage && anlage === current ? dok.id : null);
  const typBekannt = (t) => Object.prototype.hasOwnProperty.call(TYPEN, t);
  const istBild = (t) => typBekannt(t) && t !== "application/pdf";
  const pfadUrl = (pfad) => String(pfad).split("/").map(encodeURIComponent).join("/");
  const groesse = (b) => (b == null ? "" : b < 1024 * 1024 ? `${nf0.format(Math.max(1, Math.round(b / 1024)))} kB` : `${nf1.format(b / 1024 / 1024)} MB`);
  const dokMsg = (text) => { $("dokMsg").textContent = text; $("dokMsg").hidden = !text; };
  const dokHinweis = (text) => { $("dokHinweis").textContent = text; $("dokHinweis").hidden = !text; };
  const abgebrochen = (e) => e && (e.veraltet || e.name === "AbortError");

  // Supabase Storage: Rohdaten senden bzw. eine Datei lesen. Sitzung wie bei rpc() über token().
  async function speicher(path, opt = {}) {
    const tok = await token();
    const r = await fetch(URL_ + path, { ...opt, headers: { apikey: KEY, Authorization: `Bearer ${tok}`, ...(opt.headers || {}) } });
    if (!r.ok) {
      let d = null;
      try { d = await r.json(); } catch (e) { /* leer */ }
      throw new ApiError(r.status, (d && (d.message || d.error)) || `Fehler ${r.status}`);
    }
    return r;
  }
  function dokFehler(m, id, e) {
    if (abgebrochen(e)) return;
    onErr(e);
    if (e.status === 401 || m !== marke || !id || dokId() !== id) return;
    $("pDoku").hidden = false;
    showErr("dokErr", e);
  }
  function dokDetailLeeren() {
    if (dok.abbruch) dok.abbruch.abort();
    dok.abbruch = null; dok.sel = null; dok.blob = null;
    if (dok.url) URL.revokeObjectURL(dok.url);
    dok.url = null;
    $("dokBild").removeAttribute("src"); $("dokBild").hidden = true;
    $("dokLink").removeAttribute("href"); $("dokLink").removeAttribute("download");
    $("dokDetailTitel").textContent = ""; $("dokInfo").textContent = "";
    $("dokTitel").value = ""; $("dokKategorie").value = "sonstiges"; $("dokSichtbar").value = "false";
    $("dokDetail").hidden = true; $("dokBearbeiten").hidden = true;
    sureReset("dokLoeschen");
  }
  function dokLeeren() {
    if (dok.hoch) dok.hoch.abort();
    dok.hoch = null; dok.id = null; dok.liste = [];
    dokDetailLeeren();
    $("dokListe").textContent = ""; $("fDok").reset();
    ["dokHinweis", "dokMsg", "dokErr"].forEach((id) => { $(id).textContent = ""; $(id).hidden = true; });
    $("pDoku").hidden = true; $("fDok").hidden = true; $("dokHochladen").disabled = false;
  }
  async function loadDoku(id) {
    dokLeeren();
    if (anlage !== id || current !== id) return;
    dok.id = id;
    await dokListe();
  }
  function dokPills(d) {
    if (d.status === "hochladen") return '<span class="pill warn">unvollständig</span>';
    if (d.status === "loeschen") return '<span class="pill bad">wird gelöscht</span>';
    return d.sichtbar_kunde ? '<span class="pill ok">für Kunde</span>' : '<span class="pill off">nur Solarmaa</span>';
  }
  async function dokListe() {
    const id = dokId(), m = marke;
    if (!id) return;
    let rows;
    try { rows = await get(`dokumente?select=${DOK_COLS}&anlage_id=eq.${encodeURIComponent(id)}&order=erstellt.desc`); }
    catch (e) {
      if (m !== marke || dokId() !== id || e.status !== 404) throw e;
      // Datenbank ohne Anlagedoku (noch nicht eingespielt): nur diese Karte fällt aus
      $("pDoku").hidden = !admin; $("fDok").hidden = true; $("dokListe").textContent = "";
      dokHinweis("Dokumente nicht verfügbar.");
      return;
    }
    if (m !== marke || dokId() !== id) return;
    dok.liste = rows;
    // Kunden ohne freigegebene Dokumente: keine Karte (ausser eine Fehlermeldung steht darin)
    $("pDoku").hidden = !admin && !rows.length && $("dokErr").hidden;
    $("fDok").hidden = !admin;
    dokHinweis(admin && aal() !== "aal2" ? "Interne Dokumente, Hochladen und Ändern erst nach der Zwei-Faktor-Bestätigung (unten bei «Fernwartung»)." : "");
    $("dokListe").innerHTML = rows.map((d) => `<li><span class="dok"><button class="btn link dok-titel" type="button" data-id="${esc(d.id)}" data-anlage="${esc(d.anlage_id)}">${esc(d.titel)}</button><br><span class="pill">${esc(KATEGORIEN[d.kategorie] || d.kategorie)}</span>${admin ? dokPills(d) : ""}<span class="muted small">${esc([typBekannt(d.typ) ? TYPEN[d.typ].name : "", groesse(d.groesse), zeit(d.erstellt)].filter(Boolean).join(" · "))}</span></span></li>`).join("")
      || `<li class="muted">${admin ? "Noch keine Dokumente" : "Keine Dokumente freigegeben"}</li>`;
    $("dokListe").querySelectorAll("button[data-id]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.anlage !== id || dokId() !== id) return;      // Knopf gehört nicht (mehr) zur angezeigten Anlage
      const d = dok.liste.find((x) => x.id === b.dataset.id);
      if (d) dokOeffnen(d);
    }));
    // geöffnetes Dokument: verschwunden -> schliessen, sonst Angaben auffrischen (Datei bleibt geladen)
    if (dok.sel) {
      const neu = rows.find((x) => x.id === dok.sel.id);
      if (!neu || neu.status !== dok.sel.status) dokDetailLeeren();
      else { dok.sel = neu; dokDetailZeigen(); }
    }
  }
  function dokDetailZeigen() {
    const d = dok.sel;
    if (!d) return;
    $("dokDetailTitel").textContent = d.titel;
    $("dokInfo").textContent = [KATEGORIEN[d.kategorie], d.dateiname, typBekannt(d.typ) ? TYPEN[d.typ].name : "", groesse(d.groesse), zeit(d.erstellt),
      admin ? (d.status === "hochladen" ? "unvollständig" : d.status === "loeschen" ? "wird gelöscht" : d.sichtbar_kunde ? "auch für Kunde" : "nur Solarmaa") : ""].filter(Boolean).join(" · ");
    $("dokLaden").hidden = d.status !== "fertig";
    $("dokBearbeiten").hidden = !admin;
    $("dokFelder").hidden = d.status === "loeschen"; $("dokSpeichern").hidden = d.status === "loeschen";
    $("dokFertig").hidden = d.status !== "hochladen";
  }
  function dokOeffnen(d) {
    const id = dokId(), m = marke;
    if (!id || d.anlage_id !== id) return;
    dokDetailLeeren();
    $("dokErr").hidden = true; dokMsg("");
    dok.sel = d;
    $("dokTitel").value = d.titel; $("dokKategorie").value = d.kategorie; $("dokSichtbar").value = d.sichtbar_kunde ? "true" : "false";
    dokDetailZeigen();
    $("dokDetail").hidden = false;
    if (d.status === "fertig" && istBild(d.typ)) dokVorschau(d).catch((e) => dokLadeFehler(m, id, e));
  }
  // Datei holen (nie über eine Adresse im Dokument, immer mit der Sitzung). Den Typ des Blobs bestimmt die
  // eigene Liste, nie der Server: So entsteht daraus nur ein Bild oder ein PDF zum Herunterladen.
  async function dokDatei(d) {
    const id = dokId(), m = marke;
    if (!id || d.anlage_id !== id || dok.sel?.id !== d.id || !typBekannt(d.typ)) throw veraltet();
    if (dok.blob) return dok.blob;
    if (dok.abbruch) dok.abbruch.abort();
    const ac = new AbortController();
    dok.abbruch = ac;
    const r = await speicher(`/storage/v1/object/authenticated/anlagedoku/${pfadUrl(d.pfad)}`, { signal: ac.signal, cache: "no-store" });
    const daten = await r.arrayBuffer();
    if (m !== marke || dokId() !== id || dok.sel?.id !== d.id || ac.signal.aborted) throw veraltet();
    dok.abbruch = null;
    dok.blob = new Blob([daten], { type: d.typ });
    dok.url = URL.createObjectURL(dok.blob);
    return dok.blob;
  }
  async function dokVorschau(d) {
    await dokDatei(d);
    if (dok.sel?.id !== d.id || !dok.url) return;
    $("dokBild").src = dok.url; $("dokBild").hidden = false;
  }
  function dokLadeFehler(m, id, e) {
    if (abgebrochen(e) || e.status === 401) return dokFehler(m, id, e);
    dokFehler(m, id, new Error("Die Datei konnte nicht geladen werden. Sie ist nicht mehr vorhanden oder nicht mehr freigegeben."));
    if (m === marke && dokId() === id) dokListe().catch((e2) => dokFehler(m, id, e2));
  }
  // Name zum Speichern: ohne Steuer-, Richtungs- und in Dateisystemen verbotene Zeichen, mit passender Endung
  function dateiName(d) {
    const t = TYPEN[d.typ];
    let n = String(d.dateiname || "").normalize("NFC").replace(/[\u0000-\u001f\u007f-\u009f‎‏‪-‮⁦-⁩/\\:*?"<>|]/g, "_")
      .replace(/^[.\s]+|[.\s]+$/g, "").slice(0, 120);
    if (!n) n = "dokument";
    const endung = n.includes(".") ? n.split(".").pop().toLowerCase() : "";
    if (!(endung === t.endung || (t.endung === "jpg" && endung === "jpeg"))) n += "." + t.endung;
    return n;
  }
  $("dokZu").addEventListener("click", () => { dokDetailLeeren(); $("dokErr").hidden = true; dokMsg(""); });
  $("dokLaden").addEventListener("click", async () => {
    const d = dok.sel, id = dokId(), m = marke;
    if (!id || !d || d.anlage_id !== id || d.status !== "fertig") return;
    $("dokErr").hidden = true;
    try {
      await dokDatei(d);
      if (m !== marke || dokId() !== id || dok.sel?.id !== d.id || !dok.url) return;
      // Herunterladen über einen Link mit download-Attribut: keine Anzeige im Browser, keine Navigation
      const a = $("dokLink");
      a.href = dok.url; a.download = dateiName(d);
      a.click();
    } catch (e) { dokLadeFehler(m, id, e); }
  });
  // Inhalt prüfen (erste Bytes), nicht Endung oder Angabe des Browsers
  async function dateiTyp(f) {
    const b = new Uint8Array(await f.slice(0, 12).arrayBuffer());
    const ist = (ab, bytes) => bytes.every((x, i) => b[ab + i] === x);
    if (ist(0, [0x25, 0x50, 0x44, 0x46, 0x2d])) return "application/pdf";              // %PDF-
    if (ist(0, [0xff, 0xd8, 0xff])) return "image/jpeg";
    if (ist(0, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return "image/png";
    if (ist(0, [0x52, 0x49, 0x46, 0x46]) && ist(8, [0x57, 0x45, 0x42, 0x50])) return "image/webp";   // RIFF....WEBP
    return null;
  }
  $("fDok").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("dokErr").hidden = true; dokMsg("");
    const id = dokId(), m = marke;
    if (!id || !admin) return;                             // Anlage noch nicht geladen: nichts senden
    const fehler = (text) => showErr("dokErr", new Error(text));
    const f = $("dokDatei").files[0];
    if (!f) return fehler("Bitte eine Datei wählen.");
    if (!f.size || f.size > DOK_MAX) return fehler("Die Datei ist leer oder grösser als 20 MB.");
    const name = f.name.normalize("NFC");
    if (!name || name.length > 120 || /[\u0000-\u001f\u007f-\u009f/\\]/.test(name)) return fehler("Der Dateiname ist länger als 120 Zeichen oder enthält unzulässige Zeichen. Bitte die Datei umbenennen.");
    const titel = ($("dokNeuTitel").value.trim() || name.replace(/\.[^.]*$/, "").trim() || name).slice(0, 120);
    const kat = $("dokNeuKategorie").value, sichtbar = $("dokNeuSichtbar").value === "true";
    $("dokHochladen").disabled = true;
    try {
      const typ = await dateiTyp(f);
      if (m !== marke || dokId() !== id) return;
      if (!typ) return fehler("Nur PDF, JPEG, PNG oder WebP. Der Inhalt dieser Datei passt zu keinem davon.");
      const r = await rpc("dokument_anlegen", { p_anlage: id, p_titel: titel, p_kategorie: kat, p_dateiname: name, p_typ: typ, p_groesse: f.size, p_sichtbar: sichtbar });
      if (m !== marke || dokId() !== id) return;
      const ac = new AbortController();
      dok.hoch = ac;
      try {
        await speicher(`/storage/v1/object/anlagedoku/${pfadUrl(r.pfad)}`, { method: "POST", body: f, signal: ac.signal, headers: { "Content-Type": typ, "x-upsert": "false", "Cache-Control": "no-store" } });
      } catch (e) {
        if (abgebrochen(e) || e.status === 401) throw e;
        throw new ApiError(e.status, "Die Datei wurde nicht angenommen. Der Eintrag steht als «unvollständig» in der Liste und kann gelöscht werden.");
      } finally { if (dok.hoch === ac) dok.hoch = null; }
      // Abgemeldet oder andere Anlage: nicht fertigstellen (der Eintrag bleibt «unvollständig»)
      if (m !== marke || dokId() !== id || ac.signal.aborted) return;
      await rpc("dokument_fertig", { p_id: r.id });
      if (m !== marke || dokId() !== id) return;
      $("fDok").reset(); dokMsg(`«${titel}» hochgeladen.`);
      await dokListe();
    } catch (e) {
      dokFehler(m, id, e);
      if (!abgebrochen(e) && e.status !== 401 && m === marke && dokId() === id) dokListe().catch((e2) => dokFehler(m, id, e2));
    } finally { if (m === marke) $("dokHochladen").disabled = false; }
  });
  $("dokFertig").addEventListener("click", async () => {
    const d = dok.sel, id = dokId(), m = marke;
    if (!id || !d || d.anlage_id !== id || d.status !== "hochladen") return;
    $("dokErr").hidden = true; dokMsg("");
    try { await rpc("dokument_fertig", { p_id: d.id }); if (m !== marke || dokId() !== id) return; dokMsg("Fertiggestellt."); await dokListe(); }
    catch (e) { dokFehler(m, id, e); }
  });
  $("dokSpeichern").addEventListener("click", async () => {
    const d = dok.sel, id = dokId(), m = marke;
    if (!id || !d || d.anlage_id !== id) return;
    $("dokErr").hidden = true; dokMsg("");
    const titel = $("dokTitel").value.trim();
    if (!titel || titel.length > 120) return showErr("dokErr", new Error("Bitte einen Titel mit höchstens 120 Zeichen eingeben."));
    try {
      await rpc("dokument_aendern", { p_id: d.id, p_titel: titel, p_kategorie: $("dokKategorie").value, p_sichtbar: $("dokSichtbar").value === "true" });
      if (m !== marke || dokId() !== id) return;
      dokMsg("Gespeichert.");
      await dokListe();
    } catch (e) { dokFehler(m, id, e); }
  });
  // Löschen: zuerst in der Datenbank beginnen (für Kunden sofort weg), dann die Datei, dann die Zeile.
  $("dokLoeschen").addEventListener("click", async () => {
    const d = dok.sel, id = dokId(), m = marke;
    if (!id || !d || d.anlage_id !== id) return;
    if (!sure("dokLoeschen", "Wirklich löschen?")) return;
    $("dokErr").hidden = true; dokMsg("");
    try {
      if (d.status !== "loeschen") await rpc("dokument_loeschen_beginnen", { p_id: d.id });
      if (m !== marke || dokId() !== id) return;
      const r = await speicher("/storage/v1/object/anlagedoku", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prefixes: [d.pfad] }) });
      let weg = null;
      try { weg = await r.json(); } catch (e) { /* leer */ }
      if (m !== marke || dokId() !== id) return;
      // Eine leere Antwortliste beweist nicht, dass die Datei weg ist. Das prüft dokument_loeschen() in der
      // Datenbank selbst und lehnt ab, solange die Datei noch im Bucket liegt.
      const geloescht = Array.isArray(weg) && weg.some((o) => o && o.name === d.pfad);
      await rpc("dokument_loeschen", { p_id: d.id });
      if (m !== marke || dokId() !== id) return;
      dokDetailLeeren();
      dokMsg(geloescht ? `«${d.titel}» gelöscht.` : `Eintrag «${d.titel}» gelöscht (die Datei lag nicht mehr im Speicher).`);
      await dokListe();
    } catch (e) {
      dokFehler(m, id, e);
      if (!abgebrochen(e) && e.status !== 401 && m === marke && dokId() === id) dokListe().catch((e2) => dokFehler(m, id, e2));
    }
  });

  /* ------------------------------------------------------------ Anmeldung */
  async function enter() {
    const a = anm;
    let istAdmin = false;
    try { istAdmin = (await rpc("ist_admin")) === true; } catch (e) { if (e.status === 401) throw e; }
    if (a !== anm || !sess) return;
    admin = istAdmin;
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
  // Zweistufige Rückfragen («Wirklich?»): gelten nur für die Ansicht, in der sie gestellt wurden, und
  // nur einige Sekunden. Sonst löste ein einzelner Klick bei einer anderen Anlage die Aktion aus.
  const SURE = { tokenNew: "Neuen Pi-Token erzeugen", fernUpdate: "Update auslösen", fernKonfigSenden: "Konfiguration senden", dokLoeschen: "Löschen" };
  const SURE_MS = 8000, sureT = {};
  function sureReset(nur) {
    for (const id of nur ? [nur] : Object.keys(SURE)) { clearTimeout(sureT[id]); $(id).dataset.sure = ""; $(id).textContent = SURE[id]; }
  }
  function sure(id, frage) {
    const b = $(id);
    if (b.dataset.sure === "1") { sureReset(id); return true; }
    b.dataset.sure = "1"; b.textContent = frage;
    clearTimeout(sureT[id]); sureT[id] = setTimeout(() => sureReset(id), SURE_MS);
    return false;
  }
  // Abmelden, auch bei abgelaufener Sitzung (401): nichts vom vorherigen Konto im Dokument stehen lassen.
  // anm und marke (in show) ändern sich, damit keine noch laufende Abfrage danach etwas einträgt.
  function abmeldenLokal() {
    anm++;
    setSess(null); plants = []; current = null; admin = false; anlage = null;
    ["plants", "members", "summary", "pNow", "pToday", "tokenText", "pName", "pMeta", "pAlarm", "pCurtail", "chDay", "chDays", "who", "memErr", "listErr"].forEach((id) => { $(id).textContent = ""; });
    $("pAdmin").hidden = true; $("addOpen").hidden = true; $("tokenBox").hidden = true; $("fAdd").hidden = true;
    $("pAlarm").hidden = true; $("pCurtail").hidden = true;
    fernLeeren(); $("pFern").hidden = true;
    dokLeeren();                                         // bricht Laden und Hochladen ab, gibt Blob-Adressen frei
    $("pState").textContent = ""; $("pState").className = "pill";
    ["email", "pw", "mEmail", "npw", "aName", "aOrt", "aKwp", "dokTitel", "dokNeuTitel"].forEach((id) => { $(id).value = ""; });
    // alle hidden-, disabled- und Knopfzustände wie beim ersten Laden der Seite
    AUSGANG.forEach(([n, h, d, t]) => { n.hidden = h; n.disabled = d; if (t !== null) n.textContent = t; });
    show("vLogin");
  }
  $("logout").addEventListener("click", () => {
    const tok = sess?.access;
    abmeldenLokal();                                     // zuerst lokal aufräumen, dann dem Server melden
    if (tok) raw("/auth/v1/logout", { method: "POST" }, tok).catch(() => { /* egal */ });
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
    try { await enter(); } catch (e) { abmeldenLokal(); }
  })();
})();
