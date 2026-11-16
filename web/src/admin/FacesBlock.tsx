// Het blok "Gezichten" in de tab "Zoeken en foto's" op /beheer (fase 6, van X2; Keuze 67): de
// container analyse en de modellen, de rij, aantallen (nooit per gebruiker, nooit een gezicht), de
// schakelaar "Gezichtsherkenning toestaan" en de zin over reservekopieën. Z0b zette het blok neer
// (Keuze 55), in de chunk van de tab, met het gebied photos_faces. De stand komt uit
// AdminSearch.faces (./searchApi); de tab ververst hem elke 5 s.
//
// null (de analyse staat uit of de map met sleutels ontbreekt: gezichten draaien niet op deze
// server) houdt het blok van 0.8.0: de kop en één zin, zonder knop of schakelaar; sinds 0.8.1 (I2)
// zegt die zin dat het niet draait en waar de beheerder ziet waarom (photos_faces.notReady).
// Met een stand (X2b) de regels van Keuze 67; de kwaliteit is "niet gemeten" tot app.cli
// analyse-kwaliteit draaide (Vraag 8).
//
// Toetsenbord: Tab naar het vinkje (Spatie; uitzetten vraagt eerst, de focus staat dan op
// Annuleren) en naar "Mislukte opnieuw" (Enter); de focus blijft daarna op die plek.

import { type JSX, useEffect, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { toast } from "../ui/toast";
import { type AdminFaces, patchAdminSearch, rebuildSearch } from "./searchApi";

export function FacesBlock({ faces = null }: { faces?: AdminFaces | null }): JSX.Element {
  if (faces === null) {
    return (
      <section aria-labelledby="admin-faces-title">
        <h3 id="admin-faces-title">{t("photos_faces.title")}</h3>
        <p className="muted">{t("photos_faces.notReady")}</p>
      </section>
    );
  }
  return <FacesState faces={faces} />;
}

/** De redenen van down die de app kent (app.analyse.client.REASONS, bad_answer en no_sqlite van
 * app.analyse.monitor); een andere reden staat er letterlijk. */
const REASONS = [
  "key_missing",
  "key_reused",
  "no_socket",
  "refused",
  "handshake",
  "no_answer",
  "sandbox",
  "bad_answer",
  "no_sqlite",
] as const;

/** De regel Analyse: de stand, met bij down de reden en bij models_missing de namen. */
export function analyseText(f: Pick<AdminFaces, "analyse" | "reason">): string {
  const state = t(`photos_faces.admin.state.${f.analyse}`);
  if (!f.reason || (f.analyse !== "down" && f.analyse !== "models_missing")) return state;
  const known = (REASONS as readonly string[]).includes(f.reason);
  const reason = known ? t(`photos_faces.admin.reason.${f.reason as (typeof REASONS)[number]}`) : f.reason;
  return `${state}: ${reason}`;
}

/** De regel Rij analyse: wachtend, gedaan in het laatste uur, en waar hij op wacht. */
export function queueText(q: AdminFaces["queue"]): string {
  const text = t("photos_faces.admin.queueValue", { waiting: fmt.number(q.waiting), hour: fmt.number(q.done_hour) });
  return q.waits_for ? `${text}; ${t(`photos_faces.admin.waitsFor.${q.waits_for}`)}` : text;
}

/** De regel Snelheid: de laatste analyse-bench en de geschatte tijd voor wat er wacht. */
export function speedText(f: Pick<AdminFaces, "bench" | "eta_s">): string {
  if (!f.bench) return t("photos_faces.admin.notBenched");
  const text = t("photos_faces.admin.speedValue", {
    n: fmt.number(f.bench.photos_per_minute, { maximumFractionDigits: 1 }),
    time: fmt.dateTime(f.bench.at),
  });
  return f.eta_s && f.eta_s > 0 ? `${text}; ${t("photos_faces.admin.eta", { time: fmt.duration(f.eta_s) })}` : text;
}

/** De regel Kwaliteit: de laatste analyse-kwaliteit, of "niet gemeten". */
export function qualityText(q: AdminFaces["quality"]): string {
  if (!q) return t("photos_faces.admin.notMeasured");
  return t(q.ok ? "photos_faces.admin.qualityOk" : "photos_faces.admin.qualityBad", { time: fmt.dateTime(q.at) });
}

function FacesState({ faces }: { faces: AdminFaces }): JSX.Element {
  const [allowed, setAllowed] = useState(faces.allowed);
  const [busy, setBusy] = useState<"allowed" | "retry" | null>(null);
  // Een nieuwe stand van de tab (elke 5 s) wint van wat hier werd gezet
  useEffect(() => setAllowed(faces.allowed), [faces.allowed]);

  async function toggle(want: boolean) {
    if (busy) return; // niet uitschakelen: dan raakt het vinkje de focus kwijt
    if (!want) {
      const ok = await confirm({
        title: t("photos_faces.admin.offTitle"),
        message: t("photos_faces.admin.offText"),
        confirm: t("photos_faces.admin.offConfirm"),
        danger: true,
      });
      if (!ok) return;
    }
    setBusy("allowed");
    try {
      const next = await patchAdminSearch({ faces_allowed: want });
      setAllowed(next.faces?.allowed ?? want);
      toast(t("photos_faces.admin.saved"), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    setBusy("retry");
    try {
      await rebuildSearch("faces");
      toast(t("photos_faces.admin.retried"), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  const models =
    faces.models.length === 0
      ? t("photos_faces.admin.noModels")
      : faces.models
          .map(
            (m) =>
              `${t("photos_faces.admin.model", { name: m.name, version: m.version })}: ${t(
                m.sha_ok ? "photos_faces.admin.shaOk" : "photos_faces.admin.shaBad",
              )}`,
          )
          .join("; ");
  const limits = faces.limits
    ? t("photos_faces.admin.limitsValue", {
        cpus: fmt.number(faces.limits.cpus, { maximumFractionDigits: 2 }),
        mem: fmt.bytes(faces.limits.mem_mb * 1024 * 1024),
      })
    : t("photos_faces.admin.noLimits");
  const failed =
    faces.failed_by_code.length === 0
      ? t("photos_faces.admin.noFailed")
      : faces.failed_by_code.map((c) => `${c.code} ${fmt.number(c.n)}`).join(", ");
  const rows: [string, string, string][] = [
    ["analyse", t("photos_faces.admin.analyse"), analyseText(faces)],
    ["models", t("photos_faces.admin.models"), models],
    ["limits", t("photos_faces.admin.limits"), limits],
    ["users", t("photos_faces.admin.usersOn"), fmt.number(faces.users_on)],
    ["queue", t("photos_faces.admin.queue"), queueText(faces.queue)],
    ["failed", t("photos_faces.admin.failed"), failed],
    [
      "total",
      t("photos_faces.admin.total"),
      t("photos_faces.admin.totalValue", {
        faces: fmt.number(faces.faces),
        named: fmt.number(faces.named),
        bytes: fmt.bytes(faces.db_bytes),
      }),
    ],
    ["speed", t("photos_faces.admin.speed"), speedText(faces)],
  ];
  if (faces.wipe_waiting) rows.push(["wipe", t("photos_faces.admin.wipe"), t("photos_faces.admin.wipeWaiting")]);
  rows.push(["quality", t("photos_faces.admin.quality"), qualityText(faces.quality)]);

  return (
    <section aria-labelledby="admin-faces-title">
      <h3 id="admin-faces-title">{t("photos_faces.title")}</h3>
      <dl className="admin-totals" data-search="faces">
        {rows.map(([key, label, value]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd data-search={`faces-${key}`}>{value}</dd>
          </div>
        ))}
      </dl>
      <p>
        <label className="check">
          <input
            type="checkbox"
            checked={allowed}
            aria-busy={busy === "allowed" || undefined}
            data-search="faces-allowed"
            onChange={(e) => void toggle(e.target.checked)}
          />
          {t("photos_faces.admin.allowed")}
        </label>
      </p>
      <p className="muted">{t("photos_faces.admin.backups")}</p>
      <p>
        <Button busy={busy === "retry"} data-search="faces-retry" onClick={() => void retry()}>
          {t("photos_faces.admin.retry")}
        </Button>
      </p>
    </section>
  );
}
