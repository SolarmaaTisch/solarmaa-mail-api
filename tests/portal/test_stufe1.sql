-- Stufe 1: Wer sieht was? (Kunde, Fremder, nicht angemeldet, Solarmaa) und die Meldung des Pi.
\set chef  '00000000-0000-0000-0000-000000000001'
\set kunde '00000000-0000-0000-0000-000000000002'
\set fremd '00000000-0000-0000-0000-000000000003'
insert into public.admins (user_id) values (:'chef') on conflict do nothing;

select t.als(:'chef');
select public.anlage_anlegen('Anlage A', 'Biel', 240) as a \gset
select public.anlage_anlegen('Anlage B', 'Lyss', 10) as b \gset
select (:'a'::jsonb ->> 'id') as a_id, (:'a'::jsonb ->> 'token') as a_tok, (:'b'::jsonb ->> 'id') as b_id \gset
select public.mitglied_setzen(:'a_id'::uuid, 'Kunde@example.ch');

-- Pi meldet zweimal (nicht angemeldet, nur mit Token)
select t.als('');
begin;   -- eine Transaktion: beide Meldungen fallen sicher in denselben 5-Minuten-Wert
select public.pi_melden(:'a_tok', '{"version":"1.4","status":"ok","stoerung":false,"live":{"pv_w":5000,"load_w":1200,"grid_w":-3800,"soc":null},"heute":{"day":"2026-11-11","pv_wh":12000,"load_wh":4000,"import_wh":100,"export_wh":8000}}');
select public.pi_melden(:'a_tok', '{"status":"ok","live":{"pv_w":7000,"load_w":1000,"grid_w":-6000},"heute":{"day":"2026-11-11","pv_wh":12500,"load_wh":4100}}');
commit;
select t.scheitert('falscher Token wird abgelehnt', $$select public.pi_melden('x-falscher-token-1234567890', '{}')$$, '28000');
select t.scheitert('zu kurzer Token wird abgelehnt', $$select public.pi_melden('kurz', '{}')$$, '22023');
select t.scheitert('nicht angemeldet: Anlagen nicht lesbar', 'select count(*) from public.anlagen', '42501');
select t.scheitert('nicht angemeldet: Messwerte nicht lesbar', 'select count(*) from public.messwerte', '42501');
select t.scheitert('nicht angemeldet: keine Anlage anlegen', $$select public.anlage_anlegen('Hack')$$, '42501');

-- Kunde
select t.als(:'kunde');
select t.gleich('Kunde sieht genau eine Anlage', (select count(*) from public.anlagen), 1::bigint);
select t.gleich('Kunde sieht Anlage A', (select name from public.anlagen), 'Anlage A'::text);
select t.gleich('Mittelwert der zwei Meldungen', (select pv_w from public.messwerte), 6000::real);
select t.gleich('Anzahl Meldungen im 5-Minuten-Wert', (select n from public.messwerte), 2);
select t.gleich('Tageswert ist der letzte gemeldete', (select pv_wh from public.tageswerte), 12500::real);
select t.scheitert('Token-Hash ist nicht lesbar', 'select token_hash from public.anlagen', '42501');
select t.scheitert('select * auf Anlagen scheitert (wegen Token-Hash)', 'select * from public.anlagen', '42501');
select t.scheitert('Kunde kann keine Anlage anlegen', $$select public.anlage_anlegen('Kunde-Hack')$$, '42501');
select t.scheitert('Kunde kann sich keine Anlage zuordnen',
                   format($$select public.mitglied_setzen(%L, 'kunde@example.ch')$$, :'b_id'), '42501');
select t.scheitert('Kunde kann Anlagen nicht ändern', $$update public.anlagen set name = 'x'$$, '42501');
select t.scheitert('Kunde kann Anlagen nicht löschen', 'delete from public.anlagen', '42501');
select t.scheitert('Kunde kann sich nicht zum Admin machen',
                   format('insert into public.admins values (%L)', :'kunde'), '42501');
select t.scheitert('Kunde kann keine Messwerte schreiben',
                   format($$insert into public.messwerte (anlage_id, ts) values (%L, now())$$, :'a_id'), '42501');

-- Fremdes Konto
select t.als(:'fremd');
select t.gleich('Fremder sieht keine Anlage', (select count(*) from public.anlagen), 0::bigint);
select t.gleich('Fremder sieht keine Messwerte', (select count(*) from public.messwerte), 0::bigint);
select t.gleich('Fremder sieht keine Tageswerte', (select count(*) from public.tageswerte), 0::bigint);
select t.gleich('Fremder sieht keine Mitglieder', (select count(*) from public.mitglieder), 0::bigint);
select t.gleich('Fremder ist kein Admin', public.ist_admin(), false);

-- Solarmaa
select t.als(:'chef');
select t.gleich('Solarmaa sieht beide Anlagen', (select count(*) from public.anlagen), 2::bigint);
select t.gleich('Solarmaa sieht das Mitglied', (select email from public.mitglieder), 'kunde@example.ch'::text);
select t.scheitert('auch Solarmaa liest keinen Token-Hash', 'select token_hash from public.anlagen', '42501');
select t.scheitert('auch Solarmaa schreibt nicht direkt in Tabellen', $$update public.anlagen set name = 'x'$$, '42501');
reset role;
