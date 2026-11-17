-- Fadecast database: step 2 (real service list, logins and roles)

-- Clear Cuts only offers two services
update public.services set name = 'Haircut and beard' where name = 'Cut and beard';
update public.services set active = false where name in ('Kids cut', 'Skin fade');

-- When someone signs up, give them a profile. Everyone starts as a customer;
-- only the owner (through the database) can make someone a barber or owner (R17)
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, new.raw_user_meta_data ->> 'full_name');
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- A helper that returns the role of whoever is logged in
create or replace function public.my_role()
returns text
language sql
stable
security definer set search_path = ''
as $$
  select role from public.profiles where id = auth.uid();
$$;

-- People can see their own profile; the owner can see everyone's.
-- There is no update rule, so nobody can change their own role from the website.
create policy "People can view their own profile" on public.profiles
  for select to authenticated using (id = auth.uid());
create policy "Owner can view all profiles" on public.profiles
  for select to authenticated using (public.my_role() = 'owner');
