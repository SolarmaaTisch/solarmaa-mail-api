-- Nach dem Einspielen von schema.sql über Stufe 1: Bestand ist erhalten, Fernwartung ist aus.
select tok from public.bestand_token \gset
drop table public.bestand_token;
select t.gleich('Upgrade: Anlage erhalten', (select count(*) from public.anlagen where name = 'Bestand'), 1::bigint);
select t.gleich('Upgrade: Fernwartung im Bestand aus', (select fernwartung from public.anlagen where name = 'Bestand'), false);
select t.gleich('Upgrade: Messwert erhalten', (select pv_w from public.messwerte), 5000::real);
select t.als('');
select t.gleich('Upgrade: alter Pi meldet wie bisher', public.pi_melden(:'tok', '{"version":"1.5.0","status":"ok"}'), '{"ok": true, "befehle": []}'::jsonb);
select t.als('00000000-0000-0000-0000-000000000002');
select t.gleich('Upgrade: Kunde sieht seine Anlage samt neuer Spalte', (select name || '/' || fernwartung from public.anlagen), 'Bestand/false'::text);
select t.scheitert('Upgrade: Token-Hash weiterhin gesperrt', 'select token_hash from public.anlagen', '42501');
reset role;
