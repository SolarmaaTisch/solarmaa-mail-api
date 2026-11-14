-- Nach (erneutem) Einspielen von schema.sql: Tabelle mit RLS, Bucket privat mit Grenze und Typen, Regeln vollständig.
select t.gleich('Tabelle dokumente mit RLS', (select relrowsecurity from pg_class where oid = 'public.dokumente'::regclass), true);
select t.gleich('Bucket privat, 20 MB, vier Typen',
                (select public::text || '/' || file_size_limit || '/' || array_to_string(allowed_mime_types, ',') from storage.buckets where id = 'anlagedoku'),
                'false/20971520/application/pdf,image/jpeg,image/png,image/webp'::text);
select t.gleich('drei Regeln auf storage.objects',
                (select count(*) from pg_policies where schemaname = 'storage' and tablename = 'objects' and policyname like 'anlagedoku%'), 3::bigint);
