//! Bounded administrative lists. Count and filters are evaluated before reading a page.
use super::*;
use axum::extract::Query;
use rusqlite::{ToSql, types::ValueRef};

const SIZE: i64 = 20;

#[derive(Deserialize)]
pub struct PageQuery {
    page: Option<u32>,
    #[serde(default)]
    search: String,
}

fn bounds(requested: Option<u32>, total: i64) -> Result<(i64, i64), ApiError> {
    let page = requested.unwrap_or(1) as i64;
    if !(1..=1_000_000).contains(&page) {
        return Err(problem(StatusCode::BAD_REQUEST, "页码无效"));
    }
    let page = page.min(((total + SIZE - 1) / SIZE).max(1));
    Ok((page, (page - 1) * SIZE))
}

fn rows(db: &Connection, sql: &str, args: &[&dyn ToSql]) -> rusqlite::Result<Vec<Value>> {
    let mut stmt = db.prepare(sql)?;
    let names: Vec<String> = stmt.column_names().iter().map(|n| n.to_string()).collect();
    stmt.query_map(args, |row| {
        let mut obj = serde_json::Map::new();
        for (index, name) in names.iter().enumerate() {
            let value = match row.get_ref(index)? {
                ValueRef::Null => Value::Null,
                ValueRef::Integer(n) => json!(n),
                ValueRef::Real(n) => json!(n),
                ValueRef::Text(s) => json!(String::from_utf8_lossy(s)),
                ValueRef::Blob(_) => Value::Null,
            };
            obj.insert(name.clone(), value);
        }
        Ok(Value::Object(obj))
    })?.collect()
}

pub async fn list(
    State(app): State<App>, Path(kind): Path<String>, headers: HeaderMap,
    Query(query): Query<PageQuery>,
) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    if query.search.chars().count() > 100 {
        return Err(problem(StatusCode::BAD_REQUEST, "搜索内容过长"));
    }
    let db = app.db.lock().unwrap();
    if kind == "accounts" {
        let removed: HashSet<String> = db.prepare("SELECT id FROM removed_accounts")?
            .query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        let mut configured: Vec<_> = app.accounts.values().filter(|a| !removed.contains(&a.id))
            .filter(|a| db.query_row("SELECT EXISTS(SELECT 1 FROM merchant_accounts WHERE id=?1)", [&a.id], |r| r.get::<_,bool>(0)).map(|exists| !exists).unwrap_or(false))
            .map(|a| json!({"id":a.id,"merchant_id":a.merchant_id,"douyin_nickname":a.douyin_nickname,"industry":a.industry,"fastgpt_app_id":a.fastgpt_app_id})).collect();
        configured.sort_by(|a,b| a["id"].as_str().cmp(&b["id"].as_str()));
        let stored: i64 = db.query_row("SELECT COUNT(*) FROM merchant_accounts WHERE id NOT IN (SELECT id FROM removed_accounts)", [], |r| r.get(0))?;
        let total = stored + configured.len() as i64;
        let (page, offset) = bounds(query.page, total)?;
        let start = (offset as usize).min(configured.len());
        let mut items: Vec<Value> = configured.into_iter().skip(start).take(SIZE as usize).collect();
        let remaining = SIZE - items.len() as i64;
        if remaining > 0 {
            let db_offset = (offset - (total - stored)).max(0);
            items.extend(rows(&db, "SELECT id,merchant_id,douyin_nickname,industry,fastgpt_app_id FROM merchant_accounts WHERE id NOT IN (SELECT id FROM removed_accounts) ORDER BY rowid DESC LIMIT ?1 OFFSET ?2", &[&remaining,&db_offset])?);
        }
        return Ok(Json(json!({"items":items,"total":total,"page":page,"page_size":SIZE})));
    }
    let (from, order, search) = match kind.as_str() {
        "conversations" => ("conversations c", "c.rowid DESC", ""),
        "outbox" => ("outbox o", "o.rowid DESC", ""),
        "errors" => ("events e JOIN conversations c ON c.id=e.conversation_id WHERE e.status IN ('uncertain','blocked')", "e.rowid DESC", ""),
        "industries" => ("industries i", "i.rowid DESC", ""),
        "projects" => ("industry_projects p JOIN industries i ON i.id=p.industry_id WHERE (?1='' OR instr(lower(i.draft_name),lower(?1))>0 OR instr(lower(COALESCE(i.published_name,'')),lower(?1))>0)", "p.rowid", "search"),
        "invitations" => ("merchant_invitations i", "i.rowid DESC", ""),
        "merchants" => ("merchant_versions v JOIN merchants m ON m.id=v.merchant_id", "v.rowid DESC", ""),
        _ => return Err(problem(StatusCode::NOT_FOUND, "列表不存在")),
    };
    let where_args: Vec<&dyn ToSql> = if search.is_empty() { vec![] } else { vec![&query.search] };
    let total: i64 = db.query_row(&format!("SELECT COUNT(*) FROM {from}"), where_args.as_slice(), |r| r.get(0))?;
    let (page, offset) = bounds(query.page, total)?;
    let select = match kind.as_str() {
        "conversations" => "c.*",
        "outbox" => "o.*",
        "errors" => "e.*,c.external_id,c.customer_id,c.customer_nickname,c.mode AS conversation_mode,c.revision AS conversation_revision,c.reply_rounds,(SELECT COUNT(*) FROM outbox o WHERE o.event_id=e.id) AS outbox_count",
        "industries" => "i.id,i.draft_name,i.published_name,i.published_at,i.updated_at",
        "projects" => "p.id,p.industry_id,p.draft_name,p.draft_question,p.published_name,p.published_question,p.published_at,i.draft_name AS industry_name",
        "invitations" => "i.id,i.merchant_id,i.status,i.expires_at,i.created_at,i.used_at",
        "merchants" => "v.id,v.merchant_id,v.version,v.status,v.profile_json,v.contact_name,v.contact_phone,v.submitted_at,v.reviewed_at,v.review_reason,(m.current_version_id=v.id) AS current",
        _ => unreachable!(),
    };
    let sql = format!("SELECT {select} FROM {from} ORDER BY {order} LIMIT ?{} OFFSET ?{}", where_args.len()+1, where_args.len()+2);
    let mut args = where_args;
    args.push(&SIZE);
    args.push(&offset);
    let mut items = rows(&db, &sql, &args)?;
    if kind == "merchants" {
        for item in &mut items {
            let profile = item["profile_json"].as_str().and_then(|s| serde_json::from_str::<Value>(s).ok()).unwrap_or(Value::Null);
            item["profile"] = profile;
            item.as_object_mut().unwrap().remove("profile_json");
        }
    }
    Ok(Json(json!({"items":items,"total":total,"page":page,"page_size":SIZE})))
}

pub async fn history(
    State(app): State<App>, Path(conversation): Path<String>, headers: HeaderMap,
    Query(query): Query<PageQuery>,
) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let item = rows(&db, "SELECT * FROM conversations WHERE id=?1", &[&conversation])?.into_iter().next()
        .ok_or_else(|| problem(StatusCode::NOT_FOUND, "会话不存在"))?;
    let total: i64 = db.query_row("SELECT COUNT(*) FROM events WHERE conversation_id=?1", [&conversation], |r| r.get(0))?;
    let (page, offset) = bounds(query.page, total)?;
    let mut events = rows(&db, "SELECT id,account_id,event_id,message_id,conversation_id,text,revision,status,error,created_at,direction,(image_data IS NOT NULL) AS has_image FROM events WHERE conversation_id=?1 ORDER BY rowid DESC LIMIT ?2 OFFSET ?3", &[&conversation,&SIZE,&offset])?;
    for event in &mut events {
        let event_id = event["id"].as_str().unwrap_or_default();
        event["replies"] = json!(rows(&db, "SELECT * FROM outbox WHERE event_id=?1 ORDER BY part_index", &[&event_id])?);
    }
    Ok(Json(json!({"conversation":item,"items":events,"total":total,"page":page,"page_size":SIZE})))
}

pub async fn options(State(app): State<App>, Path(kind): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let sql = match kind.as_str() {
        "industries" => "SELECT id,draft_name FROM industries ORDER BY draft_name",
        "merchants" => "SELECT m.id AS merchant_id,m.bot_paused,COALESCE(json_extract(v.profile_json,'$.fields.\"完整门店名称\"'),m.id) AS store_name FROM merchants m LEFT JOIN merchant_versions v ON v.id=(SELECT id FROM merchant_versions WHERE merchant_id=m.id ORDER BY version DESC LIMIT 1) ORDER BY m.rowid DESC",
        _ => return Err(problem(StatusCode::NOT_FOUND, "选项不存在")),
    };
    Ok(Json(json!({"items":rows(&db,sql,&[])?})))
}
