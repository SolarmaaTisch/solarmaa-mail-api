-- Hilfen für die Datenbanktests. Jede bestandene Prüfung meldet «ok - …»; run.sh zählt diese Zeilen.
create schema if not exists t;
grant usage on schema t to anon, authenticated;

-- Als Konto mit Sicherheitsstufe auftreten ('' = nicht angemeldet, Rolle anon). Zurück: reset role;
create or replace function t.als(uid text, aal text default 'aal1') returns void language plpgsql as $$
begin
  if coalesce(uid, '') = '' then
    perform set_config('request.jwt.claims', '', false);
    perform set_config('role', 'anon', false);
  else
    perform set_config('request.jwt.claims',
                       jsonb_build_object('sub', uid, 'aal', aal, 'role', 'authenticated')::text, false);
    perform set_config('role', 'authenticated', false);
  end if;
end;
$$;

create or replace function t.gleich(was text, ist anyelement, soll anyelement) returns void language plpgsql as $$
begin
  if ist is distinct from soll then
    raise exception 'FEHLGESCHLAGEN: % (ist: %, soll: %)', was, ist, soll;
  end if;
  raise notice 'ok - %', was;
end;
$$;

-- Der Befehl muss scheitern (optional mit genau diesem SQLSTATE). Läuft mit den Rechten des Aufrufers.
create or replace function t.scheitert(was text, befehl text, sqlstate_soll text default null) returns void
language plpgsql as $$
declare v_code text := null; v_text text;
begin
  begin
    execute befehl;
  exception when others then
    v_code := sqlstate; v_text := sqlerrm;
  end;
  if v_code is null then
    raise exception 'FEHLGESCHLAGEN: % (kein Fehler, erwartet war einer)', was;
  end if;
  if sqlstate_soll is not null and v_code <> sqlstate_soll then
    raise exception 'FEHLGESCHLAGEN: % (SQLSTATE %, soll %: %)', was, v_code, sqlstate_soll, v_text;
  end if;
  raise notice 'ok - % [%]', was, v_code;
end;
$$;
grant execute on all functions in schema t to anon, authenticated;
