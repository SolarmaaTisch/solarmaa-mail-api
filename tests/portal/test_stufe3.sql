-- Stufe 3: Fernwartung. Läuft auf einer neuen und auf einer von Stufe 1 hochgezogenen Datenbank.
\set chef  '00000000-0000-0000-0000-000000000001'
\set kunde '00000000-0000-0000-0000-000000000002'
\set fremd '00000000-0000-0000-0000-000000000003'
insert into public.admins (user_id) values (:'chef') on conflict do nothing;

select t.als(:'chef', 'aal2');
select public.anlage_anlegen('Fern A') as a \gset
select public.anlage_anlegen('Fern B') as b \gset
select (:'a'::jsonb ->> 'id') as a_id, (:'a'::jsonb ->> 'token') as a_tok,
       (:'b'::jsonb ->> 'id') as b_id, (:'b'::jsonb ->> 'token') as b_tok \gset
select public.mitglied_setzen(:'a_id'::uuid, 'kunde@example.ch');
select t.gleich('neue Anlage: Fernwartung aus', (select fernwartung from public.anlagen where id = :'a_id'), false);
select t.gleich('hat_2fa mit aal2', public.hat_2fa(), true);

-- ---------------------------------------------------------------- Fernwartung aus
select t.scheitert('Befehl an Anlage ohne Fernwartung abgelehnt',
                   format($$select public.befehl_anlegen(%L, 'update')$$, :'a_id'), '55000');
-- alter Pi (meldet den Schalter nicht) und Pi mit unsinnigem Wert: bleibt aus, Meldung gelingt
select t.als('');
select t.gleich('Meldung ohne Schalter gelingt', public.pi_melden(:'a_tok', '{"status":"ok"}') -> 'befehle', '[]'::jsonb);
select t.gleich('Meldung mit unsinnigem Schalter gelingt', public.pi_melden(:'a_tok', '{"status":"ok","fernwartung":"ja bitte"}') ->> 'ok', 'true'::text);
select t.als(:'chef', 'aal2');
select t.gleich('unsinniger Schalter gilt als aus', (select fernwartung from public.anlagen where id = :'a_id'), false);

-- ---------------------------------------------------------------- Fernwartung ein
select t.als('');
select public.pi_melden(:'a_tok', '{"status":"ok","fernwartung":true}');
select public.pi_melden(:'b_tok', '{"status":"ok","fernwartung":true}');
select t.als(:'chef', 'aal2');
select t.gleich('Pi meldet Fernwartung ein', (select fernwartung from public.anlagen where id = :'a_id'), true);

-- Wer darf Befehle anlegen?
select t.als(:'chef', 'aal1');
select t.gleich('hat_2fa mit aal1', public.hat_2fa(), false);
select t.scheitert('Befehl ohne Zwei-Faktor abgelehnt', format($$select public.befehl_anlegen(%L, 'update')$$, :'a_id'), '42501');
select t.scheitert('Neuer Pi-Token ohne Zwei-Faktor abgelehnt', format('select public.anlage_token_neu(%L)', :'a_id'), '42501');
select t.scheitert('Anlage löschen ohne Zwei-Faktor abgelehnt', format('select public.anlage_loeschen(%L)', :'a_id'), '42501');
select t.gleich('Konfiguration ohne Zwei-Faktor: 0 Zeilen', (select count(*) from public.anlagen_konfig), 0::bigint);
select t.als(:'kunde', 'aal2');
select t.scheitert('Kunde (auch mit aal2) kann keinen Befehl anlegen', format($$select public.befehl_anlegen(%L, 'update')$$, :'a_id'), '42501');
select t.scheitert('Kunde kann keinen Token erzeugen', format('select public.anlage_token_neu(%L)', :'a_id'), '42501');
select t.scheitert('Kunde kann keine Anlage löschen', format('select public.anlage_loeschen(%L)', :'a_id'), '42501');
select t.als('');
select t.scheitert('nicht angemeldet: kein Befehl', format($$select public.befehl_anlegen(%L, 'update')$$, :'a_id'), '42501');
select t.scheitert('nicht angemeldet: Befehle nicht lesbar', 'select count(*) from public.befehle', '42501');
select t.scheitert('nicht angemeldet: Konfiguration nicht lesbar', 'select count(*) from public.anlagen_konfig', '42501');

select t.als(:'chef', 'aal2');
select t.scheitert('unbekannte Befehlsart abgelehnt', format($$select public.befehl_anlegen(%L, 'loeschen')$$, :'a_id'), '22023');
select t.scheitert('konfig ohne Text abgelehnt', format($$select public.befehl_anlegen(%L, 'konfig', '{"basis":"x"}')$$, :'a_id'), '22023');
select t.scheitert('unbekannte Anlage abgelehnt', $$select public.befehl_anlegen('00000000-0000-0000-0000-00000000dead', 'update')$$, '02000');
select t.scheitert('Befehle nicht direkt einfügbar', format($$insert into public.befehle (anlage_id, art) values (%L, 'update')$$, :'a_id'), '42501');
select t.scheitert('Konfiguration nicht direkt schreibbar', format($$insert into public.anlagen_konfig (anlage_id, text) values (%L, 'x')$$, :'a_id'), '42501');

-- ---------------------------------------------------------------- Ablauf offen -> gesendet -> erledigt
select public.befehl_anlegen(:'a_id'::uuid, 'update') as u1 \gset
select public.befehl_anlegen(:'a_id'::uuid, 'update') as u2 \gset
select t.gleich('zweiter Befehl derselben Art ersetzt den offenen',
                (select status from public.befehle where id = :'u1'), 'verfallen'::text);
select public.befehl_anlegen(:'a_id'::uuid, 'konfig', '{"text":"[easee]\npasswort = \"neu-geheim\"","basis":"abc"}') as k1 \gset
select t.scheitert('Nutzlast ist für Solarmaa nicht lesbar', 'select nutzlast from public.befehle', '42501');
select t.scheitert('select * auf Befehle scheitert (wegen Nutzlast)', 'select * from public.befehle', '42501');
select t.gleich('Ersteller wird festgehalten', (select erstellt_von from public.befehle where id = :'k1'), 'chef@solarmaa.ch'::text);
reset role;
select t.gleich('Nutzlast liegt bis zur Abholung in der Tabelle', (select nutzlast ->> 'basis' from public.befehle where id = :'k1'), 'abc'::text);

-- Pi ohne Schalter holt nichts ab, Befehle bleiben offen
select t.als('');
select t.gleich('Meldung ohne Schalter: keine Befehle', public.pi_melden(:'a_tok', '{"status":"ok"}') -> 'befehle', '[]'::jsonb);
reset role;
select t.gleich('Befehle bleiben offen', (select count(*) from public.befehle where anlage_id = :'a_id' and status = 'offen'), 2::bigint);
select t.gleich('Schalter ist wieder aus', (select fernwartung from public.anlagen where id = :'a_id'), false);

-- fremder Pi (Anlage B) bekommt die Befehle von A nicht
select t.als('');
select t.gleich('anderer Pi erhält fremde Befehle nicht', public.pi_melden(:'b_tok', '{"status":"ok","fernwartung":true}') -> 'befehle', '[]'::jsonb);

-- Pi mit Schalter holt ab: Antwort samt Nutzlast, in der Tabelle danach gelöscht
select public.pi_melden(:'a_tok', '{"status":"ok","fernwartung":true}') as antwort \gset
select t.gleich('Pi erhält beide Befehle', jsonb_array_length(:'antwort'::jsonb -> 'befehle'), 2);
select t.gleich('ältester Befehl zuerst', :'antwort'::jsonb -> 'befehle' -> 0 ->> 'id', :'u2'::text);
select t.gleich('Antwort enthält die Nutzlast', :'antwort'::jsonb -> 'befehle' -> 1 -> 'nutzlast' ->> 'basis', 'abc'::text);
select t.gleich('zweite Meldung: leere Liste', public.pi_melden(:'a_tok', '{"status":"ok","fernwartung":true}') -> 'befehle', '[]'::jsonb);
reset role;
select t.gleich('nach der Herausgabe ist keine Nutzlast mehr gespeichert', (select count(*) from public.befehle where nutzlast is not null), 0::bigint);
select t.gleich('Befehle sind gesendet', (select count(*) from public.befehle where anlage_id = :'a_id' and status = 'gesendet' and gesendet_am is not null), 2::bigint);

-- Quittung
select t.als('');
select t.scheitert('Quittung mit falschem Token abgelehnt',
                   format($$select public.pi_ergebnis('falscher-token-xxxxxxxxxxxxxxxx', %L, true, 'x')$$, :'k1'), '28000');
select public.pi_ergebnis(:'b_tok', :'k1'::uuid, true, 'von fremdem Pi');
reset role;
select t.gleich('fremder Pi kann den Befehl nicht quittieren', (select status from public.befehle where id = :'k1'), 'gesendet'::text);
select t.als('');
select public.pi_ergebnis(:'a_tok', :'k1'::uuid, true, 'Gespeichert');
select public.pi_ergebnis(:'a_tok', :'u2'::uuid, false, 'FEHLER: Tests fehlgeschlagen');
select public.pi_ergebnis(:'a_tok', :'k1'::uuid, false, 'zweite Quittung');
select t.als(:'chef', 'aal2');
select t.gleich('Quittung ok', (select status || ': ' || ergebnis from public.befehle where id = :'k1'), 'erledigt: Gespeichert'::text);
select t.gleich('Quittung Fehler', (select status || ': ' || ergebnis from public.befehle where id = :'u2'), 'fehler: FEHLER: Tests fehlgeschlagen'::text);
select t.gleich('gesendet blockiert keinen neuen Befehl', public.befehl_anlegen(:'a_id'::uuid, 'update') is not null, true);

-- ---------------------------------------------------------------- Verfall nach einer Stunde
select public.befehl_anlegen(:'b_id'::uuid, 'konfig', '{"text":"x","basis":"y"}') as kb \gset
reset role;
update public.befehle set erstellt = now() - interval '2 hours' where status = 'offen';
select t.als('');
select t.gleich('verfallener Befehl wird nicht mehr herausgegeben', public.pi_melden(:'a_tok', '{"status":"ok","fernwartung":true}') -> 'befehle', '[]'::jsonb);
reset role;
select t.gleich('Verfall in pi_melden', (select count(*) from public.befehle where anlage_id = :'a_id' and status = 'offen'), 0::bigint);
select t.gleich('Befehl der anderen Anlage ist noch offen (Pi kam nie)', (select status from public.befehle where id = :'kb'), 'offen'::text);
select t.als(:'chef', 'aal2');
select public.befehl_anlegen(:'a_id'::uuid, 'update') as u3 \gset
reset role;
select t.gleich('befehl_anlegen räumt auch andere Anlagen auf', (select status from public.befehle where id = :'kb'), 'verfallen'::text);
select t.gleich('verfallene Nutzlast ist gelöscht', (select count(*) from public.befehle where nutzlast is not null), 0::bigint);

-- ---------------------------------------------------------------- Konfiguration
select t.als('');
select public.pi_konfig(:'a_tok', '[anlage]' || chr(10) || 'name = "x"');
select t.scheitert('zu grosse Konfiguration abgelehnt', format('select public.pi_konfig(%L, repeat(%L, 200001))', :'a_tok', 'x'), '22023');
select t.scheitert('Konfiguration mit falschem Token abgelehnt', $$select public.pi_konfig('falscher-token-xxxxxxxxxxxxxxxx', 'x')$$, '28000');
select t.als(:'chef', 'aal2');
select t.gleich('Konfiguration mit Zwei-Faktor lesbar', (select text from public.anlagen_konfig where anlage_id = :'a_id'), E'[anlage]\nname = "x"'::text);
select t.als(:'chef', 'aal1');
select t.gleich('Konfiguration ohne Zwei-Faktor: 0 Zeilen', (select count(*) from public.anlagen_konfig), 0::bigint);
select t.gleich('Befehlsliste ohne Zwei-Faktor sichtbar (ohne Nutzlast)', (select count(*) > 0 from public.befehle), true);
select t.als(:'kunde', 'aal2');
select t.gleich('Kunde sieht keine Konfiguration', (select count(*) from public.anlagen_konfig), 0::bigint);
select t.gleich('Kunde sieht keine Befehle', (select count(*) from public.befehle), 0::bigint);
select t.gleich('Kunde sieht den Schalter seiner Anlage', (select fernwartung from public.anlagen where id = :'a_id'), true);
select t.als(:'fremd', 'aal2');
select t.gleich('Fremder sieht keine Konfiguration', (select count(*) from public.anlagen_konfig), 0::bigint);
select t.gleich('Fremder sieht keine Befehle', (select count(*) from public.befehle), 0::bigint);

-- ---------------------------------------------------------------- Neuer Token, Löschen
select t.als(:'chef', 'aal2');
select public.anlage_token_neu(:'a_id'::uuid) ->> 'token' as a_tok2 \gset
reset role;
select t.gleich('neuer Token: offener Befehl verfällt', (select status from public.befehle where id = :'u3'), 'verfallen'::text);
select t.gleich('neuer Token: hochgeladene Konfiguration gelöscht', (select count(*) from public.anlagen_konfig where anlage_id = :'a_id'), 0::bigint);
select t.als('');
select t.scheitert('alter Token ist ungültig', format($$select public.pi_melden(%L, '{}')$$, :'a_tok'), '28000');
select t.gleich('neuer Token gilt', public.pi_melden(:'a_tok2', '{"status":"ok"}') ->> 'ok', 'true'::text);
select t.als(:'chef', 'aal2');
select public.anlage_loeschen(:'b_id'::uuid);
select t.gleich('Anlage mit Zwei-Faktor gelöscht', (select count(*) from public.anlagen where id = :'b_id'), 0::bigint);
reset role;
select t.gleich('Befehle der gelöschten Anlage sind weg', (select count(*) from public.befehle where anlage_id = :'b_id'), 0::bigint);
