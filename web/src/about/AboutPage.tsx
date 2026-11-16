// /over (fase 6, I2; Keuze 29 en K1-13): de bronnen van de kaart en de modellen van de analyse,
// met hun licentie. Altijd bereikbaar voor wie is ingelogd (ook een gast), met de analyse aan of
// uit: de bronvermelding van de plaatsen hoort er altijd te staan. De modellen staan er ook
// altijd: het image heeft hun pakketten altijd, en het ontwerp (bouwontwerp.md :1403, :2244,
// :5300-5303, K1-13 op :5763) en de README noemen ze zonder voorwaarde. De pagina vraagt de
// server niets en toont niets van een gebruiker.

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { PageTitle } from "../ui/PageTitle";
import { GEO, GEO_LINKS, LICENSE_PLACES, MODEL_LINKS, MODELS, PACKAGES, type SourceLink } from "./sources";

/** De regels over de kaart, in de taal van nu. */
export function mapLines(): string[] {
  const pd = t("about.map.publicDomain");
  return [
    t("about.map.places", GEO.places),
    t("about.map.countries", { ...GEO.countries, license: pd }),
    t("about.map.world", { ...GEO.world, license: pd }),
  ];
}

/** De regels over de modellen, in de taal van nu. */
export function modelLines(): string[] {
  return [
    t("about.analyse.yunet", MODELS.yunet),
    t("about.analyse.sface", MODELS.sface),
    t("about.analyse.siglip", MODELS.siglip),
  ];
}

/** De pakketten van de analyse, één regel per pakket. */
export function packageLines(): string[] {
  return PACKAGES.map((p) => t("about.packages.item", p));
}

function Links({ links }: { links: readonly SourceLink[] }): JSX.Element {
  return (
    <ul>
      {links.map((l) => (
        <li key={l.url}>
          <a href={l.url} rel="noreferrer">
            {l.text}
          </a>
        </li>
      ))}
    </ul>
  );
}

function Lines({ lines }: { lines: string[] }): JSX.Element {
  return (
    <ul>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

export default function AboutPage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("about.title")}</PageTitle>
      <p>{t("about.intro")}</p>
      <section aria-labelledby="about-map">
        <h2 id="about-map">{t("about.map.title")}</h2>
        <Lines lines={mapLines()} />
        <Links links={GEO_LINKS} />
      </section>
      <section aria-labelledby="about-analyse">
        <h2 id="about-analyse">{t("about.analyse.title")}</h2>
        <p className="muted">{t("about.analyse.intro")}</p>
        <Lines lines={modelLines()} />
        <p>{t("about.analyse.changed")}</p>
        <p>{t("about.analyse.texts", LICENSE_PLACES)}</p>
        <Links links={MODEL_LINKS} />
      </section>
      <section aria-labelledby="about-packages">
        <h2 id="about-packages">{t("about.packages.title")}</h2>
        <p>{t("about.packages.intro")}</p>
        <Lines lines={packageLines()} />
      </section>
    </main>
  );
}
