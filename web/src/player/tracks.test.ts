import { describe, expect, it } from "vitest";
import type { AudioTrack, SubtitleTrack } from "../media/types";
import {
  audioLabel,
  autoSubtitle,
  currentAudio,
  langFits,
  langKey,
  languageName,
  type LabelWords,
  nextAudio,
  srclang,
  subtitleLabel,
  textTracks,
  toggleSubtitle,
} from "./tracks";

function sub(id: string, o: Partial<SubtitleTrack> = {}): SubtitleTrack {
  return { id, lang: null, label: id.toUpperCase(), forced: false, sdh: false, default: false, state: "ready", ...o };
}

function audio(index: number, o: Partial<AudioTrack> = {}): AudioTrack {
  return { index, lang: null, title: null, codec: "aac", channels: 2, default: false, ...o };
}

const WORDS: LabelWords = {
  forced: "geforceerd",
  sdh: "SDH",
  burn: (l) => `${l} (wordt ingebrand, kost omzetten)`,
  unknown: "Onbekend",
  channels: (n) => (n === 6 ? "5.1" : n === 2 ? "stereo" : `${n} kanalen`),
};

describe("talen", () => {
  it("nld, dut en nl zijn dezelfde taal; und, zxx en leeg zijn geen taal", () => {
    expect(langKey("nld")).toBe("nl");
    expect(langKey("dut")).toBe("nl");
    expect(langKey("NL")).toBe("nl");
    expect(langKey("pt_BR")).toBe("pt");
    for (const none of ["und", "zxx", "mis", "mul", "", null, undefined, "  "]) expect(langKey(none)).toBeNull();
  });

  it("langFits: zonder taal past alles (m3.md, afwijking 19)", () => {
    expect(langFits("nld", "dut")).toBe(true);
    expect(langFits("nld", "eng")).toBe(false);
    expect(langFits("und", "eng")).toBe(true);
    expect(langFits(null, "eng")).toBe(true);
    expect(langFits("nld", "und")).toBe(true);
    expect(langFits("nld", null)).toBe(true);
  });

  it("namen via Intl.DisplayNames, in de taal van de app", () => {
    expect(languageName("nld", "nl")).toBe("Nederlands");
    expect(languageName("eng", "nl")).toBe("Engels");
    expect(languageName("ger", "en")).toBe("German");
    expect(languageName("fre", "en")).toBe("French");
    expect(languageName("und", "nl")).toBeNull();
    expect(languageName("x!", "nl")).toBeNull();
  });

  it("srclang van een <track>", () => {
    expect(srclang("eng")).toBe("en");
    expect(srclang("und")).toBe("");
  });
});

describe("de regel voor geforceerd (Keuze 29)", () => {
  const subs = [
    sub("e3", { lang: "eng", forced: true }),
    sub("e4", { lang: "nld", forced: true }),
    sub("e5", { lang: "nld" }),
    sub("e6", { lang: "nld", forced: true, state: "burn" }),
  ];

  it("een geforceerd tekstspoor in de taal van het audiospoor gaat aan", () => {
    expect(autoSubtitle(subs, "nld")).toBe("e4");
    expect(autoSubtitle(subs, "dut")).toBe("e4");
    expect(autoSubtitle(subs, "eng")).toBe("e3");
  });

  it("niet in een andere taal", () => {
    expect(autoSubtitle(subs, "fra")).toBeNull();
  });

  it("und telt als geen taal: past altijd (net als decide)", () => {
    expect(autoSubtitle([sub("e2", { lang: "und", forced: true })], "eng")).toBe("e2");
    expect(autoSubtitle([sub("e2", { lang: "fra", forced: true })], "und")).toBe("e2");
    expect(autoSubtitle([sub("e2", { lang: null, forced: true })], null)).toBe("e2");
  });

  it("een geforceerd beeldspoor gaat nooit vanzelf aan", () => {
    expect(autoSubtitle([sub("e6", { lang: "nld", forced: true, state: "burn" })], "nld")).toBeNull();
  });

  it("een mislukt spoor ook niet; een spoor in voorbereiding wel", () => {
    expect(autoSubtitle([sub("e7", { forced: true, state: "failed" })], "nld")).toBeNull();
    expect(autoSubtitle([sub("e7", { forced: true, state: "pending" })], "nld")).toBe("e7");
  });

  it("niet-geforceerde sporen gaan niet vanzelf aan", () => {
    expect(autoSubtitle([sub("e5", { lang: "nld" })], "nld")).toBeNull();
  });

  it("textTracks laat de beeldsporen weg", () => {
    expect(textTracks(subs).map((s) => s.id)).toEqual(["e3", "e4", "e5"]);
  });
});

describe("audiosporen", () => {
  const tracks = [audio(1, { lang: "nld", default: true }), audio(2, { lang: "eng", channels: 6 }), audio(4, { lang: "eng", title: "Commentaar" })];

  it("het spoor dat speelt: gekozen, standaard, eerste", () => {
    expect(currentAudio(tracks, 2)?.index).toBe(2);
    expect(currentAudio(tracks, null)?.index).toBe(1);
    expect(currentAudio([audio(3), audio(5)], undefined)?.index).toBe(3);
    expect(currentAudio([], 1)).toBeNull();
  });

  it("A: het volgende, rondom; met één spoor niets", () => {
    expect(nextAudio(tracks, null)).toBe(2);
    expect(nextAudio(tracks, 2)).toBe(4);
    expect(nextAudio(tracks, 4)).toBe(1);
    expect(nextAudio([audio(1)], 1)).toBeNull();
  });

  it("labels: taal, titel en kanalen", () => {
    expect(audioLabel(tracks[0], "nl", WORDS)).toBe("Nederlands · stereo");
    expect(audioLabel(tracks[1], "nl", WORDS)).toBe("Engels · 5.1");
    expect(audioLabel(tracks[2], "en", WORDS)).toBe("English · Commentaar · stereo");
    expect(audioLabel(audio(9, { channels: null }), "nl", WORDS)).toBe("Onbekend");
  });
});

describe("ondertitels: labels en C", () => {
  it("de taal in plaats van NLD, plus geforceerd en SDH", () => {
    expect(subtitleLabel(sub("e3", { lang: "nld", label: "NLD" }), "nl", WORDS)).toBe("Nederlands");
    expect(subtitleLabel(sub("e3", { lang: "eng", label: "ENG", forced: true }), "nl", WORDS)).toBe("Engels (geforceerd)");
    expect(subtitleLabel(sub("x0", { lang: "eng", label: "ENG", sdh: true }), "en", WORDS)).toBe("English (SDH)");
    expect(subtitleLabel(sub("e2", { lang: null, label: "#2" }), "nl", WORDS)).toBe("#2");
  });

  it("een titel van het spoor blijft, met de taal ervoor", () => {
    expect(subtitleLabel(sub("e3", { lang: "eng", label: "Director's commentary" }), "en", WORDS)).toBe(
      "English · Director's commentary",
    );
    expect(subtitleLabel(sub("e3", { lang: "nld", label: "Nederlands volledig" }), "nl", WORDS)).toBe("Nederlands volledig");
  });

  it("een beeldspoor: wordt ingebrand, kost omzetten", () => {
    expect(subtitleLabel(sub("e6", { lang: "nld", label: "NLD", state: "burn" }), "nl", WORDS)).toBe(
      "Nederlands (wordt ingebrand, kost omzetten)",
    );
  });

  it("C: aan gaat uit; uit gaat naar de laatste keuze, of het eerste tekstspoor", () => {
    const subs = [sub("e6", { state: "burn" }), sub("e3"), sub("e4")];
    expect(toggleSubtitle("e3", null, subs)).toBeNull();
    expect(toggleSubtitle(null, "e4", subs)).toBe("e4");
    expect(toggleSubtitle(null, null, subs)).toBe("e3");
    expect(toggleSubtitle(null, "weg", subs)).toBe("e3");
    expect(toggleSubtitle(null, null, [])).toBeNull();
  });
});
