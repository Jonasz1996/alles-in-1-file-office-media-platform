// Het blok "Gezichten" in de tab "Zoeken en foto's" op /beheer (fase 6, van X2; Keuze 67): de
// container analyse en de modellen, de rij, aantallen (nooit per gebruiker, nooit een gezicht), de
// schakelaar "Gezichtsherkenning toestaan" en de zin over reservekopieën. Z0b zette het blok neer
// (Keuze 55), in de chunk van de tab, met het gebied photos_faces (de kop en de zin zonder
// analyse); de regels staan sinds 0.8.2 in admin.search.faces, zodat de schermen van gezichten ze
// niet meeladen (budget faces, 12 kB). De stand komt uit AdminSearch.faces (./searchApi); de tab
// ververst hem elke 5 s.
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

/** De redenen van down die de app kent (app.analyse.client.REASONS, en bad_answer,
 * pg_not_enabled en no_pg_triggers van app.analyse.monitor); een andere reden staat er
 * letterlijk. */
const REASONS = [
  "key_missing",
  "key_reused",
  "no_socket",
  "refused",
  "handshake",
  "no_answer",
  "sandbox",
  "bad_answer",
  "pg_not_enabled",
  "no_pg_triggers",
] as const;

/** De regel Analyse: de stand, met bij down de reden en bij models_missing de namen. */
export function analyseText(f: Pick<AdminFaces, "analyse" | "reason">): string {
  const state = t(`admin.search.faces.state.${f.analyse}`);
  if (!f.reason || (f.analyse !== "down" && f.analyse !== "models_missing")) return state;
  const known = (REASONS as readonly string[]).includes(f.reason);
  const reason = known ? t(`admin.search.faces.reason.${f.reason as (typeof REASONS)[number]}`) : f.reason;
  return `${state}: ${reason}`;
}

/** De regel Rij analyse: wachtend, gedaan in het laatste uur, en waar hij op wacht. */
export function queueText(q: AdminFaces["queue"]): string {
  const text = t("admin.search.faces.queueValue", { waiting: fmt.number(q.waiting), hour: fmt.number(q.done_hour) });
  return q.waits_for ? `${text}; ${t(`admin.search.faces.waitsFor.${q.waits_for}`)}` : text;
}

/** De regel Snelheid: de laatste analyse-bench en de geschatte tijd voor wat er wacht. */
export function speedText(f: Pick<AdminFaces, "bench" | "eta_s">): string {
  if (!f.bench) return t("admin.search.faces.notBenched");
  const text = t("admin.search.faces.speedValue", {
    n: fmt.number(f.bench.photos_per_minute, { maximumFractionDigits: 1 }),
    time: fmt.dateTime(f.bench.at),
  });
  return f.eta_s && f.eta_s > 0 ? `${text}; ${t("admin.search.faces.eta", { time: fmt.duration(f.eta_s) })}` : text;
}

/** De regel Kwaliteit: de laatste analyse-kwaliteit, of "niet gemeten". */
export function qualityText(q: AdminFaces["quality"]): string {
  if (!q) return t("admin.search.faces.notMeasured");
  return t(q.ok ? "admin.search.faces.qualityOk" : "admin.search.faces.qualityBad", { time: fmt.dateTime(q.at) });
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
        title: t("admin.search.faces.offTitle"),
        message: t("admin.search.faces.offText"),
        confirm: t("admin.search.faces.offConfirm"),
        danger: true,
      });
      if (!ok) return;
    }
    setBusy("allowed");
    try {
      const next = await patchAdminSearch({ faces_allowed: want });
      setAllowed(next.faces?.allowed ?? want);
      toast(t("admin.search.faces.saved"), { kind: "ok" });
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
      toast(t("admin.search.faces.retried"), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  const models =
    faces.models.length === 0
      ? t("admin.search.faces.noModels")
      : faces.models
          .map(
            (m) =>
              `${t("admin.search.faces.model", { name: m.name, version: m.version })}: ${t(
                m.sha_ok ? "admin.search.faces.shaOk" : "admin.search.faces.shaBad",
              )}`,
          )
          .join("; ");
  const limits = faces.limits
    ? t("admin.search.faces.limitsValue", {
        cpus: fmt.number(faces.limits.cpus, { maximumFractionDigits: 2 }),
        mem: fmt.bytes(faces.limits.mem_mb * 1024 * 1024),
      })
    : t("admin.search.faces.noLimits");
  const failed =
    faces.failed_by_code.length === 0
      ? t("admin.search.faces.noFailed")
      : faces.failed_by_code.map((c) => `${c.code} ${fmt.number(c.n)}`).join(", ");
  const rows: [string, string, string][] = [
    ["analyse", t("admin.search.faces.analyse"), analyseText(faces)],
    ["models", t("admin.search.faces.models"), models],
    ["limits", t("admin.search.faces.limits"), limits],
    ["users", t("admin.search.faces.usersOn"), fmt.number(faces.users_on)],
    ["queue", t("admin.search.faces.queue"), queueText(faces.queue)],
    ["failed", t("admin.search.faces.failed"), failed],
    [
      "total",
      t("admin.search.faces.total"),
      t("admin.search.faces.totalValue", {
        faces: fmt.number(faces.faces),
        named: fmt.number(faces.named),
        bytes: fmt.bytes(faces.db_bytes),
      }),
    ],
    ["speed", t("admin.search.faces.speed"), speedText(faces)],
  ];
  if (faces.wipe_waiting) rows.push(["wipe", t("admin.search.faces.wipe"), t("admin.search.faces.wipeWaiting")]);
  // 0.8.2: een eigenaar met versleutelde gezichten zonder sleutel (alleen het aantal)
  if (faces.keys_missing > 0)
    rows.push(["keys", t("admin.search.faceKeys.title"), t("admin.search.faceKeys.missing", { n: fmt.number(faces.keys_missing) })]);
  rows.push(["quality", t("admin.search.faces.quality"), qualityText(faces.quality)]);

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
          {t("admin.search.faces.allowed")}
        </label>
      </p>
      <p className="muted">{t("admin.search.faces.backups")}</p>
      <p>
        <Button busy={busy === "retry"} data-search="faces-retry" onClick={() => void retry()}>
          {t("admin.search.faces.retry")}
        </Button>
      </p>
    </section>
  );
}
