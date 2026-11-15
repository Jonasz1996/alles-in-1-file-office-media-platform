import { describe, expect, it } from "vitest";
import type { PhotoItem, PhotoPage } from "./api";
import { SectionStore } from "./store";

function item(id: string, shared: boolean): PhotoItem & { album: { shared: boolean } } {
  return {
    id,
    name: `${id}.jpg`,
    mime: "image/jpeg",
    size: 1,
    modified_at: "2026-11-01T10:00:00Z",
    media: "image",
    taken_at: "2026-11-01T10:00:00Z",
    taken_day: 20261101,
    width: null,
    height: null,
    duration: null,
    etag: `"${id}"`,
    thumb: null,
    large: null,
    album: { shared },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("SectionStore.want: een sectie in beeld laadt opnieuw als hij verouderd is", () => {
  it("ook als hij al geladen was toen want() kwam", async () => {
    // Een album (één sectie) met drie foto's; na "Ja, delen" zijn ze gedeeld, met hetzelfde aantal
    let shared = false;
    let fetches = 0;
    const store = new SectionStore(async (): Promise<PhotoPage> => {
      fetches++;
      return { items: ["a", "b", "c"].map((id) => item(id, shared)), next: null };
    }, [3]);
    store.want([0]);
    await tick();
    expect(fetches).toBe(1);
    // De rij tekent opnieuw en meldt dezelfde sectie nog eens: hij is al geladen
    store.want([0]);
    // Het album opnieuw gelezen: hetzelfde aantal, dus de items blijven staan tot het nieuwe er is
    shared = true;
    store.setSections([3], undefined, () => 0);
    await tick();
    expect(fetches).toBe(2);
    expect(store.section(0)?.map((it) => (it as ReturnType<typeof item>).album.shared)).toEqual([true, true, true]);
  });

  it("een sectie buiten beeld laadt niet opnieuw", async () => {
    let fetches = 0;
    const store = new SectionStore(async (s): Promise<PhotoPage> => {
      fetches++;
      return { items: [item(`f${s}`, true)], next: null };
    }, [1, 1]);
    store.want([0, 1]);
    await tick();
    expect(fetches).toBe(2);
    store.want([1]);
    store.setSections([1, 1], undefined, (s) => s);
    await tick();
    expect(fetches).toBe(3);
    // Sectie 0 staat er nog (verouderd) en laadt pas als hij weer in beeld komt
    expect(store.section(0)).toHaveLength(1);
    store.want([0]);
    await tick();
    expect(fetches).toBe(4);
  });
});
