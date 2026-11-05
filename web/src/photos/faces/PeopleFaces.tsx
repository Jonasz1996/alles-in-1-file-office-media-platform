// Gezichten op /fotos/personen (fase 6, van X2; Keuze 66): bovenaan de regel met de voortgang (of,
// als gezichten uit staan, één regel met "Lees wat dat betekent"), dan de blokken "Is dit …?" en
// "Wie is dit?". Z0b zette de plek (review contract F3): PeoplePage.tsx van Z6 laadt dit bestand
// lazy, met het gebied photos_faces. Tot X2 het vult, toont het niets. Het contract: de default
// export zonder props, een eigen chunk.

export default function PeopleFaces(): null {
  return null;
}
