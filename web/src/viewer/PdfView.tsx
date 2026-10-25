// Een PDF in de viewer: de gegevens van /document en de pagina's in PageViewer. Het adres van
// de pagina's bevat een token dat na een paar minuten verloopt: op tijd een nieuw (renew.ts),
// en na een pagina die niet laadt nog één keer (mayRenew).

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { Spinner } from "../ui/Spinner";
import { type DocumentInfo, pageHref } from "./api";
import { mayRenew } from "./media";
import PageViewer from "./PageViewer";
import { keepFresh } from "./renew";
import type { Source } from "./source";
import type { ViewerItem } from "./types";

export default function PdfView({ item, source }: { item: ViewerItem; source: Source }): JSX.Element {
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [error, setError] = useState<unknown>(null);
  const renewals = useRef({ count: 0, at: 0 });
  const renewing = useRef<Promise<number> | null>(null);

  const renew = useCallback((): Promise<number> => {
    renewing.current ??= source
      .document(item)
      .then((fresh) => {
        setDoc(fresh);
        return fresh.token_expires_in;
      })
      .finally(() => {
        renewing.current = null;
      });
    return renewing.current;
  }, [item, source]);

  useEffect(() => {
    let live = true;
    source.document(item).then(
      (d) => live && setDoc(d),
      (e: unknown) => live && setError(e),
    );
    return () => {
      live = false;
    };
  }, [item, source]);

  const expiresIn = doc?.token_expires_in;
  const loaded = doc !== null;
  useEffect(() => {
    if (!loaded || expiresIn === undefined) return;
    return keepFresh(expiresIn, renew);
    // Eén keer na het laden: keepFresh plant zelf de volgende
  }, [loaded]);

  const template = doc?.page_url ?? null;
  const url = useCallback((n: number, w: number) => (template ? pageHref(template, n, w) : ""), [template]);

  if (error) return <p className="viewer-message" role="alert">{errorText(error)}</p>;
  if (!doc) return <div className="viewer-message"><Spinner label={t("viewer.loading")} /></div>;
  if (doc.kind !== "pdf" || !doc.pages || !doc.page_sizes || !template) {
    return <p className="viewer-message muted">{t("viewer.pdf.none")}</p>;
  }
  return (
    <PageViewer
      count={doc.pages}
      sizes={doc.page_sizes}
      widths={doc.widths}
      url={url}
      toc={doc.toc}
      labels={doc.labels}
      onPageError={() => {
        if (mayRenew(renewals.current, Date.now())) renew().catch(() => {});
      }}
    />
  );
}
