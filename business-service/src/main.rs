use anyhow::{Context, Result, bail};
use axum::{
    Json, Router,
    body::Body,
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{delete, get, post},
};
use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use reqwest::Client;
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    env, fs,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use uuid::Uuid;
mod admin_tools;
mod catalog;
mod onboarding;
mod unanswered;
mod leads;
mod admin_pages;

#[derive(Clone, Deserialize)]
struct Account {
    id: String,
    merchant_id: String,
    #[serde(default)]
    douyin_nickname: String,
    industry: String,
    fastgpt_app_id: String,
    fastgpt_key_env: String,
    #[serde(default = "default_window")]
    reply_window_seconds: i64,
}
fn stored_account(db: &Connection, account_id: &str) -> rusqlite::Result<Option<Account>> {
    db.query_row(
        "SELECT id,merchant_id,douyin_nickname,industry,fastgpt_app_id,reply_window_seconds FROM merchant_accounts WHERE id=?1",
        [account_id],
        |row| Ok(Account {
            id: row.get(0)?, merchant_id: row.get(1)?, douyin_nickname: row.get(2)?,
            industry: row.get(3)?, fastgpt_app_id: row.get(4)?,
            fastgpt_key_env: "FASTGPT_APP_KEY".into(), reply_window_seconds: row.get(5)?,
        }),
    ).optional()
}
fn active_account(app: &App, account_id: &str) -> Result<Option<Account>> {
    let db = app.db.lock().unwrap();
    let removed: bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM removed_accounts WHERE id=?1)",
        [account_id], |row| row.get(0),
    )?;
    if removed { return Ok(None); }
    Ok(stored_account(&db, account_id)?.or_else(|| app.accounts.get(account_id).cloned()))
}
fn merchant_paused_for_account(db: &Connection, account_id: &str) -> rusqlite::Result<bool> {
    db.query_row(
        "SELECT EXISTS(SELECT 1 FROM merchant_accounts a JOIN merchants m ON m.id=a.merchant_id WHERE a.id=?1 AND m.bot_paused=1)",
        [account_id], |row| row.get(0),
    )
}
fn default_window() -> i64 {
    86400
}
const MAX_REPLY_ROUNDS: i64 = 50;

fn correct_staff_title(reply: &str, industry: &str) -> String {
    if industry == "教育教培" {
        return reply.to_owned();
    }
    ["店里的老师", "店里老师", "门店老师", "店内老师"]
        .into_iter().fold(reply.to_owned(), |text, title| text.replace(title, "店里工作人员"))
}

#[cfg(test)]
mod staff_title_tests {
    use super::correct_staff_title;

    #[test]
    fn preserves_education_and_corrects_other_industries() {
        let reply = "价格要店里老师确认，方便留个电话吗？";
        assert_eq!(correct_staff_title(reply, "教育教培"), reply);
        assert_eq!(correct_staff_title(reply, "家政服务"),
                   "价格要店里工作人员确认，方便留个电话吗？");
        assert_eq!(correct_staff_title("门店老师稍后答复", "汽车用品"),
                   "店里工作人员稍后答复");
    }
}

fn split_reply(text: &str) -> Vec<String> {
    let text = text.trim();
    let length = text.chars().count();
    if length <= 50 {
        return vec![text.to_owned()];
    }
    let midpoint = length / 2;
    let mut best: Option<(usize, usize)> = None;
    for (index, (byte, character)) in text.char_indices().enumerate() {
        let boundary = index + 1;
        if boundary < 15 || length - boundary < 12 {
            continue;
        }
        if !"。！？!?；;，,".contains(character) {
            continue;
        }
        let score = boundary.abs_diff(midpoint) + if "，,".contains(character) { 10 } else { 0 };
        if best.is_none_or(|(_, old_score)| score < old_score) {
            best = Some((byte + character.len_utf8(), score));
        }
    }
    let boundary = best.map(|(byte, _)| byte).unwrap_or_else(|| {
        text.char_indices().nth(midpoint).map_or(text.len(), |(byte, _)| byte)
    });
    let first = text[..boundary].trim();
    let second = text[boundary..].trim();
    if first.is_empty() || second.is_empty() {
        vec![text.to_owned()]
    } else {
        vec![first.to_owned(), second.to_owned()]
    }
}

fn solicitation_reply(round: i64) -> &'static str {
    const PHRASES: [&str; 4] = [
        "方便留个联系方式吗？",
        "您愿意留个联系方式，方便我们和您沟通吗？",
        "留个联系方式吧，店里工作人员好和您进一步沟通。",
        "方便留个联系方式，让我们再和您沟通吗？",
    ];
    PHRASES[round.rem_euclid(PHRASES.len() as i64) as usize]
}

fn last_contact_request(db: &Connection, conversation: &str) -> Result<bool> {
    let last: Option<String> = db.query_row(
        "SELECT text FROM outbox WHERE conversation_id=?1 AND source IN ('bot','bot_handoff')
         AND status IN ('accepted','mock_sent') ORDER BY rowid DESC LIMIT 1",
        [conversation], |row| row.get(0),
    ).optional()?;
    Ok(last.is_some_and(|text| text.contains("留个联系方式")))
}

fn clear_contact_promise(text: &str, last_asked: bool) -> bool {
    if !last_asked { return false; }
    let text: String = text.chars().filter(|ch| !ch.is_whitespace()).collect();
    let sends = ["发给你", "发给您", "发你", "发您", "发过来", "给你", "给您", "留给你", "留给您"]
        .iter().any(|word| text.contains(word));
    let agrees = ["行", "好", "可以", "没问题", "嗯", "恩", "会的", "等我", "稍后", "待会", "等下", "一会", "回头"]
        .iter().any(|word| text.starts_with(word));
    let uncertain = ["不发", "不给", "不留", "不能给", "不方便给", "可能", "也许", "看情况", "考虑"]
        .iter().any(|word| text.contains(word));
    let other_material = ["地址", "位置", "照片", "图片", "资料", "报价", "需求"]
        .iter().any(|word| text.contains(word));
    let contact = ["联系方式", "联系信息", "号码", "手机号", "微信号", "电话"]
        .iter().any(|word| text.contains(word));
    sends && agrees && !uncertain && (!other_material || contact)
}

fn answer_with_contact_request(reply: &str, ask: bool, round: i64) -> String {
    let mut text = reply.trim()
        .replace("电话或微信", "联系方式")
        .replace("电话/微信", "联系方式")
        .replace("电话／微信", "联系方式")
        .replace("电话号码", "联系方式")
        .replace("微信号", "联系方式")
        .replace("手机号", "联系方式")
        .replace("电话", "联系方式")
        .replace("微信", "联系方式")
        .replace("留下联系方式", "留个联系方式")
        .replace("留一下联系方式", "留个联系方式");
    if let Some(position) = text.find("留个联系方式") {
        let start = text[..position].rfind(['。', '！', '？', '!', '?', '；', ';', '，', ','])
            .map_or(0, |index| index + text[index..].chars().next().unwrap().len_utf8());
        let end = text[position..].find(['。', '！', '？', '!', '?'])
            .map_or(text.len(), |offset| position + offset + text[position + offset..].chars().next().unwrap().len_utf8());
        let before = text[..start].trim_end_matches(['，', ',', '。', '；', ';']).trim();
        let after = text[end..].trim();
        text = [before, after].into_iter().filter(|part| !part.is_empty())
            .collect::<Vec<_>>().join("。");
    }
    if text.is_empty() {
        text = "您想了解哪个项目？".to_string();
    }
    if !text.ends_with(['。', '！', '？', '!', '?']) { text.push('。'); }
    if ask {
        text.push_str(solicitation_reply(round));
    }
    text
}

#[cfg(test)]
mod contact_policy_tests {
    use super::{answer_with_contact_request, clear_contact_promise, solicitation_reply, split_reply};

    #[test]
    fn clear_promise_requires_previous_request_and_excludes_other_material() {
        assert!(clear_contact_promise("行，你等我把信息整理整理发给你", true));
        assert!(clear_contact_promise("好的，稍后把联系方式发你", true));
        for text in ["我考虑下", "好，我把地址发给你", "行，我不发给你", "可能等我发给你"] {
            assert!(!clear_contact_promise(text, true), "{text}");
        }
        assert!(!clear_contact_promise("行，你等我把信息整理整理发给你", false));
    }

    #[test]
    fn each_unqualified_round_asks_for_contact_with_varied_wording() {
        let replies: Vec<_> = (0..4).map(solicitation_reply).collect();
        assert_eq!(replies.iter().collect::<std::collections::HashSet<_>>().len(), 4);
        for reply in replies {
            assert!(reply.contains("留个联系方式"));
            assert!(!reply.contains("电话") && !reply.contains("微信"));
        }
    }

    #[test]
    fn answer_then_ask_once_in_final_split_part() {
        let reply = answer_with_contact_request(
            "事业编培训暂不在本店服务范围内，主要提供语言培训和成人学历提升。方便留个电话或微信吗？",
            true, 1);
        let parts = split_reply(&reply);
        assert_eq!(parts.len(), 2);
        assert!(parts[0].contains("暂不在本店服务范围"));
        assert_eq!(reply.matches("留个联系方式").count(), 1);
        assert!(parts[1].contains("留个联系方式"));
        assert!(!reply.contains("电话") && !reply.contains("微信"));
    }

    #[test]
    fn after_contact_answer_without_asking() {
        let reply = answer_with_contact_request("成人学历提升可咨询学习安排。方便留个联系方式吗？", false, 3);
        assert_eq!(reply, "成人学历提升可咨询学习安排。");
    }
}

fn migrate_outbox_parts(db: &mut Connection) -> Result<()> {
    let has_part_index = db.prepare("PRAGMA table_info(outbox)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?
        .iter().any(|name| name == "part_index");
    if has_part_index {
        return Ok(());
    }
    let tx = db.transaction()?;
    tx.execute_batch("CREATE TABLE outbox_new (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id), revision INTEGER NOT NULL,
        text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', receipt TEXT, error TEXT,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()), sent_at INTEGER,
        source TEXT NOT NULL DEFAULT 'bot', part_index INTEGER NOT NULL DEFAULT 0,
        UNIQUE(event_id,part_index)
    );
    INSERT INTO outbox_new(id,event_id,conversation_id,revision,text,status,receipt,error,created_at,sent_at,source,part_index)
    SELECT id,event_id,conversation_id,revision,text,status,receipt,error,created_at,sent_at,source,0 FROM outbox;
    DROP TABLE outbox;
    ALTER TABLE outbox_new RENAME TO outbox;
    CREATE INDEX outbox_queue ON outbox(status,created_at);")?;
    tx.commit()?;
    Ok(())
}
#[derive(Clone)]
struct App {
    db: Arc<Mutex<Connection>>,
    client: Client,
    accounts: Arc<HashMap<String, Account>>,
    api_key: String,
    fastgpt_url: String,
    send_mode: String,
    send_url: String,
    send_key: String,
    sessions: Arc<Mutex<HashMap<String, Instant>>>,
    secure_cookie: bool,
    tools: Arc<admin_tools::AdminTools>,
}
#[derive(Deserialize, Serialize)]
struct Incoming {
    event_id: String,
    message_id: String,
    conversation_id: String,
    customer_id: String,
    #[serde(default)]
    customer_nickname: String,
    text: String,
    #[serde(default)]
    image: Option<String>,
    #[serde(default = "customer")]
    direction: String,
}
fn customer() -> String {
    "customer".into()
}
#[derive(Clone)]
struct Job {
    id: String,
    account: String,
    conversation: String,
    text: String,
    image_mime: Option<String>,
    image_data: Option<Vec<u8>>,
    revision: i64,
    epoch: i64,
    is_peer: bool,
}
type ApiResult = std::result::Result<Json<Value>, ApiError>;
struct ApiError(StatusCode, String);
impl IntoResponse for ApiError {
    fn into_response(self) -> axum::response::Response {
        (self.0, Json(json!({"error": self.1}))).into_response()
    }
}
impl From<anyhow::Error> for ApiError {
    fn from(error: anyhow::Error) -> Self {
        eprintln!("internal operation failed: {error:#}");
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal operation failed".into(),
        )
    }
}
impl From<rusqlite::Error> for ApiError {
    fn from(error: rusqlite::Error) -> Self {
        anyhow::Error::from(error).into()
    }
}
fn problem(code: StatusCode, message: &str) -> ApiError {
    ApiError(code, message.into())
}
fn flow_error(summary: &str, detail: &str) -> ApiError {
    let excerpt: String = detail.chars().take(8000).collect();
    let truncated = if detail.chars().count() > 8000 { "\n[详情已截断]" } else { "" };
    problem(StatusCode::BAD_GATEWAY, &format!("{summary}\n\n{excerpt}{truncated}"))
}

fn setting(name: &str, fallback: &str) -> String {
    env::var(name).unwrap_or_else(|_| fallback.into())
}
fn authorized(headers: &HeaderMap, name: &str, key: &str) -> std::result::Result<(), ApiError> {
    if key.is_empty() || headers.get(name).and_then(|v| v.to_str().ok()) != Some(key) {
        return Err(problem(StatusCode::UNAUTHORIZED, "invalid authentication"));
    }
    Ok(())
}
fn id() -> String {
    Uuid::new_v4().to_string()
}

fn admin_auth(app: &App, headers: &HeaderMap, write: bool) -> std::result::Result<(), ApiError> {
    if headers.get("x-api-key").is_some() {
        return authorized(headers, "x-api-key", &app.api_key);
    }
    let cookie = headers
        .get("cookie")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let token = cookie
        .split(';')
        .filter_map(|v| v.trim().split_once('='))
        .find(|(name, _)| *name == "chatbot_session")
        .map(|(_, v)| v)
        .unwrap_or("");
    let mut sessions = app.sessions.lock().unwrap();
    sessions.retain(|_, time| time.elapsed() < Duration::from_secs(3600));
    let time = sessions
        .get_mut(token)
        .ok_or_else(|| problem(StatusCode::UNAUTHORIZED, "login required"))?;
    if write {
        same_origin(headers)?;
    }
    *time = Instant::now();
    Ok(())
}
fn same_origin(headers: &HeaderMap) -> std::result::Result<(), ApiError> {
    let origin = headers
        .get("origin")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let host = headers
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if host.is_empty()
        || ![format!("http://{host}"), format!("https://{host}")].contains(&origin.to_owned())
    {
        return Err(problem(
            StatusCode::FORBIDDEN,
            "same-origin request required",
        ));
    }
    Ok(())
}
async fn login(
    State(app): State<App>,
    headers: HeaderMap,
    Json(input): Json<Value>,
) -> std::result::Result<Response, ApiError> {
    same_origin(&headers)?;
    if input["key"].as_str() != Some(app.api_key.as_str()) {
        return Err(problem(StatusCode::UNAUTHORIZED, "管理密钥不正确"));
    }
    let token = format!("{}{}", id(), id());
    app.sessions
        .lock()
        .unwrap()
        .insert(token.clone(), Instant::now());
    let cookie = format!(
        "chatbot_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600{}",
        if app.secure_cookie { "; Secure" } else { "" }
    );
    Ok((
        [("set-cookie", cookie), ("cache-control", "no-store".into())],
        Json(json!({"ok":true})),
    )
        .into_response())
}
async fn logout(
    State(app): State<App>,
    headers: HeaderMap,
) -> std::result::Result<Response, ApiError> {
    admin_auth(&app, &headers, true)?;
    if let Some(cookie) = headers.get("cookie").and_then(|v| v.to_str().ok()) {
        for item in cookie.split(';') {
            if let Some(("chatbot_session", token)) = item.trim().split_once('=') {
                app.sessions.lock().unwrap().remove(token);
            }
        }
    }
    Ok((
        [(
            "set-cookie",
            "chatbot_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
        )],
        Json(json!({"ok":true})),
    )
        .into_response())
}

#[tokio::main]
async fn main() -> Result<()> {
    let api_key = env::var("BUSINESS_API_KEY").context("BUSINESS_API_KEY is required")?;
    let internal_key = env::var("INTERNAL_API_KEY").context("INTERNAL_API_KEY is required")?;
    if api_key.len() < 24 || internal_key.len() < 24 {
        bail!("service keys must be at least 24 characters");
    }
    let account_config: Vec<Account> =
        serde_json::from_slice(&fs::read(setting("ACCOUNTS_FILE", "config/accounts.json"))?)?;
    let mut accounts = HashMap::new();
    for account in account_config {
        if account.id.is_empty()
            || account.reply_window_seconds <= 0
            || accounts.insert(account.id.clone(), account).is_some()
        {
            bail!("invalid or duplicate account configuration");
        }
    }
    let db_path = setting("DATABASE_PATH", "data/business.sqlite");
    if let Some(parent) = std::path::Path::new(&db_path).parent() {
        fs::create_dir_all(parent)?;
    }
    let mut db = Connection::open(db_path)?;
    db.busy_timeout(Duration::from_secs(5))?;
    db.execute_batch(include_str!("schema.sql"))?;
    let has_epoch = db
        .prepare("PRAGMA table_info(conversations)")?
        .query_map([], |r| r.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?
        .iter()
        .any(|name| name == "epoch");
    if !has_epoch {
        db.execute(
            "ALTER TABLE conversations ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0",
            [],
        )?;
    }
    for (table, column, definition) in [
        ("conversations", "customer_nickname", "TEXT NOT NULL DEFAULT ''"),
        ("conversations", "is_peer", "INTEGER NOT NULL DEFAULT 0"),
        ("conversations", "project_engaged", "INTEGER NOT NULL DEFAULT 0"),
        ("conversations", "contact_refused", "INTEGER NOT NULL DEFAULT 0"),
        ("conversations", "contact_promised", "INTEGER NOT NULL DEFAULT 0"),
        ("events", "direction", "TEXT NOT NULL DEFAULT 'customer'"),
        ("events", "image_mime", "TEXT"),
        ("events", "image_data", "BLOB"),
        ("events", "image_sha256", "TEXT"),
        ("outbox", "source", "TEXT NOT NULL DEFAULT 'bot'"),
        ("merchant_versions", "review_reason", "TEXT NOT NULL DEFAULT ''"),
        ("merchants", "bot_paused", "INTEGER NOT NULL DEFAULT 0"),
        ("leads", "exported_at", "INTEGER"),
    ] {
        let columns = db
            .prepare(&format!("PRAGMA table_info({table})"))?
            .query_map([], |r| r.get::<_, String>(1))?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        if !columns.iter().any(|name| name == column) {
            db.execute(
                &format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"),
                [],
            )?;
        }
    }
    migrate_outbox_parts(&mut db)?;
    let has_reply_rounds = db.prepare("PRAGMA table_info(conversations)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?
        .iter().any(|name| name == "reply_rounds");
    if !has_reply_rounds {
        db.execute("ALTER TABLE conversations ADD COLUMN reply_rounds INTEGER NOT NULL DEFAULT 0", [])?;
        db.execute("UPDATE conversations SET reply_rounds=(
            SELECT COUNT(DISTINCT o.event_id) FROM outbox o JOIN events e ON e.id=o.event_id
            WHERE o.conversation_id=conversations.id AND e.status='processed'
              AND o.source IN ('bot','bot_handoff'))", [])?;
    }
    db.execute("UPDATE conversations SET mode='paused' WHERE reply_rounds>=?1 AND mode='bot'", [MAX_REPLY_ROUNDS])?;
    db.execute("UPDATE events SET status='held',error='automatic reply limit reached'
        WHERE status='pending' AND conversation_id IN
        (SELECT id FROM conversations WHERE reply_rounds>=?1)", [MAX_REPLY_ROUNDS])?;
    // Discard retired Typebot session mappings; preserve business history and receipts.
    db.execute(
        "UPDATE conversations SET session_id=NULL WHERE session_id IS NOT NULL",
        [],
    )?;
    // Old operator takeovers become local pauses. Chatwoot-origin replies still
    // queued at cutover must never be sent later without an operator reviewing them.
    db.execute(
        "UPDATE conversations SET mode='paused' WHERE mode='human'",
        [],
    )?;
    db.execute("UPDATE outbox SET status='cancelled',error='retired Chatwoot agent reply' WHERE source='human' AND status='queued'", [])?;
    // An incoming message which only failed Chatwoot mirroring has never reached
    // FastGPT. It can now enter the local flow; ambiguous model calls stay held.
    db.execute("UPDATE events SET status='pending',error=NULL WHERE direction='customer' AND status='uncertain' AND error LIKE 'Chatwoot incoming sync requires review:%'", [])?;
    // A process may have died after an external service accepted a request. Never replay blindly.
    db.execute("UPDATE events SET status='uncertain',error='process restarted during flow execution' WHERE status='processing'", [])?;
    db.execute("UPDATE outbox SET status='uncertain',error='process restarted during send' WHERE status='sending'", [])?;
    let send_mode = setting("SEND_MODE", "mock");
    if !["mock", "platform"].contains(&send_mode.as_str()) {
        bail!("SEND_MODE must be mock or platform");
    }
    let send_url = setting("PLATFORM_SEND_URL", "");
    let send_key = setting("PLATFORM_SEND_KEY", "");
    if send_mode == "platform" && (send_url.is_empty() || send_key.is_empty()) {
        bail!("platform send URL and key are required");
    }
    let app = App {
        db: Arc::new(Mutex::new(db)),
        accounts: Arc::new(accounts),
        api_key,
        client: Client::builder().timeout(Duration::from_secs(45)).build()?,
        sessions: Arc::new(Mutex::new(HashMap::new())),
        secure_cookie: setting("SECURE_COOKIE", "false") == "true",
        tools: Arc::new(admin_tools::AdminTools::from_env()?),
        fastgpt_url: setting("FASTGPT_URL", "http://fastgpt:3000"),
        send_mode,
        send_url,
        send_key,
    };
    let worker_app = app.clone();
    tokio::spawn(async move {
        loop {
            if let Err(error) = tick(&worker_app).await {
                eprintln!("worker failure: {error:#}");
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    });
    let router = Router::new()
        .route(
            "/",
            get(|| async {
                (
                    [("cache-control", "no-store")],
                    Html(include_str!("../web/index.html")),
                )
            }),
        )
        .route(
            "/health",
            get(|| async { Json(json!({"status":"ok", "version":env!("CARGO_PKG_VERSION")})) }),
        )
        .route("/api/login", post(login))
        .route("/api/logout", post(logout))
        .route("/api/accounts", get(account_list))
        .route("/api/fastgpt/workflows", get(admin_tools::workflow_list))
        .route("/api/accounts/{id}", delete(remove_account))
        .route("/api/catalog", get(catalog::list))
        .route("/api/catalog/public", get(catalog::public))
        .route("/api/catalog/industries", post(catalog::save_industry))
        .route(
            "/api/catalog/industries/{id}/publish",
            post(catalog::publish_industry),
        )
        .route("/api/catalog/industries/{id}", delete(catalog::delete_industry))
        .route("/api/catalog/projects", post(catalog::save_project))
        .route("/api/catalog/projects/{id}/publish", post(catalog::publish_project))
        .route("/api/catalog/projects/{id}", delete(catalog::delete_project))
        .route("/onboard", get(onboarding::page))
        .route("/api/onboard/context", get(onboarding::context))
        .route("/api/onboard/preview", post(onboarding::preview))
        .route("/api/onboard/submit", post(onboarding::submit))
        .route(
            "/api/invitations",
            get(onboarding::list_invites).post(onboarding::create_invite),
        )
        .route("/api/invitations/{id}/revoke", post(onboarding::revoke))
        .route("/api/invitations/{id}", delete(onboarding::delete_revoked))
        .route("/api/merchants", get(onboarding::list_merchants))
        .route("/api/merchants/{id}/bot-control", post(merchant_bot_control))
        .route(
            "/api/merchant-versions/{id}/review",
            post(onboarding::review),
        )
        .route("/admin-tools/{tool}", post(admin_tools::open))
        .route("/admin-entry/fastgpt", post(admin_tools::consume))
        .route("/api/simulate/{account}", post(simulate))
        .route("/webhooks/platform/{account}", post(receive))
        .route("/api/snapshot", get(snapshot))
        .route("/api/admin/pages/{kind}", get(admin_pages::list))
        .route("/api/admin/pages/history/{conversation}", get(admin_pages::history))
        .route("/api/admin/images/{event}", get(admin_image))
        .route("/api/admin/options/{kind}", get(admin_pages::options))
        .route("/api/leads", get(leads::list))
        .route("/api/leads/export", post(leads::export))
        .route("/api/leads/delete", post(leads::delete))
        .route("/api/leads/{id}/history-link", post(leads::create_history_link))
        .route("/lead-history", get(leads::history_page))
        .route("/api/unanswered", get(unanswered::list))
        .route(
            "/api/unanswered/{question}/status",
            post(unanswered::status),
        )
        .route("/api/conversations/{conversation}/control", post(control))
        .route("/api/events/{event}/cancel", post(cancel_event))
        .route("/api/events/{event}/retry", post(retry_event))
        .route("/api/outbox/{outbox}/resolve", post(resolve_outbox))
        .layer(DefaultBodyLimit::max(4 * 1024 * 1024))
        .with_state(app);
    let bind = setting("BIND", "0.0.0.0:8080");
    println!("business-service listening on {bind}");
    axum::serve(tokio::net::TcpListener::bind(bind).await?, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

/// The normalized platform contract is deliberately separate from any undocumented Douyin payload.
fn decode_image(value: Option<&str>) -> std::result::Result<Option<(String, Vec<u8>, String)>, ApiError> {
    let Some(value) = value else { return Ok(None) };
    let (prefix, encoded) = value.split_once(',').ok_or_else(|| problem(StatusCode::UNPROCESSABLE_ENTITY, "图片格式无效"))?;
    let mime = match prefix {
        "data:image/jpeg;base64" => "image/jpeg",
        "data:image/png;base64" => "image/png",
        "data:image/webp;base64" => "image/webp",
        _ => return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "仅支持 JPEG、PNG、WebP 图片")),
    };
    if encoded.len() > 2_800_000 { return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "图片不能超过 2 MB")); }
    let bytes = BASE64.decode(encoded).map_err(|_| problem(StatusCode::UNPROCESSABLE_ENTITY, "图片编码无效"))?;
    if bytes.is_empty() || bytes.len() > 2 * 1024 * 1024 {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "图片不能超过 2 MB"));
    }
    let valid = match mime {
        "image/jpeg" => bytes.starts_with(&[0xff, 0xd8, 0xff]),
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/webp" => bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP",
        _ => false,
    };
    if !valid { return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "图片内容与格式不匹配")); }
    let hash = format!("{:x}", Sha256::digest(&bytes));
    Ok(Some((mime.to_owned(), bytes, hash)))
}

async fn receive(
    State(app): State<App>,
    Path(account_id): Path<String>,
    headers: HeaderMap,
    Json(input): Json<Incoming>,
) -> ApiResult {
    authorized(&headers, "x-api-key", &app.api_key)?;
    if active_account(&app, &account_id)?.is_none() {
        return Err(problem(StatusCode::NOT_FOUND, "unknown account"));
    }
    if input.direction != "customer" {
        return Ok(Json(json!({"ignored":true, "reason":"non-customer event"})));
    }
    let image = decode_image(input.image.as_deref())?;
    let text = if input.text.trim().is_empty() && image.is_some() { "[图片]" } else { input.text.as_str() };
    let image_hash = image.as_ref().map(|(_, _, hash)| hash.as_str());
    if [
        &input.event_id,
        &input.message_id,
        &input.conversation_id,
        &input.customer_id,
    ]
    .iter()
    .any(|s| s.is_empty() || s.len() > 250)
        || text.trim().is_empty()
        || text.len() > 16000
        || input.customer_nickname.len() > 100
    {
        return Err(problem(
            StatusCode::UNPROCESSABLE_ENTITY,
            "invalid message fields",
        ));
    }
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction().map_err(anyhow::Error::from)?;
    let existing: Option<(String,String,String,String,String,Option<String>)> = tx.query_row(
        "SELECT e.id,e.status,e.text,c.external_id,c.customer_id,e.image_sha256 FROM events e JOIN conversations c ON c.id=e.conversation_id WHERE e.account_id=?1 AND (e.event_id=?2 OR e.message_id=?3)",
        params![account_id,input.event_id,input.message_id], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))
    ).optional().map_err(anyhow::Error::from)?;
    if let Some((event, status, stored_text, conversation, customer, stored_hash)) = existing {
        if stored_text != text || stored_hash.as_deref() != image_hash
            || conversation != input.conversation_id
            || customer != input.customer_id
        {
            return Err(problem(
                StatusCode::CONFLICT,
                "duplicate identity with different content",
            ));
        }
        return Ok(Json(json!({"id":event,"status":status,"duplicate":true})));
    }
    tx.execute("INSERT OR IGNORE INTO conversations(id,account_id,external_id,customer_id) VALUES(?1,?2,?3,?4)", params![id(),account_id,input.conversation_id,input.customer_id]).map_err(anyhow::Error::from)?;
    let (conversation, customer, mode): (String, String, String) = tx
        .query_row(
            "SELECT id,customer_id,mode FROM conversations WHERE account_id=?1 AND external_id=?2",
            params![account_id, input.conversation_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .map_err(anyhow::Error::from)?;
    if customer != input.customer_id {
        return Err(problem(
            StatusCode::CONFLICT,
            "conversation belongs to another customer",
        ));
    }
    if !input.customer_nickname.trim().is_empty() {
        tx.execute("UPDATE conversations SET customer_nickname=?1 WHERE id=?2",
            params![input.customer_nickname.trim(), conversation]).map_err(anyhow::Error::from)?;
    }
    tx.execute(
        "UPDATE conversations SET revision=revision+1 WHERE id=?1",
        [&conversation],
    )
    .map_err(anyhow::Error::from)?;
    let revision: i64 = tx
        .query_row(
            "SELECT revision FROM conversations WHERE id=?1",
            [&conversation],
            |r| r.get(0),
        )
        .map_err(anyhow::Error::from)?;
    let event = id();
    let status = if mode == "paused" || merchant_paused_for_account(&tx, &account_id)? { "held" } else { "pending" };
    tx.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status,image_mime,image_data,image_sha256) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)", params![event,account_id,input.event_id,input.message_id,conversation,text,revision,status,image.as_ref().map(|(mime,_,_)| mime.as_str()),image.as_ref().map(|(_,bytes,_)| bytes.as_slice()),image_hash]).map_err(anyhow::Error::from)?;
    let cancelled = tx.execute("UPDATE outbox SET status='cancelled',error='superseded by new incoming message' WHERE conversation_id=?1 AND status='queued' AND source<>'human'
        AND NOT (part_index>0 AND EXISTS(SELECT 1 FROM outbox first WHERE first.event_id=outbox.event_id
        AND first.part_index=0 AND first.status IN ('accepted','mock_sent')))", [&conversation]).map_err(anyhow::Error::from)?;
    if cancelled > 0 {
        // FastGPT already stored the generated answer, but the customer never saw it.
        tx.execute(
            "UPDATE conversations SET epoch=epoch+1 WHERE id=?1",
            [&conversation],
        )
        .map_err(anyhow::Error::from)?;
    }
    tx.commit().map_err(anyhow::Error::from)?;
    Ok(Json(
        json!({"id":event,"status":status,"conversation_id":conversation,"duplicate":false}),
    ))
}

async fn admin_image(State(app): State<App>, Path(event): Path<String>, headers: HeaderMap) -> std::result::Result<Response, ApiError> {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let image: Option<(Option<String>, Option<Vec<u8>>)> = db.query_row(
        "SELECT image_mime,image_data FROM events WHERE id=?1", [&event],
        |row| Ok((row.get(0)?, row.get(1)?)),
    ).optional().map_err(anyhow::Error::from)?;
    let (Some(mime), Some(bytes)) = image.ok_or_else(|| problem(StatusCode::NOT_FOUND, "图片不存在"))? else {
        return Err(problem(StatusCode::NOT_FOUND, "图片不存在"));
    };
    let mime = match mime.as_str() {
        "image/jpeg" => "image/jpeg", "image/png" => "image/png", "image/webp" => "image/webp",
        _ => return Err(problem(StatusCode::NOT_FOUND, "图片不存在")),
    };
    Ok(Response::builder().header("content-type", mime).header("cache-control", "private, no-store")
        .header("x-content-type-options", "nosniff").body(Body::from(bytes)).expect("static image headers"))
}

async fn control(
    State(app): State<App>,
    Path(conversation): Path<String>,
    headers: HeaderMap,
    Json(input): Json<Value>,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mode = input["mode"].as_str().unwrap_or("");
    if !["paused", "bot"].contains(&mode) {
        return Err(problem(
            StatusCode::BAD_REQUEST,
            "mode must be paused or bot",
        ));
    }
    let changed = {
        let mut db = app.db.lock().unwrap();
        let tx = db.transaction().map_err(anyhow::Error::from)?;
        let exists: bool = tx
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM conversations WHERE id=?1)",
                [&conversation],
                |r| r.get(0),
            )
            .map_err(anyhow::Error::from)?;
        if !exists {
            return Err(problem(StatusCode::NOT_FOUND, "unknown conversation"));
        }
        if mode == "bot" {
            let rounds: i64 = tx.query_row("SELECT reply_rounds FROM conversations WHERE id=?1",
                [&conversation], |row| row.get(0)).map_err(anyhow::Error::from)?;
            if rounds >= MAX_REPLY_ROUNDS {
                return Err(problem(StatusCode::CONFLICT, "本会话已达到 50 轮自动回复上限"));
            }
        }
        let changed = tx.execute("UPDATE conversations SET mode=?1,revision=revision+1,epoch=epoch+1,session_id=NULL WHERE id=?2 AND mode<>?1", params![mode,conversation]).map_err(anyhow::Error::from)?;
        if changed > 0 {
            tx.execute("UPDATE outbox SET status='cancelled',error='conversation control changed' WHERE conversation_id=?1 AND status='queued'", [&conversation]).map_err(anyhow::Error::from)?;
            if mode == "paused" {
                tx.execute(
                    "UPDATE events SET status='held' WHERE conversation_id=?1 AND status='pending'",
                    [&conversation],
                )
                .map_err(anyhow::Error::from)?;
            }
        }
        tx.commit().map_err(anyhow::Error::from)?;
        changed > 0
    };
    Ok(Json(
        json!({"mode":mode,"changed":changed,"reset_session":changed&&mode=="bot","in_flight_requests_cannot_be_recalled":true}),
    ))
}

#[derive(Deserialize)]
struct MerchantBotControl {
    paused: bool,
}

async fn merchant_bot_control(
    State(app): State<App>,
    Path(merchant_id): Path<String>,
    headers: HeaderMap,
    Json(input): Json<MerchantBotControl>,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let changed = tx.execute(
        "UPDATE merchants SET bot_paused=?1 WHERE id=?2 AND bot_paused<>?1",
        params![input.paused, merchant_id],
    )?;
    if changed == 0 {
        let exists: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM merchants WHERE id=?1)",
            [&merchant_id], |row| row.get(0),
        )?;
        if !exists { return Err(problem(StatusCode::NOT_FOUND, "商家不存在")); }
    }
    if changed > 0 && input.paused {
        // Invalidate a response already being generated without changing per-conversation modes.
        tx.execute("UPDATE conversations SET revision=revision+1,epoch=epoch+1,session_id=NULL
            WHERE account_id IN (SELECT id FROM merchant_accounts WHERE merchant_id=?1)", [&merchant_id])?;
        tx.execute("UPDATE events SET status='held',error='merchant robot paused'
            WHERE status='pending' AND conversation_id IN (SELECT c.id FROM conversations c
            JOIN merchant_accounts a ON a.id=c.account_id WHERE a.merchant_id=?1)", [&merchant_id])?;
        tx.execute("UPDATE outbox SET status='cancelled',error='merchant robot paused'
            WHERE status='queued' AND conversation_id IN (SELECT c.id FROM conversations c
            JOIN merchant_accounts a ON a.id=c.account_id WHERE a.merchant_id=?1)", [&merchant_id])?;
    }
    tx.commit()?;
    Ok(Json(json!({"merchant_id":merchant_id,"paused":input.paused,"changed":changed>0,
        "in_flight_requests_cannot_be_recalled":true})))
}

async fn cancel_event(
    State(app): State<App>,
    Path(event): Path<String>,
    headers: HeaderMap,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction().map_err(anyhow::Error::from)?;
    let conversation: Option<String> = tx.query_row("SELECT conversation_id FROM events WHERE id=?1 AND status IN ('pending','uncertain','blocked','held')", [&event], |r| r.get(0)).optional().map_err(anyhow::Error::from)?;
    let changed = tx.execute("UPDATE events SET status='cancelled',error='cancelled by operator after review' WHERE id=?1 AND status IN ('pending','uncertain','blocked','held')", [&event]).map_err(anyhow::Error::from)?;
    if let Some(conversation) = conversation.filter(|_| changed > 0) {
        // An uncertain FastGPT call may already have advanced its remote history.
        tx.execute(
            "UPDATE conversations SET epoch=epoch+1 WHERE id=?1",
            [&conversation],
        )
        .map_err(anyhow::Error::from)?;
    }
    tx.commit().map_err(anyhow::Error::from)?;
    Ok(Json(json!({"changed":changed})))
}

async fn retry_event(
    State(app): State<App>,
    Path(event): Path<String>,
    headers: HeaderMap,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let record: Option<(String, String, String, i64, i64, String, i64, String)> = tx.query_row(
        "SELECT e.conversation_id,e.account_id,e.status,e.revision,c.revision,c.mode,c.reply_rounds,e.direction
         FROM events e JOIN conversations c ON c.id=e.conversation_id WHERE e.id=?1",
        [&event], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?)),
    ).optional()?;
    let Some((conversation, account, status, revision, current_revision, mode, rounds, direction)) = record else {
        return Err(problem(StatusCode::NOT_FOUND, "异常消息不存在"));
    };
    if !["uncertain", "blocked"].contains(&status.as_str()) || direction != "customer" {
        return Err(problem(StatusCode::CONFLICT, "仅可重试待核对或已阻塞的客户消息，请刷新状态"));
    }
    if mode != "bot" || rounds >= MAX_REPLY_ROUNDS || merchant_paused_for_account(&tx, &account)? {
        return Err(problem(StatusCode::CONFLICT, "机器人已暂停或达到回复上限，不能重试"));
    }
    if revision != current_revision {
        return Err(problem(StatusCode::CONFLICT, "会话已有新消息或接待状态变更，请核对后取消旧异常"));
    }
    let removed: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM removed_accounts WHERE id=?1)", [&account], |r| r.get(0))?;
    if removed || (stored_account(&tx, &account)?.is_none() && !app.accounts.contains_key(&account)) {
        return Err(problem(StatusCode::CONFLICT, "账号接入已移除，不能重试"));
    }
    let has_send_record: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM outbox WHERE event_id=?1)", [&event], |r| r.get(0),
    )?;
    if has_send_record {
        return Err(problem(StatusCode::CONFLICT, "该消息已有发送记录，请在发送记录中核对，避免重复回复"));
    }
    let has_other_blocker: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM events WHERE conversation_id=?1 AND id<>?2 AND status IN ('processing','uncertain','blocked'))
         OR EXISTS(SELECT 1 FROM outbox WHERE conversation_id=?1 AND status IN ('queued','sending','uncertain','blocked'))",
        params![conversation,event], |r| r.get(0),
    )?;
    if has_other_blocker {
        return Err(problem(StatusCode::CONFLICT, "会话还有其他未处理异常或发送任务，请先核对"));
    }
    // The failed call may have advanced remote history. A fresh chat ID avoids reusing it.
    tx.execute("UPDATE conversations SET epoch=epoch+1,session_id=NULL WHERE id=?1", [&conversation])?;
    tx.execute("DELETE FROM decisions WHERE event_id=?1", [&event])?;
    tx.execute("UPDATE events SET status='pending' WHERE id=?1", [&event])?;
    tx.commit()?;
    Ok(Json(json!({"id":event,"status":"pending","reset_session":true})))
}

async fn snapshot(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let mut result = serde_json::Map::new();
    for table in ["conversations", "events", "outbox", "leads"] {
        let mut stmt = db
            .prepare(&format!(
                "SELECT * FROM {table} ORDER BY rowid DESC LIMIT 100"
            ))
            .map_err(anyhow::Error::from)?;
        let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let rows = stmt
            .query_map([], |row| {
                let mut object = serde_json::Map::new();
                for (index, name) in names.iter().enumerate() {
                    let value = match row.get_ref(index)? {
                        rusqlite::types::ValueRef::Null => Value::Null,
                        rusqlite::types::ValueRef::Integer(v) => json!(v),
                        rusqlite::types::ValueRef::Text(v) => json!(String::from_utf8_lossy(v)),
                        _ => Value::Null,
                    };
                    object.insert(name.clone(), value);
                }
                Ok(Value::Object(object))
            })
            .map_err(anyhow::Error::from)?
            .collect::<std::result::Result<Vec<_>, _>>()
            .map_err(anyhow::Error::from)?;
        result.insert(table.into(), json!(rows));
    }
    result.insert("send_mode".into(), json!(app.send_mode));
    let mut totals = serde_json::Map::new();
    for table in ["conversations", "events", "outbox", "leads"] {
        let count: i64 = db
            .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
            .map_err(anyhow::Error::from)?;
        totals.insert(table.into(), json!(count));
    }
    result.insert("totals".into(), Value::Object(totals));
    Ok(Json(Value::Object(result)))
}

async fn account_list(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let mut stmt = db.prepare("SELECT id FROM removed_accounts")?;
    let removed: HashSet<String> = stmt.query_map([],|r|r.get(0))?
        .collect::<std::result::Result<_,_>>()?;
    let mut accounts: Vec<Account> = app.accounts.values().filter(|a|!removed.contains(&a.id)).cloned().collect();
    let mut stmt = db.prepare("SELECT id,merchant_id,douyin_nickname,industry,fastgpt_app_id,reply_window_seconds FROM merchant_accounts WHERE id NOT IN (SELECT id FROM removed_accounts)")?;
    let stored = stmt.query_map([], |row| Ok(Account {
        id: row.get(0)?, merchant_id: row.get(1)?, douyin_nickname: row.get(2)?,
        industry: row.get(3)?, fastgpt_app_id: row.get(4)?,
        fastgpt_key_env: "FASTGPT_APP_KEY".into(), reply_window_seconds: row.get(5)?,
    }))?.collect::<std::result::Result<Vec<_>,_>>()?;
    accounts.extend(stored);
    let accounts: Vec<Value> = accounts.into_iter().map(|a| json!({"id":a.id,"merchant_id":a.merchant_id,"douyin_nickname":a.douyin_nickname,"industry":a.industry,"fastgpt_app_id":a.fastgpt_app_id})).collect();
    Ok(Json(json!({"accounts":accounts,"send_mode":app.send_mode})))
}
async fn remove_account(State(app): State<App>, Path(account_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app,&headers,true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    if stored_account(&tx,&account_id)?.is_none() && !app.accounts.contains_key(&account_id) {
        return Err(problem(StatusCode::NOT_FOUND,"账号不存在"));
    }
    let busy: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM events WHERE account_id=?1 AND status IN ('pending','processing','uncertain','blocked') UNION SELECT 1 FROM outbox o JOIN conversations c ON c.id=o.conversation_id WHERE c.account_id=?1 AND o.status IN ('queued','sending','uncertain','blocked'))",[&account_id],|r|r.get(0))?;
    if busy { return Err(problem(StatusCode::CONFLICT,"该账号仍有待处理或待核对消息，不能删除接入")); }
    let changed = tx.execute("INSERT OR IGNORE INTO removed_accounts(id) VALUES(?1)",[&account_id])?;
    if changed == 0 { return Err(problem(StatusCode::CONFLICT,"账号已删除")); }
    tx.execute("UPDATE conversations SET mode='paused' WHERE account_id=?1",[&account_id])?;
    tx.commit()?;
    Ok(Json(json!({"deleted":true})))
}
async fn simulate(
    State(app): State<App>,
    Path(account): Path<String>,
    mut headers: HeaderMap,
    Json(input): Json<Incoming>,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    if app.send_mode != "mock" {
        return Err(problem(
            StatusCode::CONFLICT,
            "simulation requires SEND_MODE=mock",
        ));
    }
    headers.insert(
        "x-api-key",
        app.api_key
            .parse()
            .map_err(|_| problem(StatusCode::INTERNAL_SERVER_ERROR, "invalid configured key"))?,
    );
    receive(State(app), Path(account), headers, Json(input)).await
}
async fn resolve_outbox(
    State(app): State<App>,
    Path(outbox): Path<String>,
    headers: HeaderMap,
    Json(input): Json<Value>,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let status = input["status"].as_str().unwrap_or("");
    if !["accepted", "cancelled"].contains(&status) {
        return Err(problem(
            StatusCode::BAD_REQUEST,
            "resolve as accepted or cancelled",
        ));
    }
    if status == "accepted" && input["message_id"].as_str().is_none_or(str::is_empty) {
        return Err(problem(
            StatusCode::BAD_REQUEST,
            "a verified platform message ID is required",
        ));
    }
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction().map_err(anyhow::Error::from)?;
    let conversation: Option<String> = tx
        .query_row(
            "SELECT conversation_id FROM outbox WHERE id=?1 AND status IN ('uncertain','blocked')",
            [&outbox],
            |r| r.get(0),
        )
        .optional()
        .map_err(anyhow::Error::from)?;
    let changed = tx.execute("UPDATE outbox SET status=?1,receipt=?2,error='resolved by operator',sent_at=CASE WHEN ?1='accepted' THEN unixepoch() ELSE sent_at END WHERE id=?3 AND status IN ('uncertain','blocked')",params![status,input.to_string(),outbox]).map_err(anyhow::Error::from)?;
    if status == "cancelled" {
        tx.execute("UPDATE outbox SET status='cancelled',error='first reply part cancelled by operator'
            WHERE event_id=(SELECT event_id FROM outbox WHERE id=?1) AND part_index>0 AND status='queued'",
            [&outbox]).map_err(anyhow::Error::from)?;
        if let Some(conversation) = conversation.filter(|_| changed > 0) {
            tx.execute(
                "UPDATE conversations SET epoch=epoch+1 WHERE id=?1",
                [&conversation],
            )
            .map_err(anyhow::Error::from)?;
        }
    }
    tx.commit().map_err(anyhow::Error::from)?;
    Ok(Json(json!({"changed":changed})))
}

#[derive(Deserialize)]
struct UnderstandingInput {
    account_id: String,
    conversation_id: String,
    text: String,
}

/// The worker calls the configured FastGPT application/workflow directly.
async fn understand(app: &App, job: &Job) -> std::result::Result<Value, ApiError> {
    let input = UnderstandingInput {
        account_id: job.account.clone(),
        conversation_id: job.conversation.clone(),
        text: job.text.clone(),
    };
    let account = active_account(app,&input.account_id)?
        .ok_or_else(|| problem(StatusCode::NOT_FOUND, "unknown account"))?;
    let event = {
        let db = app.db.lock().unwrap();
        let event: Option<String> = db.query_row("SELECT id FROM events WHERE id=?1 AND account_id=?2 AND conversation_id=?3 AND status='processing'", params![job.id,input.account_id,input.conversation_id], |r| r.get(0)).optional().map_err(anyhow::Error::from)?;
        event.ok_or_else(|| problem(StatusCode::CONFLICT, "no matching active event"))?
    };
    if job.is_peer {
        let decision = json!({"reply":"不用呢 谢谢","action":"peer","fields":{}});
        app.db.lock().unwrap().execute(
            "INSERT OR REPLACE INTO decisions(event_id,result) VALUES(?1,?2)",
            params![event, decision.to_string()],
        ).map_err(anyhow::Error::from)?;
        return Ok(decision);
    }
    let key = env::var(&account.fastgpt_key_env).unwrap_or_default();
    if key.is_empty() || account.fastgpt_app_id.is_empty() {
        return Err(problem(
            StatusCode::SERVICE_UNAVAILABLE,
            "FastGPT application is not configured",
        ));
    }
    let merchant_profile: Option<Value> = {
        let db = app.db.lock().unwrap();
        let raw: Option<String> = db.query_row(
            "SELECT v.profile_json FROM merchants m JOIN merchant_versions v ON v.id=m.current_version_id WHERE m.id=?1 AND v.status='approved'",
            [&account.merchant_id], |r| r.get(0)).optional()?;
        raw.and_then(|value| serde_json::from_str(&value).ok())
    };
    let mut merchant_profile = merchant_profile.unwrap_or_else(|| json!({}));
    if merchant_profile["fields"]["门店电话可公开"].as_str() != Some("是") {
        if let Some(fields) = merchant_profile["fields"].as_object_mut() {
            fields.remove("门店联系电话");
        }
    }
    let industry = merchant_profile["fields"]["所属行业"]
        .as_str().filter(|value| !value.is_empty()).unwrap_or(&account.industry);
    let projects: Vec<&str> = ["main", "available"].into_iter()
        .flat_map(|group| merchant_profile["projects"][group].as_array().into_iter().flatten())
        .filter_map(|item| item["name"].as_str())
        .filter(|name| !name.trim().is_empty())
        .collect();
    let project_selection_question = if projects.is_empty() {
        "你好呀～想了解什么项目或服务呢？".to_string()
    } else {
        format!("你好呀～想咨询{}，还是其他相关需求呢？", projects.join("、"))
    };
    let last_contact_request = {
        let db = app.db.lock().unwrap();
        last_contact_request(&db, &job.conversation)?
    };
    let content = match (&job.image_mime, &job.image_data) {
        (Some(mime), Some(bytes)) => json!([
            {"type":"text","text":input.text},
            {"type":"image_url","image_url":{"url":format!("data:{mime};base64,{}", BASE64.encode(bytes))}}
        ]),
        _ => json!(input.text),
    };
    let response = app
        .client
        .post(format!(
            "{}/api/v1/chat/completions",
            app.fastgpt_url.trim_end_matches('/')
        ))
        .bearer_auth(key)
        .json(
            &json!({"appId":account.fastgpt_app_id,"chatId":format!("{}-{}",job.conversation,job.epoch),"stream":false,"messages":[{"role":"user","content":content}],
            "variables":{"merchant_id":account.merchant_id,"industry":industry,"project_selection_question":project_selection_question,"last_contact_request":last_contact_request.to_string(),"merchant_profile":merchant_profile.to_string()}}),
        )
        .send()
        .await
        .map_err(|error| flow_error("模型平台请求失败", &error.to_string()))?;
    let status = response.status();
    let body = response.text().await
        .map_err(|error| flow_error("模型平台响应读取失败", &error.to_string()))?;
    if !status.is_success() {
        return Err(flow_error("FastGPT rejected request", &format!("HTTP {status}\n{body}")));
    }
    let raw: Value = serde_json::from_str(&body)
        .map_err(|error| flow_error("模型平台响应不是有效 JSON", &format!("{error}\n{body}")))?;
    let content = raw["choices"][0]["message"]["content"]
        .as_str()
        .ok_or_else(|| flow_error("FastGPT response has no content", &body))?;
    let mut decision: Value = serde_json::from_str(content).map_err(|_| {
        flow_error("FastGPT must return the configured decision JSON", content)
    })?;
    // Older workflows may still request handoff. There are no live agents;
    // record a review item and acknowledge the limit without inventing a queue.
    if decision["action"] == "handoff" {
        decision["action"] = json!("unanswered");
        decision["reason"] = json!("客户请求人工，当前未提供在线人工接待");
        decision["reply"] = json!(
            "目前这里由机器人接待，暂时无法提供人工回复。您也可以继续说明具体问题，我会根据已有信息回答。"
        );
    }
    let action = decision["action"].as_str().unwrap_or("").to_owned();
    let reply = decision["reply"].as_str().unwrap_or("");
    if !["reply", "unanswered", "stop", "peer"].contains(&action.as_str())
        || reply.trim().is_empty()
        || reply.len() > 6000
        || !decision["fields"].is_object()
        || decision["fields"]["contact_method"]
            .as_str()
            .is_some_and(|method| !["phone", "wechat", "none"].contains(&method))
        || ["contact_promised", "project_selection_only"].iter().any(|key|
            !decision["fields"][key].is_null() && !decision["fields"][key].is_boolean())
    {
        return Err(flow_error("invalid decision schema", content));
    }
    if action == "peer" {
        decision["reply"] = json!("不用呢 谢谢");
        decision["fields"] = json!({});
    } else {
        // An older workflow may still choose stop; keep the conversation active.
        if action == "stop" {
            decision["reply"] = json!("您想了解哪个项目？");
        }
        decision["action"] = json!("reply");
        decision.as_object_mut().unwrap().remove("reason");
    }
    if action != "peer" && industry != "教育教培" {
        // Keep an older published prompt or a model slip from calling every shop's staff "老师".
        if let Some(reply) = decision["reply"].as_str() {
            decision["reply"] = json!(correct_staff_title(reply, industry));
        }
    }
    if action == "unanswered"
        && decision["reason"]
            .as_str()
            .is_none_or(|v| v.trim().is_empty() || v.len() > 1000)
    {
        return Err(flow_error("unanswered decision requires a reason up to 1000 bytes", content));
    }
    let db = app.db.lock().unwrap();
    db.execute(
        "INSERT OR REPLACE INTO decisions(event_id,result) VALUES(?1,?2)",
        params![event, decision.to_string()],
    )
    .map_err(anyhow::Error::from)?;
    Ok(decision)
}

/// One worker owns all flow and send mutations. SQLite deployment intentionally has one replica.
async fn tick(app: &App) -> Result<()> {
    let job = {
        let db = app.db.lock().unwrap();
        let job = db.query_row("SELECT e.id,e.account_id,e.conversation_id,e.text,e.revision,c.epoch,c.is_peer,e.image_mime,e.image_data FROM events e JOIN conversations c ON c.id=e.conversation_id WHERE e.status='pending' AND c.mode='bot' AND c.reply_rounds<?1 AND NOT EXISTS (SELECT 1 FROM merchant_accounts a JOIN merchants m ON m.id=a.merchant_id WHERE a.id=e.account_id AND m.bot_paused=1) AND NOT EXISTS (SELECT 1 FROM events prior WHERE prior.conversation_id=e.conversation_id AND prior.status IN ('uncertain','blocked')) AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.conversation_id=e.conversation_id AND o.status IN ('queued','sending','uncertain','blocked')) ORDER BY e.rowid LIMIT 1", [MAX_REPLY_ROUNDS], |r| Ok(Job{id:r.get(0)?,account:r.get(1)?,conversation:r.get(2)?,text:r.get(3)?,revision:r.get(4)?,epoch:r.get(5)?,is_peer:r.get::<_,i64>(6)?!=0,image_mime:r.get(7)?,image_data:r.get(8)?})).optional()?;
        if let Some(job) = &job {
            db.execute(
                "UPDATE events SET status='processing' WHERE id=?1",
                [&job.id],
            )?;
        }
        job
    };
    if let Some(job) = job {
        if let Err(error) = run_flow(app, &job).await {
            // Even a local timeout may have advanced the remote workflow.
            let db = app.db.lock().unwrap();
            let paused = merchant_paused_for_account(&db, &job.account)?;
            db.execute(
                "UPDATE events SET status=?1,error=?2 WHERE id=?3 AND status='processing'",
                params![if paused { "held" } else { "uncertain" }, error.to_string(), job.id],
            )?;
        }
    }
    send_next(app).await?;
    Ok(())
}

async fn run_flow(app: &App, job: &Job) -> Result<()> {
    active_account(app, &job.account)?.context("unknown account")?;
    let decision = understand(app, job)
        .await
        .map_err(|error| anyhow::anyhow!("{}", error.1))?;
    let model_reply = decision["reply"]
        .as_str()
        .context("missing FastGPT reply")?
        .to_owned();
    // The transaction below rejects a response generated before a local pause
    // or a newer customer message changed this conversation's revision.
    {
        let mut db = app.db.lock().unwrap();
        let tx = db.transaction()?;
        let (revision, mode, reply_rounds, contact_promised): (i64, String, i64, bool) = tx.query_row(
            "SELECT revision,mode,reply_rounds,contact_promised FROM conversations WHERE id=?1",
            [&job.conversation],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )?;
        let merchant_paused = merchant_paused_for_account(&tx, &job.account)?;
        let applied = mode == "bot" && !merchant_paused && revision == job.revision && reply_rounds < MAX_REPLY_ROUNDS;
        if !applied && mode == "bot" {
            // A newer message superseded this answer after FastGPT saved it.
            tx.execute(
                "UPDATE conversations SET epoch=epoch+1 WHERE id=?1 AND epoch=?2",
                params![job.conversation, job.epoch],
            )?;
        }
        if applied {
            let has_contact: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM leads WHERE conversation_id=?1)",
                [&job.conversation], |row| row.get(0))?;
            let new_contact = decision["fields"]["contact_method"] != "none"
                && ["phone", "wechat"].iter().any(|key| {
                    decision["fields"][key].as_str().is_some_and(|value|
                        value.len() >= 5 && job.text.contains(value))
                });
            let promised_now = decision["fields"]["contact_promised"] == true
                || clear_contact_promise(&job.text,
                    last_contact_request(&tx, &job.conversation)?);
            let project_selection_only = decision["fields"]["project_selection_only"] == true;
            let text = if decision["action"] == "peer" {
                model_reply
            } else if new_contact && !has_contact {
                "收到，稍后店长联系您详聊。".to_string()
            } else {
                answer_with_contact_request(&model_reply,
                    !has_contact && !new_contact && !contact_promised && !promised_now
                        && !project_selection_only, reply_rounds)
            };
            if promised_now && decision["action"] != "peer" {
                tx.execute("UPDATE conversations SET contact_promised=1 WHERE id=?1",
                    [&job.conversation])?;
            }
            let at_limit = reply_rounds + 1 == MAX_REPLY_ROUNDS;
            let source = if at_limit { "bot_handoff" } else { "bot" };
            for (part_index, part) in split_reply(&text).iter().enumerate() {
                tx.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,source,part_index) VALUES(?1,?2,?3,?4,?5,?6,?7)",
                    params![id(),job.id,job.conversation,revision,part,source,part_index as i64])?;
            }
            tx.execute("UPDATE conversations SET reply_rounds=reply_rounds+1 WHERE id=?1", [&job.conversation])?;
            // Candidate only: does not assert verification, CRM delivery or successful contact.
            if decision["action"] != "stop" && decision["action"] != "peer" {
                let fields = &decision["fields"];
                let method = fields["contact_method"].as_str();
                let phone = fields["phone"].as_str()
                    .filter(|v| v.len() >= 5 && v.len() <= 64 && job.text.contains(*v));
                let wechat = fields["wechat"].as_str()
                    .filter(|v| v.len() >= 5 && v.len() <= 64 && job.text.contains(*v));

                // A later customer message may change the permitted channel without repeating
                // the number. The model identifies the earlier value; the database must confirm it.
                if let Some(kind @ ("phone" | "wechat")) = method {
                    let existing: Vec<String> = tx.prepare(
                        "SELECT DISTINCT value FROM leads WHERE conversation_id=?1 AND kind IN ('phone','wechat')"
                    )?.query_map([&job.conversation], |row| row.get(0))?
                        .collect::<std::result::Result<_, _>>()?;
                    let indicated_value = fields["contact_value"].as_str().or(phone);
                    let indicated = indicated_value
                        .filter(|value| existing.iter().any(|item| item == value));
                    let mobile_values: Vec<&str> = existing.iter().map(String::as_str)
                        .filter(|value| value.len() == 11 && value.bytes().all(|byte| byte.is_ascii_digit()))
                        .collect();
                    let value = indicated.or_else(|| {
                        if indicated_value.is_none() && mobile_values.len() == 1 {
                            Some(mobile_values[0])
                        } else { None }
                    });
                    if let Some(value) = value {
                        let already_exists: bool = tx.query_row(
                            "SELECT EXISTS(SELECT 1 FROM leads WHERE conversation_id=?1 AND value=?2 AND kind=?3)",
                            params![job.conversation, value, kind], |row| row.get(0))?;
                        if already_exists {
                            tx.execute(
                                "UPDATE leads SET exported_at=COALESCE(exported_at,(SELECT MAX(exported_at) FROM leads WHERE conversation_id=?1 AND value=?2)) WHERE conversation_id=?1 AND value=?2 AND kind=?3",
                                params![job.conversation, value, kind],
                            )?;
                            tx.execute("DELETE FROM leads WHERE conversation_id=?1 AND value=?2 AND kind<>?3",
                                params![job.conversation, value, kind])?;
                        } else {
                            tx.execute("UPDATE leads SET kind=?1 WHERE conversation_id=?2 AND value=?3",
                                params![kind, job.conversation, value])?;
                        }
                    }
                }

                let candidates: Vec<(&str, &str)> = match method {
                    Some("none") => Vec::new(),
                    Some("phone") => phone.map(|value| vec![("phone", value)]).unwrap_or_default(),
                    Some("wechat") => wechat.map(|value| vec![("wechat", value)])
                        .or_else(|| phone.map(|value| vec![("wechat", value)]))
                        .unwrap_or_default(),
                    _ => phone.into_iter().map(|value| ("phone", value))
                        .chain(wechat.into_iter().map(|value| ("wechat", value))).collect(),
                };
                for (kind, value) in candidates {
                    tx.execute("INSERT OR IGNORE INTO leads(id,conversation_id,event_id,kind,value) VALUES(?1,?2,?3,?4,?5)",
                        params![id(),job.conversation,job.id,kind,value])?;
                }
            }
            if decision["action"] == "peer" {
                tx.execute("UPDATE conversations SET is_peer=1 WHERE id=?1", [&job.conversation])?;
                tx.execute("DELETE FROM leads WHERE conversation_id=?1 AND status='candidate'", [&job.conversation])?;
            }
            if at_limit {
                // The acknowledgment can be sent; later incoming messages are held for manual review.
                tx.execute(
                    "UPDATE conversations SET mode='paused' WHERE id=?1",
                    [&job.conversation],
                )?;
                if at_limit {
                    tx.execute("UPDATE events SET status='held',error='automatic reply limit reached'
                        WHERE conversation_id=?1 AND status='pending'", [&job.conversation])?;
                }
            }
        }
        let event_status = if applied {
            "processed"
        } else if mode == "paused" || merchant_paused || reply_rounds >= MAX_REPLY_ROUNDS {
            "held"
        } else {
            "cancelled"
        };
        tx.execute(
            "UPDATE events SET status=?1,error=NULL WHERE id=?2",
            params![event_status, job.id],
        )?;
        tx.commit()?;
    }
    Ok(())
}

async fn send_next(app: &App) -> Result<()> {
    let job: Option<(String, String, String, String, String, String, String)> = {
        let db = app.db.lock().unwrap();
        db.execute("UPDATE outbox SET status='cancelled',error='merchant robot paused'
            WHERE status='queued' AND EXISTS (SELECT 1 FROM conversations c
            JOIN merchant_accounts a ON a.id=c.account_id JOIN merchants m ON m.id=a.merchant_id
            WHERE c.id=outbox.conversation_id AND m.bot_paused=1)", [])?;
        db.execute("UPDATE outbox SET status='cancelled',error='stale conversation version' WHERE status='queued'
            AND NOT (part_index>0 AND EXISTS(SELECT 1 FROM outbox first WHERE first.event_id=outbox.event_id
            AND first.part_index=0 AND first.status IN ('accepted','mock_sent')))
            AND (source='human' OR (source='bot' AND (revision != (SELECT revision FROM conversations WHERE id=outbox.conversation_id)
            OR (SELECT mode FROM conversations WHERE id=outbox.conversation_id)<>'bot'))
            OR (source='bot_handoff' AND revision != (SELECT revision FROM conversations WHERE id=outbox.conversation_id)))",[])?;
        let job = db.query_row("SELECT o.id,c.account_id,c.external_id,c.customer_id,e.message_id,o.text,o.source FROM outbox o JOIN conversations c ON c.id=o.conversation_id JOIN events e ON e.id=o.event_id WHERE o.status='queued'
            AND (o.part_index=0 OR EXISTS(SELECT 1 FROM outbox first WHERE first.event_id=o.event_id
            AND first.part_index=0 AND first.status IN ('accepted','mock_sent')))
            ORDER BY o.rowid LIMIT 1",[],|r| Ok((r.get(0)?,r.get::<_,String>(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?))).optional()?;
        if let Some((id, account, _, _, _, _, _)) = &job {
            let age: i64 = db.query_row("SELECT unixepoch()-MAX(e.created_at) FROM outbox o JOIN events e ON e.conversation_id=o.conversation_id AND e.direction='customer' WHERE o.id=?1",[id],|r| r.get(0))?;
            let account_config = stored_account(&db,account)?.or_else(|| app.accounts.get(account).cloned());
            if account_config.is_none_or(|config| age > config.reply_window_seconds) {
                db.execute("UPDATE outbox SET status='blocked',error='configured reply window expired' WHERE id=?1",[id])?;
                return Ok(());
            }
        }
        job
    };
    let Some((id, account, conversation, customer, message, text, _source)) = job else {
        return Ok(());
    };
    let claimed=app.db.lock().unwrap().execute("UPDATE outbox SET status='sending' WHERE id=?1 AND status='queued'
        AND NOT EXISTS (SELECT 1 FROM conversations c JOIN merchant_accounts a ON a.id=c.account_id
        JOIN merchants m ON m.id=a.merchant_id WHERE c.id=outbox.conversation_id AND m.bot_paused=1)
        AND ((part_index>0 AND EXISTS(SELECT 1 FROM outbox first WHERE first.event_id=outbox.event_id
        AND first.part_index=0 AND first.status IN ('accepted','mock_sent')))
        OR (revision=(SELECT revision FROM conversations WHERE id=outbox.conversation_id)
        AND (source='bot_handoff' OR (source='bot' AND (SELECT mode FROM conversations WHERE id=outbox.conversation_id)='bot'))))",[&id])?;
    if claimed == 0 {
        return Ok(());
    }
    let result: Result<Value> = if app.send_mode == "mock" {
        Ok(json!({"mock":true,"message_id":format!("mock-{id}")}))
    } else {
        async {
            let response = app.client.post(&app.send_url).bearer_auth(&app.send_key)
                .header("Idempotency-Key",&id).json(&json!({"account_id":account,"conversation_id":conversation,"customer_id":customer,"reply_to_message_id":message,"text":text,"idempotency_key":id})).send().await?;
            if !response.status().is_success() { bail!("platform HTTP {}",response.status()); }
            let receipt: Value = response.json().await?;
            if receipt["accepted"] != true || receipt["message_id"].as_str().is_none_or(str::is_empty) { bail!("platform response has no confirmed acceptance receipt"); }
            Ok(receipt)
        }.await
    };
    let db = app.db.lock().unwrap();
    match result {
        Ok(receipt) => {
            db.execute(
                "UPDATE outbox SET status=?1,receipt=?2,sent_at=unixepoch() WHERE id=?3",
                params![
                    if app.send_mode == "mock" {
                        "mock_sent"
                    } else {
                        "accepted"
                    },
                    receipt.to_string(),
                    id
                ],
            )?;
        }
        Err(error) => {
            db.execute(
                "UPDATE outbox SET status='uncertain',error=?1 WHERE id=?2",
                params![error.to_string(), id],
            )?;
        }
    }
    Ok(())
}
