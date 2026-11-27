// Een uitsnede van een gezicht (fase 6, X2b). De route wil de inlog als Bearer-header, en die
// stuurt een <img> niet mee (review Z0b, veilig V2): dus faceCrop (fetch) en een object-URL, die
// weer vrijkomt als het gezicht verdwijnt. De uitsnede is no-store: de browser bewaart hem niet.

import { type JSX, useEffect, useState } from "react";
import { faceCrop } from "./api";
import type { CropSize } from "./types";

export function Crop({ id, size }: { id: string; size: CropSize }): JSX.Element {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let url: string | null = null;
    faceCrop(id, size).then(
      (blob) => {
        if (!live) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      },
      () => {}, // weg of geen verbinding: dan een leeg vlak
    );
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, size]);
  // De naam of de vraag staat bij de cel zelf; de uitsnede is versiering (alt leeg)
  return src ? (
    <img className="face-crop" src={src} alt="" width={size / 2} height={size / 2} decoding="async" />
  ) : (
    <span className="face-crop" aria-hidden="true" />
  );
}
