-- Days of the week count from Sunday = 0 everywhere in Relay (#522, app/timewin.py). Call times were saved counting
-- from Monday = 0 (Python's weekday()), so Monday 0 becomes 1 ... Sunday 6 becomes 0.
update crew_calltimes set weekday = (weekday + 1) % 7 where weekday is not null;
