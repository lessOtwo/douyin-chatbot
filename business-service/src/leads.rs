use super::{App, ApiError, ApiResult, admin_auth, problem};
use axum::{
    Json,
    body::Body,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{Html, Response},
};
use rusqlite::{OptionalExtension, params};
use rust_xlsxwriter::Workbook;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use uuid::Uuid;

const LINK_SECONDS: i64 = 7 * 24 * 60 * 60;

#[derive(Serialize, Clone)]
struct LeadRow {
    id: String,
    conversation_id: String,
    account_id: String,
    douyin_nickname: String,
    store_name: String,
    external_id: String,
    kind: String,
    value: String,
    status: String,
    created_at: i64,
    exported_at: Option<i64>,
}

fn all_rows(app: &App, db: &rusqlite::Connection) -> rusqlite::Result<Vec<LeadRow>> {
    let mut stmt = db.prepare(
        "SELECT l.id,l.conversation_id,c.account_id,COALESCE(a.douyin_nickname,''),
         COALESCE(json_extract(v.profile_json,'$.fields.\"完整门店名称\"'),''),
         c.external_id,l.kind,l.value,l.status,l.created_at,l.exported_at
         FROM leads l JOIN conversations c ON c.id=l.conversation_id
         LEFT JOIN merchant_accounts a ON a.id=c.account_id
         LEFT JOIN merchants m ON m.id=a.merchant_id
         LEFT JOIN merchant_versions v ON v.id=m.current_version_id
         ORDER BY l.created_at DESC,l.rowid DESC",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(LeadRow {
            id: row.get(0)?, conversation_id: row.get(1)?, account_id: row.get(2)?,
            douyin_nickname: row.get(3)?, store_name: row.get(4)?,
            external_id: row.get(5)?, kind: row.get(6)?, value: row.get(7)?,
            status: row.get(8)?, created_at: row.get(9)?, exported_at: row.get(10)?,
        })
    })?;
    rows.map(|row| {
        let mut row = row?;
        if row.douyin_nickname.is_empty() {
            if let Some(account) = app.accounts.get(&row.account_id) {
                row.douyin_nickname = account.douyin_nickname.clone();
            }
        }
        Ok(row)
    }).collect()
}

#[derive(Deserialize)]
pub struct ListQuery {
    #[serde(default)]
    search: String,
    #[serde(default)]
    exported: String,
}

pub async fn list(State(app): State<App>, headers: HeaderMap, Query(query): Query<ListQuery>) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let search = query.search.trim();
    if search.len() > 100 || !["", "all", "yes", "no"].contains(&query.exported.as_str()) {
        return Err(problem(StatusCode::BAD_REQUEST, "无效的线索筛选条件"));
    }
    let db = app.db.lock().unwrap();
    let rows: Vec<LeadRow> = all_rows(&app, &db)?.into_iter().filter(|row| {
        let matches_search = search.is_empty() || [&row.account_id, &row.douyin_nickname, &row.store_name]
            .iter().any(|field| field.to_lowercase().contains(&search.to_lowercase()));
        let matches_exported = match query.exported.as_str() {
            "yes" => row.exported_at.is_some(), "no" => row.exported_at.is_none(), _ => true,
        };
        matches_search && matches_exported
    }).collect();
    Ok(Json(json!({"items": rows, "total": rows.len()})))
}

fn token_hash(token: &str) -> String {
    format!("{:x}", Sha256::digest(token.as_bytes()))
}

fn public_base() -> String {
    std::env::var("BUSINESS_URL").unwrap_or_else(|_| "http://localhost:8088".into())
        .trim_end_matches('/').to_string()
}

fn issue_link(tx: &rusqlite::Transaction<'_>, conversation: &str) -> rusqlite::Result<(String, i64)> {
    tx.execute("DELETE FROM lead_history_links WHERE expires_at<=unixepoch()", [])?;
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let max_event: i64 = tx.query_row(
        "SELECT COALESCE(MAX(rowid),0) FROM events WHERE conversation_id=?1",
        [conversation], |row| row.get(0))?;
    let expires: i64 = tx.query_row("SELECT unixepoch()+?1", [LINK_SECONDS], |row| row.get(0))?;
    tx.execute(
        "INSERT INTO lead_history_links(token_hash,conversation_id,max_event_rowid,expires_at) VALUES(?1,?2,?3,?4)",
        params![token_hash(&token), conversation, max_event, expires])?;
    Ok((format!("{}/lead-history?token={token}", public_base()), expires))
}

pub async fn create_history_link(
    State(app): State<App>, Path(lead_id): Path<String>, headers: HeaderMap,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let conversation: Option<String> = tx.query_row(
        "SELECT conversation_id FROM leads WHERE id=?1", [&lead_id], |row| row.get(0))
        .optional()?;
    let conversation = conversation.ok_or_else(|| problem(StatusCode::NOT_FOUND, "线索不存在"))?;
    let (url, expires_at) = issue_link(&tx, &conversation)?;
    tx.commit()?;
    Ok(Json(json!({"url":url,"expires_at":expires_at})))
}

#[derive(Deserialize)]
pub struct ExportInput {
    ids: Vec<String>,
}

pub async fn export(
    State(app): State<App>, headers: HeaderMap, Json(input): Json<ExportInput>,
) -> Result<Response, ApiError> {
    admin_auth(&app, &headers, true)?;
    if input.ids.is_empty() || input.ids.len() > 10_000 ||
        input.ids.iter().any(|value| value.is_empty() || value.len() > 100) ||
        input.ids.iter().collect::<HashSet<_>>().len() != input.ids.len() {
        return Err(problem(StatusCode::BAD_REQUEST, "请选择不超过一万条不同的线索"));
    }
    let mut db = app.db.lock().unwrap();
    let available: HashMap<String, LeadRow> = all_rows(&app, &db)?.into_iter()
        .map(|row| (row.id.clone(), row)).collect();
    let mut selected = Vec::with_capacity(input.ids.len());
    for lead_id in &input.ids {
        selected.push(available.get(lead_id)
            .cloned().ok_or_else(|| problem(StatusCode::NOT_FOUND, "所选线索不存在，请刷新列表"))?);
    }
    let tx = db.transaction()?;
    let mut workbook = Workbook::new();
    let sheet = workbook.add_worksheet();
    for (column, title) in ["类型", "联系字段", "会话记录链接"].iter().enumerate() {
        sheet.write_string(0, column as u16, *title).map_err(anyhow::Error::from)?;
    }
    let mut links: HashMap<String, String> = HashMap::new();
    for (index, row) in selected.iter().enumerate() {
        let kind = match row.kind.as_str() {
            "phone" => "电话", "wechat" => "微信",
            _ => return Err(problem(StatusCode::BAD_REQUEST, "所选线索类型不支持导出")),
        };
        let link = if let Some(link) = links.get(&row.conversation_id) { link.clone() }
            else {
                let (link, _) = issue_link(&tx, &row.conversation_id)?;
                links.insert(row.conversation_id.clone(), link.clone());
                link
            };
        let line = (index + 1) as u32;
        sheet.write_string(line, 0, kind)
            .map_err(anyhow::Error::from)?;
        sheet.write_string(line, 1, &row.value).map_err(anyhow::Error::from)?;
        sheet.write_url(line, 2, link.as_str()).map_err(anyhow::Error::from)?;
    }
    let bytes = workbook.save_to_buffer().map_err(anyhow::Error::from)?;
    for row in &selected {
        tx.execute("UPDATE leads SET exported_at=unixepoch() WHERE id=?1", [&row.id])?;
    }
    tx.commit()?;
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        .header("content-disposition", "attachment; filename=leads.xlsx")
        .header("cache-control", "no-store")
        .header("x-content-type-options", "nosniff")
        .body(Body::from(bytes)).expect("static export response headers"))
}

fn html_escape(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
        .replace('"', "&quot;").replace('\'', "&#39;")
}

pub async fn history_page(
    State(app): State<App>, Query(query): Query<HashMap<String, String>>,
) -> Result<Response, ApiError> {
    let token = query.get("token").map(String::as_str).unwrap_or("");
    if token.len() != 64 || !token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(problem(StatusCode::NOT_FOUND, "会话链接无效或已过期"));
    }
    let db = app.db.lock().unwrap();
    let info: Option<(String, i64, i64, i64, String, String, String, String)> = db.query_row(
        "SELECT h.conversation_id,h.max_event_rowid,h.created_at,h.expires_at,
         c.external_id,c.account_id,COALESCE(a.douyin_nickname,''),
         COALESCE(json_extract(v.profile_json,'$.fields.\"完整门店名称\"'),'')
         FROM lead_history_links h JOIN conversations c ON c.id=h.conversation_id
         LEFT JOIN merchant_accounts a ON a.id=c.account_id
         LEFT JOIN merchants m ON m.id=a.merchant_id
         LEFT JOIN merchant_versions v ON v.id=m.current_version_id
         WHERE h.token_hash=?1 AND h.expires_at>unixepoch()",
        [token_hash(token)], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?,
            row.get(4)?,row.get(5)?,row.get(6)?,row.get(7)?)))
        .optional()?;
    let (conversation, max_event, created, expires, external, account, nickname, store) =
        info.ok_or_else(|| problem(StatusCode::NOT_FOUND, "会话链接无效或已过期"))?;
    let mut stmt = db.prepare(
        "SELECT e.id,e.direction,e.text,e.created_at,o.text,o.sent_at FROM events e
         LEFT JOIN outbox o ON o.event_id=e.id AND o.status IN ('accepted','mock_sent')
              AND o.sent_at<=?3
         WHERE e.conversation_id=?1 AND e.rowid<=?2 ORDER BY e.rowid,o.part_index",
    )?;
    let messages = stmt.query_map(params![conversation,max_event,created], |row| {
        Ok((row.get::<_, String>(0)?,row.get::<_, String>(1)?,row.get::<_, String>(2)?,
            row.get::<_, i64>(3)?,row.get::<_, Option<String>>(4)?,row.get::<_, Option<i64>>(5)?))
    })?.collect::<rusqlite::Result<Vec<_>>>()?;
    let mut transcript = String::new();
    let mut previous_event = String::new();
    for (event_id, direction, body, timestamp, reply, sent_at) in messages {
        if previous_event != event_id {
            let who = if direction == "agent" { "历史人工" } else { "客户" };
            transcript.push_str(&format!(
                "<div class=\"bubble customer\"><div class=\"meta\">{who} · <time data-time=\"{timestamp}\"></time></div>{}</div>",
                html_escape(&body)));
            previous_event = event_id;
        }
        if let Some(reply) = reply {
            transcript.push_str(&format!(
                "<div class=\"bubble bot\"><div class=\"meta\">机器人 · <time data-time=\"{}\"></time></div>{}</div>",
                sent_at.unwrap_or(timestamp), html_escape(&reply)));
        }
    }
    let title = if store.is_empty() { format!("{} {}", account, nickname) }
        else { format!("{} · {} {}", store, account, nickname) };
    let page = format!(r#"<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>会话记录</title><style>
body{{margin:0;background:#f5f7fb;color:#17243b;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:900px;margin:24px auto;padding:20px}}h1{{font-size:22px;margin:0}}
.sub{{color:#758096;margin:6px 0 22px}}.bubble{{padding:13px 16px;border-radius:9px;margin:9px 0;white-space:pre-wrap;overflow-wrap:anywhere}}
.customer{{background:#edf1f9}}.bot{{background:#e8f0ff;margin-left:26px}}.meta{{color:#66758f;font-size:12px;margin-bottom:4px}}
</style></head><body><main><h1>会话记录</h1><div class="sub">{} · 会话 {}<br>链接有效至 <time data-time="{}"></time></div>{}</main>
<script>document.querySelectorAll('time[data-time]').forEach(e=>e.textContent=new Date(Number(e.dataset.time)*1000).toLocaleString('zh-CN',{{hour12:false}}))</script>
</body></html>"#, html_escape(&title), html_escape(&external), expires, transcript);
    Ok(Response::builder().status(StatusCode::OK)
        .header("content-type", "text/html; charset=utf-8")
        .header("cache-control", "no-store")
        .header("referrer-policy", "no-referrer")
        .header("x-robots-tag", "noindex")
        .header("x-content-type-options", "nosniff")
        .body(Body::from(Html(page).0)).expect("static history response headers"))
}
