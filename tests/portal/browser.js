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
      klein: [...document.querySelectorAll("button, input, textarea, a")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && !e.closest("[hidden]") && r.height < 44; }).map((e) => e.id || e.className),
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
  await oeffne(A);
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
    await pruefen(`Ansicht ${wer}`);
    await abmelden();
  }
  ok("beide Anlagen bestehen noch", sql("select count(*) from public.anlagen") === "2");
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
