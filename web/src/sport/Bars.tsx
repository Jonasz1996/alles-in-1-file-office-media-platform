// Een eenvoudige staafgrafiek (afstand per week of per maand), met de waarden ook als tekst voor
// een schermlezer (een tabel die alleen voor hen zichtbaar is).

import type { JSX } from "react";
import { t } from "../i18n";
import type { Totals } from "./api";
import { km } from "./format";

export function Bars({
  title,
  rows,
  label,
}: {
  title: string;
  rows: readonly Totals[];
  label(row: Totals): string;
}): JSX.Element {
  const max = Math.max(1, ...rows.map((r) => r.distance));
  return (
    <section className="sport-bars">
      <h2>{title}</h2>
      <div className="sport-bars-row" aria-hidden="true">
        {rows.map((r) => (
          <div key={r.key} className="sport-bar" title={`${label(r)}: ${t("sport.units.km", { v: km(r.distance, 1) })}`}>
            <span className="sport-bar-fill" style={{ height: `${(r.distance / max) * 100}%` }} />
            <span className="sport-bar-label">{label(r)}</span>
          </div>
        ))}
      </div>
      <table className="sr-only">
        <caption>{title}</caption>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{label(r)}</th>
              <td>{t("sport.units.km", { v: km(r.distance, 1) })}</td>
              <td>{t("sport.stats.count", { n: r.count })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
