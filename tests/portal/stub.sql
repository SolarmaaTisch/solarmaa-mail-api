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

-- Supabase Storage: Schema «storage» mit den Tabellen, die schema.sql und die Attrappe brauchen.
-- Spalten wie am echten Projekt nachgesehen (14.11.2026, nur lesend), aber nur die genutzten.
-- Die Tabellen gehören wie dort einer eigenen Rolle (schema.sql ändert sie nicht, legt nur Regeln an).
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'supabase_storage_admin') then
    create role supabase_storage_admin nologin;
  end if;
end;
$$;
create schema storage authorization supabase_storage_admin;
create table storage.buckets (
  id text primary key,
  name text not null unique,
  owner uuid,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  public boolean default false,
  file_size_limit bigint,
  allowed_mime_types text[]
);
create table storage.objects (
  id uuid primary key default gen_random_uuid(),
  bucket_id text references storage.buckets (id),
  name text,
  owner uuid,
  owner_id text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  last_accessed_at timestamptz default now(),
  metadata jsonb,
  path_tokens text[] generated always as (string_to_array(name, '/')) stored,
  version text,
  user_metadata jsonb
);
create unique index bucketid_objname on storage.objects (bucket_id, name);
alter table storage.buckets owner to supabase_storage_admin;
alter table storage.objects owner to supabase_storage_admin;
alter table storage.buckets enable row level security;
alter table storage.objects enable row level security;
-- Hilfsfunktionen wie bei Supabase (Ordner, Dateiname, Endung eines Objektnamens)
create function storage.foldername(name text) returns text[] language plpgsql as $$
declare _parts text[];
begin
  select string_to_array(name, '/') into _parts;
  return _parts[1:array_length(_parts, 1) - 1];
end;
$$;
create function storage.filename(name text) returns text language plpgsql as $$
declare _parts text[];
begin
  select string_to_array(name, '/') into _parts;
  return _parts[array_length(_parts, 1)];
end;
$$;
create function storage.extension(name text) returns text language plpgsql as $$
declare _parts text[]; _filename text;
begin
  select string_to_array(name, '/') into _parts;
  select _parts[array_length(_parts, 1)] into _filename;
  return reverse(split_part(reverse(_filename), '.', 1));
end;
$$;
alter function storage.foldername(text) owner to supabase_storage_admin;
alter function storage.filename(text) owner to supabase_storage_admin;
alter function storage.extension(text) owner to supabase_storage_admin;
-- Supabase gibt den drei Rollen alle Rechte auf die Storage-Tabellen; geschützt wird über RLS.
grant usage on schema storage to anon, authenticated, service_role;
grant all on storage.buckets, storage.objects to anon, authenticated, service_role;
