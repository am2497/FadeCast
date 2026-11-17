-- Fadecast database: step 3 (real barber names, owner can change shop settings)

-- Clear Cuts' barbers
update public.barbers set name = 'Ray' where name = 'Barber 1';
update public.barbers set name = 'Jay' where name = 'Barber 2';
update public.barbers set name = 'Nino' where name = 'Barber 3';

-- Only the owner can add or change services, barbers and opening hours (R06, R17)
create policy "Owner can add services" on public.services
  for insert to authenticated with check (public.my_role() = 'owner');
create policy "Owner can change services" on public.services
  for update to authenticated using (public.my_role() = 'owner') with check (public.my_role() = 'owner');

create policy "Owner can add barbers" on public.barbers
  for insert to authenticated with check (public.my_role() = 'owner');
create policy "Owner can change barbers" on public.barbers
  for update to authenticated using (public.my_role() = 'owner') with check (public.my_role() = 'owner');

create policy "Owner can change opening hours" on public.opening_hours
  for update to authenticated using (public.my_role() = 'owner') with check (public.my_role() = 'owner');
