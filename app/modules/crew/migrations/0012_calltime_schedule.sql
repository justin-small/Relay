-- A call time can be aimed at a named schedule (#526): with `schedule_ref` set, a 'before' rule pages `minutes`
-- before each window of that schedule that starts today, instead of before each Planning Center service time. Empty
-- keeps the old meaning.
alter table crew_calltimes add column schedule_ref text not null default '';
