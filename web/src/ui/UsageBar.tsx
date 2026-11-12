import { type JSX, useId } from "react";
import { fmt, t } from "../i18n";

/** Hoe vol iets is: warn vanaf 80 %, bad vanaf 95 %. */
export function usageLevel(used: number, total: number | null): "ok" | "warn" | "bad" {
  if (!total || total <= 0) return "ok";
  const f = used / total;
  return f >= 0.95 ? "bad" : f >= 0.8 ? "warn" : "ok";
}

/** Gebruikte ruimte als balk (<meter>) met de tekst "1,2 GB van 10,0 GB". total null = onbeperkt. */
export function UsageBar({ used, total, label }: { used: number; total: number | null; label: string }): JSX.Element {
  const id = useId();
  const limited = total !== null && total > 0;
  const text = limited
    ? t("common.usage.ofTotal", { used: fmt.bytes(used), total: fmt.bytes(total) })
    : t("common.usage.usedOnly", { used: fmt.bytes(used) });
  return (
    <div className={`usage-bar ${usageLevel(used, total)}`}>
      <span id={id} className="usage-label">
        {label}
      </span>
      {limited && (
        <meter
          aria-labelledby={id}
          min={0}
          max={total}
          value={Math.min(used, total)}
          low={total * 0.8}
          high={total * 0.95}
          optimum={0}
        />
      )}
      <span className="usage-text muted">{text}</span>
    </div>
  );
}
