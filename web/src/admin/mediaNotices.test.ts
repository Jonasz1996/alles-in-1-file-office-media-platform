// De meldingen van media op /beheer (§3.4, app/media/monitor.py NOTICES): de web-app vertaalt ze
// op code, net als die van office. Zonder vertaling bleef de Nederlandse tekst van de server staan.
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../i18n";
import { MEDIA_NOTICES } from "../media/types";
import type { Notice } from "./api";
import { noticeText } from "./format";

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["admin"]);
}

beforeAll(() => inLocale("nl"));
afterEach(() => inLocale("nl"));

const note = (code: string): Notice => ({ code, params: {}, text: `server: ${code}` });

describe("de meldingen van media", () => {
  it("zijn alle zes vertaald, in het Nederlands en het Engels", async () => {
    expect(MEDIA_NOTICES).toHaveLength(6);
    for (const locale of ["nl", "en"] as const) {
      await inLocale(locale);
      for (const code of MEDIA_NOTICES) {
        const text = noticeText(note(code));
        expect(text, `${locale} ${code}`).not.toBe(`server: ${code}`);
        expect(text, `${locale} ${code}`).not.toMatch(/[{}]|admin\./);
      }
    }
  });

  it("het Nederlands is de tekst van de server", async () => {
    expect(noticeText(note("media_worker_down"))).toBe(
      "De mediaworker is niet bereikbaar; video en muziek spelen nu niet af.",
    );
    await inLocale("en");
    expect(noticeText(note("media_sandbox_off"))).toBe(
      "The media worker runs without isolation (locally, inside the app).",
    );
  });
});
