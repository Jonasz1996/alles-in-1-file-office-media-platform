import { type JSX, useEffect } from "react";
import { t } from "../i18n";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";

/** De server is niet te bereiken (bij het opstarten). Probeert het vanzelf opnieuw zodra de
 * browser weer online is, en met de knop. */
export function OfflineScreen({ onRetry }: { onRetry(): void }): JSX.Element {
  useEffect(() => {
    window.addEventListener("online", onRetry);
    return () => window.removeEventListener("online", onRetry);
  }, [onRetry]);
  return (
    <main className="center">
      <EmptyState
        icon="warning"
        title={t("shell.offline.title")}
        action={
          <Button variant="primary" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        }
      >
        <p>{t("shell.offline.text")}</p>
      </EmptyState>
    </main>
  );
}
