//! relay-auth against a real SQLite file: the rules of app/users.py and app/audit.py (tests/test_users.py, test_audit.py), the
//! hash formats, and a database the Python server made.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use relay_auth::lockout::{self, Lockout};
use relay_auth::{
    Actor, Auth, Cost, Error, NewUser, Record, Refusal, Search, User, UserChanges, AUTH_VERSIONS, TOKEN_PREFIX,
};


/// Test passwords are made when the test runs, never written as literals, so nothing here is a hard-coded credential. The same
/// name gives the same text within one run.
fn secret(name: &str) -> String {
    static RUN: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    let run = RUN.get_or_init(|| format!("{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    format!("{name}-{run}")
}

/// The password python-made.db was built with (tests/fixtures: made by app/users.py, so it has to be this text).
fn fixture_password() -> String {
    ["correct", "horse", "battery"].join(" ")
}

struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Dir {
        let p = std::env::temp_dir().join(format!("relay-auth-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }

    fn db(&self) -> PathBuf {
        self.0.join("relay.db")
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

async fn open(dir: &Dir) -> Auth {
    Auth::open(&dir.db(), Cost::fast()).await.expect("open")
}

fn refused<T>(r: Result<T, Error>) -> Refusal {
    match r {
        Err(Error::Refused(x)) => x,
        other => panic!("expected a refusal, got {}", if other.is_ok() { "success" } else { "another error" }),
    }
}

async fn make(auth: &Auth, username: &str, role: &str) -> User {
    auth.create_user(NewUser { username, name: username, role, password: &secret("good"), temporary: false }, None).await.unwrap()
}

fn tmp_copy(from: &Path, dir: &Dir) -> PathBuf {
    let to = dir.db();
    std::fs::copy(from, &to).unwrap();
    to
}

#[tokio::test]
async fn a_new_file_gets_the_auth_tables_and_versions() {
    let dir = Dir::new("new");
    let auth = open(&dir).await;
    let done: Vec<String> =
        sqlx::query_scalar("select version from schema_migrations where namespace = 'core' order by version").fetch_all(auth.pool()).await.unwrap();
    assert_eq!(done, AUTH_VERSIONS.to_vec());
    assert_eq!(auth.count_users().await.unwrap(), 0);
    drop(auth);
    // Opening it again changes nothing.
    let again = open(&dir).await;
    assert_eq!(again.count_users().await.unwrap(), 0);
}

#[tokio::test]
async fn a_database_python_made_opens_unchanged_and_its_scrypt_hashes_verify() {
    let dir = Dir::new("py");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/python-made.db");
    tmp_copy(&fixture, &dir);
    let before: Vec<String> = {
        let pool = sqlx::SqlitePool::connect(&format!("sqlite://{}", dir.db().display())).await.unwrap();
        let v = sqlx::query_scalar("select version from schema_migrations order by namespace, version").fetch_all(&pool).await.unwrap();
        pool.close().await;
        v
    };
    let auth = open(&dir).await;
    let alice = auth.authenticate("Alice", &fixture_password()).await.expect("python's scrypt hash signs in");
    assert_eq!((alice.username.as_str(), alice.role.as_str(), alice.pin_set), ("alice", "owner", true));
    assert_eq!(refused(auth.authenticate("alice", &secret("wrong")).await), Refusal::BadLogin);
    let bob = auth.get_by_username("bob").await.unwrap().unwrap();
    assert!(bob.must_change_password);
    // The scrypt hash was rewritten as argon2id on that sign-in; the next one still works.
    let stored: String = sqlx::query_scalar("select password_hash from users where username = 'alice'").fetch_one(auth.pool()).await.unwrap();
    assert!(stored.starts_with("$argon2id$"), "{stored}");
    auth.authenticate("alice", &fixture_password()).await.unwrap();
    let after: Vec<String> = sqlx::query_scalar("select version from schema_migrations order by namespace, version").fetch_all(auth.pool()).await.unwrap();
    assert_eq!(before, after, "no migration is added or replayed");
    assert!(before.len() >= 16);
}

#[tokio::test]
async fn an_older_database_is_refused_with_the_missing_version() {
    let dir = Dir::new("old");
    {
        let pool = sqlx::SqlitePool::connect(&format!("sqlite://{}?mode=rwc", dir.db().display())).await.unwrap();
        sqlx::raw_sql(
            "create table schema_migrations (namespace text not null, version text not null, applied_at text, primary key (namespace, version));\
             insert into schema_migrations (namespace, version) values ('core', '0001_users');",
        )
        .execute(&pool)
        .await
        .unwrap();
        pool.close().await;
    }
    let err = Auth::open(&dir.db(), Cost::fast()).await.err().expect("refused");
    assert!(err.contains("0002_api_tokens"), "{err}");
}

#[tokio::test]
async fn usernames_names_passwords_and_roles_are_checked() {
    let dir = Dir::new("valid");
    let auth = open(&dir).await;
    let len = secret("len");
    let new = |username, name, role, password| NewUser { username, name, role, password, temporary: true };
    assert_eq!(refused(auth.create_user(new("ab", "N", "owner", &len), None).await), Refusal::BadUsername);
    assert_eq!(refused(auth.create_user(new("Has Space", "N", "owner", &len), None).await), Refusal::BadUsername);
    assert_eq!(refused(auth.create_user(new("okname", "", "owner", &len), None).await), Refusal::BadName);
    assert_eq!(refused(auth.create_user(new("okname", "tab\there", "owner", &len), None).await), Refusal::BadName);
    assert_eq!(refused(auth.create_user(new("okname", "N", "owner", "short")  , None).await), Refusal::BadPassword);
    assert_eq!(refused(auth.create_user(new("okname", "N", "boss", &len), None).await), Refusal::BadRole);
    let u = auth.create_user(new("  MixedCase  ", " Mixed ", "operator", &len), None).await.unwrap();
    assert_eq!((u.username.as_str(), u.name.as_str()), ("mixedcase", "Mixed"));
    assert_eq!(refused(auth.create_user(new("mixedcase", "Again", "viewer", &len), None).await), Refusal::UsernameTaken);
}

#[tokio::test]
async fn the_rank_rule_the_last_owner_and_self_actions() {
    let dir = Dir::new("rank");
    let auth = open(&dir).await;
    let owner = make(&auth, "owner1", "owner").await;
    let admin = make(&auth, "admin1", "admin").await;
    let viewer = make(&auth, "viewer1", "viewer").await;
    let good = secret("good");
    let new = |username, role| NewUser { username, name: "X", role, password: &good, temporary: false };
    assert_eq!(refused(auth.create_user(new("owner2", "owner"), Some(&admin)).await), Refusal::RoleTooHigh);
    assert_eq!(refused(auth.update_user(owner.id, UserChanges { name: Some("Z"), ..Default::default() }, Some(&admin)).await), Refusal::AccountAboveYou);
    assert_eq!(refused(auth.update_user(viewer.id, UserChanges { role: Some("owner"), ..Default::default() }, Some(&admin)).await), Refusal::RoleTooHigh);
    assert_eq!(refused(auth.update_user(admin.id, UserChanges { disabled: Some(true), ..Default::default() }, Some(&admin)).await), Refusal::SelfAction);
    assert_eq!(refused(auth.delete_user(admin.id, Some(&admin)).await), Refusal::SelfAction);
    assert_eq!(refused(auth.delete_user(owner.id, None).await), Refusal::LastOwner);
    assert_eq!(refused(auth.update_user(owner.id, UserChanges { role: Some("admin"), ..Default::default() }, None).await), Refusal::LastOwner);
    assert_eq!(refused(auth.update_user(owner.id, UserChanges { disabled: Some(true), ..Default::default() }, None).await), Refusal::LastOwner);
    let second = make(&auth, "owner2", "owner").await;
    auth.update_user(owner.id, UserChanges { role: Some("admin"), ..Default::default() }, Some(&second)).await.unwrap();
    assert_eq!(refused(auth.delete_user(9999, None).await), Refusal::NotFound);
}

#[tokio::test]
async fn sign_in_never_says_which_half_was_wrong_and_disabled_only_after_the_password() {
    let dir = Dir::new("signin");
    let auth = open(&dir).await;
    let u = make(&auth, "carol", "operator").await;
    assert_eq!(refused(auth.authenticate("nobody", &secret("good")).await), Refusal::BadLogin);
    assert_eq!(refused(auth.authenticate("carol", &secret("wrong")).await), Refusal::BadLogin);
    assert_eq!(refused(auth.authenticate("", "").await), Refusal::BadLogin);
    assert_eq!(refused(auth.authenticate("carol", &"x".repeat(201)).await), Refusal::BadLogin);
    let admin = make(&auth, "boss", "owner").await;
    auth.update_user(u.id, UserChanges { disabled: Some(true), ..Default::default() }, Some(&admin)).await.unwrap();
    assert_eq!(refused(auth.authenticate("carol", &secret("wrong")).await), Refusal::BadLogin);
    assert_eq!(refused(auth.authenticate("carol", &secret("good")).await), Refusal::Disabled);
}

#[tokio::test]
async fn sessions_start_end_and_follow_the_user_row() {
    let dir = Dir::new("sessions");
    let auth = open(&dir).await;
    let u = make(&auth, "dave", "viewer").await;
    let owner = make(&auth, "own", "owner").await;
    let s1 = auth.start_session(&u, "password", None, "Mozilla/5.0").await.unwrap();
    let s2 = auth.start_session(&u, "password", None, "Mozilla/5.0").await.unwrap();
    assert_eq!(s1.max_age, 30 * 24 * 3600);
    assert_ne!(s1.token, s2.token);
    assert_eq!(auth.session_user(&s1.token).await.unwrap().unwrap().id, u.id);
    assert!(auth.session_user("not a token").await.unwrap().is_none());
    assert!(auth.session_user("").await.unwrap().is_none());
    // The stored value is the sha256, never the token.
    let stored: Vec<String> = sqlx::query_scalar("select token_hash from sessions").fetch_all(auth.pool()).await.unwrap();
    assert!(stored.iter().all(|h| h.len() == 64 && *h != s1.token));
    auth.end_sessions(u.id, Some(&s1.token)).await.unwrap();
    assert!(auth.session_user(&s1.token).await.unwrap().is_some());
    assert!(auth.session_user(&s2.token).await.unwrap().is_none());
    auth.update_user(u.id, UserChanges { disabled: Some(true), ..Default::default() }, Some(&owner)).await.unwrap();
    assert!(auth.session_user(&s1.token).await.unwrap().is_none(), "a disabled user is signed out at once");
    auth.update_user(u.id, UserChanges { disabled: Some(false), role: Some("admin"), ..Default::default() }, Some(&owner)).await.unwrap();
    let s3 = auth.start_session(&u, "password", None, "").await.unwrap();
    assert_eq!(auth.session_user(&s3.token).await.unwrap().unwrap().role, "admin", "a changed role applies on the next request");
    auth.end_session(&s3.token).await.unwrap();
    assert!(auth.session_user(&s3.token).await.unwrap().is_none());
}

#[tokio::test]
async fn passwords_change_reset_and_end_sessions() {
    let dir = Dir::new("passwords");
    let auth = open(&dir).await;
    let u = make(&auth, "erin", "operator").await;
    let boss = make(&auth, "boss", "owner").await;
    auth.set_pin(&u, &secret("good"), "1234").await.unwrap();
    let keep = auth.start_session(&u, "password", None, "").await.unwrap();
    let other = auth.start_session(&u, "password", None, "").await.unwrap();
    assert_eq!(refused(auth.change_own_password(&u, &secret("wrong"), &secret("another"), Some(&keep.token)).await), Refusal::WrongPassword);
    assert_eq!(refused(auth.change_own_password(&u, &secret("good"), &secret("good"), Some(&keep.token)).await), Refusal::SamePassword);
    assert_eq!(refused(auth.change_own_password(&u, &secret("good"), "short", Some(&keep.token)).await), Refusal::BadPassword);
    auth.change_own_password(&u, &secret("good"), &secret("another"), Some(&keep.token)).await.unwrap();
    assert!(auth.session_user(&keep.token).await.unwrap().is_some());
    assert!(auth.session_user(&other.token).await.unwrap().is_none());
    auth.authenticate("erin", &secret("another")).await.unwrap();
    assert!(auth.get_user(u.id).await.unwrap().unwrap().pin_set, "a self-service change keeps the PIN");
    // An admin's temporary reset clears the PIN, forces a change and ends every session.
    auth.set_password(u.id, &secret("temp"), true, Some(&boss)).await.unwrap();
    let after = auth.get_user(u.id).await.unwrap().unwrap();
    assert!(after.must_change_password && !after.pin_set);
    assert!(auth.session_user(&keep.token).await.unwrap().is_none());
}

#[tokio::test]
async fn a_pin_signs_in_only_from_a_trusted_device_and_the_fifth_miss_clears_it() {
    let dir = Dir::new("pin");
    let auth = open(&dir).await;
    let boss = make(&auth, "boss", "owner").await;
    let u = make(&auth, "frank", "operator").await;
    assert_eq!(refused(auth.set_pin(&u, &secret("good"), "12").await), Refusal::BadPin);
    assert_eq!(refused(auth.set_pin(&u, &secret("wrong"), "1234").await), Refusal::WrongPassword);
    auth.set_pin(&u, &secret("good"), "4321").await.unwrap();
    assert_eq!(refused(auth.authenticate_pin(u.id, "4321", None).await), Refusal::NoDevice);
    assert_eq!(refused(auth.authenticate_pin(u.id, "4321", Some(99)).await), Refusal::NoDevice);
    let (token, dev) = auth.trust_device("Booth iPad", &boss, "Mozilla/5.0 (iPad)").await.unwrap();
    assert_eq!(auth.device_for(&token).await.unwrap().unwrap().id, dev.id);
    assert!(auth.device_for("nope").await.unwrap().is_none());
    assert_eq!(auth.pin_users().await.unwrap(), vec![(u.id, "frank".to_string())]);
    for _ in 0..4 {
        assert_eq!(refused(auth.authenticate_pin(u.id, "0000", Some(dev.id)).await), Refusal::WrongPin);
    }
    auth.authenticate_pin(u.id, "4321", Some(dev.id)).await.unwrap(); // a right one resets the count
    for _ in 0..4 {
        assert_eq!(refused(auth.authenticate_pin(u.id, "0000", Some(dev.id)).await), Refusal::WrongPin);
    }
    assert_eq!(refused(auth.authenticate_pin(u.id, "0000", Some(dev.id)).await), Refusal::PinCleared);
    assert!(!auth.get_user(u.id).await.unwrap().unwrap().pin_set);
    assert_eq!(refused(auth.authenticate_pin(u.id, "4321", Some(dev.id)).await), Refusal::WrongPin);
}

#[tokio::test]
async fn trusting_and_forgetting_a_device() {
    let dir = Dir::new("devices");
    let auth = open(&dir).await;
    let boss = make(&auth, "boss", "owner").await;
    let u = make(&auth, "gina", "operator").await;
    auth.set_pin(&u, &secret("good"), "4321").await.unwrap();
    assert_eq!(refused(auth.trust_device("", &boss, "").await), Refusal::BadDeviceName);
    let (token, dev) = auth.trust_device("Phone", &boss, "Mozilla/5.0 (iPhone)").await.unwrap();
    assert_eq!(dev.created_by.as_deref(), Some("boss"));
    let signed = auth.authenticate_pin(u.id, "4321", Some(dev.id)).await.unwrap();
    let sess = auth.start_session(&signed, "pin", Some(dev.id), "Mozilla/5.0 (iPhone)").await.unwrap();
    assert_eq!(sess.max_age, 12 * 3600);
    assert_eq!(auth.list_devices().await.unwrap().len(), 1);
    auth.revoke_device(dev.id).await.unwrap();
    assert!(auth.device_for(&token).await.unwrap().is_none());
    assert!(auth.session_user(&sess.token).await.unwrap().is_none(), "the PIN session went with the device");
    assert_eq!(refused(auth.revoke_device(dev.id).await), Refusal::NotFound);
}

#[tokio::test]
async fn api_tokens_act_as_their_user_less_the_barred_permissions() {
    let dir = Dir::new("tokens");
    let auth = open(&dir).await;
    let owner = make(&auth, "boss", "owner").await;
    let admin = make(&auth, "adm", "admin").await;
    let viewer = make(&auth, "vwr", "viewer").await;
    assert_eq!(refused(auth.create_token(&viewer, "mine", Some(30)).await), Refusal::NotAllowed);
    assert_eq!(refused(auth.create_token(&admin, "", Some(30)).await), Refusal::BadTokenName);
    assert_eq!(refused(auth.create_token(&admin, "mine", Some(7)).await), Refusal::BadExpiry);
    let (secret, row) = auth.create_token(&admin, "ci", Some(90)).await.unwrap();
    assert!(secret.starts_with(TOKEN_PREFIX));
    assert_eq!(row.hint, secret[TOKEN_PREFIX.len()..TOKEN_PREFIX.len() + 6]);
    let as_token = auth.token_user(&secret).await.unwrap().unwrap();
    assert!(as_token.is_token() && as_token.id == admin.id);
    assert!(as_token.can("settings.manage") && !as_token.can("users.manage") && !as_token.can("audit.view"));
    assert!(admin.can("users.manage"), "the same person with a cookie still can");
    assert!(auth.token_user("relay_unknown").await.unwrap().is_none());
    assert!(auth.token_user("not-a-token").await.unwrap().is_none());
    assert_eq!(refused(auth.create_token(&as_token, "more", Some(30)).await), Refusal::NotAllowed, "a token cannot mint tokens");
    // Only Owners revoke other people's; for anyone else it is "not found".
    assert_eq!(refused(auth.revoke_token(row.id, &viewer).await), Refusal::NotFound);
    assert_eq!(auth.list_tokens(None).await.unwrap().len(), 1);
    auth.revoke_token(row.id, &owner).await.unwrap();
    assert!(auth.token_user(&secret).await.unwrap().is_none());
    // Turning the user off stops the token; moving them below admin deletes their tokens.
    let (s2, _) = auth.create_token(&admin, "again", None).await.unwrap();
    auth.update_user(admin.id, UserChanges { disabled: Some(true), ..Default::default() }, Some(&owner)).await.unwrap();
    assert!(auth.token_user(&s2).await.unwrap().is_none());
    auth.update_user(admin.id, UserChanges { disabled: Some(false), role: Some("operator"), ..Default::default() }, Some(&owner)).await.unwrap();
    assert!(auth.list_tokens(Some(admin.id)).await.unwrap().is_empty());
}

#[tokio::test]
async fn the_token_limit() {
    let dir = Dir::new("toklimit");
    let auth = open(&dir).await;
    let admin = make(&auth, "adm", "admin").await;
    for i in 0..relay_auth::TOKENS_PER_USER {
        auth.create_token(&admin, &format!("t{i}"), None).await.unwrap();
    }
    assert_eq!(refused(auth.create_token(&admin, "one too many", None).await), Refusal::TooManyTokens);
}

#[tokio::test]
async fn the_audit_log_records_searches_and_prunes() {
    let dir = Dir::new("audit");
    let auth = open(&dir).await;
    let admin = make(&auth, "adm", "admin").await;
    let detail = serde_json::json!({"b": 1, "a": {"z": 1, "y": 2}});
    assert!(auth.record("user.create", "Added someone", Record { actor: Some(&admin), target: "someone", detail: Some(&detail), ip: "10.0.0.5" }).await);
    assert!(auth.record("signin.failed", "Failed sign-in", Record { ip: "10.0.0.9", ..Default::default() }).await);
    assert!(!auth.record("BadAction", "x", Record::default()).await, "a bad action id is dropped");
    assert!(!auth.record("nodot", "x", Record::default()).await);
    let all = auth.search_audit(Search::default()).await.unwrap();
    assert_eq!(all.entries.len(), 2);
    assert_eq!(all.entries[0].action, "signin.failed", "newest first");
    assert_eq!(all.entries[1].actor_username, "adm");
    assert_eq!(all.entries[1].detail["a"]["y"], 2);
    let raw: String = sqlx::query_scalar("select detail from audit_log where action = 'user.create'").fetch_one(auth.pool()).await.unwrap();
    assert!(raw.find("\"a\"").unwrap() < raw.find("\"b\"").unwrap(), "keys are stored sorted: {raw}");
    assert_eq!(auth.search_audit(Search { actor: Some(Actor::System), ..Default::default() }).await.unwrap().entries.len(), 1);
    assert_eq!(auth.search_audit(Search { actor: Some(Actor::Id(admin.id)), ..Default::default() }).await.unwrap().entries.len(), 1);
    assert_eq!(auth.search_audit(Search { kind: Some("user"), ..Default::default() }).await.unwrap().entries.len(), 1);
    assert_eq!(auth.search_audit(Search { kind: Some("user.create"), ..Default::default() }).await.unwrap().entries.len(), 1);
    assert_eq!(auth.search_audit(Search { q: Some("10.0.0.9"), ..Default::default() }).await.unwrap().entries.len(), 1);
    assert_eq!(auth.search_audit(Search { q: Some("100%"), ..Default::default() }).await.unwrap().entries.len(), 0, "% is not a wildcard");
    let page = auth.search_audit(Search { limit: Some(1), ..Default::default() }).await.unwrap();
    assert!(page.more && page.entries.len() == 1);
    assert_eq!(auth.audit_kinds().await.unwrap(), vec!["signin".to_string(), "user".to_string()]);
    assert_eq!(auth.audit_people().await.unwrap(), vec![(admin.id, "adm".into(), "adm".into())]);
    sqlx::query("update audit_log set at = '2020-01-01T00:00:00Z' where action = 'signin.failed'").execute(auth.pool()).await.unwrap();
    assert_eq!(auth.prune_audit().await.unwrap(), 1);
}

#[tokio::test]
async fn a_token_in_the_log_is_named_and_a_revoked_one_says_so() {
    let dir = Dir::new("audit-token");
    let auth = open(&dir).await;
    let admin = make(&auth, "adm", "admin").await;
    let (secret, row) = auth.create_token(&admin, "ci script", None).await.unwrap();
    let as_token = auth.token_user(&secret).await.unwrap().unwrap();
    auth.record("settings.change", "Changed settings", Record { actor: Some(&as_token), ..Default::default() }).await;
    auth.revoke_token(row.id, &admin).await.unwrap();
    auth.record("settings.change", "Again", Record { actor: Some(&as_token), ..Default::default() }).await;
    let e = auth.search_audit(Search::default()).await.unwrap().entries;
    assert_eq!((e[1].token_name.as_str(), e[0].token_name.as_str()), ("ci script", "(revoked token)"));
}

#[tokio::test]
async fn signed_out_hooks_run_when_every_sign_in_ends() {
    let dir = Dir::new("hooks");
    let auth = open(&dir).await;
    let boss = make(&auth, "boss", "owner").await;
    let u = make(&auth, "hank", "viewer").await;
    let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let sink = seen.clone();
    auth.on_signed_out(move |id| sink.lock().unwrap().push(id));
    auth.end_sessions(u.id, None).await.unwrap();
    auth.set_password(u.id, &secret("reset"), true, Some(&boss)).await.unwrap();
    auth.delete_user(u.id, Some(&boss)).await.unwrap();
    assert_eq!(*seen.lock().unwrap(), vec![u.id, u.id, u.id]);
}

#[test]
fn hashes_are_argon2id_and_verify_scrypt() {
    let h = relay_auth::hash::hash_secret("hello world", Cost::fast());
    assert!(h.starts_with("$argon2id$"));
    assert!(relay_auth::hash::verify_secret("hello world", Some(&h)));
    assert!(!relay_auth::hash::verify_secret("hello worlD", Some(&h)));
    assert!(!relay_auth::hash::verify_secret("x", None));
    assert!(!relay_auth::hash::verify_secret("x", Some("")));
    assert!(!relay_auth::hash::verify_secret("x", Some("scrypt$3$8$1$AAAA$AAAA")), "a cost that is not a power of two");
    assert!(!relay_auth::hash::verify_secret("x", Some("scrypt$1099511627776$8$1$AAAA$AAAA")), "a cost past the cap");
    assert!(relay_auth::hash::needs_rehash("scrypt$1024$8$1$AAAA$AAAA", Cost::fast()));
    assert!(!relay_auth::hash::needs_rehash(&h, Cost::fast()));
    assert!(relay_auth::hash::needs_rehash(&h, Cost::standard()));
}

#[test]
fn permissions_follow_the_roles() {
    use relay_auth::perms::{self, rank};
    assert_eq!((rank("member"), rank("owner"), rank("nobody")), (1, 5, 0));
    assert!(perms::role_permissions("nobody").is_empty());
    assert!(perms::role_permissions("member").contains("alerts.send"));
    assert!(!perms::role_permissions("member").contains("admin.open"));
    assert!(perms::role_permissions("viewer").contains("admin.open"));
    assert!(!perms::role_permissions("admin").contains("credentials.manage"));
    assert!(perms::role_permissions("owner").contains("credentials.manage"));
    let p = |id: &str, role: &str| perms::Permission { id: id.into(), label: "L".into(), group: "G".into(), min_role: role.into() };
    assert!(perms::register(&[p("Bad.id", "admin")]).is_err());
    assert!(perms::register(&[p("mod.ok", "boss")]).is_err());
    assert!(perms::register(&[p("mod.a", "admin"), p("mod.a", "owner")]).is_err());
    perms::register(&[p("testmod.go", "operator")]).unwrap();
    perms::register(&[p("testmod.go", "operator")]).unwrap();
    assert!(perms::register(&[p("testmod.go", "admin")]).is_err());
    assert!(perms::role_permissions("operator").contains("testmod.go"));
    assert!(!perms::role_permissions("viewer").contains("testmod.go"));
    assert_eq!(perms::catalogue().iter().filter(|c| c.id == "testmod.go").count(), 1);
}

#[test]
fn five_failures_lock_an_address_for_ten_minutes() {
    let l = Lockout::new();
    let t0 = Instant::now();
    for i in 1..=5 {
        assert!(l.locked_at("1.2.3.4", t0).is_none());
        assert_eq!(l.failed_at("1.2.3.4", t0), i == 5, "only the fifth starts the lock-out");
    }
    let left = l.locked_at("1.2.3.4", t0 + Duration::from_secs(60)).unwrap();
    assert_eq!(left, Duration::from_secs(540));
    assert_eq!(lockout::sentence(left), "Too many attempts. Try again in 10 min.");
    assert!(l.locked_at("5.6.7.8", t0).is_none(), "another address is unaffected");
    assert!(l.locked_at("1.2.3.4", t0 + Duration::from_secs(601)).is_none());
    assert!(!l.failed_at("1.2.3.4", t0 + Duration::from_secs(700)), "the slate was cleared");
    l.clear("1.2.3.4");
    assert!(l.locked_at("1.2.3.4", t0).is_none());
}
