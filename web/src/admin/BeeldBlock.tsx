// Het blok "Zoeken op beeld" in de tab "Zoeken en foto's" op /beheer (fase 6, van X3; Keuze 82):
// aan of uit voor iedereen, het model, de container analyse, hoeveel foto's bekeken zijn (en
// mislukt per code), het afgelopen uur, de ruimte, de uitkomst van beeld-bench en beeld-kwaliteit,
// de vaste zin over snapshots, en de knoppen "Zet uit voor iedereen", "Opnieuw berekenen" en
// "Beeldindex wissen" (met bevestiging). Z0b zette het blok neer (Keuze 55), in de chunk van de
// tab, met het gebied beeld. De stand komt uit AdminSearch.beeld (./searchApi): Z7 geeft hem mee
// en ververst elke 5 seconden.
//
// Zonder stand (null: de analyse staat uit of is niet helemaal geïnstalleerd, dan kent de server
// zoeken op beeld niet) staat er wat 0.8.0 toonde, de kop en één zin; sinds 0.8.1 (I2) zegt die zin
// dat het niet draait en waar de beheerder ziet waarom (beeld.notReady). Alleen aantallen, nooit een
// vector, een vraag of een naam (Keuze 51 en 82).
//
// Na een knop: de nieuwe stand meteen (de PATCH geeft hem terug), of bij de volgende ronde van de
// tab; de focus blijft op de knop. Alles met Tab en Enter of Spatie.

import { type JSX, useEffect, useState } from "react";
import { errorText, fmt, type MessageKey, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { toast } from "../ui/toast";
import { type AdminBeeld, patchAdminSearch, rebuildSearch, wipeBeeld } from "./searchApi";

/** De tekst bij het model (Keuze 82): in orde, ontbreekt of niet nagerekend in CI. */
export function modelKey(model: AdminBeeld["model"]): MessageKey {
  if (model === "ok") return "beeld.admin.modelOk";
  return model === "missing" ? "beeld.admin.modelMissing" : "beeld.admin.modelUnverified";
}

/** Het afgelopen uur in één regel; zonder vragen geen p95. */
export function hourText(hour: AdminBeeld["hour"]): string {
  return t("beeld.admin.hourValue", {
    n: hour.queries,
    count: fmt.number(hour.queries),
    p95: hour.p95_ms === null ? t("beeld.admin.noP95") : `${fmt.number(Math.round(hour.p95_ms))} ms`,
    slow: fmt.number(hour.slow),
    down: fmt.number(hour.down),
    noModel: fmt.number(hour.no_model),
  });
}

export function BeeldBlock({ beeld = null }: { beeld?: AdminBeeld | null }): JSX.Element {
  if (beeld === null) {
    return (
      <section aria-labelledby="admin-beeld-title">
        <h3 id="admin-beeld-title">{t("beeld.title")}</h3>
        <p className="muted">{t("beeld.notReady")}</p>
      </section>
    );
  }
  return <BeeldView beeld={beeld} />;
}

type Busy = "switch" | "rebuild" | "wipe" | null;

function BeeldView({ beeld }: { beeld: AdminBeeld }): JSX.Element {
  // De stand van de PATCH, tot de tab een nieuwe meegeeft
  const [mine, setMine] = useState<AdminBeeld | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setMine(null), [beeld]);
  const s = mine ?? beeld;

  async function run(what: Exclude<Busy, null>, fn: () => Promise<string>): Promise<void> {
    setBusy(what);
    try {
      const message = await fn();
      setError(null);
      toast(message, { kind: "ok" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const failed = s.failed_by_code.reduce((sum, c) => sum + c.n, 0);
  return (
    <section aria-labelledby="admin-beeld-title">
      <div className="admin-head">
        <h3 id="admin-beeld-title">{t("beeld.title")}</h3>
      </div>
      <p data-search="beeld-on">
        {t("beeld.admin.state")}:{" "}
        <span className={`badge ${s.on ? "ok" : "degraded"}`}>{t(s.on ? "beeld.admin.on" : "beeld.admin.off")}</span>
      </p>
      <p>
        <Button
          busy={busy === "switch"}
          data-search="beeld-switch"
          onClick={() =>
            void run("switch", async () => {
              const next = await patchAdminSearch({ beeld: !s.on });
              if (next.beeld) setMine(next.beeld);
              return t(s.on ? "beeld.admin.turnedOff" : "beeld.admin.turnedOn");
            })
          }
        >
          {t(s.on ? "beeld.admin.turnOff" : "beeld.admin.turnOn")}
        </Button>{" "}
        <Button
          busy={busy === "rebuild"}
          data-search="beeld-rebuild"
          onClick={() =>
            void run("rebuild", async () => {
              await rebuildSearch("beeld");
              return t("beeld.admin.rebuildStarted");
            })
          }
        >
          {t("beeld.admin.rebuild")}
        </Button>{" "}
        <Button
          variant="danger"
          busy={busy === "wipe"}
          data-search="beeld-wipe"
          onClick={async () => {
            const yes = await confirm({
              title: t("beeld.admin.wipeTitle"),
              message: t("beeld.admin.wipeText"),
              confirm: t("beeld.admin.wipeConfirm"),
              danger: true,
            });
            if (!yes) return;
            await run("wipe", async () => {
              await wipeBeeld();
              return t("beeld.admin.wiped");
            });
          }}
        >
          {t("beeld.admin.wipe")}
        </Button>
      </p>
      {error && <p role="alert">{error}</p>}
      <dl className="admin-totals" data-search="beeld">
        <div>
          <dt>{t("beeld.admin.model")}</dt>
          <dd data-search="beeld-model">
            {t("beeld.admin.modelName")} {t(modelKey(s.model))}
          </dd>
        </div>
        <div>
          <dt>{t("beeld.admin.analyse")}</dt>
          <dd data-search="beeld-analyse">{t(s.analyse === "ok" ? "beeld.admin.running" : "beeld.admin.notRunning")}</dd>
        </div>
        <div>
          <dt>{t("beeld.admin.seen")}</dt>
          <dd data-search="beeld-seen">{t("beeld.admin.seenValue", { seen: fmt.number(s.seen), photos: fmt.number(s.photos) })}</dd>
        </div>
        <div>
          <dt>{t("beeld.admin.failed")}</dt>
          <dd data-search="beeld-failed">
            {failed === 0
              ? fmt.number(0)
              : s.failed_by_code.map((c) => t("beeld.admin.failedCode", { code: c.code, count: fmt.number(c.n) })).join(", ")}
          </dd>
        </div>
        <div>
          <dt>{t("beeld.admin.hour")}</dt>
          <dd data-search="beeld-hour">{hourText(s.hour)}</dd>
        </div>
        <div>
          <dt>{t("beeld.admin.space")}</dt>
          <dd data-search="beeld-space">
            {t("beeld.admin.spaceValue", {
              db: fmt.bytes(s.space.db_bytes),
              cache: fmt.bytes(s.space.cache_bytes),
              models: fmt.bytes(s.space.models_bytes),
            })}
          </dd>
        </div>
        <div>
          <dt>{t("beeld.admin.bench")}</dt>
          <dd data-search="beeld-bench">
            {s.bench
              ? t("beeld.admin.benchValue", {
                  time: fmt.dateTime(s.bench.at),
                  p95: `${fmt.number(Math.round(s.bench.p95_ms))} ms`,
                  rate: fmt.number(Math.round(s.bench.photos_per_minute)),
                })
              : t("beeld.admin.notDone")}
          </dd>
        </div>
        <div>
          <dt>{t("beeld.admin.quality")}</dt>
          <dd data-search="beeld-quality">
            {s.quality
              ? t(s.quality.ok ? "beeld.admin.qualityOk" : "beeld.admin.qualityBad", { time: fmt.dateTime(s.quality.at) })
              : t("beeld.admin.notDone")}
          </dd>
        </div>
      </dl>
      <p className="muted">{t("beeld.admin.privacy")}</p>
    </section>
  );
}
