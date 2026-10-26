import { describe, expect, it } from "vitest";
import { classify } from "./routing";

const ORIGIN = "https://platform.voorbeeld.nl";

function req(path: string, o: { method?: string; mode?: string; headers?: Record<string, string> } = {}) {
  return { url: new URL(path, ORIGIN).href, method: o.method ?? "GET", mode: o.mode ?? "cors", headers: o.headers };
}

describe("classify", () => {
  it("navigaties naar pagina's van de app zijn de schil", () => {
    for (const path of ["/", "/map/0b5e5f8e", "/fotos", "/s/abc_DEF-1", "/beheer/gebruikers?x=1", "/index.html"]) {
      expect(classify(req(path, { mode: "navigate" }), ORIGIN)).toBe("shell");
    }
  });

  it("chunks en de vaste bestanden van de schil zijn assets", () => {
    for (const path of [
      "/assets/x.js",
      "/assets/Files-abc123.js",
      "/assets/shell-1.css",
      "/boot.js",
      "/manifest.webmanifest",
      "/favicon.svg",
      "/favicon.ico",
      "/icons/icon-192.png",
    ]) {
      expect(classify(req(path), ORIGIN)).toBe("asset");
    }
  });

  it("de API, de live stroom, health en .well-known nooit", () => {
    for (const path of [
      "/api/v1/events",
      "/api/v1/files/root",
      "/api/v1/previews/ab/256.webp",
      "/api",
      "/health",
      "/ready",
      "/.well-known/acme-challenge/x",
      "/sw.js",
    ]) {
      expect(classify(req(path), ORIGIN)).toBeNull();
      expect(classify(req(path, { mode: "navigate" }), ORIGIN)).toBeNull();
    }
  });

  it("office (fase 4) nooit: de editor, zijn websocket en zijn bestanden komen altijd van het net", () => {
    for (const path of [
      "/office",
      "/office/",
      "/office/web-apps/apps/api/documents/api.js",
      "/office/9.0.0-abc/web-apps/apps/documenteditor/main/index.html",
      "/office/cache/files/x/output.docx",
    ]) {
      expect(classify(req(path), ORIGIN)).toBeNull();
      expect(classify(req(path, { mode: "navigate" }), ORIGIN)).toBeNull();
    }
    // Een pad dat alleen zo begint, is gewoon een pagina van de app
    expect(classify(req("/officetuin", { mode: "navigate" }), ORIGIN)).toBe("shell");
    expect(classify(req("/bewerken/abc", { mode: "navigate" }), ORIGIN)).toBe("shell");
  });

  it("tus PATCH, POST en andere methoden nooit", () => {
    expect(classify(req("/api/v1/tus/abc", { method: "PATCH" }), ORIGIN)).toBeNull();
    expect(classify(req("/assets/x.js", { method: "POST" }), ORIGIN)).toBeNull();
    expect(classify(req("/", { method: "POST", mode: "navigate" }), ORIGIN)).toBeNull();
    expect(classify(req("/assets/x.js", { method: "HEAD" }), ORIGIN)).toBeNull();
  });

  it("Range-verzoeken nooit (video en audio)", () => {
    expect(classify(req("/assets/x.js", { headers: { Range: "bytes=0-" } }), ORIGIN)).toBeNull();
    const headers = new Map([["range", "bytes=10-20"]]);
    const withGet = { url: `${ORIGIN}/assets/x.js`, method: "GET", headers: { get: (n: string) => headers.get(n) ?? null } };
    expect(classify(withGet, ORIGIN)).toBeNull();
  });

  it("een andere origin of een bestand als navigatie niet", () => {
    expect(classify({ url: "https://elders.nl/assets/x.js", method: "GET" }, ORIGIN)).toBeNull();
    expect(classify({ url: "https://elders.nl/", method: "GET", mode: "navigate" }, ORIGIN)).toBeNull();
    expect(classify(req("/icons/icon-192.png", { mode: "navigate" }), ORIGIN)).toBeNull();
    expect(classify(req("/assets/", {}), ORIGIN)).toBeNull();
    expect(classify(req("/iets-anders.txt"), ORIGIN)).toBeNull();
    expect(classify({ url: "chrome-extension://abc/x.js", method: "GET" })).toBeNull();
  });

  it("zonder origin geldt die van het adres", () => {
    expect(classify({ url: `${ORIGIN}/assets/x.js`, method: "get" })).toBe("asset");
    expect(classify({ url: "niet een adres", method: "GET" })).toBeNull();
  });
});
