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
function piRunde(token) {
  const aus = execFileSync("python3", [path.join(REPO, "tests/portal/pi_ablauf.py"), URL_, "--runde", piDir, token], { encoding: "utf8" });
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
  ok("Block für den Pi enthält fernwartung = true", /\nfernwartung = true\n/.test(block));
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
  await abmelden();
  const rest = await p.evaluate(() => ({ text: document.querySelector("#fernKonfig").value, liste: document.querySelector("#fernBefehle").children.length,
    secret: document.querySelector("#mfaSecret").textContent, qr: document.querySelector("#mfaQr").getAttribute("src"), html: document.documentElement.outerHTML, speicher: JSON.stringify(localStorage) }));
  ok("nach dem Abmelden: Textfeld, Befehlsliste, Schlüssel und QR-Code leer", rest.text === "" && rest.liste === 0 && rest.secret === "" && rest.qr === null);
  ok("nach dem Abmelden: nichts von der Anlage im Dokument oder Speicher", !/Vom Portal geändert|ablauf-|TESTSECRET|Browser-Anlage|chef@|api_key/.test(rest.html) && rest.speicher === "{}",
    `(gefunden: ${(rest.html.match(/.{30}(Vom Portal geändert|ablauf-|TESTSECRET|Browser-Anlage|chef@|api_key).{30}/s) || [""])[0]}; Speicher: ${rest.speicher})`);
  ok("Karte Fernwartung versteckt", await p.isHidden("#pFern"));

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
  await abmelden();

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
  ok("Anlage besteht noch", sql("select count(*) from public.anlagen") === "1");
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
