//! FastGPT administrator handoff; upstream sessions retain their own authentication rules.
use super::*;
use axum::{Form, response::Redirect};
use sha2::{Digest, Sha256};

pub struct AdminTools {
    enabled: bool,
    fastgpt_public: reqwest::Url,
    fastgpt_password: String,
    fastgpt_internal: String,
    client: Client,
    tickets: Mutex<HashMap<String, Instant>>,
    login_lock: tokio::sync::Mutex<()>,
}

impl AdminTools {
    pub fn from_env() -> Result<Self> {
        fn public_url(key: &str, fallback: &str) -> Result<reqwest::Url> {
            let url = reqwest::Url::parse(&setting(key, fallback))?;
            if !["http", "https"].contains(&url.scheme())
                || url.host_str().is_none()
                || !url.username().is_empty()
                || url.password().is_some()
                || url.path() != "/"
                || url.query().is_some()
                || url.fragment().is_some()
            {
                bail!("{key} must be an HTTP(S) origin");
            }
            Ok(url)
        }
        let enabled = setting("ADMIN_TOOLS_AUTO_LOGIN", "false") == "true";
        let tools = Self {
            enabled,
            fastgpt_public: public_url("FASTGPT_PUBLIC_URL", "http://localhost:3000")?,
            fastgpt_password: setting("FASTGPT_ROOT_PASSWORD", ""),
            fastgpt_internal: setting(
                "ADMIN_FASTGPT_INTERNAL_URL",
                &setting("FASTGPT_URL", "http://fastgpt:3000"),
            ),
            client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(30))
                .build()?,
            tickets: Mutex::new(HashMap::new()),
            login_lock: tokio::sync::Mutex::new(()),
        };
        if enabled && tools.fastgpt_password.is_empty() {
            bail!("administrator login configuration is incomplete");
        }
        Ok(tools)
    }

    // Local ports share host-only cookies. Normalize localhost/127.0.0.1 to the
    // browser's loopback host; arbitrary Host values never choose a destination.
    fn destination(&self, configured: &reqwest::Url, headers: &HeaderMap) -> reqwest::Url {
        let mut url = configured.clone();
        let host = headers
            .get("host")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        if let Ok(incoming) = reqwest::Url::parse(&format!("http://{host}")) {
            if matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
                && matches!(incoming.host_str(), Some("localhost" | "127.0.0.1"))
            {
                let _ = url.set_host(incoming.host_str());
            }
        }
        url
    }

    /// Use FastGPT's password policy and server session API; never expose the
    /// root password, its hash, or the resulting token in an HTML document/URL.
    async fn session_cookie(&self) -> Result<String> {
        if self.fastgpt_password.is_empty() {
            bail!("FastGPT administrator password is not configured");
        }
        let _guard = self.login_lock.lock().await;
        let base = self.fastgpt_internal.trim_end_matches('/');
        let pre: Value = self
            .client
            .get(format!("{base}/api/support/user/account/preLogin"))
            .query(&[("username", "root")])
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        let code = pre["data"]["code"]
            .as_str()
            .context("FastGPT did not issue a pre-login code")?;
        let password = format!("{:x}", Sha256::digest(self.fastgpt_password.as_bytes()));
        let result = self
            .client
            .post(format!("{base}/api/support/user/account/loginByPassword"))
            .json(&json!({"username":"root","password":password,"code":code,"language":"zh-CN"}))
            .send()
            .await?
            .error_for_status()?;
        let cookies = result
            .headers()
            .get_all("set-cookie")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .filter(|value| value.starts_with("fastgpt_token="))
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let body: Value = result.json().await?;
        if body["code"].as_i64() != Some(200)
            || cookies.len() != 1
            || body["data"]["status"] == "verificationRequired"
        {
            bail!("FastGPT administrator login did not complete");
        }
        let pair = cookies[0]
            .split(';')
            .next()
            .context("invalid login cookie")?;
        if pair.len() < 30 {
            bail!("FastGPT returned an empty login cookie");
        }
        Ok(pair.to_owned())
    }

    async fn fastgpt_login(&self, public: reqwest::Url) -> Result<Response> {
        let pair = self.session_cookie().await?;
        let cookie = format!(
            "{pair}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800{}",
            if public.scheme() == "https" {
                "; Secure"
            } else {
                ""
            }
        );
        let mut response = Redirect::to(public.join("dashboard/agent")?.as_str()).into_response();
        response.headers_mut().insert("set-cookie", cookie.parse()?);
        Ok(response)
    }

    async fn request_json(&self, cookie: &str, path: &str, body: Option<Value>) -> Result<Value> {
        let url = format!("{}{}", self.fastgpt_internal.trim_end_matches('/'), path);
        let request = match body {
            Some(body) => self.client.post(url).json(&body),
            None => self.client.get(url),
        };
        let response: Value = request
            .header("cookie", cookie)
            .header("fastgpt-web-request", "1")
            .send().await?.error_for_status()?.json().await?;
        if response["code"].as_i64() != Some(200) {
            bail!("FastGPT request failed: {path}");
        }
        Ok(response["data"].clone())
    }

    pub async fn published_workflows(&self) -> Result<Vec<Value>> {
        let cookie = self.session_cookie().await?;
        let mut folders = vec![String::new()];
        let mut workflows = Vec::new();
        let mut seen = 0usize;
        while let Some(parent_id) = folders.pop() {
            let mut page_num = 1;
            loop {
                let mut query = json!({"type":["advanced","folder"],"pageNum":page_num,"pageSize":100});
                if !parent_id.is_empty() { query["parentId"] = json!(parent_id); }
                let page = self.request_json(&cookie, "/api/core/app/listV2", Some(query)).await?;
                let list = page["list"].as_array().context("FastGPT workflow list is invalid")?;
                seen += list.len();
                if seen > 1000 { bail!("FastGPT workflow list exceeds the supported limit"); }
                for item in list {
                    let app_id = item["_id"].as_str().context("FastGPT app ID is invalid")?;
                    match item["type"].as_str() {
                        Some("folder") => folders.push(app_id.to_owned()),
                        Some("advanced") => {
                            let detail = self.request_json(&cookie, &format!("/api/core/app/detail?appId={app_id}"), None).await?;
                            if detail["type"] == "advanced"
                                && detail["publishedVersionId"].as_str().is_some_and(|id| !id.is_empty()) {
                                workflows.push(json!({"id":app_id,"name":item["name"]}));
                            }
                        }
                        _ => {}
                    }
                }
                if (page_num * 100) as u64 >= page["total"].as_u64().context("FastGPT workflow total is invalid")? { break; }
                page_num += 1;
            }
        }
        workflows.sort_by(|a,b| a["name"].as_str().cmp(&b["name"].as_str()));
        Ok(workflows)
    }

    pub async fn verify_call_key(&self) -> Result<()> {
        let key = setting("FASTGPT_APP_KEY", "");
        if key.trim().is_empty() { bail!("FastGPT call key is not configured"); }
        let response: Value = self.client
            .post(format!("{}/api/core/app/listV2", self.fastgpt_internal.trim_end_matches('/')))
            .header("authorization", format!("Bearer {key}"))
            .json(&json!({"type":"advanced","pageNum":1,"pageSize":1}))
            .send().await?.error_for_status()?.json().await?;
        if response["code"].as_i64() != Some(200) {
            bail!("FastGPT call key was rejected");
        }
        Ok(())
    }
}

pub async fn workflow_list(State(app): State<App>, headers: HeaderMap) -> ApiResult {
    admin_auth(&app, &headers, false)?;
    let items = app.tools.published_workflows().await.map_err(|error| {
        eprintln!("FastGPT workflow listing failed: {error:#}");
        problem(StatusCode::BAD_GATEWAY, "无法读取模型平台已发布的工作流，请检查连接或管理员配置")
    })?;
    let call_ready = app.tools.verify_call_key().await.is_ok();
    Ok(Json(json!({"items":items,"call_ready":call_ready})))
}

/// The sidebar entry requires the business administrator and a same-origin POST.
pub async fn open(
    State(app): State<App>,
    Path(tool): Path<String>,
    headers: HeaderMap,
) -> std::result::Result<Response, ApiError> {
    admin_auth(&app, &headers, true)?;
    let tools = &app.tools;
    if tool != "fastgpt" {
        return Err(problem(StatusCode::NOT_FOUND, "unknown administrator tool"));
    }
    let public = tools.destination(&tools.fastgpt_public, &headers);
    let result: Result<Response> = async {
        if !tools.enabled { return Ok(Redirect::to(public.as_str()).into_response()); }
        let incoming = headers.get("host").and_then(|v| v.to_str().ok()).unwrap_or("");
        let incoming = reqwest::Url::parse(&format!("http://{incoming}"))?;
        if incoming.host_str() == public.host_str() {
            return tools.fastgpt_login(public).await;
        }
        // Different production hosts cannot set each other's cookies. Post a
        // single-use 60-second handoff to the destination host's Caddy route.
        let ticket = format!("{}{}", id(), id());
        {
            let mut tickets = tools.tickets.lock().unwrap();
            tickets.retain(|_, created| created.elapsed() < Duration::from_secs(60));
            if tickets.len() >= 128 { bail!("too many pending administrator handoffs"); }
            tickets.insert(ticket.clone(), Instant::now());
        }
        let target = public.join("admin-entry/fastgpt")?;
        let page = format!("<!doctype html><meta charset=utf-8><meta name=referrer content=no-referrer><title>正在进入模型与商家知识</title><form method=post action=\"{}\"><input type=hidden name=ticket value=\"{ticket}\"><button>继续进入模型与商家知识</button></form><script>document.forms[0].submit()</script>", html_escape(target.as_str()));
        Ok(Html(page).into_response())
    }.await;
    finish(result)
}

#[derive(Deserialize)]
pub struct Handoff {
    ticket: String,
}

/// Served on the FastGPT origin by Caddy. No business cookie is expected here;
/// the authenticated business entry already issued this short-lived capability.
pub async fn consume(
    State(app): State<App>,
    headers: HeaderMap,
    Form(input): Form<Handoff>,
) -> std::result::Result<Response, ApiError> {
    let tools = &app.tools;
    let expected = tools.fastgpt_public.authority();
    let actual = headers
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if !tools.enabled || actual != expected {
        return Err(problem(
            StatusCode::FORBIDDEN,
            "invalid administrator handoff host",
        ));
    }
    let created = tools.tickets.lock().unwrap().remove(&input.ticket);
    if !created.is_some_and(|time| time.elapsed() < Duration::from_secs(60)) {
        return Err(problem(
            StatusCode::UNAUTHORIZED,
            "administrator handoff expired or used",
        ));
    }
    finish(tools.fastgpt_login(tools.fastgpt_public.clone()).await)
}

fn finish(result: Result<Response>) -> std::result::Result<Response, ApiError> {
    match result {
        Ok(mut response) => {
            response
                .headers_mut()
                .insert("cache-control", "no-store".parse().unwrap());
            response
                .headers_mut()
                .insert("referrer-policy", "no-referrer".parse().unwrap());
            Ok(response)
        }
        Err(error) => {
            eprintln!("administrator tool login failed: {error:#}");
            Err(problem(
                StatusCode::BAD_GATEWAY,
                "自动登录失败，请检查管理员配置，或使用该系统的独立登录页",
            ))
        }
    }
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}
