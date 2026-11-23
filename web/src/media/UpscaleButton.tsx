// Knop "AI-upscaling" op de pagina van een film of aflevering (fase 11, stap 5c). Alleen voor
// beheerders: zet de video in de rij van de upscaler op de RTX 3060 Ti. De rij en het venster
// staan op /beheer → Media (admin/UpscaleBlock.tsx).
import { type JSX, useState } from "react";
import { api } from "../api";
import { useCurrentUser } from "../currentUser";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { toast } from "../ui/toast";

export function UpscaleButton({ nodeId, kind }: { nodeId: string; kind: string }): JSX.Element | null {
  const me = useCurrentUser();
  const [busy, setBusy] = useState(false);
  if (me?.role !== "admin" || !["movie", "episode", "video"].includes(kind)) return null;
  return (
    <Button
      busy={busy}
      data-upscale=""
      onClick={() => {
        setBusy(true);
        api
          .request<unknown>("POST", "/api/v1/arr/upscale", { node_id: nodeId })
          .then(() => toast(t("media.upscale.queued"), { kind: "ok" }))
          .catch((e) => toast(errorText(e), { kind: "error" }))
          .finally(() => setBusy(false));
      }}
    >
      {t("media.upscale.button")}
    </Button>
  );
}
