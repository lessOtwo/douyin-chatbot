//! Operator-managed industries and project opening questions. Drafts do not change published copy.
use super::*;

#[derive(Deserialize)]
pub struct IndustryDraft {
    id: Option<String>,
    name: String,
}

#[derive(Deserialize)]
pub struct ProjectDraft {
    id: Option<String>,
    industry_id: String,
    name: String,
    question: String,
}

pub async fn list(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let db = app.db.lock().unwrap();
    let mut stmt = db.prepare("SELECT id,draft_name,published_name,published_at,updated_at FROM industries ORDER BY rowid DESC")?;
    let industries = stmt.query_map([], |r| Ok(json!({
        "id":r.get::<_,String>(0)?,"draft_name":r.get::<_,String>(1)?,
        "published_name":r.get::<_,Option<String>>(2)?,
        "published_at":r.get::<_,Option<i64>>(3)?,"updated_at":r.get::<_,i64>(4)?
    })))?.collect::<std::result::Result<Vec<_>,_>>()?;
    let mut stmt = db.prepare("SELECT id,industry_id,draft_name,draft_question,published_name,published_question,published_at FROM industry_projects ORDER BY rowid")?;
    let projects = stmt.query_map([], |r| Ok(json!({
        "id":r.get::<_,String>(0)?,"industry_id":r.get::<_,String>(1)?,
        "draft_name":r.get::<_,String>(2)?,"draft_question":r.get::<_,String>(3)?,
        "published_name":r.get::<_,Option<String>>(4)?,
        "published_question":r.get::<_,Option<String>>(5)?,
        "published_at":r.get::<_,Option<i64>>(6)?
    })))?.collect::<std::result::Result<Vec<_>,_>>()?;
    Ok(Json(json!({"industries":industries,"projects":projects})))
}

pub async fn public(State(app): State<App>) -> ApiResult {
    let db = app.db.lock().unwrap();
    let mut stmt = db.prepare("SELECT id,published_name FROM industries WHERE published_at IS NOT NULL ORDER BY published_name")?;
    let industries = stmt.query_map([], |r| Ok(json!({
        "id":r.get::<_,String>(0)?,"name":r.get::<_,String>(1)?
    })))?.collect::<std::result::Result<Vec<_>,_>>()?;
    let mut stmt = db.prepare("SELECT p.id,p.industry_id,p.published_name,p.published_question FROM industry_projects p JOIN industries i ON i.id=p.industry_id WHERE p.published_at IS NOT NULL AND i.published_at IS NOT NULL ORDER BY p.rowid")?;
    let projects = stmt.query_map([], |r| Ok(json!({
        "id":r.get::<_,String>(0)?,"industry_id":r.get::<_,String>(1)?,
        "name":r.get::<_,String>(2)?,"question":r.get::<_,String>(3)?
    })))?.collect::<std::result::Result<Vec<_>,_>>()?;
    Ok(Json(json!({"industries":industries,"projects":projects})))
}

pub async fn save_industry(State(app): State<App>, headers: HeaderMap, Json(input): Json<IndustryDraft>) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let name = input.name.trim();
    if name.is_empty() || name.chars().count() > 80 {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "行业名称无效"));
    }
    let db = app.db.lock().unwrap();
    let industry_id = input.id.unwrap_or_else(id);
    let changed = db.execute("INSERT INTO industries(id,draft_name,draft_question) VALUES(?1,?2,'') ON CONFLICT(id) DO UPDATE SET draft_name=excluded.draft_name,updated_at=unixepoch()",params![industry_id,name])?;
    Ok(Json(json!({"id":industry_id,"changed":changed})))
}

pub async fn publish_industry(State(app): State<App>, Path(industry_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let db = app.db.lock().unwrap();
    let duplicate: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM industries source JOIN industries other ON other.published_name=source.draft_name AND other.id<>source.id AND other.published_at IS NOT NULL WHERE source.id=?1)",[&industry_id],|r|r.get(0))?;
    if duplicate { return Err(problem(StatusCode::CONFLICT, "已存在同名的已发布行业")); }
    let changed = db.execute("UPDATE industries SET published_name=draft_name,published_at=unixepoch(),updated_at=unixepoch() WHERE id=?1",[&industry_id])?;
    if changed == 0 { return Err(problem(StatusCode::NOT_FOUND, "行业不存在")); }
    Ok(Json(json!({"id":industry_id,"published":true})))
}

pub async fn save_project(State(app): State<App>, headers: HeaderMap, Json(input): Json<ProjectDraft>) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let name = input.name.trim();
    let question = input.question.trim();
    if name.is_empty() || name.chars().count() > 80 || question.is_empty() || question.chars().count() > 500 {
        return Err(problem(StatusCode::UNPROCESSABLE_ENTITY, "项目名称或开场问句无效"));
    }
    let db = app.db.lock().unwrap();
    let exists: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM industries WHERE id=?1)",[&input.industry_id],|r|r.get(0))?;
    if !exists { return Err(problem(StatusCode::NOT_FOUND, "行业不存在")); }
    let project_id = input.id.unwrap_or_else(id);
    let changed = db.execute("INSERT INTO industry_projects(id,industry_id,draft_name,draft_question) VALUES(?1,?2,?3,?4) ON CONFLICT(id) DO UPDATE SET draft_name=excluded.draft_name,draft_question=excluded.draft_question,updated_at=unixepoch() WHERE industry_id=excluded.industry_id",params![project_id,input.industry_id,name,question])?;
    if changed == 0 { return Err(problem(StatusCode::CONFLICT, "项目不能转移到其他行业")); }
    Ok(Json(json!({"id":project_id,"changed":changed})))
}

pub async fn publish_project(State(app): State<App>, Path(project_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    let db = app.db.lock().unwrap();
    let duplicate: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM industry_projects source JOIN industry_projects other ON other.industry_id=source.industry_id AND other.published_name=source.draft_name AND other.id<>source.id AND other.published_at IS NOT NULL WHERE source.id=?1)",[&project_id],|r|r.get(0))?;
    if duplicate { return Err(problem(StatusCode::CONFLICT, "该行业已存在同名的已发布项目")); }
    let changed = db.execute("UPDATE industry_projects SET published_name=draft_name,published_question=draft_question,published_at=unixepoch(),updated_at=unixepoch() WHERE id=?1",[&project_id])?;
    if changed == 0 { return Err(problem(StatusCode::NOT_FOUND, "项目不存在")); }
    Ok(Json(json!({"id":project_id,"published":true})))
}

fn used_by_merchant(db: &Connection, target: &str, project: bool) -> rusqlite::Result<bool> {
    let mut stmt = db.prepare("SELECT profile_json FROM merchant_versions")?;
    let profiles = stmt.query_map([], |r| r.get::<_,String>(0))?;
    for profile in profiles {
        let Ok(value) = serde_json::from_str::<Value>(&profile?) else { continue };
        if !project && value["industry_id"].as_str() == Some(target) { return Ok(true); }
        if project && ["main","available","excluded"].iter().any(|kind|
            value["projects"][kind].as_array().is_some_and(|items|
                items.iter().any(|item| item["id"].as_str() == Some(target)))) {
            return Ok(true);
        }
    }
    Ok(false)
}

pub async fn delete_project(State(app): State<App>, Path(project_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app,&headers,true)?;
    let db = app.db.lock().unwrap();
    if used_by_merchant(&db,&project_id,true)? {
        return Err(problem(StatusCode::CONFLICT,"项目已被商家资料使用，不能删除"));
    }
    let changed = db.execute("DELETE FROM industry_projects WHERE id=?1",[&project_id])?;
    if changed == 0 { return Err(problem(StatusCode::NOT_FOUND,"项目不存在")); }
    Ok(Json(json!({"deleted":true})))
}

pub async fn delete_industry(State(app): State<App>, Path(industry_id): Path<String>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app,&headers,true)?;
    let mut db = app.db.lock().unwrap();
    let name: Option<String> = db.query_row("SELECT COALESCE(published_name,draft_name) FROM industries WHERE id=?1",[&industry_id],|r|r.get(0)).optional()?;
    if !db.query_row("SELECT EXISTS(SELECT 1 FROM industries WHERE id=?1)",[&industry_id],|r|r.get::<_,bool>(0))? {
        return Err(problem(StatusCode::NOT_FOUND,"行业不存在"));
    }
    if used_by_merchant(&db,&industry_id,false)? {
        return Err(problem(StatusCode::CONFLICT,"行业已被商家资料使用，不能删除"));
    }
    if let Some(name) = name.as_deref() {
        for account in app.accounts.values().filter(|account|account.industry==name) {
            let removed: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM removed_accounts WHERE id=?1)",[&account.id],|r|r.get(0))?;
            if !removed { return Err(problem(StatusCode::CONFLICT,"行业仍被接入账号使用，不能删除")); }
        }
    }
    let tx = db.transaction()?;
    tx.execute("DELETE FROM industry_projects WHERE industry_id=?1",[&industry_id])?;
    tx.execute("DELETE FROM industries WHERE id=?1",[&industry_id])?;
    tx.commit()?;
    Ok(Json(json!({"deleted":true})))
}
