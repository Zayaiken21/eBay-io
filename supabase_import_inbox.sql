-- ============================================================================
-- import_inbox — transient relay for browser-captured pages
--
-- Run once in the Supabase SQL editor.
--
-- This is NOT storage. Supabase holds the user database; captured pages are
-- only ever in flight through this table:
--   * the app deletes a row in the same call that reads it,
--   * anything older than 15 minutes is swept on the next read,
--   * only raw HTML and its source URL pass through — never an extracted
--     product, a draft, or anything derived from one.
-- Under normal use this table is empty.
-- ============================================================================

create table if not exists public.import_inbox (
  id          uuid primary key default gen_random_uuid(),
  owner       text        not null,
  token       text        not null,
  source_url  text,
  html        text        not null,
  created_at  timestamptz not null default now()
);

create index if not exists import_inbox_owner_created_idx
  on public.import_inbox (owner, created_at desc);

alter table public.import_inbox enable row level security;

-- The bookmarklet carries a publishable key and may ONLY insert. It cannot
-- read, list, or delete — so a capture can never expose another seller's
-- page, and the key cannot be used to drain the table. Reads and deletes
-- happen in the app with the service-role key, which bypasses RLS.
drop policy if exists import_inbox_insert_only on public.import_inbox;
create policy import_inbox_insert_only
  on public.import_inbox
  for insert
  to anon
  with check (true);

-- Optional, if pg_cron is available: sweep stale rows server-side as well,
-- so nothing lingers even if the app is never opened again.
-- select cron.schedule(
--   'sweep_import_inbox', '*/15 * * * *',
--   $$delete from public.import_inbox
--     where created_at < now() - interval '15 minutes'$$
-- );
