//! Een map van deze computer in twee richtingen gelijk houden met een map op het platform,
//! zoals de Nextcloud-client (en precies zoals de Android-app, apps/android/.../FolderSync.kt).
//! Per bestand onthoudt de app de stand na de vorige ronde (grootte en tijd hier, etag op het
//! platform); daarmee ziet ze wat aan welke kant veranderde:
//!
//! - alleen hier veranderd of nieuw: uploaden (vervangen; het platform houdt de oude versie);
//! - alleen op het platform veranderd of nieuw: downloaden;
//! - aan beide kanten veranderd: conflict. Het bestand van hier gaat als
//!   "naam (conflict <datum> <computer>).ext" naar het platform; dat van het platform komt hier
//!   onder de gewone naam. Er gaat niets verloren;
//! - hier weg (op het platform onveranderd): naar de prullenbak van het platform;
//! - op het platform weg (hier onveranderd): hier ook weg. Hier veranderd: opnieuw uploaden.
//!
//! Verborgen bestanden (die met een punt beginnen) en tijdelijke bestanden van
//! kantoorprogramma's (~$…, .~lock…) gaan niet mee.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::api::{Api, Node};
use crate::config;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Seen {
    pub size: u64,
    pub mtime: u64,
    pub etag: String,
}

#[derive(Default, Debug, Clone, Serialize)]
pub struct Counts {
    pub up: u32,
    pub down: u32,
    pub conflicts: u32,
    pub removed: u32,
}

type State = BTreeMap<String, Seen>;

fn load_state() -> State {
    std::fs::read(config::state_path())
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn save_state(s: &State) -> Result<()> {
    std::fs::create_dir_all(config::dir())?;
    std::fs::write(config::state_path(), serde_json::to_vec(s)?)?;
    Ok(())
}

pub fn forget_state() {
    let _ = std::fs::remove_file(config::state_path());
}

fn skip(name: &str) -> bool {
    name.starts_with('.') || name.starts_with("~$") || name.ends_with(".platform-tmp") || name == "desktop.ini" || name == "Thumbs.db"
}

fn mtime_ms(meta: &std::fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn local_seen(path: &Path, etag: &str) -> Result<Seen> {
    let meta = std::fs::metadata(path)?;
    Ok(Seen { size: meta.len(), mtime: mtime_ms(&meta), etag: etag.to_owned() })
}

fn same(path: &Path, seen: &Seen) -> bool {
    std::fs::metadata(path)
        .map(|m| m.len() == seen.size && mtime_ms(&m) == seen.mtime)
        .unwrap_or(false)
}

/// "2026-11-23 1910" (UTC) uit de seconden sinds 1970, zonder extra crate.
fn stamp(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rest = secs % 86_400;
    // De kalender van Howard Hinnant (civil_from_days)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    format!("{y:04}-{m:02}-{d:02} {:02}{:02}", rest / 3600, rest % 3600 / 60)
}

fn host() -> String {
    std::env::var("COMPUTERNAME")
        .ok()
        .or_else(|| std::fs::read_to_string("/etc/hostname").ok().map(|h| h.trim().to_owned()))
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| "computer".into())
}

fn conflict_name(name: &str) -> String {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let tag = format!("conflict {} {}", stamp(secs), host());
    match name.rfind('.') {
        Some(dot) if dot > 0 => format!("{} ({tag}){}", &name[..dot], &name[dot..]),
        _ => format!("{name} ({tag})"),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn stamp() {
        assert_eq!(super::stamp(0), "1970-01-01 0000");
        assert_eq!(super::stamp(1_795_457_440), "2026-11-23 1810");
        assert_eq!(super::stamp(951_782_400), "2000-02-29 0000");
    }
}

/// Eén ronde. `progress`: het pad dat nu aan de beurt is.
pub async fn run(api: &Api, progress: &(dyn Fn(&str) + Sync)) -> Result<Option<Counts>> {
    let cfg = config::load();
    let Some(folder) = cfg.folder.clone() else { return Ok(None) };
    if cfg.refresh_token.is_none() {
        return Ok(None);
    }
    std::fs::create_dir_all(&folder)?;
    let remote = match cfg.remote_folder.clone() {
        Some(id) => id,
        None => {
            let name = folder.file_name().and_then(|n| n.to_str()).unwrap_or("Computer").to_owned();
            let root = api.root().await?;
            let id = api.folder(&root.folder.id, &name).await?.id;
            let keep = id.clone();
            config::update(|c| c.remote_folder = Some(keep))?;
            id
        }
    };
    let old = load_state();
    let mut next = State::new();
    let mut counts = Counts::default();
    walk(api, &folder, &remote, "", &old, &mut next, &mut counts, progress).await?;
    save_state(&next)?;
    Ok(Some(counts))
}

#[allow(clippy::too_many_arguments)]
fn walk<'a>(
    api: &'a Api,
    dir: &'a Path,
    remote: &'a str,
    prefix: &'a str,
    old: &'a State,
    next: &'a mut State,
    counts: &'a mut Counts,
    progress: &'a (dyn Fn(&str) + Sync),
) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<()>> + Send + 'a>> {
    Box::pin(async move {
        let mut locals: HashMap<String, PathBuf> = HashMap::new();
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            if let Some(name) = entry.file_name().to_str() {
                if !skip(name) {
                    locals.insert(name.to_owned(), entry.path());
                }
            }
        }
        let remotes: HashMap<String, Node> = api
            .children(remote)
            .await?
            .into_iter()
            .filter(|n| !skip(&n.name))
            .map(|n| (n.name.clone(), n))
            .collect();
        let names: BTreeSet<String> = locals.keys().chain(remotes.keys()).cloned().collect();
        for name in names {
            let path = format!("{prefix}{name}");
            let l = locals.get(&name);
            let r = remotes.get(&name);
            let l_dir = l.map(|p| p.is_dir()).unwrap_or(false);
            let r_dir = r.map(|n| n.kind == "dir").unwrap_or(false);
            if l_dir || r_dir {
                // Een map en een bestand met dezelfde naam: overslaan
                if (l.is_some() && !l_dir) || (r.is_some() && !r_dir) {
                    continue;
                }
                let local_dir = match l {
                    Some(p) => p.clone(),
                    None => {
                        let p = dir.join(&name);
                        std::fs::create_dir_all(&p)?;
                        p
                    }
                };
                let remote_dir = match r {
                    Some(n) => n.id.clone(),
                    None => api.folder(remote, &name).await?.id,
                };
                let sub = format!("{path}/");
                walk(api, &local_dir, &remote_dir, &sub, old, next, counts, progress).await?;
                continue;
            }
            progress(&path);
            let seen = old.get(&path);
            match (l, r) {
                (Some(lp), None) => {
                    if seen.is_some_and(|s| same(lp, s)) {
                        std::fs::remove_file(lp)?;
                        counts.removed += 1;
                    } else {
                        let up = api.upload(remote, &name, lp, "replace", Some(local_seen(lp, "")?.mtime)).await?;
                        next.insert(path, local_seen(lp, &up.etag)?);
                        counts.up += 1;
                    }
                }
                (None, Some(rn)) => {
                    if seen.is_some_and(|s| s.etag == rn.etag) {
                        api.trash(&rn.id).await?;
                        counts.removed += 1;
                    } else {
                        let target = dir.join(&name);
                        api.download(&rn.id, &target).await?;
                        next.insert(path, local_seen(&target, &rn.etag)?);
                        counts.down += 1;
                    }
                }
                (Some(lp), Some(rn)) => {
                    let here = seen.map(|s| !same(lp, s)).unwrap_or(true);
                    let there = seen.map(|s| s.etag != rn.etag).unwrap_or(true);
                    let size = std::fs::metadata(lp)?.len();
                    if seen.is_none() && size == rn.size {
                        next.insert(path, local_seen(lp, &rn.etag)?);
                    } else if here && there {
                        let mtime = local_seen(lp, "")?.mtime;
                        api.upload(remote, &conflict_name(&name), lp, "rename", Some(mtime)).await?;
                        api.download(&rn.id, lp).await?;
                        next.insert(path, local_seen(lp, &rn.etag)?);
                        counts.conflicts += 1;
                    } else if here {
                        let mtime = local_seen(lp, "")?.mtime;
                        let up = api.upload(remote, &name, lp, "replace", Some(mtime)).await?;
                        next.insert(path, local_seen(lp, &up.etag)?);
                        counts.up += 1;
                    } else if there {
                        api.download(&rn.id, lp).await?;
                        next.insert(path, local_seen(lp, &rn.etag)?);
                        counts.down += 1;
                    } else if let Some(s) = seen {
                        next.insert(path, s.clone());
                    }
                }
                (None, None) => {}
            }
        }
        Ok(())
    })
}
