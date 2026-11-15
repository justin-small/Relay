-- A channel's version counts its saves, so a second admin saving from an old copy is refused (#275).
alter table intercom_channels add column version integer not null default 1;
