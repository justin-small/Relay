# Sourced by entrypoint.sh in the `serve` path only (never run on its own).
#
# Defines pg_supervisor, which the entrypoint runs as a background subshell: it
# prepares the data directory, starts Postgres, and restarts it with backoff if
# it dies. A database problem NEVER stops the container -- captions are the job,
# and the app tolerates the database being down -- so every failure here is
# logged and retried (or, for the two permanent kinds, logged and given up on).
#
# The database holds the user accounts. It listens on a unix socket in a private
# directory and on NO TCP port, so "trust" authentication is safe: the only way
# to reach it is to be this container's user. Data lives on the `relay-db` named
# volume, apart from docker-config/, because Postgres needs a real POSIX
# filesystem (ownership, 0700, fsync), which a Docker Desktop bind mount is not.

PGDATA_ROOT="${RELAY_DB_ROOT:-/var/lib/relay-db}"
PGDATA="$PGDATA_ROOT/data"
PGSOCK=/tmp/relay-pg
PG_BOOT=/tmp/relay-pg-boot     # created once the first start attempt is settled
BACKUPS="${RELAY_DB_BACKUPS:-/app/config/backups}"
PGMAJOR="$(postgres --version | sed -E 's/^[^0-9]*([0-9]+).*/\1/')"
MY_UID="$(id -u)"
MY_GID="$(id -g)"

# What the app is told, whether or not the database is up yet: it re-probes, so
# it connects as soon as the database appears.
export RELAY_DATABASE_URL="${RELAY_DATABASE_URL:-postgresql://relay@/relay?host=$PGSOCK}"
export RELAY_DB_BACKUPS="$BACKUPS"

say() { echo "relay: $*" >&2; }

# Settings go on the command line: nothing in the read-only root is written, and
# the data directory carries no config of ours to drift out of date. Sized for a
# box whose real job is audio. dynamic_shared_memory_type=posix lives in
# /dev/shm, which Docker sizes at 64 MB; with no parallel workers it needs a few
# kilobytes of it.
PG_OPTS=(
    -c listen_addresses=
    -c unix_socket_directories="$PGSOCK"
    -c unix_socket_permissions=0700
    -c shared_buffers=32MB
    -c max_connections=20
    -c work_mem=4MB
    -c maintenance_work_mem=16MB
    -c max_parallel_workers_per_gather=0
    -c dynamic_shared_memory_type=posix
    -c max_wal_size=256MB
    -c log_destination=stderr
    -c logging_collector=off
    -c "log_line_prefix=%m [%p] "
    -c log_checkpoints=on
)

PGENV=(env)
PG_PID=""
NEED_RESTORE=""    # set when old-major files were moved aside; cleared once restored

# initdb and postgres refuse to run for a uid with no passwd entry, and on a
# Linux host the container runs as the host user's uid. Do what the official
# postgres image does: nss_wrapper, for the Postgres commands only. LD_PRELOAD
# is passed through `env` per command and is never exported, so Caddy and the
# app never see it.
pg_nss() {
    id -un >/dev/null 2>&1 && return 0
    local lib
    lib="$(ls /usr/lib/*-linux-gnu/libnss_wrapper.so 2>/dev/null | head -1 || true)"
    if [ -z "$lib" ]; then
        say "DATABASE OFF: uid $MY_UID has no passwd entry and libnss_wrapper is missing. Captions are unaffected; sign-in is unavailable."
        return 2
    fi
    mkdir -p -m 0700 /tmp/relay-nss
    echo "relay:x:$MY_UID:$MY_GID:relay:$HOME:/usr/sbin/nologin" > /tmp/relay-nss/passwd
    echo "relay:x:$MY_GID:" > /tmp/relay-nss/group
    PGENV=(env LD_PRELOAD="$lib" NSS_WRAPPER_PASSWD=/tmp/relay-nss/passwd NSS_WRAPPER_GROUP=/tmp/relay-nss/group)
}

# Get $PGDATA ready. 0 = ready, 1 = failed but worth retrying, 2 = permanent
# (retrying cannot help, and nothing has been touched).
pg_prepare() {
    pg_nss || return $?

    if [ -d "$PGDATA" ]; then
        # Data written by another uid (the operator changed users): Postgres
        # would refuse it anyway, so say what to do, and touch nothing.
        local data_uid
        data_uid="$(stat -c %u "$PGDATA")"
        if [ "$data_uid" != "$MY_UID" ]; then
            say "DATABASE OFF: the data in $PGDATA belongs to uid $data_uid but Relay now runs as uid $MY_UID. Start Relay as uid $data_uid again (RELAY_UID/RELAY_GID), or use a new volume; nothing was changed. Captions are unaffected; sign-in is unavailable."
            return 2
        fi
    elif [ ! -w "$PGDATA_ROOT" ]; then
        say "DATABASE OFF: $PGDATA_ROOT is not writable (is the relay-db volume mounted? use docker compose, or add -v relay-db:$PGDATA_ROOT). Captions are unaffected; sign-in is unavailable."
        return 2
    fi

    if [ -f "$PGDATA/PG_VERSION" ]; then
        local old
        old="$(cat "$PGDATA/PG_VERSION")"
        if [ "$old" != "$PGMAJOR" ]; then
            local aside="$PGDATA_ROOT/data.pg$old-$(date +%Y%m%d-%H%M%S)"
            say "=============================================================="
            say "the database files are Postgres $old but this image runs Postgres $PGMAJOR."
            say "moving the old files aside to $aside (nothing is deleted)."
            mv "$PGDATA" "$aside" || { say "DATABASE OFF: could not move the old files aside; will retry."; return 1; }
            NEED_RESTORE="$aside"
        fi
    elif [ -d "$PGDATA" ]; then
        # Present but never finished, or foreign files. Keep it rather than guess.
        local aside="$PGDATA_ROOT/data.incomplete-$(date +%Y%m%d-%H%M%S)"
        say "$PGDATA has no PG_VERSION, so it is not a finished database; moving it to $aside."
        mv "$PGDATA" "$aside" || { say "DATABASE OFF: could not move it aside; will retry."; return 1; }
    fi

    if [ ! -d "$PGDATA" ]; then
        say "creating a new database in $PGDATA (Postgres $PGMAJOR)"
        # Built beside the real name and renamed last, so a crash in the middle
        # leaves a scratch directory, never a half-made `data/`.
        rm -rf "$PGDATA_ROOT/data.initdb"
        if ! "${PGENV[@]}" initdb --pgdata="$PGDATA_ROOT/data.initdb" --username=relay \
                --auth-local=trust --encoding=UTF8 --locale=C.UTF-8 --data-checksums >&2 \
                || ! mv "$PGDATA_ROOT/data.initdb" "$PGDATA"; then
            say "DATABASE OFF: initdb failed (output above); will retry. Captions are unaffected; sign-in is unavailable."
            return 1
        fi
    fi
    chmod 0700 "$PGDATA"
    return 0
}

# Stop the postmaster we started (fast shutdown, so it checkpoints) and wait.
pg_halt() {
    if [ -n "$PG_PID" ] && kill -INT "$PG_PID" 2>/dev/null; then
        wait "$PG_PID" 2>/dev/null || true
    fi
    PG_PID=""
}

# Start the postmaster and get the relay database ready. 0 = up, 1 = not.
pg_start() {
    # The previous postmaster is dead (it was ours, in this container), so its
    # lock file is stale by construction. Left in place it can name a pid that a
    # new process now holds, and Postgres refuses to start.
    if [ -f "$PGDATA/postmaster.pid" ]; then
        say "removing a stale postmaster.pid left by an unclean stop"
        rm -f "$PGDATA/postmaster.pid"
    fi
    rm -rf "$PGSOCK"
    mkdir -m 0700 "$PGSOCK" || return 1

    "${PGENV[@]}" postgres -D "$PGDATA" "${PG_OPTS[@]}" &
    PG_PID=$!

    # Wait for it, but not forever, and not if it has already died.
    local up=0 _
    for _ in $(seq 60); do
        kill -0 "$PG_PID" 2>/dev/null || break
        if pg_isready -q -h "$PGSOCK" -U relay -d postgres; then up=1; break; fi
        sleep 0.5
    done
    if [ "$up" != 1 ]; then
        say "DATABASE OFF: Postgres did not become ready within 30 s (its own messages are above); will retry. Captions are unaffected; sign-in is unavailable."
        pg_halt
        return 1
    fi

    local psql=(psql -h "$PGSOCK" -U relay -v ON_ERROR_STOP=1 -qtAX)
    local have
    have="$("${psql[@]}" -d postgres -c "SELECT 1 FROM pg_database WHERE datname = 'relay'")" || have=err
    if [ "$have" = err ]; then
        say "DATABASE OFF: could not query the new server; will retry."
        pg_halt
        return 1
    fi
    if [ "$have" != 1 ]; then
        say "creating database relay"
        if ! "${psql[@]}" -d postgres -c 'CREATE DATABASE relay ENCODING UTF8 TEMPLATE template0 LOCALE "C.UTF-8"' >&2; then
            say "DATABASE OFF: could not create database relay; will retry."
            pg_halt
            return 1
        fi
    fi

    if [ -n "$NEED_RESTORE" ]; then
        # relay-YYYYMMDD-HHMMSS.dump sorts by name, so the last one is the newest.
        local dump
        dump="$(ls "$BACKUPS"/relay-[0-9]*-[0-9]*.dump 2>/dev/null | sort | tail -1 || true)"
        if [ -n "$dump" ]; then
            say "restoring $dump into the new Postgres $PGMAJOR database"
            if pg_restore --clean --if-exists --no-owner -h "$PGSOCK" -U relay -d relay "$dump" >&2; then
                say "restore finished; the old files stay at $NEED_RESTORE"
            else
                say "RESTORE REPORTED ERRORS (above); the database is up but may be incomplete. The old files stay at $NEED_RESTORE"
            fi
        else
            say "no dump found in $BACKUPS, so the database starts EMPTY; the old Postgres files are kept at $NEED_RESTORE"
        fi
        NEED_RESTORE=""
    fi
    say "Postgres $PGMAJOR is up (unix socket $PGSOCK, no TCP)"
    return 0
}

# The supervisor. Run it in the background (`pg_supervisor &`): it is NOT one of
# the pids the entrypoint's `wait -n` guards, so Postgres coming and going never
# takes the container down. The entrypoint stops it with SIGTERM, after the app
# and Caddy are gone; it then shuts Postgres down with SIGINT and waits for it.
pg_supervisor() {
    set +e
    local delays=(2 5 15 60) idx=0 rc delay started status sleeper=""
    rm -f "$PG_BOOT"

    trap '[ -n "$sleeper" ] && kill "$sleeper" 2>/dev/null; trap - TERM INT; pg_halt; exit 0' TERM INT

    while :; do
        pg_prepare; rc=$?
        if [ "$rc" = 2 ]; then
            : > "$PG_BOOT"
            say "not retrying: that needs a person to fix, then a container restart."
            return 0
        fi
        [ "$rc" = 0 ] && { pg_start; rc=$?; }
        : > "$PG_BOOT"    # the first attempt is settled, one way or the other
        if [ "$rc" = 0 ]; then
            started=$SECONDS
            wait "$PG_PID"; status=$?
            PG_PID=""
            say "Postgres exited unexpectedly (status $status); captions keep running."
            # A server that ran for a while before dying starts the backoff over.
            [ $((SECONDS - started)) -ge 120 ] && idx=0
        fi
        delay=${delays[$idx]}
        [ "$idx" -lt $((${#delays[@]} - 1)) ] && idx=$((idx + 1))
        say "restarting Postgres in ${delay} s"
        sleep "$delay" &
        sleeper=$!
        wait "$sleeper"
        sleeper=""
    done
}
