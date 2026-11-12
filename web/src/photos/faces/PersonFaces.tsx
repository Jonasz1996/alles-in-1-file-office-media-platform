// Gezichten op de pagina van een persoon (fase 6, van X2; Keuze 66): het blok "Misschien ook
// Anna". Z0b zette de plek (review contract F3): PersonPage.tsx van Z6 laadt dit bestand lazy,
// met het gebied photos_faces. Tot X2 het vult, toont het niets. Het contract: de default export
// met PersonFacesProps, een eigen chunk.

export interface PersonFacesProps {
  /** De persoon van de pagina (params.id van /fotos/personen/:id). */
  personId: string;
}

export default function PersonFaces(_: PersonFacesProps): null {
  return null;
}
