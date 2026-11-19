-- Fadecast database: step 10 (rebuild the replay test data to match the real shop)
-- Run on 19 Nov 2026 after the owner and barber interviews gave the real week:
--   open Mon, Tue, Fri, Sat 09:00-20:00 and Sun 10:00-18:00; closed Wed and Thu;
--   Haircut 45 min, Haircut and beard 60 min;
--   busiest Friday to Sunday, mostly after 3pm; Tuesday quiet.
-- It deletes every old test row (is_test = true) and makes 20 weeks of new ones up to yesterday.
-- Real bookings (is_test = false) are not touched. Remove the test data with:
--   delete from public.bookings where is_test;

delete from public.bookings where is_test;  -- their wait_estimates go too (on delete cascade)

with settings as (
  -- space test appointments by the longest service, so they never overlap
  select max(duration_minutes) as gap,
         min(id) as first_service, max(id) as second_service
  from public.services where active
),
days as (
  select d::date as day, (extract(isodow from d) - 1)::int as dow, (current_date - d::date) as days_ago
  from generate_series(current_date - 140, current_date - 1, interval '1 day') d
),
plan as (
  -- customers per barber per day (Mon, Tue, Wed, Thu, Fri, Sat, Sun), growing by about 2 over 20 weeks
  select dy.day, dy.dow, h.opens, b.id as barber_id, st.gap, st.first_service, st.second_service,
         (extract(epoch from h.closes - h.opens) / 60 / st.gap)::int as capacity,
         greatest(1, round((array[4, 3, 0, 0, 8, 9, 7])[dy.dow + 1]
                           + (140 - dy.days_ago) / 70.0
                           + random() * 2.4 - 1.2)::int) as wanted
  from days dy
  join public.opening_hours h on h.day_of_week = dy.dow and not h.closed
  cross join public.barbers b
  cross join settings st
  where b.active
),
slots as (
  -- pick which hours are used; later slots are favoured, strongly from Friday to Sunday
  select p.day, p.opens, p.barber_id, p.gap, x.k, random() as r_type, random() as r_show, random() as r_late,
         case when random() < 0.6 then p.first_service else p.second_service end as service_id
  from plan p
  cross join lateral (
    select k from generate_series(0, p.capacity - 1) k
    order by random() - k * (case when p.dow in (4, 5, 6) then 0.12 else 0.06 end)
    limit least(p.wanted, p.capacity)
  ) x
),
booking_rows as (
  select s.*, sv.duration_minutes,
         ((s.day + s.opens + make_interval(mins => s.k * s.gap)) at time zone 'Europe/London') as start_at,
         case when s.r_type >= 0.4 and s.r_show < 0.08 then 'no_show' else 'finished' end as final_status
  from slots s join public.services sv on sv.id = s.service_id
)
insert into public.bookings (walk_in_name, is_walk_in, barber_id, service_id, start_time, end_time, status,
                             created_at, started_at, finished_at, is_test)
select case when r.r_type < 0.4 then 'Test walk-in' end,
       r.r_type < 0.4,
       r.barber_id,
       r.service_id,
       r.start_at,
       r.start_at,  -- the finish time is filled in by the booking trigger
       r.final_status,
       r.start_at - interval '2 days',
       case when r.final_status = 'finished' then r.start_at + make_interval(mins => (r.r_late * 8)::int) end,
       case when r.final_status = 'finished'
            then r.start_at + make_interval(mins => (r.r_late * 8)::int + r.duration_minutes) end,
       true
from booking_rows r;

-- Test wait estimates for walk-ins: what was shown, and how long they really waited (random, not from the estimator)
insert into public.wait_estimates (booking_id, estimated_minutes, estimated_at, actual_start)
select b.id, e.est, b.started_at - make_interval(mins => greatest(0, e.est + e.noise)), b.started_at
from public.bookings b
cross join lateral (
  select (5 + floor(random() * 35))::int as est, (floor(random() * 17) - 8)::int as noise, b.id as same_row
) e
where b.is_test and b.is_walk_in and b.status = 'finished';

-- A walk-in "arrives" when their wait estimate was given
update public.bookings b set created_at = w.estimated_at
from public.wait_estimates w
where w.booking_id = b.id and b.is_test;

select count(*) as test_bookings,
       count(*) filter (where is_walk_in) as walk_ins,
       count(*) filter (where status = 'no_show') as no_shows,
       min(start_time)::date as first_day, max(start_time)::date as last_day
from public.bookings where is_test;
