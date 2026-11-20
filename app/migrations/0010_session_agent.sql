-- A password session remembers the browser it came from (#521), so forgetting a phone (app/devices.py `revoke`) also
-- signs that phone out: its sessions with the same user and the same user agent as the device go with it. A PIN session
-- already went with its trusted device. Sessions from before this have no user agent and are left alone.

alter table sessions add column user_agent text not null default '';
