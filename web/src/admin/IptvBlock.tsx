// Live tv op /beheer → Media (IPTV): de kanaallijst (m3u, als adres of bestand), de gids
// (XMLTV), hoeveel kanalen tegelijk, kopiëren of omzetten, de laatste ronde bijwerken, wie er nu
// kijkt, en kanalen verbergen. De m3u bevat de logins van de aanbieder: hij gaat alleen naar de
// server (versleuteld bewaard) en komt nooit terug in een antwoord. De API staat in
// api/app/routers/admin_iptv.py.
import { type JSX, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const ADMIN = "/api/v1/admin/iptv";

interface Last {
  at: string;
  ok: boolean;
  error: string | null;
  guide_error: string | null;
  channels: number;
  programmes: number;
  skipped: number;
}

interface Tuner {
  channel_id: string;
  name: string;
  viewers: number;
  mode: "copy" | "transcode";
  since: string;
}

interface View {
  playlist_set: boolean;
  playlist_kind: "url" | "file" | null;
  guide_url: string | null;
  max_streams: number;
  video: "auto" | "transcode";
  last: Last | null;
  channels: number;
  programmes: number;
  tuners: Tuner[];
}

interface AdminChannel {
  id: string;
  name: string;
  number: number | null;
  group: string | null;
  xmltv_id: string | null;
  hidden: boolean;
  programmes: boolean;
}

interface Saved {
  channels: number | null;
  guide_url: string | null;
}

export default function IptvBlock(): JSX.Element {
  const [view, setView] = useState<View | null>(null);
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [guide, setGuide] = useState("");
  const [maxStreams, setMaxStreams] = useState(2);
  const [video, setVideo] = useState<"auto" | "transcode">("auto");
  const [channels, setChannels] = useState<AdminChannel[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const v = await api.request<View>("GET", ADMIN);
    setView(v);
    return v;
  }, []);

  useEffect(() => {
    reload()
      .then((v) => {
        setGuide(v.guide_url ?? "");
        setMaxStreams(v.max_streams);
        setVideo(v.video);
      })
      .catch((e) => setError(errorText(e)));
  }, [reload]);

  async function run(key: string, fn: () => Promise<string | null>) {
    setBusy(key);
    try {
      const message = await fn();
      setError(null);
      if (message) toast(message, { kind: "ok" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  function save() {
    void run("save", async () => {
      const content = file ? await file.text() : null;
      const saved = await api.request<Saved>("PUT", ADMIN, {
        playlist_url: content ? null : url.trim() || null,
        playlist_content: content,
        guide_url: guide.trim() || null,
        max_streams: maxStreams,
        video,
      });
      setUrl("");
      setFile(null);
      await reload();
      if (saved.guide_url) toast(t("admin.media.iptv.guideFromList", { url: saved.guide_url }));
      return saved.channels === null
        ? t("admin.media.iptv.saved")
        : t("admin.media.iptv.savedCount", { n: saved.channels });
    });
  }

  async function toggleHidden(c: AdminChannel) {
    await run(`hide-${c.id}`, async () => {
      const next = await api.request<AdminChannel>("PATCH", `${ADMIN}/channels/${encodeURIComponent(c.id)}`, {
        hidden: !c.hidden,
      });
      setChannels((list) => list?.map((x) => (x.id === c.id ? next : x)) ?? null);
      return null;
    });
  }

  const last = view?.last;
  return (
    <section aria-labelledby="admin-media-iptv" data-iptv="admin">
      <div className="admin-head">
        <h3 id="admin-media-iptv">{t("admin.media.iptv.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.iptv.intro")}</p>
      {view && (
        <div className="stack">
          <p>
            <strong>{t("admin.media.iptv.playlist")}:</strong>{" "}
            {t(`admin.media.iptv.playlistSet.${view.playlist_kind ?? "none"}`)}
          </p>
          <Field label={t("admin.media.iptv.playlistUrl")} hint={t("admin.media.iptv.playlistUrlHint")}>
            {(c) => (
              <input
                {...c}
                type="password"
                autoComplete="off"
                name="iptv-playlist-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("admin.media.iptv.playlistFile")}>
            {(c) => (
              <input
                {...c}
                type="file"
                name="iptv-playlist-file"
                accept=".m3u,.m3u8,audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            )}
          </Field>
          <Field label={t("admin.media.iptv.guideUrl")} hint={t("admin.media.iptv.guideHint")}>
            {(c) => (
              <input {...c} type="url" name="iptv-guide-url" value={guide} onChange={(e) => setGuide(e.target.value)} />
            )}
          </Field>
          <Field label={t("admin.media.iptv.maxStreams")} hint={t("admin.media.iptv.maxStreamsHint")}>
            {(c) => (
              <input
                {...c}
                type="number"
                min={1}
                max={20}
                name="iptv-max-streams"
                value={maxStreams}
                onChange={(e) => setMaxStreams(Number(e.target.value))}
              />
            )}
          </Field>
          <Field label={t("admin.media.iptv.video")}>
            {(c) => (
              <select
                {...c}
                name="iptv-video"
                value={video}
                onChange={(e) => setVideo(e.target.value === "transcode" ? "transcode" : "auto")}
              >
                <option value="auto">{t("admin.media.iptv.videoAuto")}</option>
                <option value="transcode">{t("admin.media.iptv.videoTranscode")}</option>
              </select>
            )}
          </Field>
          <div className="toolbar">
            <Button variant="primary" busy={busy === "save"} onClick={save}>
              {t("admin.media.iptv.save")}
            </Button>
            {view.playlist_set && (
              <Button
                busy={busy === "refresh"}
                onClick={() =>
                  void run("refresh", async () => {
                    await api.request<void>("POST", `${ADMIN}/refresh`);
                    return t("admin.media.iptv.refreshed");
                  })
                }
              >
                {t("admin.media.iptv.refresh")}
              </Button>
            )}
            {view.playlist_set && (
              <Button
                variant="danger"
                busy={busy === "forget"}
                onClick={async () => {
                  const yes = await confirm({
                    title: t("admin.media.iptv.forget"),
                    message: t("admin.media.iptv.forgetConfirm"),
                    confirm: t("admin.media.iptv.forget"),
                    danger: true,
                  });
                  if (!yes) return;
                  await run("forget", async () => {
                    await api.request<void>("DELETE", ADMIN);
                    setChannels(null);
                    await reload();
                    return t("admin.media.iptv.forgotten");
                  });
                }}
              >
                {t("admin.media.iptv.forget")}
              </Button>
            )}
          </div>
          <p className="muted" data-iptv="counts">
            {t("admin.media.iptv.counts", { channels: view.channels, programmes: view.programmes })}
            {" · "}
            {last ? t("admin.media.iptv.last", { when: fmt.dateTime(last.at) }) : t("admin.media.iptv.lastNever")}
          </p>
          {last && (last.error || last.guide_error) && (
            <p className="error" role="status">
              {t("admin.media.iptv.lastError", { error: [last.error, last.guide_error].filter(Boolean).join("; ") })}
            </p>
          )}
          {last && last.skipped > 0 && <p className="muted">{t("admin.media.iptv.skipped", { n: last.skipped })}</p>}
          <h4>{t("admin.media.iptv.tuners")}</h4>
          {view.tuners.length === 0 ? (
            <p className="muted">{t("admin.media.iptv.tunersNone")}</p>
          ) : (
            <ul data-iptv="tuners">
              {view.tuners.map((tu) => (
                <li key={tu.channel_id}>
                  {t("admin.media.iptv.tuner", {
                    name: tu.name,
                    viewers: tu.viewers,
                    mode: t(`admin.media.iptv.modes.${tu.mode}`),
                  })}
                </li>
              ))}
            </ul>
          )}
          {view.channels > 0 && (
            <div className="toolbar">
              <Button
                busy={busy === "channels"}
                onClick={() =>
                  channels
                    ? setChannels(null)
                    : void run("channels", async () => {
                        setChannels(await api.request<AdminChannel[]>("GET", `${ADMIN}/channels`));
                        return null;
                      })
                }
              >
                {channels ? t("admin.media.iptv.hideChannels") : t("admin.media.iptv.showChannels")}
              </Button>
            </div>
          )}
          {channels && (
            <ul className="iptv-admin-channels" data-iptv="channels">
              {channels.map((c) => (
                <li key={c.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={c.hidden}
                      disabled={busy === `hide-${c.id}`}
                      onChange={() => void toggleHidden(c)}
                    />{" "}
                    {t("admin.media.iptv.hidden")}
                  </label>{" "}
                  <strong>
                    {c.number !== null ? `${c.number} ` : ""}
                    {c.name}
                  </strong>
                  {c.group && <span className="muted"> · {c.group}</span>}
                  {!c.programmes && <span className="muted"> · {t("admin.media.iptv.noGuide")}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
