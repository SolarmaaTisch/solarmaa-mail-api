-- Bestand einer Datenbank auf dem Stand von Stufe 1: eine Anlage mit Kunde und einer Meldung.
insert into public.admins (user_id) values ('00000000-0000-0000-0000-000000000001');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-000000000001","aal":"aal1"}', false);
select public.anlage_anlegen('Bestand', 'Biel', 240) ->> 'token' as tok \gset
select public.mitglied_setzen((select id from public.anlagen where name = 'Bestand'), 'kunde@example.ch');
select public.pi_melden(:'tok', '{"version":"1.5.0","status":"ok","live":{"pv_w":5000,"load_w":1200},"heute":{"day":"2026-11-11","pv_wh":12000}}');
create table public.bestand_token as select :'tok'::text as tok;
