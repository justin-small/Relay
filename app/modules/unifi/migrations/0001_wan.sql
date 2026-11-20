-- Internet health history (#358). The console keeps only about a day of 5-minute reports, so Relay keeps its own:
-- one row a minute for 48 hours, then one row per 5 minutes for 30 days, then nothing. Numbers only, never an
-- address or a name.

create table unifi_wan_1m (
    ts            integer primary key,        -- start of the minute, seconds
    down_bps      real,
    up_bps        real,
    latency_ms    real,                       -- the console's own measurement to the internet
    drops         real,
    availability  real,                       -- percent, the console's WAN monitors
    gw_ms         real,                       -- Relay's own probe to the gateway
    gw_loss       real,                       -- percent of probes that failed
    ext_ms        real,                       -- Relay's own probe to the outside target
    ext_loss      real
);

create table unifi_wan_5m (
    ts            integer primary key,
    down_bps      real,
    up_bps        real,
    latency_ms    real,
    drops         real,
    availability  real,
    gw_ms         real,
    gw_loss       real,
    ext_ms        real,
    ext_loss      real
);
