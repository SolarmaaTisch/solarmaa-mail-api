-- Solarmaa-Portal (Stufen 1 und 3): Datenbankstruktur für Supabase (Postgres)
-- Im Supabase-Dashboard unter «SQL Editor» einfügen und ausführen. Mehrfach ausführbar.
--
-- Sicherheitsmodell
--   * Jede Tabelle hat Row Level Security. Ohne passende Regel sieht niemand etwas.
--   * Solarmaa-Mitarbeitende stehen in «admins» und sehen alle Anlagen.
--   * Kunden sehen nur Anlagen, bei denen sie in «mitglieder» eingetragen sind.
--   * Die Pis schreiben nie direkt in Tabellen. Sie rufen pi_melden() mit ihrem geheimen
--     Anlagen-Token auf; gespeichert ist nur dessen SHA-256-Hash.
--   * Geschrieben wird ausschliesslich über die Funktionen unten (security definer).
--
-- Fernwartung (Stufe 3)
--   * Befehle anlegen, Konfiguration lesen, Pi-Token erneuern und Anlage löschen: nur Solarmaa-Konten
--     mit Zwei-Faktor-Anmeldung (aal2).
--   * Ein Pi erhält Befehle nur, wenn er in seiner Meldung fernwartung = true mitschickt
--     (ältere Programmversionen tun das nie und bekommen darum keine Befehle).
--   * Zustände eines Befehls:  offen -> gesendet -> erledigt | fehler      (Pi holt ab und quittiert)
--                              offen -> verfallen                          (nach einer Stunde, oder ersetzt)
--     «gesendet» ohne Quittung bleibt stehen, damit eine späte Quittung noch ankommt; das Portal
--     zeigt nach 45 Minuten «keine Quittung».
--   * Die Nutzlast (kann neue Passwörter enthalten) ist für niemanden lesbar und wird bei der
--     Herausgabe an den Pi, beim Verfallen und bei der Quittung gelöscht.

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------- Tabellen
create table if not exists public.admins (
  user_id uuid primary key references auth.users (id) on delete cascade,
  erstellt timestamptz not null default now()
);

create table if not exists public.anlagen (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(name) between 1 and 120),
  ort text not null default '',
  kwp numeric not null default 0,
  token_hash text not null unique,
  erstellt timestamptz not null default now(),
  letzter_kontakt timestamptz,
  version text,
  live jsonb,          -- letzter Messwert vom Pi
  heute jsonb,         -- Tagessummen vom Pi
  status text,         -- "ok" | "fehler"
  stoerung boolean not null default false,
  abregelung jsonb,
  alarme jsonb not null default '[]'::jsonb
);

create table if not exists public.mitglieder (
  anlage_id uuid not null references public.anlagen (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  email text not null default '',
  erstellt timestamptz not null default now(),
  primary key (anlage_id, user_id)
);

create table if not exists public.messwerte (       -- 5-Minuten-Mittel für die Tageskurve
  anlage_id uuid not null references public.anlagen (id) on delete cascade,
  ts timestamptz not null,
  pv_w real, load_w real, grid_w real, battery_w real, soc real, n int not null default 1,
  primary key (anlage_id, ts)
);

create table if not exists public.tageswerte (
  anlage_id uuid not null references public.anlagen (id) on delete cascade,
  tag date not null,
  pv_wh real not null default 0, load_wh real not null default 0, import_wh real not null default 0,
  export_wh real not null default 0, ev_wh real not null default 0,
  primary key (anlage_id, tag)
);

-- Stufe 3: Fernwartung ist pro Anlage aus, bis der Pi sie meldet ([portal] fernwartung = true).
alter table public.anlagen add column if not exists fernwartung boolean not null default false;

-- Stufe 3: Fernwartung. Befehle vom Portal an den Pi und die (maskierte) Konfiguration des Pi.
create table if not exists public.befehle (
  id uuid primary key default gen_random_uuid(),
  anlage_id uuid not null references public.anlagen (id) on delete cascade,
  art text not null check (art in ('update', 'konfig')),
  nutzlast jsonb,                       -- wird bei Herausgabe, Verfall und Quittung gelöscht
  status text not null default 'offen' check (status in ('offen', 'gesendet', 'erledigt', 'fehler', 'verfallen')),
  ergebnis text,
  erstellt timestamptz not null default now(),
  erstellt_von text not null default '',
  gesendet_am timestamptz,
  erledigt_am timestamptz
);
create index if not exists befehle_anlage on public.befehle (anlage_id, erstellt desc);

create table if not exists public.anlagen_konfig (
  anlage_id uuid primary key references public.anlagen (id) on delete cascade,
  text text not null,                   -- Geheimnisse sind vom Pi durch ******** ersetzt
  stand timestamptz not null default now()
);

alter table public.befehle enable row level security;
alter table public.anlagen_konfig enable row level security;
alter table public.admins enable row level security;
alter table public.anlagen enable row level security;
alter table public.mitglieder enable row level security;
alter table public.messwerte enable row level security;
alter table public.tageswerte enable row level security;

-- ---------------------------------------------------------------- Hilfsfunktionen
create or replace function public.ist_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.admins where user_id = auth.uid());
$$;

create or replace function public.darf_anlage(a uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select public.ist_admin()
      or exists (select 1 from public.mitglieder where anlage_id = a and user_id = auth.uid());
$$;

-- Zwei-Faktor: Sitzung wurde mit zweitem Faktor bestätigt (Supabase setzt aal = aal2 im Token)
create or replace function public.hat_2fa() returns boolean
language sql stable as $$
  select coalesce(auth.jwt() ->> 'aal', '') = 'aal2';
$$;

-- ---------------------------------------------------------------- Leseregeln (RLS)
drop policy if exists befehle_lesen on public.befehle;
create policy befehle_lesen on public.befehle for select to authenticated
  using (public.ist_admin());

drop policy if exists konfig_lesen on public.anlagen_konfig;
create policy konfig_lesen on public.anlagen_konfig for select to authenticated
  using (public.ist_admin() and public.hat_2fa());

drop policy if exists admins_lesen on public.admins;
create policy admins_lesen on public.admins for select to authenticated
  using (user_id = auth.uid() or public.ist_admin());

drop policy if exists anlagen_lesen on public.anlagen;
create policy anlagen_lesen on public.anlagen for select to authenticated
  using (public.darf_anlage(id));

drop policy if exists mitglieder_lesen on public.mitglieder;
create policy mitglieder_lesen on public.mitglieder for select to authenticated
  using (user_id = auth.uid() or public.ist_admin());

drop policy if exists messwerte_lesen on public.messwerte;
create policy messwerte_lesen on public.messwerte for select to authenticated
  using (public.darf_anlage(anlage_id));

drop policy if exists tageswerte_lesen on public.tageswerte;
create policy tageswerte_lesen on public.tageswerte for select to authenticated
  using (public.darf_anlage(anlage_id));

-- Rechte: nur lesen, und der Token-Hash ist für niemanden lesbar.
revoke all on public.admins, public.anlagen, public.mitglieder, public.messwerte, public.tageswerte from anon, authenticated;
revoke all on public.befehle, public.anlagen_konfig from anon, authenticated;
grant select on public.admins, public.mitglieder, public.messwerte, public.tageswerte, public.anlagen_konfig to authenticated;
-- Befehle: alles ausser der Nutzlast (kann neue Passwörter enthalten)
grant select (id, anlage_id, art, status, ergebnis, erstellt, erstellt_von, gesendet_am, erledigt_am) on public.befehle to authenticated;
grant select (id, name, ort, kwp, erstellt, letzter_kontakt, version, live, heute, status, stoerung, abregelung, alarme, fernwartung)
  on public.anlagen to authenticated;

-- ---------------------------------------------------------------- Pi meldet Daten
create or replace function public.pi_melden(p_token text, p_daten jsonb) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare
  a_id uuid;
  l jsonb := p_daten -> 'live';
  h jsonb := p_daten -> 'heute';
  slot timestamptz := to_timestamp(floor(extract(epoch from now()) / 300) * 300);
  v_tag date;
  v_befehle jsonb := '[]'::jsonb;
  -- Textvergleich statt Cast: ein unsinniger Wert bricht die Meldung nicht ab, sondern gilt als «aus»
  v_fern boolean := coalesce((p_daten ->> 'fernwartung') = 'true', false);
begin
  if p_token is null or length(p_token) < 20 or pg_column_size(p_daten) > 60000 then
    raise exception 'ungueltig' using errcode = '22023';
  end if;
  select id into a_id from public.anlagen where token_hash = encode(digest(p_token, 'sha256'), 'hex');
  if a_id is null then
    perform pg_sleep(0.5);
    raise exception 'unbekannte Anlage' using errcode = '28000';
  end if;

  update public.anlagen set
    letzter_kontakt = now(),
    version = left(p_daten ->> 'version', 40),
    live = l, heute = h,
    status = left(p_daten ->> 'status', 20),
    stoerung = coalesce((p_daten ->> 'stoerung')::boolean, false),
    abregelung = p_daten -> 'abregelung',
    alarme = coalesce(p_daten -> 'alarme', '[]'::jsonb),
    fernwartung = v_fern
  where id = a_id;

  if l is not null then
    insert into public.messwerte as m (anlage_id, ts, pv_w, load_w, grid_w, battery_w, soc)
    values (a_id, slot, (l ->> 'pv_w')::real, (l ->> 'load_w')::real, (l ->> 'grid_w')::real,
            (l ->> 'battery_w')::real, (l ->> 'soc')::real)
    on conflict (anlage_id, ts) do update set
      pv_w = (coalesce(m.pv_w, 0) * m.n + coalesce(excluded.pv_w, 0)) / (m.n + 1),
      load_w = (coalesce(m.load_w, 0) * m.n + coalesce(excluded.load_w, 0)) / (m.n + 1),
      grid_w = case when excluded.grid_w is null then m.grid_w
                    else (coalesce(m.grid_w, 0) * m.n + excluded.grid_w) / (m.n + 1) end,
      battery_w = case when excluded.battery_w is null then m.battery_w
                       else (coalesce(m.battery_w, 0) * m.n + excluded.battery_w) / (m.n + 1) end,
      soc = coalesce(excluded.soc, m.soc),
      n = m.n + 1;
  end if;

  if h is not null and (h ->> 'day') ~ '^\d{4}-\d{2}-\d{2}$' then
    v_tag := (h ->> 'day')::date;
    insert into public.tageswerte (anlage_id, tag, pv_wh, load_wh, import_wh, export_wh, ev_wh)
    values (a_id, v_tag, coalesce((h ->> 'pv_wh')::real, 0), coalesce((h ->> 'load_wh')::real, 0),
            coalesce((h ->> 'import_wh')::real, 0), coalesce((h ->> 'export_wh')::real, 0),
            coalesce((h ->> 'ev_wh')::real, 0))
    on conflict (anlage_id, tag) do update set
      pv_wh = excluded.pv_wh, load_wh = excluded.load_wh, import_wh = excluded.import_wh,
      export_wh = excluded.export_wh, ev_wh = excluded.ev_wh;
  end if;
  -- Fernwartung: offene Befehle mitgeben (höchstens eine Stunde alt), als «gesendet» markieren.
  -- Die Nutzlast wird in derselben Transaktion gelöscht: Sie liegt nur, bis der Pi sie abholt.
  update public.befehle set status = 'verfallen', nutzlast = null
   where anlage_id = a_id and status = 'offen' and erstellt < now() - interval '1 hour';
  if v_fern then        -- nur an Pis, die Fernwartung eingeschaltet haben und Befehle verstehen
    with b as (
      select id, art, nutzlast, erstellt from public.befehle
       where anlage_id = a_id and status = 'offen' order by erstellt for update
    ), u as (
      update public.befehle x set status = 'gesendet', gesendet_am = now(), nutzlast = null
        from b where x.id = b.id
    )
    select coalesce(jsonb_agg(jsonb_build_object('id', id, 'art', art, 'nutzlast', nutzlast) order by erstellt), '[]'::jsonb)
      into v_befehle from b;
  end if;
  return jsonb_build_object('ok', true, 'befehle', v_befehle);
end;
$$;

-- ---------------------------------------------------------------- Verwaltung (nur Admins)
create or replace function public.anlage_anlegen(p_name text, p_ort text default '', p_kwp numeric default 0)
returns jsonb language plpgsql security definer set search_path = public, extensions as $$
declare
  tok text := encode(gen_random_bytes(24), 'hex');
  neu uuid;
begin
  if not public.ist_admin() then raise exception 'nur fuer Solarmaa' using errcode = '42501'; end if;
  insert into public.anlagen (name, ort, kwp, token_hash)
  values (p_name, coalesce(p_ort, ''), coalesce(p_kwp, 0), encode(digest(tok, 'sha256'), 'hex'))
  returning id into neu;
  return jsonb_build_object('id', neu, 'token', tok);   -- Token wird nur dieses eine Mal angezeigt
end;
$$;

create or replace function public.anlage_token_neu(p_anlage uuid)
returns jsonb language plpgsql security definer set search_path = public, extensions as $$
declare tok text := encode(gen_random_bytes(24), 'hex');
begin
  if not public.ist_admin() then raise exception 'nur fuer Solarmaa' using errcode = '42501'; end if;
  -- Mit einem neuen Token liessen sich sonst wartende Befehle abholen und eine Konfiguration unterschieben.
  if not public.hat_2fa() then raise exception 'Zwei-Faktor-Anmeldung noetig' using errcode = '42501'; end if;
  update public.anlagen set token_hash = encode(digest(tok, 'sha256'), 'hex') where id = p_anlage;
  if not found then raise exception 'Anlage nicht gefunden' using errcode = '02000'; end if;
  -- Was für den bisherigen Pi bestimmt war oder von ihm stammt, gilt für den neuen Token nicht.
  update public.befehle set status = 'verfallen', nutzlast = null where anlage_id = p_anlage and status = 'offen';
  delete from public.anlagen_konfig where anlage_id = p_anlage;
  return jsonb_build_object('id', p_anlage, 'token', tok);
end;
$$;

create or replace function public.anlage_loeschen(p_anlage uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.ist_admin() then raise exception 'nur fuer Solarmaa' using errcode = '42501'; end if;
  if not public.hat_2fa() then raise exception 'Zwei-Faktor-Anmeldung noetig' using errcode = '42501'; end if;
  delete from public.anlagen where id = p_anlage;
end;
$$;

create or replace function public.mitglied_setzen(p_anlage uuid, p_email text, p_entfernen boolean default false)
returns jsonb language plpgsql security definer set search_path = public as $$
declare u uuid;
begin
  if not public.ist_admin() then raise exception 'nur fuer Solarmaa' using errcode = '42501'; end if;
  select id into u from auth.users where lower(email) = lower(trim(p_email));
  if u is null then
    raise exception 'Kein Konto mit dieser E-Mail. Zuerst unter Authentication > Users anlegen oder einladen.'
      using errcode = '02000';
  end if;
  if p_entfernen then
    delete from public.mitglieder where anlage_id = p_anlage and user_id = u;
  else
    insert into public.mitglieder (anlage_id, user_id, email) values (p_anlage, u, lower(trim(p_email)))
    on conflict do nothing;
  end if;
  return jsonb_build_object('ok', true);
end;
$$;

-- ---------------------------------------------------------------- Fernwartung
-- Nur Solarmaa-Konten MIT Zwei-Faktor-Anmeldung dürfen Befehle an einen Pi schicken.
create or replace function public.befehl_anlegen(p_anlage uuid, p_art text, p_nutzlast jsonb default null)
returns uuid language plpgsql security definer set search_path = public as $$
declare neu uuid; mail text; v_fern boolean;
begin
  if not public.ist_admin() then raise exception 'nur fuer Solarmaa' using errcode = '42501'; end if;
  if not public.hat_2fa() then raise exception 'Zwei-Faktor-Anmeldung noetig' using errcode = '42501'; end if;
  if p_art not in ('update', 'konfig') then raise exception 'unbekannter Befehl' using errcode = '22023'; end if;
  if p_art = 'konfig' and (p_nutzlast ->> 'text') is null then raise exception 'Konfiguration fehlt' using errcode = '22023'; end if;
  if pg_column_size(p_nutzlast) > 250000 then raise exception 'zu gross' using errcode = '22023'; end if;
  -- Aufräumen für alle Anlagen: Was eine Stunde nicht abgeholt wurde, verfällt samt Nutzlast
  -- (auch wenn der betreffende Pi sich nie mehr meldet).
  update public.befehle set status = 'verfallen', nutzlast = null
   where status = 'offen' and erstellt < now() - interval '1 hour';
  select fernwartung into v_fern from public.anlagen where id = p_anlage;
  if not found then raise exception 'Anlage nicht gefunden' using errcode = '02000'; end if;
  if not v_fern then
    raise exception 'Fernwartung ist auf dieser Anlage nicht eingeschaltet' using errcode = '55000';
  end if;
  select email into mail from auth.users where id = auth.uid();
  -- ein noch nicht abgeholter Befehl derselben Art wird ersetzt
  update public.befehle set status = 'verfallen', nutzlast = null
   where anlage_id = p_anlage and art = p_art and status = 'offen';
  insert into public.befehle (anlage_id, art, nutzlast, erstellt_von)
  values (p_anlage, p_art, p_nutzlast, coalesce(mail, '')) returning id into neu;
  return neu;
end;
$$;

create or replace function public.pi_ergebnis(p_token text, p_befehl uuid, p_ok boolean, p_text text default '')
returns void language plpgsql security definer set search_path = public, extensions as $$
declare a_id uuid;
begin
  select id into a_id from public.anlagen where token_hash = encode(digest(coalesce(p_token, ''), 'sha256'), 'hex');
  if a_id is null then raise exception 'unbekannte Anlage' using errcode = '28000'; end if;
  update public.befehle
     set status = case when p_ok then 'erledigt' else 'fehler' end, ergebnis = left(coalesce(p_text, ''), 2000),
         erledigt_am = now(), nutzlast = null
   where id = p_befehl and anlage_id = a_id and status = 'gesendet';
end;
$$;

create or replace function public.pi_konfig(p_token text, p_text text)
returns void language plpgsql security definer set search_path = public, extensions as $$
declare a_id uuid;
begin
  if length(coalesce(p_text, '')) > 200000 then raise exception 'zu gross' using errcode = '22023'; end if;
  select id into a_id from public.anlagen where token_hash = encode(digest(coalesce(p_token, ''), 'sha256'), 'hex');
  if a_id is null then raise exception 'unbekannte Anlage' using errcode = '28000'; end if;
  insert into public.anlagen_konfig (anlage_id, text) values (a_id, p_text)
  on conflict (anlage_id) do update set text = excluded.text, stand = now();
end;
$$;

-- Alte 5-Minuten-Werte aufräumen (Tageswerte bleiben). Bei Bedarf als Cron-Job einrichten.
create or replace function public.aufraeumen(p_tage int default 400)
returns void language sql security definer set search_path = public as $$
  delete from public.messwerte where ts < now() - make_interval(days => p_tage);
$$;

-- Ausführrechte: Standard ist «jeder» – zuerst alles entziehen, dann gezielt vergeben.
revoke execute on function public.pi_melden(text, jsonb), public.anlage_anlegen(text, text, numeric),
  public.anlage_token_neu(uuid), public.anlage_loeschen(uuid), public.mitglied_setzen(uuid, text, boolean),
  public.aufraeumen(int), public.ist_admin(), public.darf_anlage(uuid), public.hat_2fa(),
  public.befehl_anlegen(uuid, text, jsonb), public.pi_ergebnis(text, uuid, boolean, text), public.pi_konfig(text, text)
  from public, anon, authenticated;
grant execute on function public.pi_melden(text, jsonb), public.pi_ergebnis(text, uuid, boolean, text),
  public.pi_konfig(text, text) to anon, authenticated;
grant execute on function public.hat_2fa(), public.befehl_anlegen(uuid, text, jsonb) to authenticated;
grant execute on function public.ist_admin(), public.darf_anlage(uuid), public.anlage_anlegen(text, text, numeric),
  public.anlage_token_neu(uuid), public.anlage_loeschen(uuid), public.mitglied_setzen(uuid, text, boolean)
  to authenticated;
