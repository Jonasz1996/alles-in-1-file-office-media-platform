// Een album achter een publieke link (/s/<token>): de foto's met de sleutel van de link
// (GET /api/v1/public/album/items). Zonder inloggen, dus niet via api.request.

import type { PublicLink } from "../publicApi";
import { PAGE, type PhotoItem } from "./api";

/** PhotoItem zonder shared. */
export type PublicPhotoItem = Omit<PhotoItem, "shared">;

export interface PublicPhotoPage {
  items: PublicPhotoItem[];
  next: string | null;
}

type Requester = Pick<PublicLink, "request">;

export function publicAlbumPage(link: Requester, after: string | null, limit = PAGE): Promise<PublicPhotoPage> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (after) q.set("after", after);
  return link.request<PublicPhotoPage>(`/api/v1/public/album/items?${q}`);
}

/** Alle foto's van het album, pagina na pagina; onPage krijgt wat er tot nu toe is. */
export async function publicAlbumItems(
  link: Requester,
  onPage?: (items: PublicPhotoItem[]) => void,
): Promise<PublicPhotoItem[]> {
  const items: PublicPhotoItem[] = [];
  let after: string | null = null;
  do {
    const page: PublicPhotoPage = await publicAlbumPage(link, after);
    items.push(...page.items);
    onPage?.(items.slice());
    after = page.next;
  } while (after !== null);
  return items;
}
