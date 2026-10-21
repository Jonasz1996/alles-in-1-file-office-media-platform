// Plaatshouder (P0) voor P5: eigen foto's en video's aan een album toevoegen.
import type { JSX } from "react";
import { t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

export interface AddToAlbumProps {
  nodeIds: string[];
  onClose(): void;
}

export default function AddToAlbumDialog({ onClose }: AddToAlbumProps): JSX.Element {
  return (
    <Dialog
      title={t("photos.addToAlbum")}
      size="s"
      onClose={onClose}
      actions={
        <Button data-autofocus="" onClick={onClose}>
          {t("common.close")}
        </Button>
      }
    >
      <p className="muted">{t("photos.albumsSoon")}</p>
    </Dialog>
  );
}
