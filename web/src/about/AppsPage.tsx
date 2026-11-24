// /apps (fase 11, stap 9): de downloadpagina voor de client-apps. Nu de app voor de LG webOS-tv
// (het .ipk maakt de server zelf, met het adres van dit platform erin); Android en later Windows
// en Linux staan erbij als "later" tot een beheerder het bestand in de map apps van de data legt.
// De API: GET /api/v1/apps. Downloaden gaat met het token (fetch), dus via een blob.

import { type JSX, useEffect, useState } from "react";
import { api, formatBytes } from "../api";
import { useCurrentUser } from "../currentUser";
import { errorText, t } from "../i18n";
import type { RouteProps } from "../router";
import { Button } from "../ui/Button";
import { PageTitle } from "../ui/PageTitle";
import { toast } from "../ui/toast";

interface ClientApp {
  id: string;
  platform: "webos" | "android" | "windows" | "linux" | "linuxarm" | "macos" | "ios";
  state: "ready" | "later" | "web";
  version: string | null;
  file: string | null;
  size: number | null;
}

async function download(app: ClientApp): Promise<void> {
  if (!app.file) return;
  const res = await api.rawRequest(app.file);
  const name =
    /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ??
    app.file.split("/").pop() ??
    "app";
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// Platformen met uitleg in stappen: about.apps.<platform>.step1 … stepN
const STEPS: Partial<Record<ClientApp["platform"], number>> = { webos: 4, linuxarm: 3, macos: 3, ios: 3 };

function Steps({ platform }: { platform: ClientApp["platform"] }): JSX.Element | null {
  const count = STEPS[platform];
  if (!count) return null;
  return (
    <ol className="apps-steps">
      {Array.from({ length: count }, (_, i) => (
        <li key={i}>{t(`about.apps.${platform}.step${i + 1}` as Parameters<typeof t>[0])}</li>
      ))}
    </ol>
  );
}

export default function AppsPage(_: RouteProps): JSX.Element {
  const [apps, setApps] = useState<ClientApp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const admin = useCurrentUser()?.role === "admin";

  async function refresh() {
    setBusy("refresh");
    try {
      setApps(await api.request<ClientApp[]>("POST", "/api/v1/apps/refresh"));
      toast(t("about.apps.refreshed"), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    api
      .request<ClientApp[]>("GET", "/api/v1/apps")
      .then(setApps)
      .catch((e) => setError(errorText(e)));
  }, []);

  async function get(app: ClientApp) {
    setBusy(app.id);
    try {
      await download(app);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="page about-page" data-apps="">
      <PageTitle>{t("about.apps.title")}</PageTitle>
      <p>{t("about.apps.intro")}</p>
      {admin && (
        <p>
          <Button busy={busy === "refresh"} onClick={() => void refresh()}>
            {t("about.apps.refresh")}
          </Button>{" "}
          <span className="muted">{t("about.apps.refreshHint")}</span>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {apps?.map((app) => (
        <section key={app.id} aria-labelledby={`apps-${app.id}`} data-app={app.id}>
          <h2 id={`apps-${app.id}`}>{t(`about.apps.${app.platform}.name`)}</h2>
          <p className="muted">{t(`about.apps.${app.platform}.text`)}</p>
          {app.state === "web" ? (
            <Steps platform={app.platform} />
          ) : app.state === "ready" ? (
            <>
              <p>
                <Button variant="primary" busy={busy === app.id} onClick={() => void get(app)}>
                  {t("about.apps.download")}
                </Button>{" "}
                <span className="muted">
                  {app.version ? t("about.apps.version", { version: app.version }) : ""}
                  {app.size ? ` ${formatBytes(app.size)}` : ""}
                </span>
              </p>
              <Steps platform={app.platform} />
            </>
          ) : (
            <p className="muted">{t("about.apps.later")}</p>
          )}
        </section>
      ))}
    </main>
  );
}
