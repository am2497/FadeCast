-- Fadecast database: step 6 (walk-ins, live queue, barber's day, wait estimates, owner dashboard)

-- 1. Extra details on each booking
alter table public.bookings
  add column if not exists started_at timestamptz,
  add column if not exists finished_at timestamptz,
  add column if not exists is_test boolean not null default false;

-- A walk-in can wait for "any barber"
alter table public.bookings alter column barber_id drop not null;

-- The no-double-booking rule is for online bookings. Walk-ins are served when a barber is free.
alter table public.bookings drop constraint no_double_booking;
alter table public.bookings add constraint no_double_booking exclude using gist (
  barber_id with =,
  tstzrange(start_time, end_time) with &&
) where (not is_walk_in and status not in ('cancelled', 'no_show'));

-- People waiting in the queue do not block online booking slots
create or replace function public.busy_times(p_barber bigint, p_from timestamptz, p_to timestamptz)
returns table (start_time timestamptz, end_time timestamptz)
language sql
stable
security definer set search_path = ''
as $$
  select b.start_time, b.end_time
  from public.bookings b
  where b.barber_id = p_barber
    and b.status not in ('cancelled', 'no_show')
    and not (b.is_walk_in and b.status = 'waiting')
    and b.start_time < p_to
    and b.end_time > p_from;
$$;

-- 2. Today's queue for the live queue screen (no names, so anyone can see it)
create or replace function public.queue_snapshot()
returns table (id bigint, is_walk_in boolean, barber_id bigint, status text, start_time timestamptz,
               end_time timestamptz, started_at timestamptz, created_at timestamptz,
               duration_minutes integer, mine boolean)
language sql
stable
security definer set search_path = ''
as $$
  select b.id, b.is_walk_in, b.barber_id, b.status, b.start_time, b.end_time, b.started_at, b.created_at,
         s.duration_minutes, coalesce(b.customer_id = auth.uid(), false)
  from public.bookings b
  join public.services s on s.id = b.service_id
  where b.start_time >= ((now() at time zone 'Europe/London')::date::timestamp at time zone 'Europe/London')
    and b.start_time < (((now() at time zone 'Europe/London')::date + 1)::timestamp at time zone 'Europe/London')
    and (b.status in ('waiting', 'in_chair')
         or (b.status = 'booked' and b.start_time >= now() - interval '30 minutes'))
  order by b.created_at;
$$;

-- 3. A logged-in customer joins the queue from the website; the wait estimate shown is saved (RQ2)
create or replace function public.join_queue(p_service bigint, p_barber bigint, p_estimate integer)
returns bigint
language plpgsql
security definer set search_path = ''
as $$
declare
  new_id bigint;
  who text;
begin
  if auth.uid() is null then
    raise exception 'Please log in first.';
  end if;
  if exists (select 1 from public.bookings where customer_id = auth.uid() and status = 'waiting') then
    raise exception 'You are already in the queue.';
  end if;
  select full_name into who from public.profiles where id = auth.uid();
  insert into public.bookings (customer_id, walk_in_name, is_walk_in, barber_id, service_id, start_time, status)
  values (auth.uid(), coalesce(who, 'Customer'), true, p_barber, p_service, now(), 'waiting')
  returning id into new_id;
  insert into public.wait_estimates (booking_id, estimated_minutes) values (new_id, p_estimate);
  return new_id;
end;
$$;

-- 4. Staff add someone who walked in
create or replace function public.add_walk_in(p_name text, p_service bigint, p_barber bigint, p_estimate integer)
returns bigint
language plpgsql
security definer set search_path = ''
as $$
declare
  new_id bigint;
begin
  if coalesce(public.my_role(), '') not in ('barber', 'owner') then
    raise exception 'Only staff can add walk-ins.';
  end if;
  insert into public.bookings (walk_in_name, is_walk_in, barber_id, service_id, start_time, status)
  values (coalesce(nullif(trim(p_name), ''), 'Walk-in'), true, p_barber, p_service, now(), 'waiting')
  returning id into new_id;
  insert into public.wait_estimates (booking_id, estimated_minutes) values (new_id, p_estimate);
  return new_id;
end;
$$;

-- 5. Staff move a customer through the day: in the chair, finished, or no-show
create or replace function public.set_status(p_id bigint, p_status text, p_barber bigint default null)
returns void
language plpgsql
security definer set search_path = ''
as $$
declare
  n integer;
begin
  if coalesce(public.my_role(), '') not in ('barber', 'owner') then
    raise exception 'Only staff can do this.';
  end if;
  if p_status = 'in_chair' then
    update public.bookings
    set status = 'in_chair',
        started_at = now(),
        barber_id = coalesce(p_barber, barber_id),
        start_time = case when is_walk_in then now() else start_time end
    where id = p_id and status in ('booked', 'waiting');
    get diagnostics n = row_count;
    -- record when they really started, to check how good the wait estimate was (RQ2)
    update public.wait_estimates set actual_start = now() where booking_id = p_id and actual_start is null;
  elsif p_status = 'finished' then
    update public.bookings set status = 'finished', finished_at = now()
    where id = p_id and status = 'in_chair';
    get diagnostics n = row_count;
  elsif p_status = 'no_show' then
    update public.bookings set status = 'no_show'
    where id = p_id and status in ('booked', 'waiting');
    get diagnostics n = row_count;
  else
    raise exception 'Unknown status.';
  end if;
  if n = 0 then
    raise exception 'That booking has already changed. Refresh the page.';
  end if;
end;
$$;

-- 6. Cancelling also works for someone leaving the queue
create or replace function public.cancel_booking(p_id bigint)
returns void
language plpgsql
security definer set search_path = ''
as $$
begin
  update public.bookings
  set status = 'cancelled'
  where id = p_id
    and ((status = 'booked' and start_time > now()) or status = 'waiting')
    and (customer_id = auth.uid() or public.my_role() in ('barber', 'owner'));
  if not found then
    raise exception 'This booking cannot be cancelled.';
  end if;
end;
$$;

-- 7. The owner links a barber's login to their name, so they get the barber pages
create or replace function public.make_barber(p_email text, p_barber bigint)
returns void
language plpgsql
security definer set search_path = ''
as $$
declare
  uid uuid;
begin
  if public.my_role() is distinct from 'owner' then
    raise exception 'Only the owner can do this.';
  end if;
  select id into uid from auth.users where lower(email) = lower(trim(p_email));
  if uid is null then
    raise exception 'No account uses that email yet. Ask them to sign up first.';
  end if;
  update public.profiles set role = 'barber' where id = uid and role <> 'owner';
  update public.barbers set profile_id = null where profile_id = uid;
  update public.barbers set profile_id = uid where id = p_barber;
end;
$$;

-- 8. Numbers for the owner dashboard and forecast (owner only)
create or replace function public.daily_stats(p_days integer)
returns table (day date, served integer, no_shows integer, walk_ins integer, online integer)
language sql
stable
security definer set search_path = ''
as $$
  select (b.start_time at time zone 'Europe/London')::date,
         (count(*) filter (where b.status = 'finished'))::int,
         (count(*) filter (where b.status = 'no_show'))::int,
         (count(*) filter (where b.status = 'finished' and b.is_walk_in))::int,
         (count(*) filter (where b.status = 'finished' and not b.is_walk_in))::int
  from public.bookings b
  where public.my_role() = 'owner'
    and b.start_time >= now() - make_interval(days => p_days)
    and b.start_time < now()
  group by 1
  order by 1;
$$;

create or replace function public.hourly_stats(p_days integer)
returns table (day_of_week integer, hour integer, served integer)
language sql
stable
security definer set search_path = ''
as $$
  select (extract(isodow from b.start_time at time zone 'Europe/London') - 1)::int,
         extract(hour from b.start_time at time zone 'Europe/London')::int,
         count(*)::int
  from public.bookings b
  where public.my_role() = 'owner'
    and b.status = 'finished'
    and b.start_time >= now() - make_interval(days => p_days)
  group by 1, 2;
$$;

create or replace function public.wait_accuracy(p_days integer)
returns table (estimated_at timestamptz, estimated_minutes integer, actual_minutes numeric)
language sql
stable
security definer set search_path = ''
as $$
  select w.estimated_at, w.estimated_minutes,
         round((extract(epoch from w.actual_start - w.estimated_at) / 60.0)::numeric, 1)
  from public.wait_estimates w
  where public.my_role() = 'owner'
    and w.actual_start is not null
    and w.estimated_at >= now() - make_interval(days => p_days)
  order by w.estimated_at;
$$;

-- Only logged-in people can use the staff and customer functions
revoke execute on function public.join_queue(bigint, bigint, integer) from public, anon;
revoke execute on function public.add_walk_in(text, bigint, bigint, integer) from public, anon;
revoke execute on function public.set_status(bigint, text, bigint) from public, anon;
revoke execute on function public.make_barber(text, bigint) from public, anon;
revoke execute on function public.daily_stats(integer) from public, anon;
revoke execute on function public.hourly_stats(integer) from public, anon;
revoke execute on function public.wait_accuracy(integer) from public, anon;
grant execute on function public.join_queue(bigint, bigint, integer) to authenticated;
grant execute on function public.add_walk_in(text, bigint, bigint, integer) to authenticated;
grant execute on function public.set_status(bigint, text, bigint) to authenticated;
grant execute on function public.make_barber(text, bigint) to authenticated;
grant execute on function public.daily_stats(integer) to authenticated;
grant execute on function public.hourly_stats(integer) to authenticated;
grant execute on function public.wait_accuracy(integer) to authenticated;
