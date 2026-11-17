import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import type { PageWord } from "../search/types";
import pageViewerSource from "./PageViewer.tsx?raw";
import pdfViewSource from "./PdfView.tsx?raw";
import { TextLayer } from "./TextLayer";
import {
  firstHit,
  fold,
  markQuery,
  matcher,
  PageWordsStore,
  place,
  queryTerms,
  SCAN_EMPTY,
  SCAN_PAGES,
  tokenOf,
  WORDS_GAP_MS,
} from "./textLayer";

const w = (t: string, x = 0.1, y = 0.1, wd = 0.1, h = 0.02): PageWord => ({ t, x, y, w: wd, h });

function marks(q: string, words: string[]): string[] {
  const terms = queryTerms(q);
  if (!terms) return [];
  const match = matcher(terms);
  return words.filter((t) => match(t));
}

describe("welke woorden de tekstlaag markeert (Keuze 18, zoals parse() en de snippets)", () => {
  it("vouwt zoals de API: zonder accenten en hoofdletters, ß als ss", () => {
    expect(fold("Straße")).toBe("strasse");
    expect(fold("Café CRÈME")).toBe("cafe creme");
    expect(fold("İstanbul")).toBe("istanbul");
  });

  it("de woorden van de vraag, en het laatste als voorvoegsel alleen als de vraag op een letter of cijfer eindigt", () => {
    expect(queryTerms("Factuur 2024")).toEqual({ words: ["factuur", "2024"], prefix: true });
    expect(queryTerms("factuur ")).toEqual({ words: ["factuur"], prefix: false });
    expect(queryTerms('"de factuur"')).toEqual({ words: ["de", "factuur"], prefix: false });
    // Korter dan drie tekens: een heel woord
    expect(queryTerms("ab")).toEqual({ words: ["ab"], prefix: false });
    expect(queryTerms("e-mail")).toEqual({ words: ["e", "mail"], prefix: true });
    // Een zero-width space aan het eind telt niet mee (q.rstrip(_ZWSP) in parse())
    expect(queryTerms("factuur\u200b")).toEqual({ words: ["factuur"], prefix: true });
  });

  it("niets te zoeken: null; meer dan 10 woorden: de eerste 10", () => {
    expect(queryTerms(null)).toBeNull();
    expect(queryTerms("")).toBeNull();
    expect(queryTerms(" ,, - ")).toBeNull();
    expect(queryTerms("a b c d e f g h i j k l")?.words).toEqual(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]);
  });

  it("een woord van de pagina (tot witruimte) past als een van zijn stukken past", () => {
    const page = ["Factuur,", "facturen", "(factuur)", "fact", "Straße", "e-mailadres", "MAIL:"];
    expect(marks("factuur ", page)).toEqual(["Factuur,", "(factuur)"]);
    // Voorvoegsel: "factu" vindt factuur en facturen, niet "fact"
    expect(marks("factu", page)).toEqual(["Factuur,", "facturen", "(factuur)"]);
    expect(marks("strasse", page)).toEqual(["Straße"]);
    expect(marks("straße ", ["Strasse", "strassen"])).toEqual(["Strasse"]);
    // Een heel woord: "mail" past niet in "e-mailadres", wel in "MAIL:"
    expect(marks("mail ", page)).toEqual(["MAIL:"]);
  });

  it("?q= van de pagina, behalve in de stand beeld (Keuze 81)", () => {
    expect(markQuery(new URLSearchParams("q=factuur&bestand=f1"))).toBe("factuur");
    expect(markQuery(new URLSearchParams("q=hond+op+het+strand&mode=image"))).toBeNull();
    expect(markQuery(new URLSearchParams("bestand=f1"))).toBeNull();
  });

  it("het download-token uit page_url", () => {
    expect(tokenOf("/api/v1/files/f1/pages/{n}.webp?w={w}&token=abc.def-1")).toBe("abc.def-1");
    expect(tokenOf("/api/v1/public/k/files/f1/pages/{n}.webp?w={w}")).toBeNull();
    expect(tokenOf("/zonder-vraag")).toBeNull();
  });

  it("een regel eindigt waar het volgende woord lager of weer links begint", () => {
    const words = [w("Een", 0.1, 0.1), w("regel", 0.25, 0.1), w("Nog", 0.1, 0.13), w("een", 0.25, 0.13)];
    const terms = queryTerms("een ");
    const placed = place(words, terms && matcher(terms));
    expect(placed.map((p) => [p.word.t, p.marked, p.lineEnd])).toEqual([
      ["Een", true, false],
      ["regel", false, true],
      ["Nog", false, false],
      ["een", true, true],
    ]);
    expect(place(words, null).some((p) => p.marked)).toBe(false);
  });
});

describe("de tekstlaag tekenen", () => {
  it("elk woord doorzichtig op zijn vak, de treffers in <mark>, spaties en regeleinden voor kopiëren", () => {
    const terms = queryTerms("factuur ");
    const html = renderToStaticMarkup(
      createElement(TextLayer, {
        words: [w("De", 0.1, 0.2, 0.05, 0.02), w("factuur", 0.16, 0.2, 0.12, 0.02), w("<b>maart</b>", 0.1, 0.25, 0.2, 0.02)],
        match: terms && matcher(terms),
        width: 600,
        height: 800,
      }),
    );
    expect(html).toContain('<div class="text-layer" style="font-size:800px">');
    // Zonder canvas (vitest): het vak precies, zonder scaleX
    expect(html).toContain(
      '<span style="left:10.000%;top:20.000%;width:5.000%;height:2.000%;font-size:0.02em">De </span>',
    );
    expect(html).toContain(
      '<mark style="left:16.000%;top:20.000%;width:12.000%;height:2.000%;font-size:0.02em">factuur</mark><br/>',
    );
    // Tekst uit het bestand blijft tekst
    expect(html).toContain("&lt;b&gt;maart&lt;/b&gt;</span></div>");
    expect(html.match(/<mark/g)).toHaveLength(1);
  });
});

describe("de eerste pagina met een treffer", () => {
  const match = (t: string) => t === "factuur";
  const pages: Record<number, PageWord[] | null> = {
    1: [w("voorblad")],
    2: null,
    3: [w("de"), w("factuur", 0.1, 0.6)],
    4: [w("factuur", 0.1, 0.1)],
  };
  const want = async (n: number) => pages[n] ?? null;

  it("leest de pagina's na elkaar en stopt bij de eerste treffer", async () => {
    const read: number[] = [];
    const result = await firstHit(4, async (n) => (read.push(n), want(n)), match, () => true);
    expect(result).toEqual({ kind: "found", page: 3, y: 0.6 });
    expect(read).toEqual([1, 2, 3]);
  });

  it("zonder treffer: none, of limit als er meer pagina's zijn dan SCAN_PAGES", async () => {
    expect(await firstHit(2, want, match, () => true)).toEqual({ kind: "none" });
    const read: number[] = [];
    const many = await firstHit(SCAN_PAGES + 10, async (n) => (read.push(n), [w("x")]), match, () => true);
    expect(many).toEqual({ kind: "limit", pages: SCAN_PAGES });
    expect(read).toHaveLength(SCAN_PAGES);
  });

  it("een scan zonder tekst: stopt na SCAN_EMPTY lege pagina's achter elkaar", async () => {
    const read: number[] = [];
    expect(await firstHit(100, async (n) => (read.push(n), null), match, () => true)).toEqual({ kind: "no_text" });
    expect(read).toHaveLength(SCAN_EMPTY);
  });

  it("de viewer dicht of een andere vraag: stopt zonder uitkomst", async () => {
    let live = true;
    const read: number[] = [];
    const result = await firstHit(
      4,
      async (n) => {
        read.push(n);
        live = false;
        return want(n);
      },
      match,
      () => live,
    );
    expect(result).toBeNull();
    expect(read).toEqual([1]);
  });
});

describe("de woorden per pagina ophalen (hooguit 20 per seconde, §3.4)", () => {
  function clock() {
    let now = 0;
    const waits: number[] = [];
    return {
      waits,
      options: {
        now: () => now,
        sleep: async (ms: number) => {
          waits.push(ms);
          now += ms;
        },
      },
    };
  }

  it("na elkaar, met minstens WORDS_GAP_MS ertussen; dezelfde pagina één keer", async () => {
    const c = clock();
    const store = new PageWordsStore(c.options);
    const asked: number[] = [];
    store.setLoader(async (n) => (asked.push(n), [w(`p${n}`)]));
    const [a, b, again] = await Promise.all([store.want(1), store.want(2), store.want(1)]);
    expect(a).toEqual([w("p1")]);
    expect(b).toEqual([w("p2")]);
    expect(again).toBe(a);
    expect(asked).toEqual([1, 2]);
    expect(c.waits).toEqual([WORDS_GAP_MS]);
    expect(store.get(2)).toEqual([w("p2")]);
    expect(await store.want(2)).toEqual([w("p2")]);
    expect(asked).toEqual([1, 2]);
  });

  it("404 (geen tekst) wordt onthouden, een andere fout niet; 429: wachten en opnieuw", async () => {
    const c = clock();
    const store = new PageWordsStore(c.options);
    const asked: number[] = [];
    let rate = 1;
    store.setLoader(async (n) => {
      asked.push(n);
      if (n === 1) throw new ApiError(404, "Deze pagina heeft geen tekst", "not_found");
      if (n === 2) throw new ApiError(401, "Ongeldig of verlopen token", "invalid_token");
      if (n === 3 && rate-- > 0) throw new ApiError(429, "Te veel", "rate_limited");
      return [w(`p${n}`)];
    });
    expect(await store.want(1)).toBeNull();
    expect(store.get(1)).toBeNull();
    expect(await store.want(2)).toBeNull();
    expect(store.get(2)).toBeUndefined();
    expect(await store.want(3)).toEqual([w("p3")]);
    expect(c.waits).toContain(1000);
    await store.want(1);
    await store.want(2);
    expect(asked).toEqual([1, 2, 3, 3, 2]);
  });

  it("zonder token vraagt hij niets; search.ready laat pagina's zonder tekst opnieuw vragen", async () => {
    const store = new PageWordsStore(clock().options);
    expect(await store.want(1)).toBeNull();
    const asked: number[] = [];
    let text = false;
    store.setLoader(async (n) => {
      asked.push(n);
      if (!text) throw new ApiError(404, "geen tekst", "not_found");
      return [w("ocr")];
    });
    let seen = 0;
    store.subscribe(() => seen++);
    expect(await store.want(1)).toBeNull();
    text = true;
    store.forgetEmpty();
    expect(await store.want(1)).toEqual([w("ocr")]);
    expect(asked).toEqual([1, 1]);
    expect(seen).toBe(3);
    expect(store.snapshot()).toBe(3);
  });
});

describe("de viewer gebruikt de tekstlaag (Z4)", () => {
  it("PdfView: alleen ingelogd (via een publieke link geen woorden), met het token uit page_url", () => {
    expect(pdfViewSource).toContain('source.kind === "user" && template ? tokenOf(template) : null');
    expect(pdfViewSource).toContain("overlay={layer?.overlay}");
    expect(pdfViewSource).toContain("onPageLoad={layer?.onPageLoad}");
    expect(pdfViewSource).toContain("jumpTo={layer?.jumpTo}");
  });

  it("PageViewer: de laag over elke pagina die laadt, en springen alleen als je nog bovenaan staat", () => {
    expect(pageViewerSource).toContain("{!src.failed && overlay?.(n, w, h)}");
    expect(pageViewerSource).toContain("onPageLoad?.(n);");
    expect(pageViewerSource).toContain("if (el.scrollTop > 0 || i < 0 || i >= count) return;");
  });
});
