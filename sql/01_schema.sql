-- Fadecast database: step 1 (tables, double-booking rule, starter data)

-- Needed for the "no double-booking" rule (R03)
create extension if not exists btree_gist;

-- Everyone who logs in: customer, barber or owner (R17)
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  full_name text,
  role text not null default 'customer' check (role in ('customer', 'barber', 'owner')),
  created_at timestamptz not null default now()
);

-- The barbers who work at the shop (R06)
create table public.barbers (
  id bigint generated always as identity primary key,
  name text not null,
  profile_id uuid references public.profiles (id) on delete set null,
  active boolean not null default true
);

-- Services and prices (R06)
create table public.services (
  id bigint generated always as identity primary key,
  name text not null,
  price numeric(6, 2) not null check (price >= 0),
  duration_minutes integer not null default 60 check (duration_minutes > 0),
  active boolean not null default true
);

-- Opening hours for each day: 0 = Monday ... 6 = Sunday (R06)
create table public.opening_hours (
  day_of_week integer primary key check (day_of_week between 0 and 6),
  opens time,
  closes time,
  closed boolean not null default false
);

-- Bookings and walk-ins share one table, so they share one queue (R01, R04, R05)
create table public.bookings (
  id bigint generated always as identity primary key,
  customer_id uuid references public.profiles (id) on delete set null,
  walk_in_name text,
  is_walk_in boolean not null default false,
  barber_id bigint not null references public.barbers (id),
  service_id bigint not null references public.services (id),
  start_time timestamptz not null,
  end_time timestamptz not null,
  status text not null default 'booked'
    check (status in ('booked', 'waiting', 'in_chair', 'finished', 'no_show', 'cancelled')),
  created_at timestamptz not null default now(),
  check (end_time > start_time),
  -- R03: two active bookings for the same barber can never overlap
  constraint no_double_booking exclude using gist (
    barber_id with =,
    tstzrange(start_time, end_time) with &&
  ) where (status not in ('cancelled', 'no_show'))
);

-- Every wait estimate shown, and when the customer actually started (RQ2 data)
create table public.wait_estimates (
  id bigint generated always as identity primary key,
  booking_id bigint references public.bookings (id) on delete cascade,
  estimated_minutes integer not null,
  estimated_at timestamptz not null default now(),
  actual_start timestamptz
);

-- Turn on Row Level Security everywhere: nothing is visible until a rule allows it
alter table public.profiles enable row level security;
alter table public.barbers enable row level security;
alter table public.services enable row level security;
alter table public.opening_hours enable row level security;
alter table public.bookings enable row level security;
alter table public.wait_estimates enable row level security;

-- The price list, barbers and opening hours are public information
create policy "Anyone can view services" on public.services for select using (true);
create policy "Anyone can view barbers" on public.barbers for select using (true);
create policy "Anyone can view opening hours" on public.opening_hours for select using (true);

-- Starter data (made-up test data)
insert into public.services (name, price, duration_minutes) values
  ('Haircut', 35, 60),
  ('Cut and beard', 45, 60),
  ('Skin fade', 38, 60),
  ('Kids cut', 20, 60);

insert into public.barbers (name) values ('Barber 1'), ('Barber 2'), ('Barber 3');

insert into public.opening_hours (day_of_week, opens, closes, closed) values
  (0, '09:00', '18:00', false),
  (1, '09:00', '18:00', false),
  (2, '09:00', '18:00', false),
  (3, '09:00', '18:00', false),
  (4, '09:00', '19:00', false),
  (5, '09:00', '18:00', false),
  (6, null, null, true);
