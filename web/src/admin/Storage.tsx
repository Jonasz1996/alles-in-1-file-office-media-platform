// /beheer/opslag: per node een gestapelde balk (bestanden, previews, uploads, database, overig,
// vrij), de totalen, het gebruik per gebruiker en de bewaartermijn van de prullenbak.

import { type FormEvent, type JSX, useEffect, useState } from "react";
import { errorText, fmt, type MessageKey, t } from "../i18n";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import { usageLevel } from "../ui/UsageBar";
import { adminApi, type NodeStorage, type StorageOverview } from "./api";
import { ago, type PartKey, segments } from "./format";
import { usePolling } from "./usePolling";

const PARTS: PartKey[] = ["blobs", "previews", "uploads", "database", "other", "free"];

/** De naam van een stuk van de balk ("other" heet in het woordenboek misc: other is een meervoudsvorm). */
export function partLabel(k: PartKey): string {
  return t(`admin.storage.parts.${k === "other" ? "misc" : k}` as MessageKey);
}

export function Storage(): JSX.Element {
  const storage = usePolling(adminApi.storage, 60_000);
  const s = storage.data;
  return (
    <section aria-labelledby="admin-storage-title">
      <h2 id="admin-storage-title">{t("admin.storage.title")}</h2>
      {storage.error !== null && (
        <p role="alert">{t("admin.refreshFailed", { error: errorText(storage.error) })}</p>
      )}
      {s && (
        <>
          <h3>{t("admin.storage.perNode")}</h3>
          {s.nodes.map((n) => (
            <NodeBar key={n.name} node={n} />
          ))}
          <Totals overview={s} />
          <PerUser overview={s} />
        </>
      )}
      <Retention />
    </section>
  );
}

function NodeBar({ node }: { node: NodeStorage }): JSX.Element {
  const disk = node.disk;
  const ours = node.parts.blobs + node.parts.previews + node.parts.uploads + node.parts.database;
  // Wat de schijf verder gebruikt (andere programma's, het systeem) telt als overig
  const parts = disk ? { ...node.parts, other: Math.max(node.parts.other, disk.used_bytes - ours) } : node.parts;
  const total = disk ? disk.total_bytes : ours + parts.other;
  const segs = segments(parts, total, disk ? disk.free_bytes : 0);
  const summary = disk
    ? t("admin.storage.diskText", {
        used: fmt.bytes(disk.used_bytes),
        total: fmt.bytes(disk.total_bytes),
        free: fmt.bytes(disk.free_bytes),
      })
    : t("admin.storage.noDisk");
  return (
    <figure className="admin-node">
      <figcaption>
        <strong>{node.name}</strong> <span className="muted">{summary}</span>{" "}
        <span className="muted">({t("admin.storage.measured", { ago: ago(node.measured_at) })})</span>
      </figcaption>
      <div
        className={`storage-bar ${disk ? usageLevel(disk.used_bytes, disk.total_bytes) : "ok"}`}
        role="img"
        aria-label={segs.map((x) => `${partLabel(x.key)} ${fmt.bytes(x.bytes)}`).join(", ")}
      >
        {segs.map((x) => (
          <span
            key={x.key}
            className={`storage-seg seg-${x.key}`}
            style={{ width: `${x.percent}%` }}
            title={`${partLabel(x.key)}: ${fmt.bytes(x.bytes)}`}
          />
        ))}
      </div>
      <ul className="storage-values" aria-label={t("admin.storage.legend")}>
        {PARTS.filter((k) => k !== "free" || disk).map((k) => (
          <li key={k}>
            <span className={`storage-swatch seg-${k}`} aria-hidden="true" />
            {partLabel(k)}: {fmt.bytes(k === "free" ? (disk?.free_bytes ?? 0) : parts[k])}
          </li>
        ))}
      </ul>
    </figure>
  );
}

function Totals({ overview }: { overview: StorageOverview }): JSX.Element {
  const x = overview.totals;
  const rows: [string, string][] = [
    [t("admin.storage.logical"), fmt.bytes(x.logical_bytes)],
    [t("admin.storage.stored"), fmt.bytes(x.stored_bytes)],
    [t("admin.storage.versions"), fmt.bytes(x.versions_bytes)],
    [t("admin.storage.trash"), fmt.bytes(x.trash_bytes)],
    [t("admin.storage.parts.previews"), fmt.bytes(x.previews_bytes)],
    [t("admin.storage.parts.uploads"), fmt.bytes(x.uploads_bytes)],
    [t("admin.storage.reserved"), fmt.bytes(x.uploads_reserved_bytes)],
    [t("admin.storage.parts.database"), fmt.bytes(x.db_bytes)],
  ];
  return (
    <section aria-labelledby="admin-storage-totals">
      <h3 id="admin-storage-totals">{t("admin.storage.totals")}</h3>
      <dl className="admin-totals">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="muted">{t("admin.storage.replicas", { n: x.replicas })}</p>
    </section>
  );
}

function PerUser({ overview }: { overview: StorageOverview }): JSX.Element {
  return (
    <section aria-labelledby="admin-storage-users">
      <h3 id="admin-storage-users">{t("admin.storage.users")}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("admin.storage.user")}</th>
              <th>{t("admin.storage.files")}</th>
              <th>{t("admin.storage.inTrash")}</th>
              <th>{t("admin.storage.oldVersions")}</th>
              <th>{t("admin.storage.quota")}</th>
            </tr>
          </thead>
          <tbody>
            {overview.users.map((u) => {
              const used = u.files_bytes + u.trash_bytes + u.versions_bytes;
              return (
                <tr key={u.id}>
                  <td>{u.display_name}</td>
                  <td>
                    {fmt.bytes(u.files_bytes)} <span className="muted">({fmt.number(u.file_count)})</span>
                  </td>
                  <td>{fmt.bytes(u.trash_bytes)}</td>
                  <td>{fmt.bytes(u.versions_bytes)}</td>
                  <td className={`admin-usage ${usageLevel(used, u.quota_bytes || null)}`}>
                    {u.quota_bytes
                      ? t("common.usage.ofTotal", { used: fmt.bytes(used), total: fmt.bytes(u.quota_bytes) })
                      : t("admin.users.unlimited")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Retention(): JSX.Element {
  const [days, setDays] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    adminApi
      .trashSettings()
      .then((s) => live && setDays(String(s.retention_days)))
      .catch((e: unknown) => live && setProblem(errorText(e)));
    return () => {
      live = false;
    };
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    const n = Number(days);
    if (!/^\d+$/.test(days ?? "") || n > 3650) {
      setProblem(t("admin.storage.retentionInvalid"));
      return;
    }
    setBusy(true);
    try {
      const s = await adminApi.setTrashSettings(n);
      setDays(String(s.retention_days));
      setProblem(null);
      toast(t("admin.storage.retentionSaved"), { kind: "ok" });
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-retention">
      <h3 id="admin-retention">{t("admin.storage.retention")}</h3>
      <form className="inline-form" onSubmit={save}>
        <Field label={t("admin.storage.retentionDays")} hint={t("admin.storage.retentionHelp")} error={problem}>
          {(c) => (
            <input
              {...c}
              type="number"
              min={0}
              max={3650}
              step={1}
              value={days ?? ""}
              disabled={days === null}
              onChange={(e) => {
                setDays(e.target.value);
                setProblem(null);
              }}
            />
          )}
        </Field>
        <Button type="submit" busy={busy} disabled={days === null}>
          {t("common.save")}
        </Button>
      </form>
    </section>
  );
}
