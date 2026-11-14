-- Anlagedoku (E8): Bucket, Tabelle «dokumente», Funktionen und Regeln auf storage.objects.
-- Läuft auf einer neuen und auf einer vom eingespielten Stand (Stufe 3) hochgezogenen Datenbank.
-- Das Hochladen in den Bucket wird hier wie von Supabase Storage nachgebildet: insert in storage.objects
-- mit der Rolle des Kontos (dort prüft Storage die Regeln genauso, am echten Projekt ungeprüft).
\set chef  '00000000-0000-0000-0000-000000000001'
\set kunde '00000000-0000-0000-0000-000000000002'
\set fremd '00000000-0000-0000-0000-000000000003'
insert into public.admins (user_id) values (:'chef') on conflict do nothing;
-- Datei löschen wie Supabase Storage (mit der Rolle des Kontos); Ergebnis: Anzahl gelöschter Zeilen
create or replace function t.datei_loeschen(p_name text) returns bigint language plpgsql as $$
declare n bigint;
begin
  with x as (delete from storage.objects where bucket_id = 'anlagedoku' and name = p_name returning 1) select count(*) into n from x;
  return n;
end;
$$;
grant execute on function t.datei_loeschen(text) to anon, authenticated;

-- ---------------------------------------------------------------- Bucket
select t.gleich('Bucket anlagedoku ist privat', (select public from storage.buckets where id = 'anlagedoku'), false);
select t.gleich('Bucket: Grenze 20 MB', (select file_size_limit from storage.buckets where id = 'anlagedoku'), 20971520::bigint);
select t.gleich('Bucket: nur PDF, JPEG, PNG, WebP', (select allowed_mime_types from storage.buckets where id = 'anlagedoku'),
                array['application/pdf', 'image/jpeg', 'image/png', 'image/webp']);
select t.gleich('erlaubende Regeln auf storage.objects: lesen, hochladen, löschen, kein Ändern',
                (select string_agg(cmd || ':' || array_to_string(roles, ','), ' ' order by cmd) from pg_policies
                  where schemaname = 'storage' and tablename = 'objects' and policyname like 'anlagedoku%' and permissive = 'PERMISSIVE'),
                'DELETE:authenticated INSERT:authenticated SELECT:authenticated'::text);
select t.gleich('einschränkende Regeln für alle Rollen: lesen, hochladen, ändern, löschen',
                (select string_agg(cmd || ':' || array_to_string(roles, ','), ' ' order by cmd) from pg_policies
                  where schemaname = 'storage' and tablename = 'objects' and policyname like 'anlagedoku%' and permissive = 'RESTRICTIVE'),
                'DELETE:public INSERT:public SELECT:public UPDATE:public'::text);

-- ---------------------------------------------------------------- Anlagen, Kunde
select t.als(:'chef', 'aal2');
select public.anlage_anlegen('Doku A') as a \gset
select public.anlage_anlegen('Doku B') as b \gset
select (:'a'::jsonb ->> 'id') as a_id, (:'b'::jsonb ->> 'id') as b_id \gset
select public.mitglied_setzen(:'a_id'::uuid, 'kunde@example.ch');

-- ---------------------------------------------------------------- dokument_anlegen: wer und was
select public.dokument_anlegen(:'a_id'::uuid, 'Prinzipschema', 'schema', 'Schema Haus.pdf', 'application/pdf', 1000, false) as d1 \gset
select (:'d1'::jsonb ->> 'id') as d1_id, (:'d1'::jsonb ->> 'pfad') as d1_pfad \gset
select t.gleich('Pfad = Anlage/Dokument.Endung, ohne Dateinamen', :'d1_pfad', :'a_id' || '/' || :'d1_id' || '.pdf');
select t.gleich('neues Dokument: Zustand hochladen, nur Solarmaa',
                (select status || '/' || sichtbar_kunde from public.dokumente where id = :'d1_id'), 'hochladen/false'::text);
select public.dokument_anlegen(:'a_id'::uuid, 'Foto', null, 'IMG_1.JPG', 'image/jpeg', 10) as f \gset
select t.gleich('Kategorie fehlt: sonstiges, Endung aus dem Typ',
                (select kategorie || ' ' || right(pfad, 4) from public.dokumente where id = (:'f'::jsonb ->> 'id')::uuid), 'sonstiges .jpg'::text);
select public.dokument_anlegen(:'a_id'::uuid, 'NFC', 'sonstiges', U&'Sa\0308ntis.pdf', 'application/pdf', 5) as f \gset
select t.gleich('Dateiname wird in NFC gespeichert',
                (select dateiname from public.dokumente where id = (:'f'::jsonb ->> 'id')::uuid), U&'S\00e4ntis.pdf');

\set anlegen 'select public.dokument_anlegen(%L, %L, %L, %L, %L, %s)'
select t.scheitert('SVG abgelehnt', format(:'anlegen', :'a_id', 'x', 'foto', 'x.svg', 'image/svg+xml', 10), '22023');
select t.scheitert('HTML abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.html', 'text/html', 10), '22023');
select t.scheitert('grösser als 20 MB abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 20971521), '22023');
select t.scheitert('leere Datei abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 0), '22023');
select t.scheitert('Dateiname mit / abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', '../x.pdf', 'application/pdf', 10), '22023');
select t.scheitert('Dateiname mit \ abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'a\b.pdf', 'application/pdf', 10), '22023');
select t.scheitert('Dateiname mit Steuerzeichen abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', E'a\nb.pdf', 'application/pdf', 10), '22023');
select t.scheitert('Dateiname länger als 120 Zeichen abgelehnt', format(:'anlegen', :'a_id', 'x', 'sonstiges', repeat('a', 117) || '.pdf', 'application/pdf', 10), '22023');
select t.scheitert('leerer Titel abgelehnt', format(:'anlegen', :'a_id', '  ', 'sonstiges', 'x.pdf', 'application/pdf', 10), '22023');
select t.scheitert('unbekannte Kategorie abgelehnt', format(:'anlegen', :'a_id', 'x', 'rechnung', 'x.pdf', 'application/pdf', 10), '22023');
select t.scheitert('unbekannte Anlage abgelehnt', format(:'anlegen', '00000000-0000-0000-0000-00000000dead', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 10), '02000');
select t.als(:'chef', 'aal1');
select t.scheitert('Solarmaa ohne Zwei-Faktor: kein Anlegen', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 10), '42501');
select t.als(:'kunde', 'aal2');
select t.scheitert('Kunde (auch mit aal2): kein Anlegen', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 10), '42501');
select t.als(:'fremd', 'aal2');
select t.scheitert('fremdes Konto: kein Anlegen', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 10), '42501');
select t.als('');
select t.scheitert('nicht angemeldet: kein Anlegen', format(:'anlegen', :'a_id', 'x', 'sonstiges', 'x.pdf', 'application/pdf', 10), '42501');
select t.scheitert('nicht angemeldet: Dokumente nicht lesbar', 'select count(*) from public.dokumente', '42501');

-- Direkt in die Tabelle schreiben geht für niemanden
select t.als(:'chef', 'aal2');
select t.scheitert('kein direktes insert', format($$insert into public.dokumente (anlage_id, pfad, titel, dateiname, typ) values (%L, %L, 'x', 'x.pdf', 'application/pdf')$$,
                   :'a_id', :'a_id' || '/00000000-0000-0000-0000-000000000abc.pdf'), '42501');
select t.scheitert('kein direktes update', format($$update public.dokumente set sichtbar_kunde = true where id = %L$$, :'d1_id'), '42501');
select t.scheitert('kein direktes delete', format($$delete from public.dokumente where id = %L$$, :'d1_id'), '42501');
select t.scheitert('erstellt_von ist nicht lesbar', 'select erstellt_von from public.dokumente', '42501');
select t.scheitert('select * scheitert (wegen erstellt_von)', 'select * from public.dokumente', '42501');
reset role;
select t.gleich('erstellt_von wird festgehalten', (select erstellt_von from public.dokumente where id = :'d1_id'), 'chef@solarmaa.ch'::text);

-- ---------------------------------------------------------------- Hochladen in den Bucket (insert in storage.objects)
\set hoch 'insert into storage.objects (bucket_id, name, owner, metadata) values (%L, %L, %L, jsonb_build_object(''size'', %s, ''mimetype'', %L))'
select t.als(:'chef', 'aal1');
select t.scheitert('Hochladen ohne Zwei-Faktor abgelehnt', format(:'hoch', 'anlagedoku', :'d1_pfad', :'chef', 1000, 'application/pdf'), '42501');
select t.als(:'kunde', 'aal2');
select t.scheitert('Hochladen als Kunde abgelehnt', format(:'hoch', 'anlagedoku', :'d1_pfad', :'kunde', 1000, 'application/pdf'), '42501');
select t.als('');
select t.scheitert('Hochladen nicht angemeldet abgelehnt', format(:'hoch', 'anlagedoku', :'d1_pfad', null, 1000, 'application/pdf'), '42501');
select t.als(:'chef', 'aal2');
select t.scheitert('Hochladen ohne Reservierung abgelehnt',
                   format(:'hoch', 'anlagedoku', :'a_id' || '/00000000-0000-0000-0000-000000000abc.pdf', :'chef', 10, 'application/pdf'), '42501');
select t.scheitert('Hochladen mit fremder Anlage im Pfad abgelehnt',
                   format(:'hoch', 'anlagedoku', :'b_id' || '/' || :'d1_id' || '.pdf', :'chef', 10, 'application/pdf'), '42501');
reset role;
insert into storage.buckets (id, name) values ('anderer', 'anderer') on conflict do nothing;
select t.als(:'chef', 'aal2');
select t.scheitert('Hochladen in einen anderen Bucket abgelehnt', format(:'hoch', 'anderer', :'d1_pfad', :'chef', 10, 'application/pdf'), '42501');
-- Zeile gehört zu A, Pfad zeigt auf B (nur durch Eingriff von Hand möglich): Ordner muss zur Anlage passen
reset role;
insert into public.dokumente (anlage_id, pfad, titel, dateiname, typ, groesse)
values (:'a_id', :'b_id' || '/00000000-0000-0000-0000-0000000000b1.pdf', 'Hand', 'h.pdf', 'application/pdf', 1);
select t.als(:'chef', 'aal2');
select t.scheitert('Ordner im Pfad passt nicht zur Anlage der Reservierung: abgelehnt',
                   format(:'hoch', 'anlagedoku', :'b_id' || '/00000000-0000-0000-0000-0000000000b1.pdf', :'chef', 1, 'application/pdf'), '42501');
reset role;
delete from public.dokumente where titel = 'Hand';
select t.als(:'chef', 'aal2');
-- Reservierung älter als eine Stunde
select public.dokument_anlegen(:'a_id'::uuid, 'Alt', 'sonstiges', 'alt.pdf', 'application/pdf', 10) ->> 'pfad' as alt_pfad \gset
reset role;
update public.dokumente set erstellt = now() - interval '61 minutes' where pfad = :'alt_pfad';
select t.als(:'chef', 'aal2');
select t.scheitert('Reservierung älter als eine Stunde: Hochladen abgelehnt', format(:'hoch', 'anlagedoku', :'alt_pfad', :'chef', 10, 'application/pdf'), '42501');
-- dokument_fertig, bevor die Datei da ist
select t.scheitert('fertig ohne Datei abgelehnt', format('select public.dokument_fertig(%L)', :'d1_id'), 'P0001');
-- mit Reservierung: erlaubt
select format(:'hoch', 'anlagedoku', :'d1_pfad', :'chef', 1234, 'application/pdf') as stmt \gset
:stmt;
select t.gleich('Hochladen mit Reservierung erlaubt', (select count(*) from storage.objects where name = :'d1_pfad'), 1::bigint);
select t.scheitert('dieselbe Datei kein zweites Mal', :'stmt');
select t.scheitert('Datei nicht überschreibbar (keine update-Regel)',
                   format($q$do $x$ begin update storage.objects set metadata = '{}' where name = %L; if not found then raise exception 'nichts geaendert' using errcode = '42501'; end if; end $x$$q$, :'d1_pfad'), '42501');
select t.gleich('fertig übernimmt die Grösse aus dem Speicher', public.dokument_fertig(:'d1_id'::uuid) ->> 'groesse', '1234'::text);
select t.gleich('… und setzt fertig', (select status from public.dokumente where id = :'d1_id'), 'fertig'::text);
-- falscher Typ: als PNG reserviert, als PDF hochgeladen
select public.dokument_anlegen(:'a_id'::uuid, 'Bild', 'foto', 'bild.png', 'image/png', 10) as d2 \gset
select (:'d2'::jsonb ->> 'id') as d2_id, (:'d2'::jsonb ->> 'pfad') as d2_pfad \gset
select format(:'hoch', 'anlagedoku', :'d2_pfad', :'chef', 10, 'application/pdf') as stmt \gset
:stmt;
select t.scheitert('fertig mit falschem Typ abgelehnt', format('select public.dokument_fertig(%L)', :'d2_id'), '22023');
select t.gleich('… bleibt im Zustand hochladen', (select status from public.dokumente where id = :'d2_id'), 'hochladen'::text);

-- ---------------------------------------------------------------- Sichtbarkeit
-- d3: fertig und für den Kunden freigegeben; d1: fertig, nur Solarmaa
select public.dokument_anlegen(:'a_id'::uuid, 'Foto Dach', 'foto', 'dach.png', 'image/png', 10, true) as d3 \gset
select (:'d3'::jsonb ->> 'id') as d3_id, (:'d3'::jsonb ->> 'pfad') as d3_pfad \gset
select format(:'hoch', 'anlagedoku', :'d3_pfad', :'chef', 10, 'image/png') as stmt \gset
:stmt;
select t.als(:'kunde');
select t.gleich('Kunde: unfertiges freigegebenes Dokument unsichtbar', (select count(*) from public.dokumente where id = :'d3_id'), 0::bigint);
select t.gleich('Kunde: unfertige Datei unsichtbar', (select count(*) from storage.objects where name = :'d3_pfad'), 0::bigint);
select t.als(:'chef', 'aal2');
select public.dokument_fertig(:'d3_id'::uuid);
select t.als(:'kunde');
select t.gleich('Kunde sieht nur das freigegebene, fertige Dokument seiner Anlage', (select string_agg(titel, ',') from public.dokumente), 'Foto Dach'::text);
select t.gleich('Kunde sieht im Bucket nur diese Datei', (select string_agg(name, ',') from storage.objects), :'d3_pfad');
select t.gleich('Kunde mit bekanntem Pfad: internes Dokument 0', (select count(*) from storage.objects where name = :'d1_pfad'), 0::bigint);
select t.gleich('Kunde sieht keine Buckets', (select count(*) from storage.buckets), 0::bigint);
select t.scheitert('Kunde kann nicht ändern', format('select public.dokument_aendern(%L, %L, null, true)', :'d1_id', 'x'), '42501');
select t.scheitert('Kunde kann nicht fertigstellen', format('select public.dokument_fertig(%L)', :'d2_id'), '42501');
select t.scheitert('Kunde kann das Löschen nicht beginnen', format('select public.dokument_loeschen_beginnen(%L)', :'d3_id'), '42501');
select t.gleich('Kunde kann keine Datei löschen (0 Zeilen)',
                t.datei_loeschen(:'d3_pfad'), 0::bigint);
select t.als(:'kunde', 'aal2');
select t.gleich('Kunde mit aal2 sieht nicht mehr', (select count(*) from public.dokumente), 1::bigint);
select t.als(:'fremd', 'aal2');
select t.gleich('fremdes Konto sieht keine Dokumente', (select count(*) from public.dokumente), 0::bigint);
select t.gleich('fremdes Konto sieht keine Dateien', (select count(*) from storage.objects), 0::bigint);
select t.als(:'chef', 'aal1');
select t.gleich('Solarmaa ohne Zwei-Faktor sieht keine Dokumente', (select count(*) from public.dokumente), 0::bigint);
select t.gleich('Solarmaa ohne Zwei-Faktor sieht keine Dateien', (select count(*) from storage.objects), 0::bigint);
select t.als('');
select t.gleich('nicht angemeldet: keine Regel greift (0 Dateien)', (select count(*) from storage.objects), 0::bigint);
select t.als(:'chef', 'aal2');
select t.gleich('Solarmaa mit Zwei-Faktor sieht alle Dokumente der Anlage', (select count(*) from public.dokumente where anlage_id = :'a_id'), 6::bigint);
-- Freigabe zurücknehmen: sofort weg
select public.dokument_aendern(:'d3_id'::uuid, 'Foto Dach Süd', 'foto', false);
select t.gleich('ändern: Titel übernommen', (select titel from public.dokumente where id = :'d3_id'), 'Foto Dach Süd'::text);
select t.scheitert('ändern: leerer Titel abgelehnt', format('select public.dokument_aendern(%L, %L)', :'d3_id', ''), '22023');
select t.scheitert('ändern: unbekannte Kategorie abgelehnt', format('select public.dokument_aendern(%L, null, %L)', :'d3_id', 'x'), '22023');
select t.als(:'kunde');
select t.gleich('nach «nur Solarmaa»: Kunde sieht das Dokument nicht mehr', (select count(*) from public.dokumente), 0::bigint);
select t.gleich('… und die Datei nicht mehr', (select count(*) from storage.objects), 0::bigint);
select t.als(:'chef', 'aal2');
select public.dokument_aendern(:'d3_id'::uuid, null, null, true);
select t.als(:'kunde');
select t.gleich('wieder freigegeben: Kunde sieht es', (select count(*) from storage.objects), 1::bigint);
reset role;
delete from public.mitglieder where anlage_id = :'a_id';
select t.als(:'kunde');
select t.gleich('Zugriff entzogen: Kunde sieht nichts mehr', (select count(*) from public.dokumente) + (select count(*) from storage.objects), 0::bigint);
select t.als(:'chef', 'aal2');
select public.mitglied_setzen(:'a_id'::uuid, 'kunde@example.ch');

-- ---------------------------------------------------------------- Zu weit gefasste fremde Regel
-- Jemand legt später eine Regel «alles erlauben» auf storage.objects an: anlagedoku bleibt geschützt.
reset role;
drop policy if exists test_alles_erlaubt on storage.objects;
create policy test_alles_erlaubt on storage.objects for all to public using (true) with check (true);
create or replace function t.datei_aendern(p_name text) returns bigint language plpgsql as $$
declare n bigint;
begin
  with x as (update storage.objects set metadata = '{"size":1,"mimetype":"image/png"}' where bucket_id = 'anlagedoku' and name = p_name returning 1)
  select count(*) into n from x;
  return n;
end;
$$;
grant execute on function t.datei_aendern(text) to anon, authenticated;
select t.als(:'kunde');
select t.gleich('alles erlaubt: Kunde liest kein internes Dokument', (select count(*) from storage.objects where name = :'d1_pfad'), 0::bigint);
select t.gleich('… sieht nur das Freigegebene', (select string_agg(name, ',') from storage.objects), :'d3_pfad');
select t.scheitert('… lädt nichts hoch', format(:'hoch', 'anlagedoku', :'a_id' || '/00000000-0000-0000-0000-0000000000c1.pdf', :'kunde', 1, 'application/pdf'), '42501');
select t.gleich('… ändert nichts', t.datei_aendern(:'d3_pfad'), 0::bigint);
select t.gleich('… löscht nichts', t.datei_loeschen(:'d1_pfad') + t.datei_loeschen(:'d3_pfad'), 0::bigint);
select t.als(:'fremd', 'aal2');
select t.gleich('alles erlaubt: fremdes Konto liest nichts', (select count(*) from storage.objects where bucket_id = 'anlagedoku'), 0::bigint);
select t.scheitert('… lädt nichts hoch', format(:'hoch', 'anlagedoku', :'b_id' || '/00000000-0000-0000-0000-0000000000c2.pdf', :'fremd', 1, 'application/pdf'), '42501');
select t.gleich('… löscht nichts', t.datei_loeschen(:'d1_pfad'), 0::bigint);
select t.als('');
select t.gleich('alles erlaubt: nicht angemeldet liest nichts', (select count(*) from storage.objects where bucket_id = 'anlagedoku'), 0::bigint);
select t.scheitert('… lädt nichts hoch', format(:'hoch', 'anlagedoku', :'a_id' || '/00000000-0000-0000-0000-0000000000c3.pdf', null, 1, 'application/pdf'), '42501');
select t.gleich('… ändert und löscht nichts', t.datei_aendern(:'d3_pfad') + t.datei_loeschen(:'d3_pfad'), 0::bigint);
select t.als(:'chef', 'aal1');
select t.gleich('alles erlaubt: Solarmaa ohne Zwei-Faktor liest nichts', (select count(*) from storage.objects where bucket_id = 'anlagedoku'), 0::bigint);
select t.als(:'chef', 'aal2');
select t.gleich('alles erlaubt: auch Solarmaa mit Zwei-Faktor ändert keine Datei', t.datei_aendern(:'d1_pfad'), 0::bigint);
select t.gleich('… und löscht keine fertige Datei ohne Beginn', t.datei_loeschen(:'d1_pfad'), 0::bigint);
reset role;
insert into storage.objects (bucket_id, name, metadata) values ('anderer', 'frei/x.txt', '{}');
select t.als(:'kunde');
select t.gleich('andere Buckets: die einschränkenden Regeln greifen dort nicht', (select count(*) from storage.objects where bucket_id = 'anderer'), 1::bigint);
reset role;
delete from storage.objects where bucket_id = 'anderer';
drop policy test_alles_erlaubt on storage.objects;
select t.als(:'chef', 'aal2');

-- ---------------------------------------------------------------- Löschen
select t.scheitert('Löschen ohne Beginn abgelehnt (fertig)', format('select public.dokument_loeschen(%L)', :'d3_id'), 'P0001');
select t.gleich('Solarmaa: Datei eines fertigen Dokuments ohne Beginn nicht löschbar (0 Zeilen)',
                t.datei_loeschen(:'d3_pfad'), 0::bigint);
select t.scheitert('anlage_loeschen mit Dokumenten abgelehnt', format('select public.anlage_loeschen(%L)', :'a_id'), 'P0001');
select public.dokument_loeschen_beginnen(:'d3_id'::uuid);
select t.als(:'kunde');
select t.gleich('Löschen begonnen: für den Kunden sofort weg', (select count(*) from public.dokumente) + (select count(*) from storage.objects), 0::bigint);
select t.als(:'chef', 'aal2');
select t.scheitert('ändern während des Löschens abgelehnt', format('select public.dokument_aendern(%L, %L)', :'d3_id', 'x'), '02000');
select t.scheitert('Zeile löschen, solange die Datei da ist: abgelehnt', format('select public.dokument_loeschen(%L)', :'d3_id'), 'P0001');
select t.als(:'chef', 'aal1');
select t.gleich('Datei löschen ohne Zwei-Faktor: 0 Zeilen',
                t.datei_loeschen(:'d3_pfad'), 0::bigint);
select t.scheitert('Zeile löschen ohne Zwei-Faktor abgelehnt', format('select public.dokument_loeschen(%L)', :'d3_id'), '42501');
select t.als(:'chef', 'aal2');
select t.gleich('Datei löschen nach Beginn: 1 Zeile',
                t.datei_loeschen(:'d3_pfad'), 1::bigint);
select public.dokument_loeschen(:'d3_id'::uuid);
select t.gleich('Zeile gelöscht', (select count(*) from public.dokumente where id = :'d3_id'), 0::bigint);
select t.scheitert('zweites Löschen: nicht gefunden', format('select public.dokument_loeschen(%L)', :'d3_id'), '02000');
-- abgebrochenes Hochladen (Zustand hochladen, Datei mit falschem Typ liegt da): direkt löschbar
select t.gleich('unfertige Datei löschbar',
                t.datei_loeschen(:'d2_pfad'), 1::bigint);
select public.dokument_loeschen(:'d2_id'::uuid);
-- übrige Dokumente der Anlage A wegräumen, dann lässt sich die Anlage löschen
select public.dokument_loeschen_beginnen(id) from public.dokumente where anlage_id = :'a_id';
select count(*) from (select 1 from storage.objects o join public.dokumente d on d.pfad = o.name where d.anlage_id = :'a_id') x \gset vorher_
with x as (delete from storage.objects o using public.dokumente d where d.pfad = o.name and d.anlage_id = :'a_id' returning 1) select count(*) as geloescht from x \gset
select t.gleich('alle Dateien der Anlage gelöscht', :'geloescht'::bigint, :'vorher_count'::bigint);
select public.dokument_loeschen(id) from public.dokumente where anlage_id = :'a_id';
select public.anlage_loeschen(:'a_id'::uuid);
select t.gleich('ohne Dokumente: Anlage gelöscht', (select count(*) from public.anlagen where id = :'a_id'), 0::bigint);
select public.dokument_anlegen(:'b_id'::uuid, 'x', 'sonstiges', 'x.pdf', 'application/pdf', 1);
reset role;
select t.scheitert('Anlage mit Dokument auch direkt nicht löschbar (Fremdschlüssel)', format('delete from public.anlagen where id = %L', :'b_id'), '23503');
