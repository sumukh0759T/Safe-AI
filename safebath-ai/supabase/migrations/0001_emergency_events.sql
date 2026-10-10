-- SafeBath AI — Supabase schema (migration 0001)
--
-- Mirrors the local SQLite `emergency_events` table so the Raspberry Pi can
-- upsert durable emergencies + acknowledgements into the cloud. This is the
-- ONLY table the sync worker writes (`backend/supabase_sync.py`); the Pi keeps
-- the authoritative copy locally and syncs opportunistically.
--
-- Apply with the Supabase CLI:
--   supabase db push
-- or paste into the SQL editor of your project.
--
-- The Pi authenticates with the SERVICE ROLE key (bypasses RLS). RLS is enabled
-- with no policies so the anon/public key cannot read or write this table.

create table if not exists public.emergency_events (
    event_id          text primary key,          -- stable id, e.g. emg-<hex>
    ts                double precision not null, -- device/RPi event time (epoch s)
    severity          text not null,             -- low | medium | high | critical
    state             text not null default 'active',
    trigger_name      text not null,
    message           text not null,
    evidence          text not null default '{}',-- JSON blob (kept as text)
    device_ids        text not null default '',  -- comma-separated device ids
    score             double precision not null default 0,
    band              text not null default 'normal',
    alert_id          integer,
    local_alarm_fired integer not null default 0,
    ack_ts            double precision,
    ack_by            text,
    ack_count         integer not null default 0,
    sync_status       text not null default 'pending',
    synced_at         double precision,
    created_at        double precision not null
);

create index if not exists idx_emergency_ts
    on public.emergency_events (ts);
create index if not exists idx_emergency_state
    on public.emergency_events (state);
create index if not exists idx_emergency_sync
    on public.emergency_events (sync_status);

-- Upserts are keyed on the primary key (event_id) by PostgREST
-- `Prefer: resolution=merge-duplicates`, so no extra unique constraint needed.

alter table public.emergency_events enable row level security;
-- No policies on purpose: only the service role (Pi sync worker) may access.
