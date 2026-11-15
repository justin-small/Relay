-- A phone presents its credential, so the server looks the device up by hash (#273).
create index intercom_devices_hash on intercom_devices (credential_hash);
create index intercom_devices_previous on intercom_devices (previous_hash);
