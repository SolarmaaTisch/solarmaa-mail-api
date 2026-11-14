// Abläufe der Portal-Seite im Browser (Handy-Format) gegen die Attrappe. Gestartet von
// run.sh --browser:  node browser.js <url-der-attrappe> <repo>   (PGHOST/PGPORT/PGDATABASE gesetzt)
// Braucht Playwright (wird hier nicht installiert). Den Pi spielt pi_ablauf.py --runde.
"use strict";
const { chromium, devices } = require("playwright");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const [URL_, REPO] = process.argv.slice(2);
const PW = "test-passwort-1";
const CHEF = "00000000-0000-0000-0000-000000000001";
const GEHEIM = ["ablauf-smartme-schluessel-1", "ablauf-easee-passwort-2"];
const piDir = fs.mkdtempSync(path.join(os.tmpdir(), "smpi-browser."));
let browser, seite, schritt = "Start";

function ok(was, bedingung, detail = "") {
  if (!bedingung) throw new Error(`${was} ${detail}`);
  console.log(`ok - ${was}`);
}
const sql = (stmt) => execFileSync("psql", ["-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", stmt], { encoding: "utf8" }).trim();
function piRunde(token, ordner = piDir) {
  const aus = execFileSync("python3", [path.join(REPO, "tests/portal/pi_ablauf.py"), URL_, "--runde", ordner, token], { encoding: "utf8" });
  return JSON.parse(aus.trim().split("\n").pop());
}

(async () => {
  sql(`insert into public.admins (user_id) values ('${CHEF}') on conflict do nothing`);
  browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices["iPhone 13"] });
  const p = await ctx.newPage();
  seite = p;
  const fehler = [], antworten = [];
  p.on("pageerror", (e) => fehler.push(`Seitenfehler: ${e.message}`));
  p.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) fehler.push(`Konsole: ${m.text()}`); });
  p.on("response", (r) => { if (r.status() >= 400) antworten.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  await p.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => { window.__csp = (window.__csp || []).concat(`${e.violatedDirective} ${e.blockedURI}`); }));
  // Erwartete Fehlantworten (z. B. absichtlich falscher Code) aus der Liste nehmen; alles andere ist ein Befund
  const erwartet = (muster, was) => {
    const i = antworten.findIndex((a) => muster.test(a));
    ok(was, i >= 0, `(keine Antwort passend zu ${muster}; gesehen: ${antworten.join(", ")})`);
    antworten.splice(i, 1);
  };
  const pruefen = async (wo) => {
    schritt = wo;
    const a = await p.evaluate(() => ({
      breit: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      klein: [...document.querySelectorAll("button, input, textarea, select, a")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && !e.closest("[hidden]") && r.height < 44; }).map((e) => e.id || e.className),
      csp: window.__csp || [],
    }));
    ok(`${wo}: kein seitliches Überlaufen`, !a.breit);
    ok(`${wo}: Bedienelemente mindestens 44 px`, a.klein.length === 0, JSON.stringify(a.klein));
    ok(`${wo}: keine CSP-Verstösse, keine Skriptfehler, keine unerwarteten Fehlantworten`, a.csp.length + fehler.length + antworten.length === 0, JSON.stringify([a.csp, fehler, antworten]));
  };
  // Direkter Aufruf an der Seite vorbei, mit der Sitzung des angemeldeten Kontos
  const direkt = (pfad, body) => p.evaluate(async ([pfad_, body_]) => {
    const s = JSON.parse(localStorage.getItem("sm_portal"));
    const r = await fetch(window.PORTAL.url + pfad_, { method: body_ ? "POST" : "GET", body: body_ ? JSON.stringify(body_) : undefined, headers: { apikey: window.PORTAL.key, Authorization: `Bearer ${s.access}`, "Content-Type": "application/json" } });
    let d = null; try { d = await r.json(); } catch (e) { /* leer */ }
    return { status: r.status, d };
  }, [pfad, body]);
  const anmelden = async (mail) => {
    await p.waitForSelector("#vLogin:not([hidden])");
    await p.fill("#email", mail); await p.fill("#pw", PW); await p.tap("#loginBtn");
  };
  const abmelden = async () => { await p.tap("#logout"); await p.waitForSelector("#vLogin:not([hidden])"); };
  const zweimal = async (sel) => { await p.tap(sel); await p.tap(sel); };
  const befehl = (n = 1) => p.locator(`#fernBefehle li:nth-child(${n})`);
  const warteStand = (text) => p.waitForFunction((t) => (document.querySelector("#fernBefehle li")?.textContent || "").includes(t), text, { timeout: 20000 });

  // ---------------------------------------------------------------- Anmelden, Anlage anlegen
  schritt = "Anmeldung";
  await p.goto(URL_ + "/");
  await p.waitForSelector("#vLogin:not([hidden])");
  const zustandSeite = () => p.evaluate(() => [...document.querySelectorAll("[id]")].map((e) => `${e.id}:${e.hidden ? "h" : ""}${e.disabled ? "d" : ""}`));
  const frisch = await zustandSeite();
  const wieFrisch = async (wo) => {
    const jetzt = await zustandSeite(), abw = jetzt.filter((z, i) => z !== frisch[i]);
    ok(`${wo}: hidden/disabled aller Elemente wie auf der frisch geladenen Seite`, jetzt.length === frisch.length && abw.length === 0, JSON.stringify(abw));
  };
  ok("verstecktes Formular «Neue Anlage» ist unsichtbar", !(await p.isVisible("#fAdd")));
  await p.fill("#email", "chef@solarmaa.ch"); await p.fill("#pw", "falsch"); await p.tap("#loginBtn");
  await p.waitForSelector("#loginErr:not([hidden])");
  erwartet(/^400 POST \/auth\/v1\/token/, "falsches Passwort wird abgelehnt");
  await anmelden("chef@solarmaa.ch");
  await p.waitForSelector("#addOpen:not([hidden])");
  await p.tap("#addOpen"); await p.fill("#aName", "Browser-Anlage"); await p.fill("#aOrt", "Biel"); await p.fill("#aKwp", "240");
  await p.tap("#fAdd button[type=submit]");
  await p.waitForSelector("#tokenBox:not([hidden])");
  const block = await p.textContent("#tokenText");
  const token = block.match(/token = "([0-9a-f]+)"/)[1];
  ok("Block für den Pi enthält fernwartung = true mit Hinweis", /\n# Fernwartung durch Solarmaa \(auf false setzen, wenn nicht vereinbart\)\nfernwartung = true\n/.test(block));
  await pruefen("Liste mit Token");
  await p.tap("#tokenClose");
  ok("Pi meldet sich", piRunde(token).fehler === null);

  // ---------------------------------------------------------------- Ablauf 2: ohne Zwei-Faktor gesperrt
  await p.reload(); await p.waitForSelector(".plant");
  await p.tap(".plant"); await p.waitForSelector("#mfaNeu:not([hidden])");
  const anlage = await p.evaluate(() => JSON.parse(localStorage.getItem("sm_portal")) && document.querySelector("#pName").textContent);
  ok("Anlage geöffnet", anlage === "Browser-Anlage");
  const aId = sql("select id from public.anlagen where name = 'Browser-Anlage'");
  ok("ohne Zwei-Faktor: Knöpfe gesperrt", (await p.isDisabled("#fernUpdate")) && (await p.isDisabled("#fernKonfigLaden")));
  ok("ohne Zwei-Faktor: Hinweis", /Zwei-Faktor/.test(await p.textContent("#fernHinweis")));
  let r = await direkt("/rest/v1/rpc/befehl_anlegen", { p_anlage: aId, p_art: "update" });
  ok("ohne Zwei-Faktor: direkter Befehl 403", r.status === 403, JSON.stringify(r));
  erwartet(/^403 POST \/rest\/v1\/rpc\/befehl_anlegen/, "… und als Fehlantwort gesehen");
  r = await direkt(`/rest/v1/anlagen_konfig?select=text,stand&anlage_id=eq.${aId}`);
  ok("ohne Zwei-Faktor: Konfiguration leer", r.status === 200 && Array.isArray(r.d) && r.d.length === 0, JSON.stringify(r));
  await zweimal("#tokenNew");
  await p.waitForSelector("#memErr:not([hidden])");
  ok("Neuer Pi-Token ohne Zwei-Faktor: verständliche Meldung", /Zwei-Faktor-Anmeldung nötig.*Fernwartung/.test(await p.textContent("#memErr")));
  erwartet(/^403 POST \/rest\/v1\/rpc\/anlage_token_neu/, "Neuer Pi-Token ohne Zwei-Faktor abgelehnt");
  await p.fill("#mEmail", "kunde@example.ch"); await p.tap("#fMember button"); await p.waitForSelector("#members button");
  await pruefen("Anlage ohne Zwei-Faktor");

  // ---------------------------------------------------------------- Ablauf 1: Zwei-Faktor einrichten
  await p.tap("#mfaSetup"); await p.waitForSelector("#mfaEnroll:not([hidden])");
  await p.waitForFunction(() => { const i = document.querySelector("#mfaQr"); return i.complete && i.naturalWidth > 0; });
  ok("QR-Code wird angezeigt (CSP erlaubt das Bild)", await p.isVisible("#mfaQr"));
  ok("Schlüssel wird angezeigt", (await p.textContent("#mfaSecret")).length > 10);
  await pruefen("Zwei-Faktor einrichten");
  await p.fill("#mfaEnrollCode", "111111"); await p.tap("#mfaEnroll button[type=submit]");
  await p.waitForSelector("#mfaErr:not([hidden])");
  ok("falscher Code: Meldung", /Code falsch/.test(await p.textContent("#mfaErr")));
  erwartet(/^422 POST \/auth\/v1\/factors\/.+\/verify/, "falscher Code wird abgelehnt");
  await p.fill("#mfaEnrollCode", "424242"); await p.tap("#mfaEnroll button[type=submit]");
  await p.waitForSelector("#mfaOk:not([hidden])");
  ok("nach dem Aktivieren: Schlüssel und QR-Code nicht mehr im Dokument",
    (await p.textContent("#mfaSecret")) === "" && (await p.getAttribute("#mfaQr", "src")) === null && !(await p.content()).includes("TESTSECRET"));
  ok("Schlüssel liegt nicht im Browser-Speicher", !(await p.evaluate(() => JSON.stringify(localStorage))).includes("TESTSECRET"));
  await p.waitForFunction(() => !document.querySelector("#fernUpdate").disabled);
  ok("mit Zwei-Faktor: Knöpfe aktiv, kein Hinweis", !(await p.isDisabled("#fernKonfigLaden")) && (await p.isHidden("#fernHinweis")));

  // ---------------------------------------------------------------- Ablauf 4: Konfiguration ändern
  await p.tap("#fernKonfigLaden"); await p.waitForSelector("#fernKonfig:not([hidden])");
  const konfig = await p.inputValue("#fernKonfig");
  ok("Konfiguration geladen, Geheimnisse verdeckt", konfig.includes("********") && !GEHEIM.some((g) => konfig.includes(g)) && !konfig.includes(token));
  await pruefen("Konfiguration geladen");
  await p.fill("#fernKonfig", konfig.replace("Ablauf Alt", "Vom Portal geändert"));
  await zweimal("#fernKonfigSenden");
  await warteStand("wartet auf den Pi");
  ok("nach dem Senden ist das Textfeld leer und versteckt", (await p.inputValue("#fernKonfig")) === "" && (await p.isHidden("#fernKonfig")));
  ok("Nutzlast ist nicht lesbar", (await direkt(`/rest/v1/befehle?select=nutzlast&anlage_id=eq.${aId}`)).status === 403);
  erwartet(/^403 GET \/rest\/v1\/befehle/, "… Abfrage der Nutzlast abgelehnt");
  ok("Pi holt den Befehl ab und startet neu", piRunde(token).neustart === true);
  await warteStand("abgeholt, läuft");
  const datei = () => fs.readFileSync(path.join(piDir, "config.toml"), "utf8");
  ok("Pi hat gespeichert, Geheimnisse erhalten", datei().includes('name = "Vom Portal geändert"') && GEHEIM.every((g) => datei().includes(g)));
  piRunde(token);                                    // neuer Prozess = Dienst neu gestartet
  await warteStand("erledigt");
  ok("Quittung wird angezeigt", /läuft mit neuer Konfiguration/.test(await befehl().textContent()));
  await pruefen("Befehl erledigt");

  // ---------------------------------------------------------------- Anlage erneut öffnen, Antworten verzögert
  await p.tap("#back"); await p.waitForSelector(".plant");
  await p.route("**/rest/v1/messwerte*", async (route) => { await new Promise((res) => setTimeout(res, 1500)); await route.continue(); });
  await p.tap(".plant"); await p.waitForSelector("#vPlant:not([hidden])");
  ok("während die Anlage lädt, sind die Knöpfe der Fernwartung gesperrt", (await p.isDisabled("#fernUpdate")) && (await p.isDisabled("#fernKonfigLaden")));
  await p.evaluate(() => { for (const id of ["fernKonfigLaden", "fernUpdate", "fernUpdate"]) { const b = document.getElementById(id); b.disabled = false; b.click(); b.disabled = true; } });
  await p.waitForFunction(() => !document.querySelector("#fernUpdate").disabled, null, { timeout: 15000 });
  await p.unroute("**/rest/v1/messwerte*");
  await warteStand("erledigt");
  ok("kein Befehl ohne Anlage gesendet", (await p.locator("#fernBefehle li").count()) === 1 && (await p.isHidden("#fernErr")));
  await pruefen("Anlage mit verzögerten Antworten");

  // ---------------------------------------------------------------- Ablauf 5: gesperrter Abschnitt
  await p.tap("#fernKonfigLaden"); await p.waitForFunction(() => document.querySelector("#fernKonfig").value.includes("Vom Portal geändert"));
  const konfig2 = await p.inputValue("#fernKonfig");
  await p.fill("#fernKonfig", konfig2.replace("fernwartung = true", "fernwartung = false"));
  await zweimal("#fernKonfigSenden"); await warteStand("wartet auf den Pi");
  const vorher = datei();
  piRunde(token);
  await warteStand("fehler");
  ok("Änderung in [portal]: verständlicher Fehler", /\[portal\] kann nur vor Ort geändert werden/.test(await befehl().textContent()));
  ok("Datei auf dem Pi unverändert", datei() === vorher);

  // ---------------------------------------------------------------- Ablauf 6: Update ohne Einrichtung
  await zweimal("#fernUpdate"); await warteStand("wartet auf den Pi");
  piRunde(token);
  await warteStand("fehler");
  ok("Update in der Demo: Fehler mit Text", /Web-Update ist auf diesem Gerät nicht eingerichtet/.test(await befehl().textContent()));

  // ---------------------------------------------------------------- Ablauf 11: Ergebnis ist nur Text
  sql(`update public.befehle set ergebnis = '<script>window.xss = 1</script><img src=x onerror="window.xss = 1"> ' || repeat('SehrLangesWortOhneLeerzeichen', 40) where id = (select id from public.befehle order by erstellt desc limit 1)`);
  await p.tap("#back"); await p.waitForSelector(".plant"); await p.tap(".plant");
  await p.waitForFunction(() => (document.querySelector("#fernBefehle li")?.textContent || "").includes("<script>"));
  await p.waitForTimeout(300);
  ok("HTML im Ergebnis wird als Text gezeigt, nichts ausgeführt", (await p.evaluate(() => window.xss)) === undefined && (await p.locator("#fernBefehle img, #fernBefehle script").count()) === 0);
  await pruefen("langes Ergebnis mit HTML");

  // ---------------------------------------------------------------- Ablauf 9: Pi kommt nicht, Befehl verfällt
  await zweimal("#fernUpdate"); await warteStand("wartet auf den Pi");
  sql("update public.befehle set erstellt = now() - interval '2 hours' where status = 'offen'");
  await p.tap("#back"); await p.waitForSelector(".plant"); await p.tap(".plant");
  await p.waitForFunction(() => /Update\s*verfallen/.test(document.querySelector("#fernBefehle").textContent));      // steht wegen des Alters zuunterst
  ok("nicht abgeholter Befehl: verfallen", true);
  sql("update public.befehle set status = 'gesendet', gesendet_am = now() - interval '50 minutes' where status = 'offen'");
  await p.tap("#back"); await p.waitForSelector(".plant"); await p.tap(".plant");
  await p.waitForFunction(() => document.querySelector("#fernBefehle").textContent.includes("keine Quittung"));
  ok("abgeholt, nie quittiert: keine Quittung", true);

  // ---------------------------------------------------------------- Ablauf 10: Abmelden räumt auf
  await p.tap("#fernKonfigLaden"); await p.waitForSelector("#fernKonfig:not([hidden])");
  await p.fill("#mEmail", "rest@example.ch");
  await p.evaluate(() => { document.getElementById("email").value = "chef@solarmaa.ch"; });     // wie nach einer Anmeldung ohne Neuladen
  ok("vor dem Abmelden steht der Zustand der Anlage im Dokument", (await p.textContent("#pState")).length > 0);
  await abmelden();
  const rest = await p.evaluate(() => ({ text: document.querySelector("#fernKonfig").value, liste: document.querySelector("#fernBefehle").children.length,
    secret: document.querySelector("#mfaSecret").textContent, felder: ["email", "pw", "mEmail", "mfaCode", "mfaEnrollCode", "aName"].map((id) => document.getElementById(id).value).join(""), zustand: document.querySelector("#pState").textContent, qr: document.querySelector("#mfaQr").getAttribute("src"), html: document.documentElement.outerHTML, speicher: JSON.stringify(localStorage) }));
  ok("nach dem Abmelden: Textfeld, Befehlsliste, Schlüssel und QR-Code leer", rest.text === "" && rest.liste === 0 && rest.secret === "" && rest.qr === null);
  ok("nach dem Abmelden: nichts von der Anlage im Dokument oder Speicher", !/Vom Portal geändert|ablauf-|TESTSECRET|Browser-Anlage|chef@|api_key/.test(rest.html) && rest.speicher === "{}",
    `(gefunden: ${(rest.html.match(/.{30}(Vom Portal geändert|ablauf-|TESTSECRET|Browser-Anlage|chef@|api_key).{30}/s) || [""])[0]}; Speicher: ${rest.speicher})`);
  ok("Karte Fernwartung versteckt", await p.isHidden("#pFern"));
  await wieFrisch("nach dem Abmelden eines Solarmaa-Kontos mit Zwei-Faktor");
  ok("nach dem Abmelden: Eingabefelder (E-Mail, Kunde) und Zustand der Anlage leer", rest.felder === "" && rest.zustand === "", JSON.stringify([rest.felder, rest.zustand]));

  // ---------------------------------------------------------------- Zweite Anmeldung: Code statt Einrichtung
  await anmelden("chef@solarmaa.ch"); await p.waitForSelector(".plant"); await p.tap(".plant");
  await p.waitForSelector("#fMfa:not([hidden])");
  ok("neue Anmeldung beginnt ohne Zwei-Faktor", (await p.isDisabled("#fernUpdate")) && (await p.isHidden("#mfaNeu")));
  await p.fill("#mfaCode", "12"); await p.tap("#fMfa button[type=submit]");
  await p.waitForSelector("#mfaErr:not([hidden])");
  ok("unvollständiger Code: Meldung ohne Anfrage", /6-stelligen Code/.test(await p.textContent("#mfaErr")));
  await p.fill("#mfaCode", "424242"); await p.tap("#fMfa button[type=submit]");
  await p.waitForSelector("#mfaOk:not([hidden])");
  await pruefen("Zwei-Faktor bestätigt");

  // ---------------------------------------------------------------- Zwei Anlagen: verspätete Antworten, Rückfragen
  const A = "Browser-Anlage", B = "Zweite Anlage";
  const tokB = JSON.parse(sql(`select set_config('request.jwt.claims', '{"sub":"${CHEF}","aal":"aal2"}', false); select public.anlage_anlegen('${B}')`).split("\n").pop()).token;
  const piDirB = fs.mkdtempSync(path.join(os.tmpdir(), "smpi-browser-b."));
  ok("zweite Anlage meldet sich", piRunde(tokB, piDirB).fehler === null);
  const bId = sql(`select id from public.anlagen where name = '${B}'`);
  const oeffne = async (name) => { await p.tap(`.plant:has-text("${name}")`); await p.waitForFunction((n) => document.querySelector("#pName").textContent === n, name); };
  const zurListe = async () => { await p.tap("#back"); await p.waitForSelector("#vList:not([hidden]) .plant"); };
  const gesendet = [];
  const mitschnitt = (r) => { if (/befehl_anlegen|anlage_token_neu|anlagen_konfig|\/befehle\?/.test(r.url())) gesendet.push(`${r.url()} ${r.postData() || ""}`); };
  p.on("request", mitschnitt);
  await zurListe();
  // Antwort für Anlage A kommt erst, wenn längst Anlage B angezeigt wird
  const langsamA = new RegExp("/rest/v1/messwerte.*" + aId);
  await p.route(langsamA, async (route) => { await new Promise((res) => setTimeout(res, 3000)); await route.continue().catch(() => {}); });
  await oeffne(A); await zurListe(); await oeffne(B);
  gesendet.length = 0;
  await p.waitForTimeout(4000);                      // die verspätete Antwort von A ist jetzt da
  await p.waitForFunction(() => !document.querySelector("#fernUpdate").disabled);
  ok("verspätete Antwort von A ändert die angezeigte Anlage B nicht", (await p.textContent("#pName")) === B);
  await p.tap("#fernKonfigLaden"); await p.waitForSelector("#fernKonfig:not([hidden])");
  const konfigB = await p.inputValue("#fernKonfig");
  ok("unter Anlage B wird die Konfiguration von B geladen", konfigB.includes('name = "Ablauf Alt"') && !konfigB.includes("Vom Portal geändert"));
  await zweimal("#fernUpdate"); await warteStand("wartet auf den Pi");
  const befehle = gesendet.filter((g) => /befehl_anlegen/.test(g));
  ok("«Update auslösen» geht an die angezeigte Anlage B", befehle.length === 1 && befehle[0].includes(bId), JSON.stringify(befehle));
  ok("kein einziger Aufruf der Fernwartung mit der ID von A", !gesendet.some((g) => g.includes(aId)), JSON.stringify(gesendet.filter((g) => g.includes(aId))));
  ok("in der Datenbank: kein neuer Befehl für A", sql(`select count(*) from public.befehle where anlage_id = '${aId}' and status = 'offen'`) === "0");
  // Konfiguration von B geladen, dann zu A gewechselt: nichts davon bleibt, «senden» ist weg
  await zurListe(); await p.unroute(langsamA); await oeffne(A);
  await p.waitForFunction(() => !document.querySelector("#fernUpdate").disabled);
  ok("nach dem Wechsel zu A: geladene Konfiguration von B ist weg", (await p.inputValue("#fernKonfig")) === "" && (await p.isHidden("#fernKonfigSenden")));
  sql("update public.befehle set status = 'verfallen' where status = 'offen'");
  // Rückfrage «Wirklich?» gilt nur für die Anlage, bei der sie gestellt wurde
  const hashB = () => sql(`select token_hash from public.anlagen where id = '${bId}'`);
  const vorher2 = hashB();
  await p.tap("#tokenNew");
  ok("erster Klick fragt nach", /Wirklich/.test(await p.textContent("#tokenNew")));
  await p.tap("#fernUpdate");
  await zurListe(); await oeffne(B);
  await p.waitForFunction(() => !document.querySelector("#fernUpdate").disabled);
  ok("Rückfragen sind nach dem Anlagenwechsel zurückgesetzt", (await p.textContent("#tokenNew")) === "Neuen Pi-Token erzeugen" && (await p.textContent("#fernUpdate")) === "Update auslösen");
  gesendet.length = 0;
  await p.tap("#tokenNew"); await p.tap("#fernUpdate"); await p.waitForTimeout(700);
  ok("ein einzelner Klick bei B löst nichts aus", hashB() === vorher2 && !gesendet.some((g) => /anlage_token_neu|befehl_anlegen/.test(g)), JSON.stringify(gesendet));
  await p.waitForFunction(() => document.querySelector("#tokenNew").textContent === "Neuen Pi-Token erzeugen" && document.querySelector("#fernUpdate").textContent === "Update auslösen", null, { timeout: 15000 });
  ok("Rückfragen verfallen nach einigen Sekunden von selbst", true);
  await p.tap("#tokenNew"); await zurListe();
  ok("Rückfrage ist auch nach dem Wechsel zur Liste zurückgesetzt", (await p.evaluate(() => document.querySelector("#tokenNew").dataset.sure)) !== "1");
  p.off("request", mitschnitt);

  // Mitglieder: Während B lädt, zeigt die Ansicht nichts von A, und nichts geht an die falsche Anlage
  await oeffne(A); await p.waitForSelector("#members button");
  await p.evaluate(() => { window.__knopfVonA = document.querySelector("#members button"); });
  await zurListe();
  const mitglied = [], merke = (r) => { if (/mitglied_setzen/.test(r.url())) mitglied.push(r.postData()); };
  p.on("request", merke);
  const langsamB = new RegExp("/rest/v1/anlagen\\?.*id=eq\\." + bId);
  await p.route(langsamB, async (route) => { await new Promise((res) => setTimeout(res, 2500)); await route.continue().catch(() => {}); });
  await p.tap(`.plant:has-text("${B}")`); await p.waitForSelector("#vPlant:not([hidden])");
  const waehrend = await p.evaluate(() => ({ name: document.getElementById("pName").textContent, members: document.getElementById("members").textContent, admin: document.getElementById("pAdmin").hidden, fern: document.getElementById("pFern").hidden }));
  ok("während B lädt: nichts von A sichtbar, Verwaltung und Fernwartung versteckt", !waehrend.name.includes(A) && waehrend.members === "" && waehrend.admin && waehrend.fern, JSON.stringify(waehrend));
  await p.evaluate(() => { document.getElementById("mEmail").value = "fremd@example.ch"; document.getElementById("fMember").requestSubmit(); });
  await p.waitForFunction((n) => document.querySelector("#pName").textContent === n, B, { timeout: 10000 });
  await p.unroute(langsamB);
  await p.waitForSelector("#members li");
  await p.evaluate(() => window.__knopfVonA.click());                 // alter Knopf «Entfernen» von A
  await p.waitForTimeout(800);
  p.off("request", merke);
  ok("kein Mitglied-Aufruf, solange B lädt oder mit einem Knopf von A", mitglied.length === 0, JSON.stringify(mitglied));
  ok("Zuordnungen unverändert", sql(`select count(*) from public.mitglieder where anlage_id = '${aId}'`) === "1" && sql(`select count(*) from public.mitglieder where anlage_id = '${bId}'`) === "0");
  await zurListe(); await oeffne(A);
  await pruefen("zwei Anlagen");
  fs.rmSync(piDirB, { recursive: true, force: true });

  // ---------------------------------------------------------------- Ablauf 7: Fernwartung auf dem Pi aus
  fs.writeFileSync(path.join(piDir, "config.toml"), datei().replace("fernwartung = true", "fernwartung = false"));
  piRunde(token);
  await p.tap("#back"); await p.waitForSelector(".plant"); await p.tap(".plant");
  await p.waitForFunction(() => /ausgeschaltet/.test(document.querySelector("#fernHinweis").textContent));
  ok("Fernwartung aus: Hinweis statt aktiver Knöpfe", (await p.isDisabled("#fernUpdate")) && (await p.isDisabled("#fernKonfigLaden")));
  r = await direkt("/rest/v1/rpc/befehl_anlegen", { p_anlage: aId, p_art: "update" });
  ok("Fernwartung aus: direkter Befehl abgelehnt", r.status >= 400 && /nicht eingeschaltet/.test(r.d.message), JSON.stringify(r));
  erwartet(/POST \/rest\/v1\/rpc\/befehl_anlegen/, "… als Fehlantwort gesehen");
  r = await direkt(`/rest/v1/anlagen_konfig?select=text&anlage_id=eq.${aId}`);
  ok("Fernwartung aus: keine Konfiguration mehr im Portal", r.status === 200 && r.d.length === 1 && r.d[0].text === "", JSON.stringify(r));
  await pruefen("Fernwartung aus");

  // ---------------------------------------------------------------- Sitzung abgelaufen: aufräumen wie beim Abmelden
  const reste = () => p.evaluate(() => ({ text: ["pName", "pMeta", "pState", "pNow", "pToday", "chDay", "chDays", "members", "plants", "fernBefehle", "who", "fernHinweis"].map((id) => document.getElementById(id).textContent.trim()).join(""),
    felder: ["email", "mEmail", "fernKonfig"].map((id) => document.getElementById(id).value).join(""), speicher: JSON.stringify(localStorage), fern: document.getElementById("pFern").hidden && document.getElementById("pAdmin").hidden }));
  await p.route("**/rest/v1/anlagen?*", (route) => route.fulfill({ status: 401, contentType: "application/json", body: '{"message":"JWT expired"}' }));
  await p.tap("#back"); await p.waitForSelector("#vLogin:not([hidden])");
  await p.unroute("**/rest/v1/anlagen?*");
  erwartet(/^401 GET \/rest\/v1\/anlagen/, "abgelaufene Sitzung führt zur Anmeldung");
  let r2 = await reste();
  ok("abgelaufene Sitzung: nichts vom Konto bleibt im Dokument oder Speicher", r2.text === "" && r2.felder === "" && r2.speicher === "{}" && r2.fern, JSON.stringify(r2));

  // ---------------------------------------------------------------- Abmelden, während die Anlage noch lädt
  await anmelden("chef@solarmaa.ch"); await p.waitForSelector(".plant");
  await p.route("**/rest/v1/anlagen?*id=eq*", async (route) => { await new Promise((res) => setTimeout(res, 2000)); await route.continue().catch(() => {}); });
  let spaet = 0;
  const zaehlen = (r) => { if (/\/rest\/v1\/anlagen\?.*id=eq/.test(r.url())) spaet++; };
  p.on("response", zaehlen);
  await p.tap(".plant"); await p.waitForSelector("#vPlant:not([hidden])");
  await p.tap("#logout"); await p.waitForSelector("#vLogin:not([hidden])");
  ok("Abmelden kam vor der Antwort", spaet === 0);
  await p.waitForTimeout(3000);                      // die verspätete Antwort ist jetzt da
  p.off("response", zaehlen);
  ok("die verspätete Antwort ist nach dem Abmelden eingetroffen", spaet === 1, `(${spaet})`);
  await p.unroute("**/rest/v1/anlagen?*id=eq*");
  r2 = await reste();
  ok("Abmelden während des Ladens: verspätete Antwort trägt nichts mehr ein", r2.text === "" && r2.felder === "" && r2.speicher === "{}" && r2.fern, JSON.stringify(r2));
  ok("… und die Anmeldeansicht bleibt stehen", (await p.isVisible("#vLogin")) && (await p.isHidden("#vPlant")));
  antworten.splice(0, antworten.length, ...antworten.filter((a) => !/^401 /.test(a)));      // Abfragen nach dem Abmelden dürfen 401 erhalten

  // ---------------------------------------------------------------- Anlagedoku (E8)
  schritt = "Anlagedoku";
  const PDF = Buffer.from("%PDF-1.4\n% Solarmaa Test\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n");
  const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
  const SVG = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>window.xss = 1</script></svg>');
  const HTML = Buffer.from("<!doctype html><script>window.xss = 1</script>");
  const dokZeile = (titel) => p.locator("#dokListe li", { hasText: titel });
  const dokTitel = (titel) => p.locator("#dokListe button.dok-titel", { hasText: titel });
  const hochladen = async (datei, titel, sichtbar = "false") => {
    await p.setInputFiles("#dokDatei", datei);
    await p.fill("#dokNeuTitel", titel);
    await p.selectOption("#dokNeuSichtbar", sichtbar);
    await p.tap("#dokHochladen");
  };
  const dokFehlerText = async (muster) => {
    await p.waitForFunction((m) => { const e = document.getElementById("dokErr"); return e.checkVisibility() && new RegExp(m).test(e.textContent); }, muster.source, { timeout: 10000 });
    return true;
  };
  // Aufruf an der Seite vorbei mit der Sitzung des angemeldeten Kontos, Antwort als Text (Storage)
  const roh = (pfad, opt = {}) => p.evaluate(async ([pfad_, opt_]) => {
    const s = JSON.parse(localStorage.getItem("sm_portal"));
    const r = await fetch(window.PORTAL.url + pfad_, { method: opt_.method || "GET", body: opt_.body, headers: { apikey: window.PORTAL.key, Authorization: `Bearer ${s.access}`, ...(opt_.headers || {}) } });
    return { status: r.status, text: await r.text() };
  }, [pfad, opt]);
  const dokAnfragen = [];
  const dokMit = (r) => { if (/rpc\/dokument_|\/storage\/v1\//.test(r.url())) dokAnfragen.push(`${r.method()} ${new URL(r.url()).pathname.replace(/[0-9a-f-]{36}/g, "ID")}`); };
  p.on("request", dokMit);

  await anmelden("chef@solarmaa.ch"); await p.waitForSelector(".plant");
  await oeffne(A); await p.waitForSelector("#pDoku:not([hidden])");
  await p.waitForSelector("#dokHinweis:not([hidden])");
  ok("Solarmaa ohne Zwei-Faktor: Hinweis in der Karte Dokumente", /Zwei-Faktor/.test(await p.textContent("#dokHinweis")));
  ok("Standard beim Hochladen: nur Solarmaa, Hinweis zu Passwörtern und GPS", (await p.inputValue("#dokNeuSichtbar")) === "false" && /Passwörter.*GPS/.test(await p.textContent("#fDok")));
  await hochladen({ name: "Schema.pdf", mimeType: "application/pdf", buffer: PDF }, "Schema");
  await dokFehlerText(/Zwei-Faktor-Anmeldung nötig/);
  ok("ohne Zwei-Faktor: Hochladen abgelehnt mit Zwei-Faktor-Hinweis", true);
  erwartet(/^403 POST \/rest\/v1\/rpc\/dokument_anlegen/, "… von der Datenbank abgelehnt");
  ok("… nichts angelegt, nichts hochgeladen", sql("select count(*) from public.dokumente") === "0" && !dokAnfragen.some((a) => /storage/.test(a)));
  await p.fill("#mfaCode", "424242"); await p.tap("#fMfa button[type=submit]"); await p.waitForSelector("#mfaOk:not([hidden])");
  await p.waitForFunction(() => document.getElementById("dokHinweis").hidden && /Noch keine Dokumente/.test(document.getElementById("dokListe").textContent));
  ok("nach Zwei-Faktor: Hinweis weg, Liste neu geladen", true);

  // SVG und HTML: im Browser (Inhalt geprüft, nicht Endung), per RPC und per Storage abgewiesen
  for (const [datei, was] of [[{ name: "bild.svg", mimeType: "image/svg+xml", buffer: SVG }, "SVG"], [{ name: "seite.html", mimeType: "text/html", buffer: HTML }, "HTML"],
    [{ name: "getarnt.png", mimeType: "image/png", buffer: HTML }, "HTML mit Endung .png"]]) {
    dokAnfragen.length = 0;
    await hochladen(datei, was);
    await dokFehlerText(/Nur PDF, JPEG, PNG oder WebP/);
    ok(`${was} im Browser abgewiesen, ohne Anfrage`, dokAnfragen.length === 0, JSON.stringify(dokAnfragen));
  }
  r = await direkt("/rest/v1/rpc/dokument_anlegen", { p_anlage: aId, p_titel: "x", p_kategorie: "foto", p_dateiname: "x.svg", p_typ: "image/svg+xml", p_groesse: 10 });
  ok("SVG per RPC abgelehnt", r.status === 400 && /Nur PDF/.test(r.d.message), JSON.stringify(r));
  erwartet(/^400 POST \/rest\/v1\/rpc\/dokument_anlegen/, "… als Fehlantwort gesehen");
  const resv = (await direkt("/rest/v1/rpc/dokument_anlegen", { p_anlage: aId, p_titel: "Reserviert", p_kategorie: "foto", p_dateiname: "x.png", p_typ: "image/png", p_groesse: 100 })).d;
  for (const [typ, inhalt] of [["image/svg+xml", SVG], ["text/html", HTML]]) {
    const rr = await roh(`/storage/v1/object/anlagedoku/${resv.pfad}`, { method: "POST", body: inhalt.toString(), headers: { "Content-Type": typ, "x-upsert": "false" } });
    ok(`${typ} per Storage-POST auf reservierten Pfad abgewiesen`, rr.status === 400 && /"415"/.test(rr.text), JSON.stringify(rr));
    erwartet(/^400 POST \/storage\/v1\/object\/anlagedoku\//, "… als Fehlantwort gesehen");
  }
  ok("… nichts im Bucket", sql(`select count(*) from storage.objects where name = '${resv.pfad}'`) === "0");
  sql("delete from public.dokumente where titel = 'Reserviert'");

  // PDF «nur Solarmaa» und PNG «auch für Kunde»
  dokAnfragen.length = 0;
  await hochladen({ name: "Schema Haus:1?.pdf", mimeType: "application/pdf", buffer: PDF }, "Prinzipschema");
  await dokZeile("Prinzipschema").waitFor();
  ok("Hochladen: anlegen, Datei senden, fertigstellen", JSON.stringify(dokAnfragen) === JSON.stringify(["POST /rest/v1/rpc/dokument_anlegen", "POST /storage/v1/object/anlagedoku/ID/ID.pdf", "POST /rest/v1/rpc/dokument_fertig"]), JSON.stringify(dokAnfragen));
  ok("… Meldung und leeres Formular", /hochgeladen/.test(await p.textContent("#dokMsg")) && (await p.inputValue("#dokNeuTitel")) === "" && (await p.inputValue("#dokNeuSichtbar")) === "false");
  await hochladen({ name: "Dach.png", mimeType: "image/png", buffer: PNG }, "Foto Dach", "true");
  await dokZeile("Foto Dach").waitFor();
  ok("PDF: Pill «nur Solarmaa»", /nur Solarmaa/.test(await dokZeile("Prinzipschema").textContent()));
  ok("PNG: Pill «für Kunde»", /für Kunde/.test(await dokZeile("Foto Dach").textContent()));
  ok("in der Datenbank: beide fertig, Grösse aus dem Speicher",
    sql("select string_agg(titel || ':' || status || ':' || groesse || ':' || sichtbar_kunde, ',' order by titel) from public.dokumente") === `Foto Dach:fertig:${PNG.length}:true,Prinzipschema:fertig:${PDF.length}:false`);
  const pngPfad = sql("select pfad from public.dokumente where titel = 'Foto Dach'"), pdfPfad = sql("select pfad from public.dokumente where titel = 'Prinzipschema'");
  await dokTitel("Foto Dach").tap();
  await p.waitForFunction(() => { const i = document.getElementById("dokBild"); return !i.hidden && i.complete && i.naturalWidth > 0; });
  ok("PNG-Vorschau aus einer Blob-Adresse", (await p.getAttribute("#dokBild", "src")).startsWith("blob:"));
  await pruefen("Dokument mit Vorschau");
  await dokTitel("Prinzipschema").tap();
  ok("PDF: keine Vorschau, Bild der vorherigen Auswahl entfernt", (await p.isHidden("#dokBild")) && (await p.getAttribute("#dokBild", "src")) === null);
  const [dl] = await Promise.all([p.waitForEvent("download"), p.tap("#dokLaden")]);
  ok("Herunterladen: gleiche Bytes", fs.readFileSync(await dl.path()).equals(PDF));
  ok("Herunterladen: bereinigter Dateiname", dl.suggestedFilename() === "Schema Haus_1_.pdf", dl.suggestedFilename());
  ok("Herunterladen: keine neue Seite, kein iframe/object/embed", ctx.pages().length === 1 && (await p.locator("iframe, object, embed").count()) === 0 && new URL(p.url()).pathname === "/");
  // umbenennen, Sichtbarkeit wechseln
  await p.fill("#dokTitel", "Prinzipschema Haus"); await p.selectOption("#dokSichtbar", "true"); await p.tap("#dokSpeichern");
  await p.waitForFunction(() => /Prinzipschema Haus.*für Kunde/s.test(document.getElementById("dokListe").textContent));
  ok("umbenannt und für Kunde freigegeben", sql("select titel || ':' || sichtbar_kunde from public.dokumente where pfad = '" + pdfPfad + "'") === "Prinzipschema Haus:true");
  await p.selectOption("#dokSichtbar", "false"); await p.tap("#dokSpeichern");
  await p.waitForFunction(() => [...document.querySelectorAll("#dokListe li")].some((li) => /Prinzipschema Haus/.test(li.textContent) && /nur Solarmaa/.test(li.textContent)));
  ok("wieder «nur Solarmaa»", sql("select sichtbar_kunde from public.dokumente where pfad = '" + pdfPfad + "'") === "f");
  await pruefen("Dokument bearbeiten");
  // Lange Liste (Handy): das geöffnete Detail kommt in den Sichtbereich und erhält den Fokus
  sql(`insert into public.dokumente (anlage_id, pfad, titel, dateiname, typ, groesse, status) select '${aId}', '${aId}/' || gen_random_uuid() || '.pdf', 'Fuellung ' || i, 'f.pdf', 'application/pdf', 10, 'fertig' from generate_series(1, 15) i`);
  await zurListe(); await oeffne(A); await dokTitel("Fuellung 15").waitFor();
  await p.evaluate(() => window.scrollTo(0, 0));
  await p.locator("#dokListe button.dok-titel").first().tap();
  await p.waitForFunction(() => { const r = document.getElementById("dokDetail").getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight - 40; }, null, { timeout: 5000 });
  ok("lange Liste: Detail im Sichtbereich", true);
  ok("… und mit Fokus", (await p.evaluate(() => document.activeElement && document.activeElement.id)) === "dokDetail");
  await p.tap("#dokZu");
  sql("delete from public.dokumente where titel like 'Fuellung %'");
  await zurListe(); await oeffne(A); await dokTitel("Foto Dach").waitFor();
  // «Herunterladen», während die Vorschau noch lädt: beide kommen an
  const bildLangsam = /\/storage\/v1\/object\/authenticated\/anlagedoku\//;
  await p.route(bildLangsam, async (route) => { await new Promise((res) => setTimeout(res, 1500)); await route.continue().catch(() => {}); });
  await dokTitel("Foto Dach").tap();
  const [dlBild] = await Promise.all([p.waitForEvent("download", { timeout: 10000 }), p.tap("#dokLaden")]);
  await p.waitForFunction(() => { const i = document.getElementById("dokBild"); return !i.hidden && i.complete && i.naturalWidth > 0; }, null, { timeout: 10000 });
  await p.unroute(bildLangsam);
  ok("Herunterladen während der Vorschau: Vorschau erscheint, Datei mit gleichen Bytes", fs.readFileSync(await dlBild.path()).equals(PNG) && dlBild.suggestedFilename() === "Dach.png", dlBild.suggestedFilename());
  await p.tap("#dokZu");

  // Abgebrochenes Hochladen: Eintrag «unvollständig», Fertigstellen scheitert, Löschen geht
  await p.route("**/storage/v1/object/anlagedoku/**", (route) => (route.request().method() === "POST" ? route.abort() : route.continue()));
  await hochladen({ name: "Abbruch.pdf", mimeType: "application/pdf", buffer: PDF }, "Abbruch");
  await dokFehlerText(/nicht angenommen.*unvollständig/);
  await p.unroute("**/storage/v1/object/anlagedoku/**");
  await p.waitForFunction(() => [...document.querySelectorAll("#dokListe li")].some((li) => /Abbruch/.test(li.textContent) && /unvollständig/.test(li.textContent)));
  ok("abgebrochenes Hochladen: Eintrag «unvollständig»", sql("select status from public.dokumente where titel = 'Abbruch'") === "hochladen");
  await dokTitel("Abbruch").tap();
  ok("unvollständig: Fertigstellen und Löschen angeboten, kein Herunterladen", (await p.isVisible("#dokFertig")) && (await p.isVisible("#dokLoeschen")) && (await p.isHidden("#dokLaden")));
  await p.tap("#dokFertig");
  await dokFehlerText(/Datei fehlt/);
  ok("Fertigstellen ohne Datei: Meldung", true);
  erwartet(/^400 POST \/rest\/v1\/rpc\/dokument_fertig/, "… von der Datenbank abgelehnt");
  await dokTitel("Abbruch").tap();
  await zweimal("#dokLoeschen");
  await p.waitForFunction(() => !/Abbruch/.test(document.getElementById("dokListe").textContent));
  ok("unvollständiger Eintrag gelöscht", sql("select count(*) from public.dokumente where titel = 'Abbruch'") === "0");

  // Löschen eines fertigen Dokuments: erst beim zweiten Tippen
  await hochladen({ name: "weg.pdf", mimeType: "application/pdf", buffer: PDF }, "Loeschtest");
  await dokZeile("Loeschtest").waitFor();
  const wegPfad = sql("select pfad from public.dokumente where titel = 'Loeschtest'");
  await dokTitel("Loeschtest").tap();
  await p.tap("#dokLoeschen"); await p.waitForTimeout(500);
  ok("einmal tippen fragt nur nach", /Wirklich/.test(await p.textContent("#dokLoeschen")) && sql("select status from public.dokumente where titel = 'Loeschtest'") === "fertig");
  dokAnfragen.length = 0;
  await p.tap("#dokLoeschen");
  await p.waitForFunction(() => !/Loeschtest/.test(document.getElementById("dokListe").textContent));
  ok("zweites Tippen löscht Zeile und Datei", sql("select count(*) from public.dokumente where titel = 'Loeschtest'") === "0" && sql(`select count(*) from storage.objects where name = '${wegPfad}'`) === "0");
  ok("… in der Reihenfolge beginnen, Datei, Zeile", JSON.stringify(dokAnfragen) === JSON.stringify(["POST /rest/v1/rpc/dokument_loeschen_beginnen", "DELETE /storage/v1/object/anlagedoku", "POST /rest/v1/rpc/dokument_loeschen"]), JSON.stringify(dokAnfragen));
  ok("… Detail geschlossen, Rückfrage zurückgesetzt", (await p.isHidden("#dokDetail")) && (await p.textContent("#dokLoeschen")) === "Löschen");

  // Wettläufe: verspätete Liste von A erscheint nie bei B
  const langsamDok = new RegExp("/rest/v1/dokumente\\?.*" + aId);
  await p.route(langsamDok, async (route) => { await new Promise((res) => setTimeout(res, 2500)); await route.continue().catch(() => {}); });
  await zurListe(); await oeffne(A); await zurListe(); await oeffne(B);
  await p.waitForFunction(() => /Noch keine Dokumente/.test(document.getElementById("dokListe").textContent));
  await p.waitForTimeout(3000);                      // die verspätete Liste von A ist jetzt da
  await p.unroute(langsamDok);
  ok("verspätete Dokumentliste von A erscheint nicht bei B", !/Prinzipschema|Foto Dach/.test(await p.textContent("#dokListe")) && (await p.textContent("#pName")) === B);
  // Datenbank ohne Anlagedoku (Tabelle fehlt): nur die Karte fällt aus
  await p.route(/\/rest\/v1\/dokumente\?/, (route) => route.fulfill({ status: 404, contentType: "application/json", body: '{"code":"42P01","message":"relation \\"public.dokumente\\" does not exist"}' }));
  await zurListe(); await oeffne(A);
  await p.waitForFunction(() => /nicht verfügbar/.test(document.getElementById("dokHinweis").textContent));
  ok("Tabelle fehlt: «Dokumente nicht verfügbar», kein Formular, Rest der Anlage intakt",
    (await p.isVisible("#pDoku")) && (await p.isHidden("#fDok")) && (await p.isHidden("#dokErr")) && (await p.locator("#pNow .tile").count()) > 0 && (await p.isVisible("#pFern")));
  await p.unroute(/\/rest\/v1\/dokumente\?/);
  erwartet(/^404 GET \/rest\/v1\/dokumente/, "… die Abfrage lieferte wirklich 404");
  // Bild von A lädt noch, Wechsel zu B: nichts davon bei B
  await zurListe(); await oeffne(A); await dokTitel("Foto Dach").waitFor();
  const langsamDatei = /\/storage\/v1\/object\/authenticated\/anlagedoku\//;
  let dateiAntworten = 0;
  const zaehleDatei = (resp) => { if (langsamDatei.test(resp.url())) dateiAntworten++; };
  p.on("response", zaehleDatei);
  await p.route(langsamDatei, async (route) => { await new Promise((res) => setTimeout(res, 2000)); await route.continue().catch(() => {}); });
  await dokTitel("Foto Dach").tap();
  await zurListe(); await oeffne(B);
  await p.waitForTimeout(2800);
  ok("Bild von A lädt, Wechsel zu B: kein Bild, kein Detail bei B", (await p.getAttribute("#dokBild", "src")) === null && (await p.isHidden("#dokDetail")));
  // Abmelden, während ein Bild lädt
  await zurListe(); await oeffne(A); await dokTitel("Foto Dach").waitFor();
  dateiAntworten = 0;
  await dokTitel("Foto Dach").tap();
  await p.tap("#logout"); await p.waitForSelector("#vLogin:not([hidden])");
  await p.waitForTimeout(2800);
  await p.unroute(langsamDatei);
  p.off("response", zaehleDatei);
  const dokRest = () => p.evaluate(() => ({ bild: document.getElementById("dokBild").getAttribute("src"), link: document.getElementById("dokLink").getAttribute("href"),
    liste: document.getElementById("dokListe").textContent, detail: document.getElementById("dokDetail").hidden, karte: document.getElementById("pDoku").hidden,
    html: document.documentElement.outerHTML, felder: ["dokTitel", "dokNeuTitel", "dokDatei"].map((id) => document.getElementById(id).value).join("") }));
  let dr = await dokRest();
  ok("Abmelden während ein Bild lädt: kein Bild, keine Liste, keine Blob-Adresse",
    dr.bild === null && dr.link === null && dr.liste === "" && dr.detail && dr.karte && !/blob:|Foto Dach|Prinzipschema/.test(dr.html) && dr.felder === "", JSON.stringify({ ...dr, html: undefined }));
  // Abmelden während des Hochladens: kein dokument_fertig danach
  await anmelden("chef@solarmaa.ch"); await p.waitForSelector(".plant");
  await oeffne(A); await p.waitForSelector("#fMfa:not([hidden])");
  await p.fill("#mfaCode", "424242"); await p.tap("#fMfa button[type=submit]"); await p.waitForSelector("#mfaOk:not([hidden])");
  await dokTitel("Foto Dach").waitFor();
  const langsamHoch = /\/storage\/v1\/object\/anlagedoku\//;
  let hochGesehen = false;
  await p.route(langsamHoch, async (route) => { hochGesehen = true; await new Promise((res) => setTimeout(res, 2000)); await route.continue().catch(() => {}); });
  dokAnfragen.length = 0;
  await hochladen({ name: "spaet.pdf", mimeType: "application/pdf", buffer: PDF }, "Abmelden beim Hochladen");
  for (let i = 0; i < 50 && !hochGesehen; i++) await p.waitForTimeout(100);
  ok("das Hochladen läuft", hochGesehen);
  await p.tap("#logout"); await p.waitForSelector("#vLogin:not([hidden])");
  await p.waitForTimeout(3000);
  await p.unroute(langsamHoch);
  ok("Abmelden während des Hochladens: kein dokument_fertig", !dokAnfragen.some((a) => /dokument_fertig/.test(a)), JSON.stringify(dokAnfragen));
  ok("… Eintrag bleibt «unvollständig», keine Datei", sql("select status from public.dokumente where titel = 'Abmelden beim Hochladen'") === "hochladen"
    && sql("select count(*) from storage.objects o join public.dokumente d on d.pfad = o.name where d.titel = 'Abmelden beim Hochladen'") === "0");
  dr = await dokRest();
  ok("… nichts von den Dokumenten im Dokument", dr.liste === "" && dr.karte && dr.felder === "" && !/Foto Dach|Abmelden beim Hochladen/.test(dr.html));
  sql("delete from public.dokumente where titel = 'Abmelden beim Hochladen'");
  p.off("request", dokMit);
  antworten.splice(0, antworten.length, ...antworten.filter((a) => !/^401 /.test(a)));

  // ---------------------------------------------------------------- Ablauf 3: Kunde und fremdes Konto
  for (const [mail, wer] of [["kunde@example.ch", "Kunde"], ["fremd@example.ch", "Fremder"]]) {
    await anmelden(mail);
    if (wer === "Kunde") { await p.waitForSelector("#pNow .tile"); await p.waitForTimeout(500); } else await p.waitForSelector("#empty:not([hidden])");
    ok(`${wer}: Fernwartung und Verwaltung unsichtbar`, (await p.isHidden("#pFern")) && (await p.isHidden("#pAdmin")) && (await p.isHidden("#addOpen")));
    r = await direkt(`/rest/v1/befehle?select=id,art,status,ergebnis&anlage_id=eq.${aId}`);
    ok(`${wer}: sieht keine Befehle`, r.status === 200 && r.d.length === 0, JSON.stringify(r));
    r = await direkt(`/rest/v1/anlagen_konfig?select=text&anlage_id=eq.${aId}`);
    ok(`${wer}: sieht keine Konfiguration`, r.status === 200 && r.d.length === 0, JSON.stringify(r));
    r = await direkt("/rest/v1/rpc/befehl_anlegen", { p_anlage: aId, p_art: "update" });
    ok(`${wer}: kann keinen Befehl anlegen`, r.status === 403, JSON.stringify(r));
    erwartet(/^403 POST \/rest\/v1\/rpc\/befehl_anlegen/, `${wer}: Befehl abgelehnt`);
    r = await direkt("/rest/v1/rpc/anlage_loeschen", { p_anlage: aId });
    ok(`${wer}: kann keine Anlage löschen`, r.status === 403, JSON.stringify(r));
    erwartet(/^403 POST \/rest\/v1\/rpc\/anlage_loeschen/, `${wer}: Löschen abgelehnt`);
    // Anlagedoku: Kunde sieht nur das Freigegebene, Fremder nichts
    let rr = await roh(`/storage/v1/object/authenticated/anlagedoku/${pdfPfad}`);
    ok(`${wer}: internes Dokument per Storage nicht abrufbar`, rr.status === 400 && /"404"/.test(rr.text), JSON.stringify(rr));
    erwartet(/^400 GET \/storage\/v1\/object\/authenticated\/anlagedoku\//, `${wer}: Abruf abgelehnt`);
    rr = await roh(`/storage/v1/object/public/anlagedoku/${pngPfad}`);
    ok(`${wer}: kein öffentlicher Abruf`, rr.status === 400, JSON.stringify(rr));
    erwartet(/^400 GET \/storage\/v1\/object\/public\//, `${wer}: öffentlicher Abruf abgelehnt`);
    r = await direkt("/rest/v1/rpc/dokument_aendern", { p_id: sql(`select id from public.dokumente where pfad = '${pdfPfad}'`), p_sichtbar: true });
    ok(`${wer}: kann keine Freigabe setzen`, r.status === 403, JSON.stringify(r));
    erwartet(/^403 POST \/rest\/v1\/rpc\/dokument_aendern/, `${wer}: Freigabe abgelehnt`);
    if (wer === "Kunde") {
      await p.waitForSelector("#pDoku:not([hidden])");
      const liste = await p.textContent("#dokListe");
      ok("Kunde: sieht nur das freigegebene Dokument, ohne Formular und Bearbeiten", /Foto Dach/.test(liste) && !/Prinzipschema/.test(liste)
        && (await p.isHidden("#fDok")) && !/nur Solarmaa|für Kunde/.test(liste));
      await dokTitel("Foto Dach").tap();
      await p.waitForFunction(() => { const i = document.getElementById("dokBild"); return !i.hidden && i.complete && i.naturalWidth > 0; });
      ok("Kunde: Vorschau des freigegebenen Bildes, nichts zum Bearbeiten", await p.isHidden("#dokBearbeiten"));
      await pruefen("Kunde mit Dokument");
      // Solarmaa nimmt die Freigabe zurück, während der Kunde die Liste offen hat
      await p.tap("#dokZu");
      sql(`update public.dokumente set sichtbar_kunde = false where pfad = '${pngPfad}'`);
      await dokTitel("Foto Dach").tap();
      await dokFehlerText(/nicht mehr freigegeben/);
      ok("Kunde: nach dem Wechsel auf «nur Solarmaa» scheitert der Abruf", (await p.getAttribute("#dokBild", "src")) === null);
      erwartet(/^400 GET \/storage\/v1\/object\/authenticated\/anlagedoku\//, "Kunde: Abruf nach Rücknahme abgelehnt");
      await p.waitForFunction(() => !/Foto Dach/.test(document.getElementById("dokListe").textContent));
      ok("… das Dokument verschwindet aus der Liste, die Meldung bleibt sichtbar", await p.isVisible("#dokErr"));
      sql(`update public.dokumente set sichtbar_kunde = true where pfad = '${pngPfad}'`);
      await p.tap("#back"); await dokTitel("Foto Dach").tap();          // eine Anlage: öffnet sich von selbst wieder
      await p.waitForFunction(() => { const i = document.getElementById("dokBild"); return !i.hidden && i.complete && i.naturalWidth > 0; });
      await p.evaluate(() => { window.__dokBlob = document.getElementById("dokBild").src; });
    } else {
      ok("Fremder: keine Karte Dokumente", await p.isHidden("#pDoku"));
      r = await direkt(`/rest/v1/dokumente?select=id,titel&anlage_id=eq.${aId}`);
      ok("Fremder: sieht keine Dokumente", r.status === 200 && r.d.length === 0, JSON.stringify(r));
      rr = await roh(`/storage/v1/object/authenticated/anlagedoku/${pngPfad}`);
      ok("Fremder: freigegebenes Bild einer fremden Anlage nicht abrufbar", rr.status === 400, JSON.stringify(rr));
      erwartet(/^400 GET \/storage\/v1\/object\/authenticated\/anlagedoku\//, "Fremder: Abruf abgelehnt");
    }
    await pruefen(`Ansicht ${wer}`);
    await abmelden();
    if (wer === "Kunde") {
      const z = await p.evaluate(async () => {
        // als Bild laden (fetch auf blob: verbietet die CSP): freigegebene Adresse liefert einen Fehler
        const lesbar = await new Promise((res) => { const i = new Image(); i.onload = () => res(true); i.onerror = () => res(false); i.src = window.__dokBlob; });
        return { src: document.getElementById("dokBild").getAttribute("src"), html: document.documentElement.outerHTML, lesbar };
      });
      ok("Kunde abgemeldet: Bild entfernt, Blob-Adresse freigegeben", z.src === null && !/blob:|Foto Dach/.test(z.html) && !z.lesbar, JSON.stringify({ src: z.src, lesbar: z.lesbar }));
    }
  }
  ok("beide Anlagen bestehen noch", sql("select count(*) from public.anlagen") === "2");
  await wieFrisch("nach dem Abmelden eines Kunden");

  // ---------------------------------------------------------------- Neuer Pi-Token: nie verloren, nie nach dem Abmelden
  const hashA = () => sql(`select token_hash from public.anlagen where id = '${aId}'`);
  const tokenPasst = (t) => sql(`select token_hash = encode(sha256(convert_to('${t}', 'UTF8')), 'hex') from public.anlagen where id = '${aId}'`) === "t";
  await anmelden("chef@solarmaa.ch"); await p.waitForSelector(".plant");
  await oeffne(A); await p.waitForSelector("#fMfa:not([hidden])");
  await p.fill("#mfaCode", "424242"); await p.tap("#fMfa button[type=submit]"); await p.waitForSelector("#mfaOk:not([hidden])");
  const liste = /\/rest\/v1\/anlagen\?.*order=name/;
  await p.route(liste, (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"message":"nicht verfügbar"}' }));
  await zweimal("#tokenNew");
  await p.waitForSelector("#tokenBox:not([hidden])");
  const tok2 = ((await p.textContent("#tokenText")).match(/token = "([0-9a-f]+)"/) || [])[1] || "";
  ok("Liste nicht ladbar: der neue Token wird trotzdem angezeigt", tok2 && tokenPasst(tok2));
  await p.waitForSelector("#listErr:not([hidden])");
  ok("… mit Hinweis, dass die Liste nicht neu geladen wurde", /konnte nicht neu geladen werden/.test(await p.textContent("#listErr")));
  await p.tap("#tokenClose");
  await p.tap("#addOpen"); await p.fill("#aName", "Liste kaputt"); await p.tap("#fAdd button[type=submit]");
  await p.waitForSelector("#tokenBox:not([hidden])");
  const tok3 = ((await p.textContent("#tokenText")).match(/token = "([0-9a-f]+)"/) || [])[1] || "";
  ok("Anlage hinzufügen bei kaputter Liste: Token angezeigt, Hinweis sichtbar", tok3.length >= 40 && (await p.isVisible("#listErr")));
  sql("delete from public.anlagen where name = 'Liste kaputt'");
  await p.unroute(liste);
  ok("… und die Liste lieferte wirklich einen Fehler", antworten.some((a) => /^503 GET \/rest\/v1\/anlagen/.test(a)));
  antworten.splice(0, antworten.length, ...antworten.filter((a) => !/^503 /.test(a)));
  await p.tap("#tokenClose");
  await oeffne(A); await p.waitForSelector("#members li");
  const vorherA = hashA();
  await p.route(liste, async (route) => { await new Promise((res) => setTimeout(res, 2500)); await route.continue().catch(() => {}); });
  await p.tap("#tokenNew");
  const tokenAntwort = p.waitForResponse((r) => /anlage_token_neu/.test(r.url()));
  await p.tap("#tokenNew"); await tokenAntwort;
  await p.tap("#logout"); await p.waitForSelector("#vLogin:not([hidden])");
  await p.waitForTimeout(3500);                      // die verspätete Liste ist jetzt da
  await p.unroute(liste);
  const ohneToken = async (wo) => {
    const z = await p.evaluate(() => ({ box: document.getElementById("tokenBox").hidden, text: document.getElementById("tokenText").textContent, html: document.documentElement.outerHTML }));
    ok(`${wo}: kein Pi-Token im Dokument`, z.box && z.text === "" && !/token = "[0-9a-f]{20,}"/.test(z.html), JSON.stringify([z.box, z.text]));
  };
  ok("der Token wurde wirklich erneuert", hashA() !== vorherA);
  await ohneToken("Abmelden während die Liste lädt");
  await anmelden("kunde@example.ch"); await p.waitForSelector("#pNow .tile"); await p.waitForTimeout(500);
  await ohneToken("nächstes Konto im selben Browser");
  await abmelden();
  antworten.splice(0, antworten.length, ...antworten.filter((a) => !/^401 /.test(a)));

  // ---------------------------------------------------------------- Ältere Token-Erneuerung überschreibt Zwei-Faktor nicht
  const ctx2 = await browser.newContext({ ...devices["iPhone 13"] });
  const q = await ctx2.newPage();
  await q.clock.install();
  await q.goto(URL_ + "/"); await q.waitForSelector("#vLogin:not([hidden])");
  await q.fill("#email", "chef@solarmaa.ch"); await q.fill("#pw", PW); await q.tap("#loginBtn");
  await q.waitForSelector(".plant"); await q.tap(`.plant:has-text("${A}")`); await q.waitForSelector("#fMfa:not([hidden])");
  let erneuert = 0;
  await q.route(/\/auth\/v1\/token\?grant_type=refresh_token/, async (route) => {
    if (erneuert++ === 0) await new Promise((res) => setTimeout(res, 4000));          // die erste Erneuerung ist langsam
    await route.continue().catch(() => {});
  });
  await q.clock.fastForward("01:00:00");             // Sitzung gilt als abgelaufen
  await q.evaluate(() => window.dispatchEvent(new Event("resize")));                    // löst eine Abfrage samt Erneuerung aus
  for (let i = 0; i < 50 && erneuert === 0; i++) await q.waitForTimeout(100);
  ok("eine Erneuerung der Sitzung läuft", erneuert >= 1);
  await q.fill("#mfaCode", "424242"); await q.tap("#fMfa button[type=submit]");
  await q.waitForSelector("#mfaOk:not([hidden])", { timeout: 15000 });
  await q.waitForTimeout(5000);                      // die langsame Erneuerung ist längst zurück
  const stufe = await q.evaluate(() => { const t = JSON.parse(localStorage.getItem("sm_portal")).access.split(".")[1]; return JSON.parse(atob(t.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (t.length % 4)) % 4))).aal; });
  ok("nach Zwei-Faktor und verspäteter Erneuerung gilt weiterhin aal2", stufe === "aal2", stufe);
  // (Die Knöpfe sind hier gesperrt, weil die Anlage nach dem Zeitsprung als offline gilt; massgebend ist die Stufe.)
  ok("… und die Seite zeigt Zwei-Faktor bestätigt", (await q.isVisible("#mfaOk")) && !/Zwei-Faktor/.test(await q.textContent("#fernHinweis")));
  await ctx2.close();

  // ---------------------------------------------------------------- Abmelden während einer hängenden Erneuerung
  const ctx3 = await browser.newContext({ ...devices["iPhone 13"] });
  const w = await ctx3.newPage();
  await w.clock.install();
  await w.goto(URL_ + "/"); await w.waitForSelector("#vLogin:not([hidden])");
  const wAnmelden = async (mail) => { await w.waitForSelector("#vLogin:not([hidden])"); await w.fill("#email", mail); await w.fill("#pw", PW); await w.tap("#loginBtn"); };
  await wAnmelden("chef@solarmaa.ch");
  await w.waitForSelector(".plant"); await w.tap(`.plant:has-text("${A}")`); await w.waitForSelector("#fMfa:not([hidden])");
  await w.fill("#mfaCode", "424242"); await w.tap("#fMfa button[type=submit]"); await w.waitForSelector("#mfaOk:not([hidden])");
  const gesendetW = [];
  w.on("request", (r) => { if (/anlage_token_neu|befehl_anlegen|mitglied_setzen/.test(r.url())) gesendetW.push(`${r.url()} ${r.headers().authorization || ""}`); });
  await w.route(/\/auth\/v1\/token\?grant_type=refresh_token/, async (route) => { await new Promise((res) => setTimeout(res, 3000)); await route.continue().catch(() => {}); });
  await w.clock.fastForward("01:00:00");             // Sitzung gilt als abgelaufen: der nächste Aufruf erneuert (langsam)
  const vorherW = hashA();
  await w.tap("#tokenNew"); await w.tap("#tokenNew");  // «Neuer Pi-Token» wartet auf die Erneuerung
  await w.tap("#logout");
  await wAnmelden("kunde@example.ch"); await w.waitForSelector("#pNow .tile");
  await w.waitForTimeout(4500);                      // die alte Erneuerung ist zurück
  ok("nach Abmelden und neuer Anmeldung geht der alte Auftrag nicht hinaus", gesendetW.length === 0, JSON.stringify(gesendetW));
  ok("… der Token der Anlage ist unverändert", hashA() === vorherW);
  ok("… die neue Anmeldung bleibt bestehen", (await w.isVisible("#pNow")) && (await w.evaluate(() => !!localStorage.getItem("sm_portal"))));
  await ctx3.close();
  await browser.close();
  fs.rmSync(piDir, { recursive: true, force: true });
})().catch(async (e) => {
  console.log(`FEHLGESCHLAGEN (nach «${schritt}»): ${e.message}`);
  try {       // Stand der Seite zur Fehlersuche
    console.log(await seite.evaluate(() => ["fernErr", "fernMsg", "fernHinweis", "mfaErr", "memErr", "loginErr"].map((id) => `${id}: ${document.getElementById(id).hidden ? "(versteckt)" : document.getElementById(id).textContent}`).join("\n")
      + "\nBefehle: " + [...document.querySelectorAll("#fernBefehle li")].map((li) => li.textContent.replace(/\s+/g, " ").slice(0, 120)).join(" | ")));
  } catch (e2) { /* Seite schon zu */ }
  if (browser) await browser.close();
  process.exit(1);
});
