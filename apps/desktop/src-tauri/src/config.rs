//! De instellingen van de app, in een JSON-bestand in de map voor instellingen van deze
//! gebruiker (Windows: %APPDATA%\org.platform.desktop, Linux: ~/.config/org.platform.desktop),
//! alleen leesbaar voor de gebruiker zelf. Het refresh-token staat erin: wie dat heeft, is
//! ingelogd. Uitloggen wist het (en trekt het op het platform in).

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Config {
    pub server: Option<String>,
    pub refresh_token: Option<String>,
    pub user: Option<String>,
    /// De map van deze computer die met het platform gelijk blijft
    pub folder: Option<PathBuf>,
    /// De map op het platform (id); leeg: de app maakt "<naam van de map>" in de hoofdmap
    pub remote_folder: Option<String>,
    /// Elke zoveel minuten een ronde (en meteen na een wijziging in de map)
    #[serde(default = "default_interval")]
    pub interval_min: u64,
    pub last_run: Option<u64>,
    pub last_error: Option<String>,
}

fn default_interval() -> u64 {
    15
}

pub fn dir() -> PathBuf {
    dirs::config_dir().unwrap_or_else(|| PathBuf::from(".")).join("org.platform.desktop")
}

fn path() -> PathBuf {
    dir().join("config.json")
}

pub fn state_path() -> PathBuf {
    dir().join("sync-state.json")
}

pub fn load() -> Config {
    std::fs::read(path())
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

pub fn save(cfg: &Config) -> anyhow::Result<()> {
    std::fs::create_dir_all(dir())?;
    let tmp = path().with_extension("tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(cfg)?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(tmp, path())?;
    Ok(())
}
