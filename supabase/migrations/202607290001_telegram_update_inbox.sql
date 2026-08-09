create table if not exists public.telegram_update_inbox (
  update_id bigint primary key,
  payload jsonb not null,
  status text not null default 'pending'
    check (status in ('pending', 'retry', 'processing', 'processed', 'dead_letter')),
  attempts integer not null default 0,
  last_error text,
  received_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  processed_at timestamptz,
  lease_until timestamptz,
  claim_token uuid
);

create index if not exists telegram_update_inbox_pending_idx
  on public.telegram_update_inbox(status, update_id);

alter table public.telegram_update_inbox enable row level security;
