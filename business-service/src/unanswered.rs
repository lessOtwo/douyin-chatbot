//! Operational review list, independent of delivery and live-agent state.
use super::*;
use axum::extract::Query;

#[derive(Deserialize)]
pub struct Filter {
    #[serde(default = "open")]
    status: String,
}
fn open() -> String {
    "open".into()
}

pub async fn list(
    State(app): State<App>,
    headers: HeaderMap,
    Query(filter): Query<Filter>,
) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    if !["open", "resolved", "all"].contains(&filter.status.as_str()) {
        return Err(problem(
            StatusCode::BAD_REQUEST,
            "invalid question status filter",
        ));
    }
    let db = app.db.lock().unwrap();
    let total: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM unanswered_questions WHERE ?1='all' OR status=?1",
            [&filter.status],
            |r| r.get(0),
        )
        .map_err(anyhow::Error::from)?;
    let mut stmt=db.prepare("SELECT u.id,u.event_id,u.conversation_id,e.account_id,c.external_id,c.customer_id,e.text,u.reason,u.status,u.created_at,u.updated_at FROM unanswered_questions u JOIN events e ON e.id=u.event_id JOIN conversations c ON c.id=u.conversation_id WHERE ?1='all' OR u.status=?1 ORDER BY u.rowid DESC LIMIT 100").map_err(anyhow::Error::from)?;
    let items=stmt.query_map([&filter.status],|r|Ok(json!({
        "id":r.get::<_,String>(0)?,"event_id":r.get::<_,String>(1)?,"conversation_id":r.get::<_,String>(2)?,
        "account_id":r.get::<_,String>(3)?,"external_id":r.get::<_,String>(4)?,"customer_id":r.get::<_,String>(5)?,
        "question":r.get::<_,String>(6)?,"reason":r.get::<_,String>(7)?,"status":r.get::<_,String>(8)?,
        "created_at":r.get::<_,i64>(9)?,"updated_at":r.get::<_,i64>(10)?
    }))).map_err(anyhow::Error::from)?.collect::<std::result::Result<Vec<_>,_>>().map_err(anyhow::Error::from)?;
    Ok(Json(json!({"items":items,"total":total,"limit":100})))
}

#[derive(Deserialize)]
pub struct Change {
    status: String,
}
pub async fn status(
    State(app): State<App>,
    Path(question): Path<String>,
    headers: HeaderMap,
    Json(input): Json<Change>,
) -> ApiResult {
    admin_auth(&app, &headers, true)?;
    if !["open", "resolved"].contains(&input.status.as_str()) {
        return Err(problem(
            StatusCode::BAD_REQUEST,
            "status must be open or resolved",
        ));
    }
    let db = app.db.lock().unwrap();
    let exists: bool = db
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM unanswered_questions WHERE id=?1)",
            [&question],
            |r| r.get(0),
        )
        .map_err(anyhow::Error::from)?;
    if !exists {
        return Err(problem(StatusCode::NOT_FOUND, "question not found"));
    }
    db.execute("UPDATE unanswered_questions SET status=?1,updated_at=unixepoch() WHERE id=?2 AND status<>?1",params![input.status,question]).map_err(anyhow::Error::from)?;
    Ok(Json(json!({"id":question,"status":input.status})))
}
