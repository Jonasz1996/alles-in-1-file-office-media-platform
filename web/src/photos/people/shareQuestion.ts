// De vraag van Keuze 42: "Nieuwe foto's die passen, worden ook gedeeld. Ja of nee?" Na een regel
// op een gedeeld album (PUT gaf needs_confirm), en na een nieuwe deling of publieke link op een
// album met een regel waarvan nieuwe items nog niet gedeeld worden. Een dialoog bovenop wat er
// open is (ook het deeldialoog); Esc is nee. Het antwoord gaat naar POST /albums/{id}/rule/confirm.

import { loadAreas, t } from "../../i18n";
import { confirm } from "../../ui/dialogs";
import { toast } from "../../ui/toast";
import { confirmAlbumRule } from "./api";
import type { AlbumRuleInfo } from "./types";

/** Stelt de vraag en geeft het antwoord (true: ja, delen). */
export async function askShareNew(albumId: string, info: Pick<AlbumRuleInfo, "items" | "private"> | null): Promise<boolean> {
  await loadAreas(["photos_people"]);
  const counts = info ? ` ${t("photos_people.share.counts", { items: info.items, private: info.private })}` : "";
  const yes = await confirm({
    title: t("photos_people.share.title"),
    message: `${t("photos_people.share.question")}${counts}`,
    confirm: t("photos_people.share.yes"),
    cancel: t("photos_people.share.no"),
  });
  await confirmAlbumRule(albumId, yes);
  toast(yes ? t("photos_people.share.yesDone") : t("photos_people.share.noDone"), { kind: "ok" });
  return yes;
}
