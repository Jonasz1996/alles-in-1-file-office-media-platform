// De speler in de viewer van Bestanden (fase 5, van M6). viewer/MediaView.tsx (de haak van M0)
// laadt dit chunk voor een video als media aan staat, en geeft de <video> van fase 3 mee als
// fallback: voor als de speler het niet kan (geen MSE, play zegt nee) of nog niet bestaat.
// In de viewer krijgt de speler alleen Spatie, K, J, L, M, C, A, P en 0–9 (Keuze 33).
//
// M0 zette een lege versie: die toont de fallback. M6 maakt de speler.

import type { JSX } from "react";
import type { ViewerPlayerProps } from "../viewer/MediaView";

export default function ViewerPlayer({ fallback }: ViewerPlayerProps): JSX.Element {
  return fallback();
}
