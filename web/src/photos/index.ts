// Wat andere pakketten van Foto's gebruiken (frozen na P0): lazy, met het woordenboek "photos".

import { lazyWithAreas } from "../i18n";
import type { AddToAlbumProps } from "./AddToAlbum";
import type { PublicAlbumProps } from "./PublicAlbum";

export type { AddToAlbumProps } from "./AddToAlbum";
export type { PublicAlbumProps } from "./PublicAlbum";

/** "Toevoegen aan album" voor een selectie (Bestanden, Foto's). */
export const AddToAlbumDialog = lazyWithAreas<AddToAlbumProps>(() => import("./AddToAlbum"), ["photos"]);

/** Een album achter een publieke link (Public.tsx kiest dit bij share.kind === "album"). */
export const PublicAlbum = lazyWithAreas<PublicAlbumProps>(() => import("./PublicAlbum"), ["photos"]);
