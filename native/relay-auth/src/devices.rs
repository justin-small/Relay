//! Trusted devices (app/users.py "trusted devices", app/devices.py): a browser an admin marked as trusted holds a token, and
//! only from there may a PIN sign in. Trusting makes the sign-in part of a core device owned by the admin who trusted it.

use sqlx::sqlite::SqliteRow;
use sqlx::{Row, Sqlite, Transaction};

use crate::accounts::clean_name;
use crate::db::{later, now, Auth};
use crate::error::{Refusal, Result};
use crate::perms::User;
use crate::sessions::{hash_token, random_token, valid_token};

const NAME_MAX: usize = 60;
const UA_MAX: usize = 200;
const MAX_PER_USER: i64 = 50;

/// A browser's user agent, tidied to what is stored.
pub fn agent(raw: &str) -> String {
    raw.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(UA_MAX).collect()
}

fn default_name(ua: &str) -> &'static str {
    let ua = ua.to_lowercase();
    for (needle, name) in [("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android phone")] {
        if ua.contains(needle) {
            return name;
        }
    }
    "Browser"
}

fn platform_of(ua: &str) -> &'static str {
    let ua = ua.to_lowercase();
    if ua.contains("iphone") || ua.contains("ipad") {
        "ios"
    } else if ua.contains("android") {
        "android"
    } else {
        "web"
    }
}

/// The trusted device a cookie belongs to.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct Device {
    pub id: i64,
    pub name: String,
}

/// One line of the Users page.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct TrustedDevice {
    pub id: i64,
    pub name: String,
    pub created: String,
    pub created_by: Option<String>,
    pub last_used: Option<String>,
}

const LIST: &str = "select d.id, d.name, d.created_at as created, u.name as created_by, d.last_used \
                    from trusted_devices d left join users u on u.id = d.created_by";

fn trusted(r: &SqliteRow) -> TrustedDevice {
    TrustedDevice {
        id: r.get("id"),
        name: r.get("name"),
        created: r.get("created"),
        created_by: r.get("created_by"),
        last_used: r.get("last_used"),
    }
}

/// Every table with a foreign key to `devices`: each holds a part of a device, in core or in a module that is switched off.
async fn part_tables(tx: &mut Transaction<'_, Sqlite>) -> Result<Vec<String>> {
    let tables: Vec<String> =
        sqlx::query_scalar("select name from sqlite_master where type = 'table' and name not like 'sqlite_%'").fetch_all(&mut **tx).await?;
    let mut out = Vec::new();
    for t in tables {
        let n: i64 = sqlx::query_scalar("select count(*) from pragma_foreign_key_list(?) where \"table\" = 'devices'")
            .bind(&t)
            .fetch_one(&mut **tx)
            .await?;
        if n > 0 {
            out.push(t);
        }
    }
    Ok(out)
}

/// A person at the cap loses their oldest device with no part, if there is one; otherwise the cap holds.
async fn drop_oldest_empty(tx: &mut Transaction<'_, Sqlite>, user_id: i64) -> Result<()> {
    let ids: Vec<i64> = sqlx::query_scalar("select id from devices where user_id = ? order by coalesce(last_seen, created_at), id")
        .bind(user_id)
        .fetch_all(&mut **tx)
        .await?;
    let tables = part_tables(tx).await?;
    for id in ids {
        let mut parts = 0i64;
        for t in &tables {
            // `t` is a table name read from sqlite_master above, never text from a request
            let n: i64 = sqlx::query_scalar(sqlx::AssertSqlSafe(format!("select count(*) from \"{}\" where device_id = ?", t.replace('"', "\"\""))))
                .bind(id)
                .fetch_one(&mut **tx)
                .await?;
            parts += n;
        }
        if parts == 0 {
            sqlx::query("delete from devices where id = ?").bind(id).execute(&mut **tx).await?;
            return Ok(());
        }
    }
    Err(Refusal::NotAllowed.into())
}

/// The core device a new sign-in part belongs to: this user's device with the same user agent that has no sign-in part yet,
/// or a new one.
async fn signin_device(tx: &mut Transaction<'_, Sqlite>, user_id: i64, name: &str, user_agent: &str) -> Result<i64> {
    let ua = agent(user_agent);
    if !ua.is_empty() {
        let free: Option<i64> = sqlx::query_scalar(
            "select id from devices where user_id = ? and user_agent = ? \
             and not exists (select 1 from trusted_devices t where t.device_id = devices.id) order by id limit 1",
        )
        .bind(user_id)
        .bind(&ua)
        .fetch_optional(&mut **tx)
        .await?;
        if let Some(id) = free {
            return Ok(id);
        }
    }
    let count: i64 = sqlx::query_scalar("select count(*) from devices where user_id = ?").bind(user_id).fetch_one(&mut **tx).await?;
    if count >= MAX_PER_USER {
        drop_oldest_empty(tx, user_id).await?;
    }
    let label: String = name.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(NAME_MAX).collect();
    let label = if label.trim().is_empty() { default_name(&ua).to_string() } else { label.trim().to_string() };
    Ok(sqlx::query_scalar(
        "insert into devices (user_id, name, platform, user_agent, last_seen, created_at) values (?, ?, ?, ?, ?, ?) returning id",
    )
    .bind(user_id)
    .bind(label)
    .bind(platform_of(&ua))
    .bind(&ua)
    .bind(now())
    .bind(now())
    .fetch_one(&mut **tx)
    .await?)
}

impl Auth {
    /// Trust this browser. Returns the cookie token (shown once; only its sha256 is stored) and the new line.
    pub async fn trust_device(&self, name: &str, actor: &User, user_agent: &str) -> Result<(String, TrustedDevice)> {
        let name = clean_name(name, Refusal::BadDeviceName)?;
        let token = random_token();
        let mut tx = self.pool.begin().await?;
        let core = signin_device(&mut tx, actor.id, &name, user_agent).await?;
        let id: i64 = sqlx::query_scalar(
            "insert into trusted_devices (name, token_hash, created_by, device_id) values (?, ?, ?, ?) returning id",
        )
        .bind(&name)
        .bind(hash_token(&token))
        .bind(actor.id)
        .bind(core)
        .fetch_one(&mut *tx)
        .await?;
        let row = sqlx::query(sqlx::AssertSqlSafe(format!("{LIST} where d.id = ?"))).bind(id).fetch_one(&mut *tx).await?;
        tx.commit().await?;
        Ok((token, trusted(&row)))
    }

    /// The trusted device a cookie token belongs to, noting that it was used. The note is refreshed at most every ten minutes:
    /// this runs on every admin page load from a trusted browser, and a read should not cost a write each time.
    pub async fn device_for(&self, token: &str) -> Result<Option<Device>> {
        if !valid_token(token) {
            return Ok(None);
        }
        let row = sqlx::query(
            "select id, name, device_id, (last_used is null or last_used < ?) as stale from trusted_devices where token_hash = ?",
        )
        .bind(later(-600))
        .bind(hash_token(token))
        .fetch_optional(&self.pool)
        .await?;
        let Some(row) = row else { return Ok(None) };
        let id: i64 = row.get("id");
        if row.get::<i64, _>("stale") != 0 {
            sqlx::query("update trusted_devices set last_used = ? where id = ?").bind(now()).bind(id).execute(&self.pool).await?;
            if let Some(core) = row.get::<Option<i64>, _>("device_id") {
                sqlx::query("update devices set last_seen = ? where id = ?").bind(now()).bind(core).execute(&self.pool).await?;
            }
        }
        Ok(Some(Device { id, name: row.get("name") }))
    }

    pub async fn list_devices(&self) -> Result<Vec<TrustedDevice>> {
        let rows = sqlx::query(sqlx::AssertSqlSafe(format!("{LIST} order by d.created_at, d.id"))).fetch_all(&self.pool).await?;
        Ok(rows.iter().map(trusted).collect())
    }

    /// Forget a device; the PIN sessions that came from it end with it (cascade).
    pub async fn revoke_device(&self, id: i64) -> Result<()> {
        let n = sqlx::query("delete from trusted_devices where id = ?").bind(id).execute(&self.pool).await?.rows_affected();
        if n == 0 { Err(Refusal::NotFound.into()) } else { Ok(()) }
    }
}
