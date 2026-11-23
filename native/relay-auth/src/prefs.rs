//! A person's own preferences (`app/prefs.py`): read here because `/api/admin/me` sends them with who is signed in. Writing
//! them stays with the Python app until its routes are ported.

use serde_json::{Map, Value};
use sqlx::Row;

use crate::db::Auth;
use crate::error::Result;

impl Auth {
    /// Every preference this user has, as `{scope: {name: value}}`. A value that is not valid JSON reads as null.
    pub async fn all_prefs(&self, user_id: i64) -> Result<Value> {
        let rows = sqlx::query("select scope, name, value from user_prefs where user_id = ? order by scope, name")
            .bind(user_id)
            .fetch_all(&self.pool)
            .await?;
        let mut out = Map::new();
        for r in rows {
            let scope: String = r.get("scope");
            let name: String = r.get("name");
            let value: String = r.get("value");
            let entry = out.entry(scope).or_insert_with(|| Value::Object(Map::new()));
            if let Value::Object(m) = entry {
                m.insert(name, serde_json::from_str(&value).unwrap_or(Value::Null));
            }
        }
        Ok(Value::Object(out))
    }
}
