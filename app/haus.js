/* Solarmaa Haus – Hausskizze mit Einheiten, Zählern, Bezügen, Rechnungen und Wartung.
   Echte Daten vom Pi (/api/...) oder Demo im Browser (?demo bzw. window.SOLARMAA_DEMO). */
(() => {
  "use strict";
  const DEMO = window.SOLARMAA_DEMO === true || new URLSearchParams(location.search).has("demo");
  const $ = (id) => document.getElementById(id);
  const L = "de-CH";
  const nf0 = new Intl.NumberFormat(L, { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat(L, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const chf = new Intl.NumberFormat(L, { style: "currency", currency: "CHF" });
  const fmtW = (w) => (w == null ? "–" : Math.abs(w) < 1000 ? `${nf0.format(w)} W` : `${nf1.format(w / 1000)} kW`);
  const dat = (s) => (s ? new Date(s + "T00:00:00").toLocaleDateString(L) : "–");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ROLLE = { admin: "Solarmaa", verwaltung: "Verwaltung", mieter: "Mieter/in" };
  const TYP = { wohnung: "Wohnung", gewerbe: "Gewerbe", allgemein: "Allgemein", technik: "Technik" };

  class ApiFehler extends Error { constructor(status, msg) { super(msg); this.status = status; } }

  /* ---------------------------------------------------------------- API */
  const realApi = {
    async req(method, path, body, type = "application/json") {
      const opt = { method, credentials: "same-origin", headers: {} };
      if (method !== "GET") { opt.headers["X-Solarmaa"] = "1"; opt.headers["Content-Type"] = type; opt.body = type === "application/json" ? JSON.stringify(body ?? {}) : body; }
      const r = await fetch(path, opt);
      let data = null;
      try { data = await r.json(); } catch (e) { /* leer */ }
      if (!r.ok) throw new ApiFehler(r.status, (data && data.fehler) || `Fehler ${r.status}`);
      return data;
    },
    me() { return this.req("GET", "/api/me"); },
    login(b, p) { return this.req("POST", "/api/login", { benutzer: b, passwort: p }); },
    logout() { return this.req("POST", "/api/logout"); },
    haus() { return this.req("GET", "/api/haus"); },
    einheit(id) { return this.req("GET", "/api/einheit?id=" + encodeURIComponent(id)); },
    rechnungen() { return this.req("GET", "/api/rechnungen"); },
    status(nr, status) { return this.req("POST", "/api/rechnungen/status", { nr, status }); },
    importCsv(text) { return this.req("POST", "/api/rechnungen/import", text, "text/csv"); },
    wartung() { return this.req("GET", "/api/wartung"); },
    update() { return this.req("POST", "/api/wartung/update"); },
    erkennen() { return this.req("POST", "/api/wartung/erkennen"); },
  };

  const demoApi = (() => {
    const geschosse = ["UG", "EG", "1. OG", "2. OG", "DG"];
    const E = [
      ["allgemein", "Allgemein", "UG", "allgemein", ["Allgemeinzähler"]],
      ["eg-links", "Wohnung EG links", "EG", "wohnung", ["Wohnungszähler"]],
      ["eg-rechts", "Wohnung EG rechts", "EG", "wohnung", ["Wohnungszähler"]],
      ["og1-links", "Wohnung 1. OG links", "1. OG", "wohnung", ["Wohnungszähler"]],
      ["og1-rechts", "Wohnung 1. OG rechts", "1. OG", "wohnung", ["Wohnungszähler", "Wärmepumpe"]],
      ["og2-links", "Wohnung 2. OG links", "2. OG", "wohnung", ["Wohnungszähler"]],
      ["og2-rechts", "Praxis 2. OG", "2. OG", "gewerbe", ["Gewerbezähler"]],
      ["dg", "Attika", "DG", "wohnung", ["Wohnungszähler"]],
    ].map(([id, name, geschoss, typ, z]) => ({ id, name, geschoss, typ, zaehler: z.map((n, i) => ({ id: `${id}-${i + 1}`, name: n })) }));
    const USERS = {
      solarmaa: { name: "solarmaa", anzeigename: "Solarmaa Service", rolle: "admin", einheit: null },
      verwaltung: { name: "verwaltung", anzeigename: "Immo Verwaltung AG", rolle: "verwaltung", einheit: null },
      mieter: { name: "mieter", anzeigename: "Familie Beispiel", rolle: "mieter", einheit: "eg-links" },
    };
    let me = null;
    const hour = () => { const d = new Date(); return d.getHours() + d.getMinutes() / 60; };
    const bump = (h, c, w, a) => a * Math.exp(-(((h - c) / w) ** 2));
    const unitW = (i) => { const h = hour(), t = Date.now() / 1000; return 160 + 40 * (i % 4) + 30 * Math.sin(t / (200 + 30 * i)) + bump(h, 7 + 0.3 * (i % 3), 0.5, 600 + 80 * i) + bump(h, 12.3, 0.7, 400 + 50 * (i % 4)) + bump(h, 18.6, 1, 800 + 110 * (i % 5)); };
    const pvW = () => { const h = hour(), m = new Date().getMonth(); const s = 0.5 - 0.5 * Math.cos(((m + 0.5) / 12) * 2 * Math.PI); const len = 8.5 + 7 * s; const x = Math.sin((Math.PI * (h - (13.1 - len / 2))) / len); return x > 0 ? 71800 * (0.28 + 0.52 * s) * x ** 1.4 * 0.8 : 0; };
    const meterW = (ei, zi) => unitW(ei) * (E[ei].zaehler.length > 1 ? (zi === 0 ? 0.55 : 0.45) : 1) * (E[ei].typ === "allgemein" ? 0.5 : 1);
    const invoices = (() => {
      const out = [], today = new Date(); let n = 1000;
      for (let q = 4; q >= 1; q--) {
        E.forEach((e, i) => {
          const bis = new Date(today.getFullYear(), today.getMonth(), 0 - 91 * (q - 1));
          const von = new Date(bis); von.setDate(von.getDate() - 90);
          const fae = new Date(bis); fae.setDate(fae.getDate() + 30);
          const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
          const status = q === 1 || (q === 2 && (i === 1 || i === 4)) ? "offen" : "bezahlt";
          out.push({ nr: `R-${today.getFullYear()}-${++n}`, einheit: e.id, einheit_name: e.name, empfaenger: `Beispiel ${e.name}`, von: iso(von), bis: iso(bis), betrag: 180 + 35 * (i % 5) + 18 * q, status, faellig: iso(fae), pdf: "" });
        });
      }
      return out;
    })();
    const withDue = (r) => ({ ...r, ueberfaellig: r.status !== "bezahlt" && r.faellig < new Date().toISOString().slice(0, 10) });
    const can = (id) => me && (me.rolle !== "mieter" || me.einheit === id);
    const need = () => { if (!me) throw new ApiFehler(401, "Bitte anmelden"); };
    const all = () => { need(); if (me.rolle === "mieter") throw new ApiFehler(403, "Nur für Verwaltung und Solarmaa"); };
    let scan = { laeuft: false, funde: null, fortschritt: null };
    return {
      async me() { need(); return me; },
      async login(b, p) { await new Promise((r) => setTimeout(r, 300)); if (!USERS[b] || p !== "demo-passwort") throw new ApiFehler(401, "Benutzername oder Passwort falsch"); me = USERS[b]; return me; },
      async logout() { me = null; return { ok: true }; },
      async haus() {
        need();
        const pv = pvW(), loads = E.map((_, i) => E[i].zaehler.reduce((a, _z, zi) => a + meterW(i, zi), 0));
        const sum = loads.reduce((a, b) => a + b, 0), selfUse = Math.min(pv, sum);
        const offen = {}; invoices.forEach((r) => { if (r.status !== "bezahlt") offen[r.einheit] = (offen[r.einheit] || 0) + r.betrag; });
        return {
          name: "MFH Beispielstrasse 12", geschosse, demo: true, ich: me,
          anlage: { pv_w: pv, grid_w: sum - pv, battery_w: null, soc: null, load_w: sum, kwp: 71.8, geraete: [] },
          einheiten: E.map((e, i) => can(e.id)
            ? { id: e.id, name: e.name, geschoss: e.geschoss, typ: e.typ, zugriff: true, anzahl_zaehler: e.zaehler.length, w: loads[i], solar_w: sum ? (selfUse * loads[i]) / sum : 0, zaehler: e.zaehler.map((z) => ({ ...z, ok: true })), offen_chf: offen[e.id] || 0 }
            : { id: e.id, name: e.name, geschoss: e.geschoss, typ: e.typ, zugriff: false, anzahl_zaehler: e.zaehler.length }),
        };
      },
      async einheit(id) {
        need(); if (!can(id)) throw new ApiFehler(403, "Keine Berechtigung für diese Einheit");
        const i = E.findIndex((e) => e.id === id), e = E[i], now = new Date(), monate = [];
        for (let k = 12; k >= 0; k--) {
          const d = new Date(now.getFullYear(), now.getMonth() - k, 1), s = 0.5 - 0.5 * Math.cos(((d.getMonth() + 0.5) / 12) * 2 * Math.PI);
          const v = (155 + 28 * (i % 4)) * (1.25 - 0.4 * s) * (e.typ === "allgemein" ? 0.45 : 1) * (k === 0 ? now.getDate() / 30 : 1);
          monate.push({ monat: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`, verbrauch_kwh: Math.round(v * 10) / 10, solar_kwh: Math.round(v * (0.12 + 0.43 * s) * 10) / 10, solar_pct: Math.round(100 * (0.12 + 0.43 * s)) });
        }
        const out = {
          id: e.id, name: e.name, geschoss: e.geschoss, typ: e.typ, flaeche_m2: null,
          zaehler: e.zaehler.map((z, zi) => ({ id: z.id, name: z.name, typ: "smartme_cloud", seriennummer: String(7100200 + i * 13 + zi), w: meterW(i, zi), import_kwh: 1500 + i * 137 + zi * 40, export_kwh: 0, fehler: null, zeit: Date.now() / 1000 })),
          heute: { verbrauch_kwh: 3.4 + i * 0.4, solar_kwh: 1.1 + i * 0.1, solar_pct: 30 }, monate,
          rechnungen: invoices.filter((r) => r.einheit === id).map(withDue),
        };
        if (me.rolle !== "mieter") out.berechtigte = [USERS.solarmaa, USERS.verwaltung, ...(id === "eg-links" ? [USERS.mieter] : [])].map((u) => ({ ...u, letzter_login: Date.now() / 1000 - 86400 * 3 }));
        return out;
      },
      async rechnungen() { all(); return invoices.map(withDue); },
      async status(nr, status) { all(); invoices.find((r) => r.nr === nr).status = status; return { ok: true }; },
      async importCsv() { all(); return { neu: 0, aktualisiert: 0, nicht_zugeordnet: [], demo: true }; },
      async wartung() {
        if (!me || me.rolle !== "admin") throw new ApiFehler(403, "Nur für Solarmaa (Rolle admin)");
        return { version: "1.1.0", commit: "demo", stand: new Date().toLocaleString(L), update_wartet: false, update_eingerichtet: true,
          update_log: "=== Update (Beispiel) ===\nBereits aktuell (demo).", geraete: [{ name: "Fronius Symo GEN24", rolle: "anlage", typ: "fronius", ok: true }, { name: "Shelly Pro 3EM Netz", rolle: "netz", typ: "shelly", ok: true }],
          geraete_fehler: {}, haus_zaehler: E.flatMap((e) => e.zaehler.map((z) => ({ id: z.id, name: `${e.name}: ${z.name}`, fehler: null }))), erkennung: scan };
      },
      async update() { return { ok: true, hinweis: "Demo: Es wird nichts installiert." }; },
      async erkennen() {
        scan = { laeuft: true, funde: null, fortschritt: "0/254" };
        let i = 0;
        const t = setInterval(() => { i += 64; scan.fortschritt = `${Math.min(i, 254)}/254`; if (i >= 254) { clearInterval(t); scan = { laeuft: false, fortschritt: "254/254", funde: [
          { host: "192.168.1.40", typ: "fronius", rolle: "anlage", modell: "Fronius (Solar API)", hinweis: "Batterie-Vorzeichen bei Sonne prüfen" },
          { host: "192.168.1.40", typ: "sunspec", unit: 1, rolle: "pv", modell: "Fronius Symo GEN24 10.0", hinweis: "" },
          { host: "192.168.1.52", typ: "shelly", rolle: "netz", modell: "Shelly SPEM-003CEBEU (Gen2)", hinweis: "Rolle prüfen: Netzanschluss, PV-Abgang oder Einheit?" },
          { host: "smart-me Cloud", typ: "smartme_cloud", rolle: "einheit", modell: "Wohnung EG links", seriennummer: "7100200", hinweis: "Einer Einheit oder Rolle zuordnen" }],
          vorschlag: '[[geraet]]\nname = "Fronius (Solar API)"\nrolle = "anlage"\ntyp = "fronius"\nhost = "192.168.1.40"\n# Batterie-Vorzeichen bei Sonne prüfen\n\n[[geraet]]\nname = "Shelly SPEM-003CEBEU (Gen2)"\nrolle = "netz"\ntyp = "shelly"\nhost = "192.168.1.52"\n# Rolle prüfen: Netzanschluss, PV-Abgang oder Einheit?\n' }; } }, 600);
        return { ok: true };
      },
    };
  })();

  const api = DEMO ? demoApi : realApi;

  /* ------------------------------------------------------------- State */
  let me = null, haus = null, sel = { einheit: null, zaehler: null }, view = "haus", rgFilter = "alle";
  const SVGNS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, text) => { const n = document.createElementNS(SVGNS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; return n; };

  function showLogin(msg) {
    $("loginView").hidden = false; $("appView").hidden = true;
    $("userChip").hidden = true; $("logout").hidden = true;
    $("demoHint").hidden = !DEMO;
    if (msg) { $("loginError").textContent = msg; $("loginError").hidden = false; }
    $("benutzer").focus();
  }

  function showApp() {
    $("loginView").hidden = true; $("appView").hidden = false;
    $("userChip").hidden = false; $("logout").hidden = false;
    $("userChip").textContent = `${me.anzeigename || me.name} · ${ROLLE[me.rolle]}`;
    const alles = me.rolle !== "mieter";
    $("tab-rechnungen").hidden = !alles;
    $("tab-wartung").hidden = me.rolle !== "admin";
    setView("haus");
  }

  function setView(v) {
    view = v;
    document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    ["haus", "rechnungen", "wartung"].forEach((x) => { $("view-" + x).hidden = x !== v; });
    const run = { haus: loadHaus, rechnungen: loadRechnungen, wartung: loadWartung }[v];
    run().catch((e) => {
      const box = v === "haus" ? $("detail") : $("view-" + v);
      alertInline(box, e.message);
    });
  }

  async function guard(fn) {
    try { return await fn(); }
    catch (e) {
      if (e.status === 401) { me = null; showLogin(); return null; }
      throw e;
    }
  }

  /* ------------------------------------------------------------ Skizze */
  async function loadHaus() {
    const h = await guard(() => api.haus());
    if (!h) return;
    haus = h;
    $("hausName").textContent = h.name;
    document.title = `${h.name} · Solarmaa`;
    $("demoBand").hidden = !h.demo;
    $("stand").textContent = `Stand ${new Date().toLocaleTimeString(L, { hour: "2-digit", minute: "2-digit" })}`;
    drawSketch();
    if (!sel.einheit) {
      const first = h.einheiten.find((e) => e.zugriff && e.typ !== "technik" && (me.rolle === "mieter" || e.typ !== "allgemein")) || h.einheiten.find((e) => e.zugriff);
      if (first) select(first.id, null, false);
    }
  }

  function drawSketch() {
    const svg = $("sketch"), h = haus;
    svg.replaceChildren();
    const isBase = (g) => /^(UG|KG|Keller|Untergeschoss|Tiefgarage)/i.test(g);
    const below = h.geschosse.filter(isBase), above = h.geschosse.filter((g) => !isBase(g)).reverse();
    // in echten Pixeln zeichnen, damit Schrift auf dem Handy lesbar bleibt
    const W = Math.round(Math.min(760, Math.max(330, svg.clientWidth || svg.parentElement.clientWidth - 36)));
    const narrow = W < 520, X0 = narrow ? 40 : 76, X1 = W - (narrow ? 8 : 40);
    const FH = narrow ? 96 : 100, RH = narrow ? 92 : 118, top = 40;
    const yR = top + RH, yG = yR + above.length * FH, yB = yG + below.length * FH, Hh = yB + 64;
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`);

    // Boden
    svg.append(el("rect", { x: 0, y: yG, width: W, height: Hh - yG, class: "ground" }));
    svg.append(el("line", { x1: 0, x2: W, y1: yG, y2: yG, class: "groundline" }));

    // Dach mit PV
    const cx = (X0 + X1) / 2;
    svg.append(el("polygon", { points: `${X0 - 18},${yR} ${cx},${top} ${X1 + 18},${yR}`, class: "roof" }));
    const rows = 3, rowH = 22;
    for (let r = 0; r < rows; r++) {
      const yb = yR - 12 - r * (rowH + 4), yt = yb - rowH;
      const half = ((yt - top) / (yR - top)) * ((X1 - X0) / 2 + 18) - 8;
      const n = Math.max(1, Math.floor((2 * half) / 30));
      const cw = (2 * half) / n;
      for (let c = 0; c < n; c++) svg.append(el("rect", { x: cx - half + c * cw + 1, y: yt, width: cw - 2, height: rowH, rx: 1.5, class: "pv-cell" }));
    }
    const a = h.anlage || {};
    svg.append(el("text", { x: W - 4, y: 18, "text-anchor": "end", class: "pv-val" }, fmtW(a.pv_w)));
    svg.append(el("text", { x: W - 4, y: 34, "text-anchor": "end", class: "pv-lbl" }, `Solar · ${nf1.format(a.kwp || 0)} kWp`));

    // Geschosse
    const floorY = {};
    above.forEach((g, i) => { floorY[g] = yR + i * FH; });
    below.forEach((g, i) => { floorY[g] = yG + i * FH; });
    svg.append(el("rect", { x: X0, y: yR, width: X1 - X0, height: (above.length + below.length) * FH, class: "wall" }));
    for (const g of h.geschosse) {
      const y = floorY[g];
      svg.append(el("line", { x1: X0, x2: X1, y1: y, y2: y, class: "wall" }));
      svg.append(el("text", { x: X0 - 6, y: y + FH / 2 + 4, "text-anchor": "end", class: "floor-lbl" }, g));
      const units = h.einheiten.filter((e) => e.geschoss === g);
      const n = Math.max(units.length, 1), gap = 8, uw = (X1 - X0 - gap * (n + 1)) / n;
      units.forEach((e, i) => drawUnit(svg, e, X0 + gap + i * (uw + gap), y + gap, uw, FH - 2 * gap));
    }

    // Hausanschluss im Boden (links unten), Speicher rechts unten
    const ny = yB + 34, gw = a.grid_w;
    if (gw != null && Math.abs(gw) > 20) svg.append(el("path", { d: `M${X0 + 40} ${ny} V${yB}`, class: `flow-ln flow-grid${gw < 0 ? " rev" : ""}` }));
    svg.append(el("path", { d: `M${X0 + 22} ${ny} H${X0 + 40}`, class: "groundline", "stroke-width": 1.5 }));
    svg.append(el("circle", { cx: X0 + 6, cy: ny, r: 15, class: "node", stroke: "var(--c-grid)" }));
    svg.append(el("text", { x: X0 + 6, y: ny + 4, "text-anchor": "middle", class: "tech-lbl" }, "Netz"));
    svg.append(el("text", { x: X0 + 50, y: ny + 2, class: "tech-val" }, fmtW(gw == null ? null : Math.abs(gw))));
    svg.append(el("text", { x: X0 + 50, y: ny + 17, class: "tech-lbl" }, gw == null ? "" : gw < -20 ? "Einspeisung" : "Bezug vom Netz"));
    if (a.soc != null) {
      svg.append(el("circle", { cx: X1 - 70, cy: ny, r: 15, class: "node", stroke: "var(--c-bat)" }));
      svg.append(el("text", { x: X1 - 70, y: ny + 4, "text-anchor": "middle", class: "tech-lbl" }, "Akku"));
      svg.append(el("text", { x: X1 - 48, y: ny + 9, class: "tech-val" }, `${a.soc_geschaetzt ? "ca. " : ""}${nf0.format(a.soc)} %`));
    }
  }

  function drawUnit(svg, e, x, y, w, hgt) {
    const g = el("g", { class: `unit${e.zugriff ? "" : " locked"}${sel.einheit === e.id ? " sel" : ""}`, tabindex: e.zugriff ? 0 : -1, role: e.zugriff ? "button" : "img", "aria-label": e.name });
    g.append(el("rect", { x, y, width: w, height: hgt, rx: 8, class: "unit-box" }));
    const maxChars = Math.max(6, Math.floor((w - 16) / 7.4));
    const name = e.name.length > maxChars ? e.name.slice(0, maxChars - 1) + "…" : e.name;
    g.append(el("text", { x: x + 10, y: y + 19, class: "u-name" }, name));
    g.append(el("title", {}, e.name));
    if (!e.zugriff) {
      g.append(el("text", { x: x + 10, y: y + 36, class: "u-lock" }, "kein Zugriff"));
      svg.append(g);
      return;
    }
    const pct = e.w > 0 ? Math.min(1, (e.solar_w || 0) / e.w) : 0;
    g.append(el("text", { x: x + 10, y: y + 37, class: "u-val" }, `${fmtW(e.w)} · ☀ ${nf0.format(pct * 100)} %`));
    const bw = Math.min(w - 20, 120);
    g.append(el("rect", { x: x + 10, y: y + 44, width: bw, height: 5, rx: 2.5, class: "bar-bg" }));
    g.append(el("rect", { x: x + 10, y: y + 44, width: bw * pct, height: 5, rx: 2.5, class: "bar-solar" }));
    g.addEventListener("click", () => select(e.id, null, true));
    g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(e.id, null, true); } });
    svg.append(g);
    (e.zaehler || []).forEach((z, i) => {
      const mx = x + 20 + i * 28, my = y + hgt - 16;
      const m = el("g", { class: `meter${z.ok ? "" : " err"}${sel.zaehler === z.id ? " sel" : ""}`, tabindex: 0, role: "button", "aria-label": `Zähler ${z.name}` });
      m.append(el("circle", { cx: mx, cy: my, r: 11 }));
      m.append(el("text", { x: mx, y: my + 3, "text-anchor": "middle" }, `Z${i + 1}`));
      m.append(el("title", {}, `${z.name}${z.ok ? "" : " (keine Daten)"}`));
      m.addEventListener("click", (ev) => { ev.stopPropagation(); select(e.id, z.id, true); });
      m.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); select(e.id, z.id, true); } });
      svg.append(m);
    });
    if (e.offen_chf > 0) {
      const txt = w > 170 ? `${chf.format(e.offen_chf)} offen` : "offen", bw2 = txt.length * 6.2 + 14;
      const b = el("g", { class: "badge" });
      b.append(el("rect", { x: x + w - bw2 - 6, y: y + hgt - 26, width: bw2, height: 19, rx: 9.5 }));
      b.append(el("text", { x: x + w - bw2 / 2 - 6, y: y + hgt - 13, "text-anchor": "middle" }, txt));
      svg.append(b);
    }
  }

  /* ------------------------------------------------------------ Detail */
  async function select(id, zid, scroll) {
    sel = { einheit: id, zaehler: zid };
    drawSketch();
    const d = await guard(() => api.einheit(id)).catch((e) => { $("detail").innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return null; });
    if (!d) return;
    renderDetail(d);
    if (scroll && window.matchMedia("(max-width: 919px)").matches) $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function statusChip(r) {
    if (r.ueberfaellig) return '<span class="chip ueber">überfällig</span>';
    return `<span class="chip ${esc(r.status)}">${esc(r.status)}</span>`;
  }

  function renderDetail(d) {
    const live = d.zaehler.reduce((a, z) => a + (z.w || 0), 0);
    const u = haus.einheiten.find((e) => e.id === d.id) || {};
    const kann = me.rolle !== "mieter";
    const offen = d.rechnungen.filter((r) => r.status !== "bezahlt").reduce((a, r) => a + (r.betrag || 0), 0);
    let html = `
      <div class="d-head">
        <span class="chip">${esc(TYP[d.typ] || d.typ)}</span><span class="chip">${esc(d.geschoss)}</span>
        <h2>${esc(d.name)}</h2>
      </div>
      <div class="kpis">
        <div class="kpi"><span>Jetzt</span><b>${fmtW(live)}</b><small class="blk">davon Solar ${fmtW(u.solar_w)}</small></div>
        <div class="kpi"><span>Heute</span><b>${nf1.format(d.heute.verbrauch_kwh)}</b><small> kWh</small>${d.heute.solar_pct != null ? `<small class="blk">${d.heute.solar_pct} % Solar</small>` : ""}</div>
        <div class="kpi"><span>Offen</span><b>${offen ? chf.format(offen) : "–"}</b></div>
      </div>
      <section class="d-sec"><h3>Zähler</h3>
        ${d.zaehler.map((z, i) => `
          <div class="meter-card${sel.zaehler === z.id ? " sel" : ""}" id="mc-${esc(z.id)}">
            <div class="m-top"><span>Z${i + 1} · ${esc(z.name)}</span><span>${fmtW(z.w)}</span></div>
            <span class="muted">${z.seriennummer ? "Seriennummer " + esc(z.seriennummer) + " · " : ""}${esc(z.typ)}</span>
            <span>Zählerstand Bezug <b>${z.import_kwh == null ? "–" : nf1.format(z.import_kwh) + " kWh"}</b>${z.export_kwh ? ` · Rückspeisung ${nf1.format(z.export_kwh)} kWh` : ""}</span>
            ${z.fehler ? `<span class="m-err">Keine aktuellen Daten: ${esc(z.fehler)}</span>` : ""}
          </div>`).join("")}
      </section>
      <section class="d-sec"><h3>Strombezug pro Monat, kWh</h3>
        <div class="mini-legend"><span class="lg lg-pv">Solarstrom</span><span class="lg lg-grid">Netzstrom</span></div>
        <svg class="month-chart" id="monthChart" role="img" aria-label="Strombezug pro Monat"></svg>
        <p class="muted small-text">Solaranteil live verteilt nach Verbrauch. Massgebend ist die smart-me-Abrechnung.</p>
      </section>
      <section class="d-sec"><h3>Rechnungen</h3>
        ${d.rechnungen.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>Nr.</th><th>Periode</th><th class="num">Betrag</th><th>Status</th>${kann ? "<th></th>" : ""}</tr></thead><tbody>
          ${d.rechnungen.map((r) => `<tr><td class="nr">${esc(r.nr)}</td><td>${dat(r.von)} – ${dat(r.bis)}<br><span class="muted small-text">fällig ${dat(r.faellig)}</span></td><td class="num">${r.betrag == null ? "–" : chf.format(r.betrag)}</td><td>${statusChip(r)}</td>${kann ? `<td class="act">${actions(r)}</td>` : ""}</tr>`).join("")}
        </tbody></table></div>` : '<p class="muted">Noch keine Rechnungen importiert.</p>'}
      </section>`;
    if (d.berechtigte) {
      html += `<section class="d-sec"><h3>Wer hat Zugriff</h3><ul class="perm-list">
        ${d.berechtigte.map((b) => `<li><span>${esc(b.anzeigename || b.name)} <span class="muted">(${esc(b.name)})</span></span><span class="muted">${esc(ROLLE[b.rolle])}${b.einheit ? "" : " · alle Einheiten"} · ${b.letzter_login ? "zuletzt " + new Date(b.letzter_login * 1000).toLocaleDateString(L) : "nie angemeldet"}</span></li>`).join("")}
      </ul><p class="muted small-text">Zugänge verwalten: auf dem Pi mit «benutzer add / del» (siehe Anleitung).</p></section>`;
    }
    $("detail").innerHTML = html;
    drawMonths(d.monate.slice(-12));
    bindActions($("detail"), () => select(sel.einheit, sel.zaehler, false));
    if (sel.zaehler) $("mc-" + sel.zaehler)?.scrollIntoView({ block: "nearest" });
  }

  function actions(r) {
    if (r.status === "bezahlt") return `<button class="btn-secondary" data-nr="${esc(r.nr)}" data-st="offen">Wieder offen</button>`;
    return `<button class="btn-secondary" data-nr="${esc(r.nr)}" data-st="bezahlt">Bezahlt</button>${r.status === "offen" ? ` <button class="btn-secondary" data-nr="${esc(r.nr)}" data-st="gemahnt">Gemahnt</button>` : ""}`;
  }

  function bindActions(root, after) {
    root.querySelectorAll("button[data-nr]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await guard(() => api.status(b.dataset.nr, b.dataset.st)); after(); loadHausQuiet(); }
      catch (e) { b.disabled = false; alertInline(root, e.message); }
    }));
  }

  function alertInline(root, msg) { const p = document.createElement("p"); p.className = "form-error"; p.textContent = msg; root.prepend(p); }

  function drawMonths(rows) {
    const svg = $("monthChart");
    const W = Math.max(280, svg.clientWidth || 400), H = 170, Lp = 34, R = 6, T = 8, B = 22;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (!rows.length) { svg.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, "Noch keine Monatswerte")); return; }
    const max = Math.max(...rows.map((r) => r.verbrauch_kwh), 1);
    const step = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000].find((s) => s * 4 >= max) || Math.ceil(max / 4);
    const top = step * 4, y = (v) => H - B - (v / top) * (H - B - T);
    for (let i = 0; i <= 4; i++) {
      svg.append(el("line", { x1: Lp, x2: W - R, y1: y(i * step), y2: y(i * step), class: "grid" }));
      svg.append(el("text", { x: Lp - 4, y: y(i * step) + 3, "text-anchor": "end" }, nf0.format(i * step)));
    }
    const sw = (W - Lp - R) / rows.length, bw = Math.min(22, sw * 0.62);
    rows.forEach((r, i) => {
      const cx = Lp + sw * (i + 0.5), solar = Math.min(r.solar_kwh, r.verbrauch_kwh), netz = r.verbrauch_kwh - solar;
      const g = el("g");
      g.append(el("rect", { x: cx - bw / 2, y: y(solar), width: bw, height: y(0) - y(solar), class: "b-solar" }));
      g.append(el("rect", { x: cx - bw / 2, y: y(r.verbrauch_kwh), width: bw, height: y(solar) - y(r.verbrauch_kwh), rx: 2, class: "b-grid" }));
      const [yy, mm] = r.monat.split("-").map(Number);
      const label = new Date(yy, mm - 1, 1).toLocaleDateString(L, { month: "narrow" });
      g.append(el("title", {}, `${new Date(yy, mm - 1, 1).toLocaleDateString(L, { month: "long", year: "numeric" })}: ${nf1.format(r.verbrauch_kwh)} kWh, davon Solar ${nf1.format(solar)} kWh (${r.solar_pct ?? "–"} %), Netz ${nf1.format(netz)} kWh`));
      svg.append(g);
      svg.append(el("text", { x: cx, y: H - 8, "text-anchor": "middle" }, label));
    });
  }

  async function loadHausQuiet() { if (view === "haus") { try { await loadHaus(); } catch (e) { /* nächster Versuch */ } } }

  /* -------------------------------------------------------- Rechnungen */
  async function loadRechnungen() {
    const list = await guard(() => api.rechnungen());
    if (!list) return;
    const shown = list.filter((r) => rgFilter === "alle" || (rgFilter === "offen" && r.status !== "bezahlt") || (rgFilter === "ueber" && r.ueberfaellig));
    const offen = list.filter((r) => r.status !== "bezahlt").reduce((a, r) => a + (r.betrag || 0), 0);
    const ueber = list.filter((r) => r.ueberfaellig).reduce((a, r) => a + (r.betrag || 0), 0);
    $("rgSumme").textContent = `Offen ${chf.format(offen)} · davon überfällig ${chf.format(ueber)}`;
    $("rgTable").innerHTML = `<thead><tr><th>Nr.</th><th>Einheit</th><th>Empfänger</th><th>Periode</th><th>Fällig</th><th class="num">Betrag</th><th>Status</th><th></th></tr></thead><tbody>
      ${shown.map((r) => `<tr><td class="nr">${esc(r.nr)}</td><td>${esc(r.einheit_name || r.einheit)}</td><td>${esc(r.empfaenger)}</td><td>${dat(r.von)} – ${dat(r.bis)}</td><td>${dat(r.faellig)}</td><td class="num">${r.betrag == null ? "–" : chf.format(r.betrag)}</td><td>${statusChip(r)}</td><td class="act">${actions(r)}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">Keine Rechnungen in dieser Auswahl.</td></tr>'}
    </tbody>`;
    bindActions($("rgTable"), loadRechnungen);
  }

  /* ----------------------------------------------------------- Wartung */
  let scanTimer = null;
  async function loadWartung() {
    const w = await guard(() => api.wartung());
    if (!w) return;
    $("swInfo").innerHTML = `<dt>Version</dt><dd>${esc(w.version)}</dd><dt>Stand</dt><dd>${esc(w.commit || "–")} ${w.stand ? "· " + esc(w.stand) : ""}</dd><dt>Web-Update</dt><dd>${w.update_eingerichtet ? (w.update_wartet ? "läuft …" : "bereit") : "nicht eingerichtet (install.sh erneut ausführen)"}</dd>`;
    $("updateLog").textContent = w.update_log || "Noch kein Update ausgeführt.";
    if ($("updateBtn")) $("updateBtn").disabled = !w.update_eingerichtet || w.update_wartet;
    $("devList").innerHTML = (w.geraete || []).map((g) => `<li><span><span class="${g.ok ? "dot-ok" : "dot-err"}"></span>${esc(g.name)}</span><span class="muted">${esc(g.rolle)} · ${esc(g.typ)}</span></li>`).join("") || '<li class="muted">Demo-Quelle, keine echten Geräte</li>';
    $("meterList").innerHTML = (w.haus_zaehler || []).map((z) => `<li><span><span class="${z.fehler ? "dot-err" : "dot-ok"}"></span>${esc(z.name)}</span><span class="muted">${z.fehler ? esc(z.fehler) : "ok"}</span></li>`).join("") || '<li class="muted">Keine Hauszähler eingerichtet</li>';
    renderScan(w.erkennung || {});
  }

  function renderScan(s) {
    $("scanBtn").disabled = !!s.laeuft;
    $("scanStatus").textContent = s.laeuft ? `Suche läuft … ${s.fortschritt || ""}` : s.fehler ? `Fehler: ${s.fehler}` : s.funde ? `${s.funde.length} Geräte gefunden` : "";
    if (s.funde) {
      $("scanTable").innerHTML = `<thead><tr><th>Adresse</th><th>Typ</th><th>Rolle</th><th>Gerät</th><th>Hinweis</th></tr></thead><tbody>${s.funde.map((f) => `<tr><td>${esc(f.host)}</td><td>${esc(f.typ)}</td><td>${esc(f.rolle)}</td><td>${esc(f.modell)}${f.seriennummer ? `<br><span class="muted small-text">${esc(f.seriennummer)}</span>` : ""}</td><td class="muted">${esc(f.hinweis)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">Nichts gefunden. Modbus TCP am Wechselrichter aktiviert?</td></tr>'}</tbody>`;
      $("scanToml").hidden = !s.vorschlag;
      $("tomlText").textContent = s.vorschlag || "";
    }
    clearTimeout(scanTimer);
    if (s.laeuft) scanTimer = setTimeout(async () => { const w = await guard(() => api.wartung()); if (w) renderScan(w.erkennung || {}); }, 2000);
  }

  $("scanBtn").addEventListener("click", async () => { await guard(() => api.erkennen()); renderScan({ laeuft: true, fortschritt: "" }); });
  $("copyToml").addEventListener("click", async () => {
    const t = $("tomlText").textContent;
    try { await navigator.clipboard.writeText(t); $("copyToml").textContent = "Kopiert"; }
    catch (e) { const r = document.createRange(); r.selectNodeContents($("tomlText")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); $("copyToml").textContent = "Markiert, mit Ctrl+C kopieren"; }
    setTimeout(() => { $("copyToml").textContent = "Kopieren"; }, 2500);
  });

  function updateRow() {
    const row = $("updateRow");
    row.innerHTML = '<button class="btn-primary" id="updateBtn">Update installieren</button>';
    $("updateBtn").addEventListener("click", () => {
      row.innerHTML = '<span>Update jetzt installieren? Der Dienst startet danach neu (ca. 1 Minute).</span><button class="btn-primary" id="updYes">Ja, installieren</button><button class="btn-secondary" id="updNo">Abbrechen</button>';
      $("updNo").addEventListener("click", () => { updateRow(); loadWartung(); });
      $("updYes").addEventListener("click", async () => {
        $("updYes").disabled = true;
        try {
          const r = await guard(() => api.update());
          $("updateHint").textContent = (r?.hinweis || "") + " Die Seite lädt in einer Minute neu.";
          setTimeout(() => location.reload(), DEMO ? 3000 : 60000);
        } catch (e) { $("updateHint").textContent = e.message; updateRow(); }
      });
    });
  }
  updateRow();

  /* ------------------------------------------------------------- Start */
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  document.querySelectorAll("#view-rechnungen [data-filter]").forEach((b) => b.addEventListener("click", () => {
    rgFilter = b.dataset.filter;
    document.querySelectorAll("#view-rechnungen [data-filter]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    loadRechnungen();
  }));
  $("csvFile").addEventListener("change", async (ev) => {
    const f = ev.target.files[0];
    if (!f) return;
    const box = $("importResult");
    box.hidden = false; box.textContent = "Importiere …";
    try {
      const r = await guard(async () => api.importCsv(await f.text()));
      if (r) box.textContent = r.demo ? "Demo: Import wird nur auf dem Pi ausgeführt." : `${r.neu} neu, ${r.aktualisiert} aktualisiert${r.nicht_zugeordnet.length ? `. Nicht zugeordnet: ${r.nicht_zugeordnet.join(", ")}` : ""}`;
      loadRechnungen();
    } catch (e) { box.textContent = e.message; }
    ev.target.value = "";
  });
  $("loginForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("loginError").hidden = true; $("loginBtn").disabled = true;
    try { me = await api.login($("benutzer").value.trim(), $("passwort").value); $("passwort").value = ""; sel = { einheit: null, zaehler: null }; showApp(); }
    catch (e) { $("loginError").textContent = e.message; $("loginError").hidden = false; }
    finally { $("loginBtn").disabled = false; }
  });
  $("logout").addEventListener("click", async () => { try { await api.logout(); } catch (e) { /* egal */ } me = null; haus = null; sel = { einheit: null, zaehler: null }; $("detail").innerHTML = ""; showLogin(); });
  if (DEMO) { $("backLink").href = "index.html?demo"; $("demoBand").hidden = false; }

  setInterval(loadHausQuiet, 15000);
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (haus && view === "haus") drawSketch(); }, 150); });
  (async () => {
    try { me = await api.me(); showApp(); }
    catch (e) { showLogin(); }
  })();
})();
