-- Fadecast database: step 4 (online booking)

update public.barbers set name = 'Rey' where name = 'Ray';

-- The finish time is always worked out from the service length, so nobody can fake it
create or replace function public.set_booking_end()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
declare
  mins integer;
begin
  select duration_minutes into mins from public.services where id = new.service_id;
  new.end_time := new.start_time + make_interval(mins => mins);
  return new;
end;
$$;

create trigger before_booking_saved
  before insert or update of start_time, service_id on public.bookings
  for each row execute procedure public.set_booking_end();

-- Shows when a barber is busy (times only, no customer details), so the website can offer free slots
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
    and b.start_time < p_to
    and b.end_time > p_from;
$$;

-- R01: customers can book for themselves, in the future only
create policy "Customers can book for themselves" on public.bookings
  for insert to authenticated
  with check (customer_id = auth.uid() and is_walk_in = false and status = 'booked' and start_time > now());

-- Customers see only their own bookings; barbers and the owner see all of them
create policy "Customers can view their own bookings" on public.bookings
  for select to authenticated using (customer_id = auth.uid());
create policy "Staff can view all bookings" on public.bookings
  for select to authenticated using (public.my_role() in ('barber', 'owner'));

-- Barbers need customers' names to see who is coming in
create policy "Barbers can view profiles" on public.profiles
  for select to authenticated using (public.my_role() = 'barber');
