//! De REST-API van het platform (dezelfde als de web-app en de Android-app gebruiken). Het
//! access-token leeft alleen in het geheugen; na een 401 één keer verversen met het
//! refresh-token (dat werkt één keer: het nieuwe gaat meteen naar de instellingen).

use anyhow::{anyhow, bail, Result};
use futures_util::StreamExt;
use reqwest::{Body, Client, Method, Response, StatusCode};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;

use crate::config;

#[derive(Debug)]
pub struct ApiError {
    #[allow(dead_code)]
    pub status: u16,
    pub code: Option<String>,
    pub message: String,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ApiError {}

#[derive(Clone, Debug, Deserialize)]
pub struct UserOut {
    #[serde(default)]
    pub display_name: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Tokens {
    pub access_token: String,
    pub refresh_token: String,
    pub user: UserOut,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Node {
    pub id: String,
    pub name: String,
    pub kind: String,
    #[serde(default)]
    pub size: u64,
    #[serde(default)]
    pub etag: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Listing {
    pub folder: Node,
    #[serde(default)]
    pub children: Vec<Node>,
}

#[derive(Serialize)]
struct LoginIn<'a> {
    email: &'a str,
    password: &'a str,
    code: Option<&'a str>,
    device: &'a str,
}

pub struct Api {
    http: Client,
    access: Mutex<Option<String>>,
}

fn device() -> String {
    format!("Desktop-app ({})", std::env::consts::OS)
}

async fn error_of(res: Response) -> anyhow::Error {
    let status = res.status().as_u16();
    let body: serde_json::Value = res.json().await.unwrap_or(serde_json::Value::Null);
    let detail = &body["detail"];
    let (code, message) = if detail.is_object() {
        (
            detail["code"].as_str().map(str::to_owned),
            detail["message"].as_str().unwrap_or("").to_owned(),
        )
    } else {
        (None, detail.as_str().unwrap_or("").to_owned())
    };
    let message = if message.is_empty() { format!("HTTP {status}") } else { message };
    ApiError { status, code, message }.into()
}

impl Api {
    pub fn new() -> Self {
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .user_agent(concat!("platform-desktop/", env!("CARGO_PKG_VERSION")))
            .build()
            .expect("http-client");
        Api { http, access: Mutex::new(None) }
    }

    fn base() -> Result<String> {
        config::load()
            .server
            .map(|s| s.trim_end_matches('/').to_owned())
            .ok_or_else(|| anyhow!("Geen platform gekozen"))
    }

    pub async fn info(&self, base: &str) -> Result<()> {
        let res = self.http.get(format!("{}/api/v1/info", base.trim_end_matches('/'))).send().await?;
        if !res.status().is_success() {
            bail!("Geen platform op dit adres (HTTP {})", res.status().as_u16());
        }
        Ok(())
    }

    pub async fn login(&self, email: &str, password: &str, code: Option<&str>) -> Result<String> {
        let dev = device();
        let res = self
            .http
            .post(format!("{}/api/v1/auth/login", Self::base()?))
            .json(&LoginIn { email, password, code, device: &dev })
            .send()
            .await?;
        if !res.status().is_success() {
            return Err(error_of(res).await);
        }
        let t: Tokens = res.json().await?;
        self.keep(&t)?;
        Ok(t.user.display_name)
    }

    fn keep(&self, t: &Tokens) -> Result<()> {
        let mut cfg = config::load();
        cfg.refresh_token = Some(t.refresh_token.clone());
        cfg.user = Some(t.user.display_name.clone());
        config::save(&cfg)?;
        if let Ok(mut a) = self.access.try_lock() {
            *a = Some(t.access_token.clone());
        }
        Ok(())
    }

    pub async fn logout(&self) -> Result<()> {
        let mut cfg = config::load();
        if let (Some(server), Some(rt)) = (cfg.server.clone(), cfg.refresh_token.clone()) {
            let _ = self
                .http
                .post(format!("{}/api/v1/auth/logout", server.trim_end_matches('/')))
                .json(&serde_json::json!({ "refresh_token": rt }))
                .send()
                .await;
        }
        cfg.refresh_token = None;
        cfg.user = None;
        config::save(&cfg)?;
        *self.access.lock().await = None;
        Ok(())
    }

    /// Nieuwe tokens; `old`: het access-token waarmee het verzoek mislukte (heeft een ander
    /// intussen al ververst, dan niet nog eens).
    async fn refresh(&self, old: Option<String>) -> Result<()> {
        let mut access = self.access.lock().await;
        if access.is_some() && *access != old {
            return Ok(());
        }
        let rt = config::load().refresh_token.ok_or_else(|| anyhow!("Niet ingelogd"))?;
        let res = self
            .http
            .post(format!("{}/api/v1/auth/refresh", Self::base()?))
            .json(&serde_json::json!({ "refresh_token": rt }))
            .send()
            .await?;
        if res.status() == StatusCode::UNAUTHORIZED || res.status() == StatusCode::FORBIDDEN {
            let mut cfg = config::load();
            cfg.refresh_token = None;
            config::save(&cfg)?;
            return Err(error_of(res).await);
        }
        if !res.status().is_success() {
            return Err(error_of(res).await);
        }
        let t: Tokens = res.json().await?;
        let mut cfg = config::load();
        cfg.refresh_token = Some(t.refresh_token.clone());
        config::save(&cfg)?;
        *access = Some(t.access_token);
        Ok(())
    }

    /// Een verzoek met het token. `body`: maakt de body (opnieuw, voor de tweede poging).
    async fn call<F>(&self, method: Method, path: &str, body: F) -> Result<Response>
    where
        F: Fn() -> Option<Body>,
    {
        let url = format!("{}{}", Self::base()?, path);
        for attempt in 0..2 {
            let token = self.access.lock().await.clone();
            if token.is_none() || attempt == 1 {
                self.refresh(token.clone()).await?;
            }
            let token = self.access.lock().await.clone();
            let mut req = self.http.request(method.clone(), &url);
            if let Some(t) = &token {
                req = req.bearer_auth(t);
            }
            if let Some(b) = body() {
                req = req.body(b);
            }
            let res = req.send().await?;
            if res.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
                continue;
            }
            if !res.status().is_success() {
                return Err(error_of(res).await);
            }
            return Ok(res);
        }
        bail!("Niet ingelogd")
    }

    pub async fn root(&self) -> Result<Listing> {
        Ok(self.call(Method::GET, "/api/v1/files/root", || None).await?.json().await?)
    }

    pub async fn children(&self, id: &str) -> Result<Vec<Node>> {
        let l: Listing = self
            .call(Method::GET, &format!("/api/v1/files/{id}/children"), || None)
            .await?
            .json()
            .await?;
        Ok(l.children)
    }

    /// De map `name` in `parent` (bestaat hij al, dan die).
    pub async fn folder(&self, parent: &str, name: &str) -> Result<Node> {
        if let Some(n) = self.children(parent).await?.into_iter().find(|n| n.kind == "dir" && n.name == name) {
            return Ok(n);
        }
        let body = serde_json::to_vec(&serde_json::json!({ "name": name }))?;
        let res = self
            .http_json(Method::POST, &format!("/api/v1/files/{parent}/folders"), body)
            .await;
        match res {
            Ok(r) => Ok(r.json().await?),
            Err(e) => {
                if e.downcast_ref::<ApiError>().and_then(|a| a.code.as_deref()) == Some("name_taken") {
                    self.children(parent)
                        .await?
                        .into_iter()
                        .find(|n| n.kind == "dir" && n.name == name)
                        .ok_or(e)
                } else {
                    Err(e)
                }
            }
        }
    }

    async fn http_json(&self, method: Method, path: &str, body: Vec<u8>) -> Result<Response> {
        // Content-Type zetten kan niet via Body; daarom hier een eigen pad met de header
        let url = format!("{}{}", Self::base()?, path);
        for attempt in 0..2 {
            let token = self.access.lock().await.clone();
            if token.is_none() || attempt == 1 {
                self.refresh(token.clone()).await?;
            }
            let token = self.access.lock().await.clone().unwrap_or_default();
            let res = self
                .http
                .request(method.clone(), &url)
                .bearer_auth(token)
                .header("Content-Type", "application/json")
                .body(body.clone())
                .send()
                .await?;
            if res.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
                continue;
            }
            if !res.status().is_success() {
                return Err(error_of(res).await);
            }
            return Ok(res);
        }
        bail!("Niet ingelogd")
    }

    /// Een bestand van deze computer uploaden in map `folder` als `name`.
    pub async fn upload(&self, folder: &str, name: &str, file: &Path, conflict: &str, mtime_ms: Option<u64>) -> Result<Node> {
        let mut path = format!(
            "/api/v1/files/{folder}/upload?name={}&conflict={conflict}",
            urlencoding::encode(name)
        );
        if let Some(m) = mtime_ms {
            path.push_str(&format!("&mtime={m}"));
        }
        let file = file.to_owned();
        // De body wordt per poging opnieuw geopend (een stream kan maar één keer)
        let open = || -> Option<Body> {
            let f = std::fs::File::open(&file).ok()?;
            let stream = tokio_util::io::ReaderStream::new(tokio::fs::File::from_std(f));
            Some(Body::wrap_stream(stream))
        };
        Ok(self.call(Method::PUT, &path, open).await?.json().await?)
    }

    /// De inhoud van een bestand van het platform naar `target` (eerst naar een tijdelijk
    /// bestand ernaast, dan in één keer op zijn plaats).
    pub async fn download(&self, id: &str, target: &Path) -> Result<()> {
        let res = self.call(Method::GET, &format!("/api/v1/files/{id}/content"), || None).await?;
        let tmp = target.with_file_name(format!(
            ".{}.platform-tmp",
            target.file_name().and_then(|n| n.to_str()).unwrap_or("download")
        ));
        let mut out = tokio::fs::File::create(&tmp).await?;
        let mut stream = res.bytes_stream();
        while let Some(chunk) = stream.next().await {
            out.write_all(&chunk?).await?;
        }
        out.flush().await?;
        drop(out);
        tokio::fs::rename(&tmp, target).await?;
        Ok(())
    }

    /// Naar de prullenbak van het platform (terug te zetten).
    pub async fn trash(&self, id: &str) -> Result<()> {
        self.call(Method::DELETE, &format!("/api/v1/files/{id}"), || None).await?;
        Ok(())
    }
}
