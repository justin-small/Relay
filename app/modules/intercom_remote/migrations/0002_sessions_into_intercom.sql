-- Remote sessions are intercom sessions with via = 'remote' (#529): one history table, so reports and the live page read
-- one place. The rows keep their start, end and reason; they take the default org, as every remote row did.
insert into intercom_sessions (device_id, user_id, via, started_at, ended_at, end_reason)
    select device_id, user_id, 'remote', started_at, ended_at, end_reason
    from intercom_remote_sessions order by id;
drop table intercom_remote_sessions;
