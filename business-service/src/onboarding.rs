//! One-use merchant invitation, project-constrained form and reviewable profile versions.
use super::*;
use axum::{extract::Query, http::header, response::Response};
use sha2::{Digest, Sha256};
use std::collections::HashSet;

fn hash(token: &str) -> String {
    format!("{:x}", Sha256::digest(token.as_bytes()))
}

#[derive(Deserialize)]
pub struct InviteInput {
    merchant_id: Option<String>,
    expires_hours: Option<i64>,
}

pub async fn create_invite(State(app): State<App>, headers: HeaderMap, Json(input): Json<InviteInput>) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let hours = input.expires_hours.unwrap_or(6);
    if ![6, 12, 24].contains(&hours) {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "有效期应为 6、12 或 24 小时"));
    }
    let token = format!("{}{}", id(), id());
    let invite_id = id();
    let db = app.db.lock().unwrap();
    if let Some(merchant_id) = input.merchant_id.as_deref() {
        let found: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM merchants WHERE id=?1)",[merchant_id],|r|r.get(0))?;
        if !found { return Err(problem(StatusCode::NOT_FOUND, "商家不存在")); }
    }
    db.execute("INSERT INTO merchant_invitations(id,token_hash,merchant_id,status,expires_at) VALUES(?1,?2,?3,'active',unixepoch()+?4)",params![invite_id,hash(&token),input.merchant_id,hours*3600])?;
    Ok(Json(json!({"id":invite_id,"token":token,"expires_hours":hours})))
}

pub async fn list_invites(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let mut stmt = db.prepare("SELECT id,merchant_id,status,expires_at,created_at,used_at FROM merchant_invitations ORDER BY rowid DESC")?;
    let items = stmt.query_map([], |r| Ok(json!({
        "id":r.get::<_,String>(0)?,"merchant_id":r.get::<_,Option<String>>(1)?,
        "status":r.get::<_,String>(2)?,"expires_at":r.get::<_,i64>(3)?,
        "created_at":r.get::<_,i64>(4)?,"used_at":r.get::<_,Option<i64>>(5)?
    })))?.collect::<std::result::Result<Vec<_>,_>>()?;
    Ok(Json(json!({"items":items})))
}

pub async fn revoke(State(app): State<App>, Path(invite_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let changed = app.db.lock().unwrap().execute(
        "UPDATE merchant_invitations SET status='revoked' WHERE id=?1 AND status='active'",[&invite_id])?;
    Ok(Json(json!({"changed":changed})))
}

pub async fn delete_revoked(State(app): State<App>, Path(invite_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let changed = app.db.lock().unwrap().execute(
        "DELETE FROM merchant_invitations WHERE id=?1 AND status='revoked'",[&invite_id])?;
    if changed == 0 { return Err(problem(StatusCode::CONFLICT,"仅已撤销邀请可以删除记录")); }
    Ok(Json(json!({"deleted":true})))
}

#[derive(Deserialize)]
pub struct TokenQuery {
    token: Option<String>,
}
fn active_invite(db: &Connection, token: &str) -> std::result::Result<Option<(String, Option<String>)>, rusqlite::Error> {
    db.query_row("SELECT id,merchant_id FROM merchant_invitations WHERE token_hash=?1 AND status='active' AND expires_at>unixepoch()",[hash(token)],|r|Ok((r.get(0)?,r.get(1)?))).optional()
}

pub async fn page(State(app): State<App>, Query(input): Query<TokenQuery>) -> std::result::Result<Response, ApiError> {
    let token = input.token.unwrap_or_default();
    if active_invite(&app.db.lock().unwrap(), &token)?.is_none() {
        return Ok((StatusCode::NOT_FOUND,[(header::CACHE_CONTROL,"no-store"),(header::REFERRER_POLICY,"no-referrer")],
            Html("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>邀请链接已失效</title><body style=\"font:16px system-ui,Microsoft YaHei,sans-serif;background:#f4f6fb;color:#1a2a45;padding:8vh 8vw\"><h1>邀请链接无效或已过期</h1><p>请联系平台重新获取入驻邀请。</p></body></html>"))
            .into_response());
    }
    Ok(([(header::CACHE_CONTROL,"no-store"),(header::REFERRER_POLICY,"no-referrer")],
        Html(include_str!("../web/onboard.html"))).into_response())
}

pub async fn context(State(app): State<App>, Query(input): Query<TokenQuery>) -> ApiResult {
    let token = input.token.unwrap_or_default();
    let prefill = {
        let db = app.db.lock().unwrap();
        let (_, merchant_id) = active_invite(&db, &token)?
            .ok_or_else(||problem(StatusCode::NOT_FOUND,"邀请链接无效或已过期"))?;
        if let Some(merchant_id) = merchant_id {
            let latest: Option<(String,String,String,String,String)> = db.query_row(
                "SELECT status,profile_json,contact_name,contact_phone,review_reason FROM merchant_versions WHERE merchant_id=?1 ORDER BY version DESC LIMIT 1",
                [&merchant_id], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?))).optional()?;
            latest.and_then(|(status,profile,contact_name,contact_phone,review_reason)| {
                (status == "rejected").then(|| json!({
                    "profile":serde_json::from_str::<Value>(&profile).unwrap_or(Value::Null),
                    "contact_name":contact_name,"contact_phone":contact_phone,
                    "review_reason":review_reason
                }))
            })
        } else { None }
    };
    let Json(mut result) = catalog::public(State(app)).await?;
    if let Some(prefill) = prefill { result["prefill"] = prefill; }
    Ok(Json(result))
}

#[derive(Deserialize)]
pub struct MerchantFields {
    douyin_account: String,
    douyin_nickname: String,
    store_name: String,
    service_area: String,
    #[serde(default)] business_hours: String,
    #[serde(default)] address: String,
    #[serde(default)] store_phone: String,
    #[serde(default)] store_phone_public: bool,
    materials_or_services: String,
}

#[derive(Deserialize)]
pub struct FormInput {
    token: String,
    industry_id: String,
    #[serde(default)] main_project_ids: Vec<String>,
    #[serde(default)] available_project_ids: Vec<String>,
    #[serde(default)] excluded_project_ids: Vec<String>,
    fields: MerchantFields,
    contact_name: String,
    contact_phone: String,
}

fn validate_profile(db: &Connection, input: &FormInput) -> std::result::Result<Value, ApiError> {
    let mut problems = Vec::new();
    let fields = &input.fields;
    for (label, value, required) in [
        ("门店抖音号",&fields.douyin_account,true),
        ("抖音昵称",&fields.douyin_nickname,true),
        ("完整门店名称",&fields.store_name,true),
        ("服务区域",&fields.service_area,true),
        ("门店营业时间",&fields.business_hours,false),
        ("门店详细地址",&fields.address,false),
        ("门店联系电话",&fields.store_phone,false),
        ("资料或服务",&fields.materials_or_services,true),
        ("入驻联系人姓名",&input.contact_name,true),
        ("入驻联系人电话",&input.contact_phone,true),
    ] {
        if required && value.trim().is_empty() { problems.push(format!("{label}必填")); }
        if value.chars().count() > 500 { problems.push(format!("{label}不能超过 500 字")); }
    }
    if !fields.douyin_account.trim().bytes().all(|digit| digit.is_ascii_digit()) {
        problems.push("门店抖音号只能填写数字，不能填写抖音昵称".into());
    }
    if input.contact_name.trim().chars().count() < 2 || input.contact_name.trim().chars().count() > 80 {
        problems.push("入驻联系人姓名应为 2 至 80 字".into());
    }
    if input.contact_phone.trim().len() < 5 || input.contact_phone.trim().len() > 30 {
        problems.push("入驻联系人电话长度无效".into());
    }
    if fields.store_phone_public && fields.store_phone.trim().is_empty() {
        problems.push("公开门店电话前请填写门店联系电话".into());
    }
    if input.main_project_ids.is_empty() { problems.push("至少选择一项主做项目".into()); }

    let industry: Option<String> = db.query_row(
        "SELECT published_name FROM industries WHERE id=?1 AND published_at IS NOT NULL",
        [&input.industry_id],|r|r.get(0)).optional()?;
    if industry.is_none() { problems.push("请选择已发布行业".into()); }

    let mut seen = HashSet::new();
    let mut projects = serde_json::Map::new();
    for (kind, ids) in [
        ("main",&input.main_project_ids),
        ("available",&input.available_project_ids),
        ("excluded",&input.excluded_project_ids)
    ] {
        let mut selected = Vec::new();
        for project_id in ids {
            if !seen.insert(project_id) {
                problems.push("同一项目不能重复选择或同时属于多个分组".into());
                continue;
            }
            let project: Option<(String,String)> = db.query_row(
                "SELECT published_name,published_question FROM industry_projects WHERE id=?1 AND industry_id=?2 AND published_at IS NOT NULL",
                params![project_id,input.industry_id],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
            if let Some((name,question)) = project {
                selected.push(json!({"id":project_id,"name":name,"question":question}));
            } else {
                problems.push("所选项目未发布或不属于当前行业".into());
            }
        }
        projects.insert(kind.into(),json!(selected));
    }
    if !problems.is_empty() {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,&problems.join("；")));
    }
    let fields = json!({
        "门店抖音号":fields.douyin_account.trim(),
        "抖音昵称":fields.douyin_nickname.trim(),
        "完整门店名称":fields.store_name.trim(),
        "所属行业":industry.as_deref().unwrap_or_default(),
        "服务区域":fields.service_area.trim(),
        "门店营业时间":fields.business_hours.trim(),
        "门店详细地址":fields.address.trim(),
        "门店联系电话":fields.store_phone.trim(),
        "门店电话可公开":if fields.store_phone_public {"是"} else {"否"},
        "资料或服务":fields.materials_or_services.trim()
    });
    Ok(json!({"fields":fields,"industry_id":input.industry_id,"projects":projects}))
}

pub async fn preview(State(app): State<App>, Json(input): Json<FormInput>) -> ApiResult {
    let db = app.db.lock().unwrap();
    if active_invite(&db, &input.token)?.is_none() {
        return Err(problem(StatusCode::NOT_FOUND, "邀请链接无效或已过期"));
    }
    let profile = validate_profile(&db,&input)?;
    Ok(Json(json!({"valid":true,"profile":profile})))
}

pub async fn submit(State(app): State<App>, Json(input): Json<FormInput>) -> ApiResult {
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let (invite_id, existing_merchant) = active_invite(&tx,&input.token)?
        .ok_or_else(||problem(StatusCode::NOT_FOUND,"邀请链接无效或已使用"))?;
    let profile = validate_profile(&tx,&input)?;
    let merchant_id = existing_merchant.unwrap_or_else(id);
    tx.execute("INSERT OR IGNORE INTO merchants(id) VALUES(?1)",[&merchant_id])?;
    let version: i64 = tx.query_row(
        "SELECT COALESCE(MAX(version),0)+1 FROM merchant_versions WHERE merchant_id=?1",
        [&merchant_id],|r|r.get(0))?;
    let version_id = id();
    tx.execute("INSERT INTO merchant_versions(id,merchant_id,version,status,profile_json,contact_name,contact_phone) VALUES(?1,?2,?3,'pending',?4,?5,?6)",
        params![version_id,merchant_id,version,profile.to_string(),input.contact_name.trim(),input.contact_phone.trim()])?;
    let used = tx.execute("UPDATE merchant_invitations SET status='used',used_at=unixepoch(),merchant_id=?2 WHERE id=?1 AND status='active' AND expires_at>unixepoch()",params![invite_id,merchant_id])?;
    if used != 1 { return Err(problem(StatusCode::CONFLICT,"邀请链接已使用")); }
    tx.commit()?;
    Ok(Json(json!({"merchant_id":merchant_id,"version_id":version_id,"version":version,"status":"pending"})))
}

pub async fn list_merchants(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let mut stmt=db.prepare("SELECT v.id,v.merchant_id,v.version,v.status,v.profile_json,v.contact_name,v.contact_phone,v.submitted_at,v.reviewed_at,v.review_reason,m.current_version_id FROM merchant_versions v JOIN merchants m ON m.id=v.merchant_id ORDER BY v.rowid DESC")?;
    let items=stmt.query_map([],|r|{
        let profile: String=r.get(4)?;
        Ok(json!({"id":r.get::<_,String>(0)?,"merchant_id":r.get::<_,String>(1)?,"version":r.get::<_,i64>(2)?,"status":r.get::<_,String>(3)?,"profile":serde_json::from_str::<Value>(&profile).unwrap_or(Value::Null),"contact_name":r.get::<_,String>(5)?,"contact_phone":r.get::<_,String>(6)?,"submitted_at":r.get::<_,i64>(7)?,"reviewed_at":r.get::<_,Option<i64>>(8)?,"review_reason":r.get::<_,String>(9)?,"current":r.get::<_,Option<String>>(10)?==Some(r.get::<_,String>(0)?)}))
    })?.collect::<std::result::Result<Vec<_>,_>>()?;
    Ok(Json(json!({"items":items})))
}

pub async fn delete_merchant(State(app): State<App>, Path(merchant_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let exists: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM merchants WHERE id=?1)",
        [&merchant_id], |row| row.get(0))?;
    if !exists { return Err(problem(StatusCode::NOT_FOUND, "商家不存在")); }

    let mut account_ids: Vec<String> = tx.prepare("SELECT id FROM merchant_accounts WHERE merchant_id=?1")?
        .query_map([&merchant_id], |row| row.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for account in app.accounts.values().filter(|account| account.merchant_id == merchant_id) {
        if stored_account(&tx, &account.id)?.is_some_and(|stored| stored.merchant_id != merchant_id) {
            continue;
        }
        if !account_ids.contains(&account.id) { account_ids.push(account.id.clone()); }
    }
    for account_id in &account_ids {
        let busy: bool = tx.query_row("SELECT EXISTS(
            SELECT 1 FROM events WHERE account_id=?1 AND status='processing'
            UNION SELECT 1 FROM outbox o JOIN conversations c ON c.id=o.conversation_id
            WHERE c.account_id=?1 AND o.status='sending')", [account_id], |row| row.get(0))?;
        if busy { return Err(problem(StatusCode::CONFLICT, "该商家仍有正在处理或发送的消息，请稍后重试")); }
    }

    for account_id in &account_ids {
        if app.accounts.get(account_id).is_some_and(|account| account.merchant_id == merchant_id) {
            tx.execute("INSERT OR IGNORE INTO removed_accounts(id) VALUES(?1)", [account_id])?;
        }
        for table in ["lead_history_links", "unanswered_questions", "leads", "outbox"] {
            tx.execute(&format!("DELETE FROM {table} WHERE conversation_id IN (
                SELECT id FROM conversations WHERE account_id=?1)"), [account_id])?;
        }
        tx.execute("DELETE FROM decisions WHERE event_id IN (
            SELECT id FROM events WHERE account_id=?1)", [account_id])?;
        tx.execute("DELETE FROM events WHERE account_id=?1", [account_id])?;
        tx.execute("DELETE FROM conversations WHERE account_id=?1", [account_id])?;
    }
    tx.execute("DELETE FROM merchant_invitations WHERE merchant_id=?1", [&merchant_id])?;
    tx.execute("DELETE FROM merchant_versions WHERE merchant_id=?1", [&merchant_id])?;
    tx.execute("DELETE FROM merchant_accounts WHERE merchant_id=?1", [&merchant_id])?;
    tx.execute("DELETE FROM merchants WHERE id=?1", [&merchant_id])?;
    tx.commit()?;
    Ok(Json(json!({"deleted":true,"merchant_id":merchant_id})))
}

#[derive(Deserialize)]
pub struct Review {
    decision: String,
    fastgpt_app_id: Option<String>,
    reason: Option<String>,
}
pub async fn review(State(app): State<App>, Path(version_id): Path<String>, headers: HeaderMap, Json(input): Json<Review>) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    if !["approved","rejected"].contains(&input.decision.as_str()) {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,"审核结果无效"));
    }
    let reason = if input.decision == "rejected" {
        let reason = input.reason.as_deref().unwrap_or("").trim();
        if reason.is_empty() || reason.chars().count() > 500 {
            return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,"退回原因应为 1 至 500 字"));
        }
        reason
    } else { "" };
    let workflow_id = if input.decision == "approved" {
        let workflow_id = input.fastgpt_app_id.as_deref().unwrap_or("").trim();
        if workflow_id.is_empty() {
            return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,"审核通过前请选择已发布的工作流"));
        }
        if setting("FASTGPT_APP_KEY", "").trim().is_empty() {
            return Err(problem(StatusCode::SERVICE_UNAVAILABLE,"平台工作流调用密钥尚未配置，不能审核通过"));
        }
        app.tools.verify_call_key().await.map_err(|error| {
            eprintln!("FastGPT call key validation failed: {error:#}");
            problem(StatusCode::BAD_GATEWAY,"平台工作流调用密钥无效或模型平台不可用，不能审核通过")
        })?;
        let workflows = app.tools.published_workflows().await.map_err(|error| {
            eprintln!("FastGPT workflow validation failed: {error:#}");
            problem(StatusCode::BAD_GATEWAY,"无法核验工作流发布状态，请稍后重试")
        })?;
        if !workflows.iter().any(|item| item["id"].as_str() == Some(workflow_id)) {
            return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,"所选工作流不可用或尚未发布，请重新选择"));
        }
        Some(workflow_id.to_owned())
    } else { None };
    let mut db = app.db.lock().unwrap();
    let tx = db.transaction()?;
    let pending: Option<(String,String)> = tx.query_row(
        "SELECT merchant_id,profile_json FROM merchant_versions WHERE id=?1 AND status='pending'",
        [&version_id],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    let (merchant,profile_json) = pending.ok_or_else(||problem(StatusCode::CONFLICT,"仅待审核版本可以审核"))?;
    if input.decision == "approved" {
        let profile: Value = serde_json::from_str(&profile_json).map_err(anyhow::Error::from)?;
        let account_id = profile["fields"]["门店抖音号"].as_str().unwrap_or("").trim();
        let douyin_nickname = profile["fields"]["抖音昵称"].as_str().unwrap_or("").trim();
        let industry = profile["fields"]["所属行业"].as_str().unwrap_or("").trim();
        if account_id.is_empty() || douyin_nickname.is_empty() || industry.is_empty() {
            return Err(problem(StatusCode::UNPROCESSABLE_ENTITY,"商家资料缺少门店抖音号、昵称或行业"));
        }
        let existing: Option<String> = tx.query_row(
            "SELECT id FROM merchant_accounts WHERE merchant_id=?1",[&merchant],|r|r.get(0)).optional()?;
        if let Some(existing_id) = existing {
            if existing_id != account_id {
                return Err(problem(StatusCode::CONFLICT,"门店抖音号已变更，请先处理账号迁移后再审核"));
            }
            tx.execute("UPDATE merchant_accounts SET industry=?1,douyin_nickname=?2,fastgpt_app_id=?3 WHERE merchant_id=?4",params![industry,douyin_nickname,workflow_id,merchant])?;
        } else {
            let occupied: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM merchant_accounts WHERE id=?1 UNION SELECT 1 FROM removed_accounts WHERE id=?1)",
                [account_id],|r|r.get(0))?;
            if occupied || app.accounts.contains_key(account_id) {
                return Err(problem(StatusCode::CONFLICT,"该门店抖音号已有账号记录，不能重复绑定"));
            }
            tx.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id) VALUES(?1,?2,?3,?4,?5)",
                params![account_id,merchant,douyin_nickname,industry,workflow_id])?;
        }
    }
    tx.execute("UPDATE merchant_versions SET status=?1,reviewed_at=unixepoch(),review_reason=?2 WHERE id=?3",
        params![input.decision,reason,version_id])?;
    if input.decision == "approved" {
        tx.execute("UPDATE merchants SET current_version_id=?1 WHERE id=?2",
            params![version_id,merchant])?;
    }
    let new_token = if input.decision == "rejected" {
        let token = format!("{}{}",id(),id());
        tx.execute("INSERT INTO merchant_invitations(id,token_hash,merchant_id,status,expires_at) VALUES(?1,?2,?3,'active',unixepoch()+21600)",
            params![id(),hash(&token),merchant])?;
        Some(token)
    } else { None };
    tx.commit()?;
    Ok(Json(json!({"id":version_id,"status":input.decision,"token":new_token,"expires_hours":new_token.as_ref().map(|_|6)})))
}
