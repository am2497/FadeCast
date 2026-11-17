-- Fadecast database: step 9
--   R02: customers can cancel or move a booking only until 2 hours before it starts
--        (staff can still change a booking up to its start time; leaving the walk-in queue is unchanged)
--   R11: the owner can list customers and erase a customer's personal data on request (UK GDPR)

-- R02: cancelling
create or replace function public.cancel_booking(p_id bigint)
returns void
language plpgsql
security definer set search_path = ''
as $$
declare
  staff boolean := coalesce(public.my_role(), '') in ('barber', 'owner');
begin
  update public.bookings
  set status = 'cancelled'
  where id = p_id
    and (
      -- leaving the walk-in queue
      (status = 'waiting' and (customer_id = auth.uid() or staff))
      -- an online booking: staff up to the start, customers up to 2 hours before
      or (status = 'booked' and (
            (staff and start_time > now())
            or (customer_id = auth.uid() and start_time > now() + interval '2 hours')))
    );
  if not found then
    if exists (select 1 from public.bookings
               where id = p_id and customer_id = auth.uid() and status = 'booked'
                 and start_time > now() and start_time <= now() + interval '2 hours') then
      raise exception 'Bookings can only be cancelled up to 2 hours before they start. Please call the shop.';
    end if;
    raise exception 'This booking cannot be cancelled.';
  end if;
end;
$$;

-- R02: moving
create or replace function public.move_booking(p_id bigint, p_barber bigint, p_start timestamptz)
returns void
language plpgsql
security definer set search_path = ''
as $$
declare
  staff boolean := coalesce(public.my_role(), '') in ('barber', 'owner');
begin
  if p_start <= now() then
    raise exception 'The new time must be in the future.';
  end if;
  -- the end time is recalculated by the before_booking_saved trigger,
  -- and the no_double_booking rule still refuses clashes
  update public.bookings
  set start_time = p_start, barber_id = p_barber
  where id = p_id
    and status = 'booked'
    and ((staff and start_time > now())
         or (customer_id = auth.uid() and start_time > now() + interval '2 hours'));
  if not found then
    if exists (select 1 from public.bookings
               where id = p_id and customer_id = auth.uid() and status = 'booked'
                 and start_time > now() and start_time <= now() + interval '2 hours') then
      raise exception 'Bookings can only be moved up to 2 hours before they start. Please call the shop.';
    end if;
    raise exception 'This booking cannot be moved.';
  end if;
end;
$$;

-- R11: the owner's list of customer accounts (email lives in auth.users, which the website cannot read directly)
create or replace function public.customer_list()
returns table (id uuid, full_name text, email text, joined timestamptz, bookings integer, last_booking timestamptz)
language plpgsql
stable
security definer set search_path = ''
as $$
begin
  if public.my_role() is distinct from 'owner' then
    raise exception 'Only the owner can do this.';
  end if;
  return query
    select p.id, p.full_name, u.email::text, p.created_at,
           count(b.id)::integer, max(b.start_time)
    from public.profiles p
    join auth.users u on u.id = p.id
    left join public.bookings b on b.customer_id = p.id
    where p.role = 'customer'
    group by p.id, p.full_name, u.email, p.created_at
    order by p.full_name;
end;
$$;

-- R11: erase a customer on request. Their login, email and name are deleted; their past
-- bookings stay only as anonymous counts for the dashboard and forecast.
create or replace function public.erase_customer(p_customer uuid)
returns integer
language plpgsql
security definer set search_path = ''
as $$
declare
  kept integer;
begin
  if public.my_role() is distinct from 'owner' then
    raise exception 'Only the owner can do this.';
  end if;
  if not exists (select 1 from public.profiles where id = p_customer and role = 'customer') then
    raise exception 'Only customer accounts can be erased here.';
  end if;
  -- cancel anything still to come, and remove the name from their walk-in visits
  update public.bookings set status = 'cancelled'
  where customer_id = p_customer and status in ('booked', 'waiting');
  update public.bookings set walk_in_name = case when is_walk_in then 'Erased customer' end
  where customer_id = p_customer;
  select count(*) into kept from public.bookings where customer_id = p_customer;
  -- deleting the login also deletes the profile; bookings.customer_id becomes empty
  delete from auth.users where id = p_customer;
  return kept;
end;
$$;

revoke execute on function public.customer_list() from public, anon;
revoke execute on function public.erase_customer(uuid) from public, anon;
grant execute on function public.customer_list() to authenticated;
grant execute on function public.erase_customer(uuid) to authenticated;
