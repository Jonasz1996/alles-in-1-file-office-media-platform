// /beheer/office: hoe office ervoor staat (de document server, de verbindingen, de piek), de open
// documenten, wat niet opgeslagen is (Terugzetten en Verwijderen), het omzetten naar PDF, de
// instellingen (aan of uit, de grens, tussentijds opslaan en versies) en hoe je office op de
// server aan- of uitzet. Ververst elke 5 seconden zolang het tabblad zichtbaar is.
//
// Van O5 (bouwontwerp §2, O5). Beheer.tsx toont het tabblad alleen als office ingesteld is.

import { type FormEvent, type JSX, type RefObject, useEffect, useRef, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import {
  checkpointAll,
  deleteUnsaved,
  getAdmin,
  officeStatus,
  putAdmin,
  restoreUnsaved,
  retryConversions,
} from "../office/api";
import type { OfficeAdmin, OfficeOpenDocument, OfficeUnsaved } from "../office/types";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import {
  ago,
  canRestore,
  licenseText,
  OFFICE_LIMITS,
  type OfficeNumber,
  officeBadge,
  PINNED_VERSION,
  parseOfficeNumber,
  tokensText,
  unsavedReason,
} from "./format";
import { usePolling } from "./usePolling";

// Het commando in de console van de server, zoals in infra/single/README.md (O6): het script
// van GitHub via curl, want op de server staat geen install.sh in de map waar je inlogt. Geen
// tekst om te vertalen.
const INSTALL =
  "curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh";
const SERVER_OFF = `${INSTALL} | OFFICE=uit bash`;
const SERVER_ON = `${INSTALL} | OFFICE=aan bash`;

export function OfficeTab(): JSX.Element {
  const polled = usePolling(getAdmin, 5000);
  // Het antwoord van een knop geldt tot de volgende ververs
  const [override, setOverride] = useState<OfficeAdmin | null>(null);
  useEffect(() => setOverride(null), [polled.data]);
  const data = override ?? polled.data;

  return (
    <section aria-labelledby="admin-office-title">
      <h2 id="admin-office-title">
        {t("admin.officeTab.title")}
        {data && (
          <>
            {": "}
            <span className={`badge ${officeBadge(data.state)}`}>{t(`admin.officeTab.state.${data.state}`)}</span>
          </>
        )}
      </h2>
      {polled.error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(polled.error) })}</p>}
      {data && <Body data={data} reload={polled.reload} onChange={setOverride} />}
    </section>
  );
}

function Body({
  data,
  reload,
  onChange,
}: {
  data: OfficeAdmin;
  reload(): Promise<void>;
  onChange(d: OfficeAdmin): void;
}): JSX.Element {
  return (
    <>
      {!data.configured && <p role="alert">{t("admin.officeTab.notConfigured")}</p>}
      {data.state === "down" && data.down_since && (
        <p role="alert">
          {t("admin.officeTab.downSince", { time: fmt.dateTime(data.down_since), ago: ago(data.down_since) })}
        </p>
      )}
      {!data.enabled && <p className="muted">{t("admin.officeTab.disabled")}</p>}
      <Facts data={data} />
      <Now data={data} />
      <OpenDocuments data={data} reload={reload} />
      <Unsaved data={data} reload={reload} />
      <Conversions data={data} reload={reload} />
      <Settings data={data} onChange={onChange} />
      <OnServer />
    </>
  );
}

/**
 * De focus na een knop die iets deed. Gaat de knop uit of weg (Verwijderen staat uit zolang het
 * bezig is, de rij is weg na Terugzetten, Mislukte opnieuw verdwijnt), dan valt de focus naar
 * <body> en begint Tab weer boven aan de pagina. Daarom, als de focus dan nergens anders staat:
 * terug op de knop als hij er nog is, en anders op de kop van het blok. press() onthoudt de knop;
 * roep het aan vóór een vraag (de bevestiging), zolang de focus nog op de knop staat.
 */
function useFocusAfter(busy: boolean, heading: RefObject<HTMLElement | null>): () => void {
  const pressed = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (busy) return;
    const el = pressed.current;
    pressed.current = null;
    const now = document.activeElement;
    if (el === null || (now !== null && now !== document.body)) return;
    (el.isConnected ? el : heading.current)?.focus();
  }, [busy, heading]);
  return () => {
    const now = document.activeElement;
    pressed.current = now instanceof HTMLElement ? now : null;
  };
}

function Facts({ data }: { data: OfficeAdmin }): JSX.Element {
  const version =
    data.version === null
      ? t("admin.officeTab.unknown")
      : data.version === PINNED_VERSION
        ? data.version
        : t("admin.officeTab.versionOther", { version: data.version, pinned: PINNED_VERSION });
  return (
    <ul className="admin-cards" aria-label={t("admin.officeTab.facts")}>
      <li className="admin-card">
        <h3>{t("admin.officeTab.version")}</h3>
        <p>{version}</p>
      </li>
      <li className="admin-card">
        <h3>{t("admin.officeTab.license")}</h3>
        <p>{licenseText(data.license_connections)}</p>
      </li>
      <li className="admin-card">
        <h3>{t("admin.officeTab.tokens")}</h3>
        <p className={data.tokens_enforced === false ? "badge down" : undefined}>{tokensText(data.tokens_enforced)}</p>
      </li>
    </ul>
  );
}

function Now({ data }: { data: OfficeAdmin }): JSX.Element {
  return (
    <section aria-labelledby="admin-office-now">
      <h3 id="admin-office-now">{t("admin.officeTab.now")}</h3>
      <ul className="admin-cards">
        <li className="admin-card">
          <h3>{t("admin.officeTab.connections")}</h3>
          <p data-office="connections">{fmt.number(data.connections)}</p>
        </li>
        <li className="admin-card">
          <h3>{t("admin.officeTab.people")}</h3>
          <p data-office="people">{fmt.number(data.people)}</p>
        </li>
        <li className="admin-card">
          <h3>{t("admin.officeTab.documents")}</h3>
          <p data-office="documents">{fmt.number(data.documents)}</p>
        </li>
        <li className="admin-card">
          <h3>{t("admin.officeTab.peak")}</h3>
          <p>
            {data.peak
              ? t("admin.officeTab.peakText", { n: data.peak.connections, time: fmt.dateTime(data.peak.at) })
              : t("admin.officeTab.noPeak")}
          </p>
        </li>
      </ul>
    </section>
  );
}

function OpenDocuments({ data, reload }: { data: OfficeAdmin; reload(): Promise<void> }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const press = useFocusAfter(busy, heading);

  async function checkpoint() {
    press();
    setBusy(true);
    try {
      const out = await checkpointAll();
      setError(null);
      toast(
        out.sent === 0 ? t("admin.officeTab.checkpointNone") : t("admin.officeTab.checkpointDone", { ...out }),
        { kind: "ok" },
      );
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-office-open">
      <div className="admin-head">
        <h3 id="admin-office-open" ref={heading} tabIndex={-1}>
          {t("admin.officeTab.open")}
        </h3>
        {data.configured && (
          <Button busy={busy} disabled={data.open.length === 0} onClick={() => void checkpoint()}>
            {t("admin.officeTab.checkpoint")}
          </Button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {data.open.length === 0 ? (
        <p className="muted">{t("admin.officeTab.noneOpen")}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("admin.officeTab.name")}</th>
                <th>{t("admin.officeTab.owner")}</th>
                <th>{t("admin.officeTab.editors")}</th>
                <th className="wide-only">{t("admin.officeTab.since")}</th>
                <th>{t("admin.officeTab.sessionState")}</th>
              </tr>
            </thead>
            <tbody>
              {data.open.map((doc, i) => (
                <OpenRow key={`${doc.name}:${doc.since}:${i}`} doc={doc} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function OpenRow({ doc }: { doc: OfficeOpenDocument }): JSX.Element {
  const state = doc.state ?? "open";
  return (
    <tr>
      <td className="wrap">{doc.name}</td>
      <td>{doc.owner}</td>
      <td className="wrap">{doc.editors.length ? doc.editors.join(", ") : t("admin.officeTab.nobody")}</td>
      <td className="wide-only">{fmt.dateTime(doc.since)}</td>
      <td>
        {t(`admin.officeTab.sessionStates.${state}`)}
        {state !== "open" && <span className="muted"> {t("admin.officeTab.blocking")}</span>}
      </td>
    </tr>
  );
}

function Unsaved({ data, reload }: { data: OfficeAdmin; reload(): Promise<void> }): JSX.Element {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const press = useFocusAfter(busy !== null, heading);

  async function act(key: string, fn: () => Promise<string>) {
    setBusy(key);
    try {
      const message = await fn();
      setError(null);
      toast(message, { kind: "ok" });
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  function restore(item: OfficeUnsaved) {
    press();
    void act(item.key, async () => {
      const copy = await restoreUnsaved(item.key);
      return t("admin.officeTab.restored", { name: copy.name });
    });
  }

  async function remove(item: OfficeUnsaved, name: string) {
    press();
    const yes = await confirm({
      title: t("admin.officeTab.removeTitle"),
      message: t("admin.officeTab.removeText", { name }),
      confirm: t("admin.officeTab.remove"),
      danger: true,
    });
    if (!yes) return;
    await act(item.key, async () => {
      await deleteUnsaved(item.key);
      return t("admin.officeTab.removed");
    });
  }

  return (
    <section aria-labelledby="admin-office-unsaved">
      <h3 id="admin-office-unsaved" ref={heading} tabIndex={-1}>
        {t("admin.officeTab.unsaved")}
      </h3>
      <p className="muted">{t("admin.officeTab.unsavedHelp")}</p>
      {error && <p role="alert">{error}</p>}
      {data.unsaved.length === 0 ? (
        <p className="muted">{t("admin.officeTab.noneUnsaved")}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("admin.officeTab.name")}</th>
                <th>{t("admin.officeTab.owner")}</th>
                <th className="wide-only">{t("admin.officeTab.at")}</th>
                <th>{t("admin.officeTab.reason")}</th>
                <th>
                  <span className="sr-only">{t("admin.officeTab.restore")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.unsaved.map((item) => {
                const name = item.name || t("admin.officeTab.unknownName");
                return (
                  <tr key={item.key}>
                    <td className="wrap">{name}</td>
                    <td>{item.owner ?? ""}</td>
                    <td className="wide-only">{item.at ? fmt.dateTime(item.at) : ""}</td>
                    <td className="wrap">{unsavedReason(item.reason)}</td>
                    <td>
                      <div className="toolbar">
                        {canRestore(item) && (
                          <Button
                            busy={busy === item.key}
                            disabled={busy !== null && busy !== item.key}
                            aria-label={t("admin.officeTab.restoreLabel", { name })}
                            onClick={() => restore(item)}
                          >
                            {t("admin.officeTab.restore")}
                          </Button>
                        )}
                        <Button
                          variant="danger"
                          disabled={busy !== null}
                          aria-label={t("admin.officeTab.removeLabel", { name })}
                          onClick={() => void remove(item, name)}
                        >
                          {t("admin.officeTab.remove")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Conversions({ data, reload }: { data: OfficeAdmin; reload(): Promise<void> }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const press = useFocusAfter(busy, heading);

  async function retry() {
    press();
    setBusy(true);
    try {
      const out = await retryConversions();
      setError(null);
      toast(t("admin.officeTab.retried", { n: out.queued }), { kind: "ok" });
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-office-conversions">
      <div className="admin-head">
        <h3 id="admin-office-conversions" ref={heading} tabIndex={-1}>
          {t("admin.officeTab.conversions")}
        </h3>
        {data.configured && data.conversions.failed > 0 && (
          <Button busy={busy} onClick={() => void retry()}>
            {t("admin.officeTab.retry")}
          </Button>
        )}
      </div>
      <p>{t("admin.officeTab.conversionsText", { ...data.conversions })}</p>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

const FIELDS: readonly { field: OfficeNumber; label: "maxEditors" | "checkpointMinutes" | "versionMinutes" }[] = [
  { field: "max_editors", label: "maxEditors" },
  { field: "checkpoint_minutes", label: "checkpointMinutes" },
  { field: "version_minutes", label: "versionMinutes" },
];

type Form = { enabled: boolean } & Record<OfficeNumber, string>;

function formOf(data: OfficeAdmin): Form {
  return {
    enabled: data.enabled,
    max_editors: String(data.max_editors),
    checkpoint_minutes: String(data.checkpoint_minutes),
    version_minutes: String(data.version_minutes),
  };
}

function Settings({ data, onChange }: { data: OfficeAdmin; onChange(d: OfficeAdmin): void }): JSX.Element {
  // Het formulier begint bij de waarden van de server; de ververs van elke 5 seconden laat
  // wat de beheerder intypt staan
  const [form, setForm] = useState<Form>(() => formOf(data));
  const [problems, setProblems] = useState<Partial<Record<OfficeNumber, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    const found: Partial<Record<OfficeNumber, string>> = {};
    const values: Partial<Record<OfficeNumber, number>> = {};
    for (const { field } of FIELDS) {
      const n = parseOfficeNumber(field, form[field]);
      if (n === undefined) found[field] = t("admin.officeTab.invalid", { ...OFFICE_LIMITS[field] });
      else values[field] = n;
    }
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    try {
      const out = await putAdmin({
        enabled: form.enabled,
        max_editors: values.max_editors ?? data.max_editors,
        checkpoint_minutes: values.checkpoint_minutes ?? data.checkpoint_minutes,
        version_minutes: values.version_minutes ?? data.version_minutes,
      });
      setForm(formOf(out));
      setError(null);
      onChange(out);
      // De status van office in de rest van de app (Bewerken, Nieuw, PDF) meteen opnieuw, niet
      // pas na STATUS_TTL: anders zag de beheerder in Bestanden nog een minuut de oude stand
      void officeStatus.load();
      toast(t("admin.officeTab.saved"), { kind: "ok" });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-office-settings">
      <h3 id="admin-office-settings">{t("admin.officeTab.settings")}</h3>
      <form className="stack" onSubmit={save}>
        <label className="check">
          <input
            type="checkbox"
            checked={form.enabled}
            aria-describedby="admin-office-enabled-help"
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          {t("admin.officeTab.enabled")}
        </label>
        <p id="admin-office-enabled-help" className="muted">
          {t("admin.officeTab.enabledHelp")}
        </p>
        {FIELDS.map(({ field, label }) => (
          <Field
            key={field}
            label={t(`admin.officeTab.${label}`)}
            hint={t("admin.officeTab.between", { ...OFFICE_LIMITS[field] })}
            error={problems[field] ?? null}
          >
            {(c) => (
              <input
                {...c}
                type="number"
                name={field}
                min={OFFICE_LIMITS[field].min}
                max={OFFICE_LIMITS[field].max}
                step={1}
                value={form[field]}
                onChange={(e) => {
                  setForm({ ...form, [field]: e.target.value });
                  setProblems({ ...problems, [field]: undefined });
                }}
              />
            )}
          </Field>
        ))}
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy}>
            {t("common.save")}
          </Button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

function OnServer(): JSX.Element {
  return (
    <section aria-labelledby="admin-office-server">
      <h3 id="admin-office-server">{t("admin.officeTab.serverTitle")}</h3>
      <p>{t("admin.officeTab.serverText")}</p>
      <ul>
        <li>
          {t("admin.officeTab.serverOff")} <code>{SERVER_OFF}</code>
        </li>
        <li>
          {t("admin.officeTab.serverOn")} <code>{SERVER_ON}</code>
        </li>
      </ul>
      <p className="muted">{t("admin.officeTab.serverNote")}</p>
    </section>
  );
}
