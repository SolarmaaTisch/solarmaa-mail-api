-- Minimale Nachbildung dessen, was ein Supabase-Projekt vor schema.sql schon mitbringt.
-- Nur für tests/portal/run.sh (lokale Wegwerf-Datenbank). Am echten Projekt ungeprüft.
do $$      -- Rollen gelten für den ganzen Cluster; run.sh legt mehrere Datenbanken an
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
    create role authenticated nologin;
    create role service_role nologin bypassrls;
  end if;
end;
$$;
create schema auth;
create schema extensions;
create table auth.users (id uuid primary key default gen_random_uuid(), email text);

-- Supabase legt die Angaben aus dem Access-Token in request.jwt.claims ab.
create function auth.jwt() returns jsonb language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true), '')::jsonb
$$;
create function auth.uid() returns uuid language sql stable as $$
  select (auth.jwt() ->> 'sub')::uuid
$$;
grant usage on schema auth, extensions, public to anon, authenticated, service_role;
grant execute on function auth.uid(), auth.jwt() to anon, authenticated, service_role;

-- Supabase-Standardrechte: Neue Tabellen, Funktionen und Sequenzen in «public» sind zunächst für
-- alle drei Rollen offen. Erst damit prüfen die Tests, ob die revoke-Zeilen in schema.sql wirken.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;

-- Testkonten (Passwort siehe README.md; es gilt nur in der Attrappe)
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-000000000001', 'chef@solarmaa.ch'),
  ('00000000-0000-0000-0000-000000000002', 'kunde@example.ch'),
  ('00000000-0000-0000-0000-000000000003', 'fremd@example.ch');
