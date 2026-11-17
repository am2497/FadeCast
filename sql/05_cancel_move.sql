-- Fadecast database: step 5 (cancel and move bookings, R02)
-- Customers can only change their own future bookings; barbers and the owner can change any.
-- These functions only touch the status, time and barber, so nobody can edit anything else.
-- (Replaced in step 9, which adds the 2-hour cut-off for customers.)

create or replace function public.cancel_booking(p_id bigint)
returns void
language plpgsql
security definer set search_path = ''
as $$
begin
  update public.bookings
  set status = 'cancelled'
  where id = p_id
    and status = 'booked'
    and start_time > now()
    and (customer_id = auth.uid() or public.my_role() in ('barber', 'owner'));
  if not found then
    raise exception 'This booking cannot be cancelled.';
  end if;
end;
$$;

create or replace function public.move_booking(p_id bigint, p_barber bigint, p_start timestamptz)
returns void
language plpgsql
security definer set search_path = ''
as $$
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
    and start_time > now()
    and (customer_id = auth.uid() or public.my_role() in ('barber', 'owner'));
  if not found then
    raise exception 'This booking cannot be moved.';
  end if;
end;
$$;

-- Only logged-in people can use them
revoke execute on function public.cancel_booking(bigint) from public, anon;
revoke execute on function public.move_booking(bigint, bigint, timestamptz) from public, anon;
grant execute on function public.cancel_booking(bigint) to authenticated;
grant execute on function public.move_booking(bigint, bigint, timestamptz) to authenticated;
