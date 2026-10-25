import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { markdown, safeHref } from "./markdown";

const html = (src: string) => renderToStaticMarkup(createElement(Fragment, null, markdown(src)));

describe("markdown", () => {
  it("koppen, alinea's, lijsten, citaten en code", () => {
    expect(html("# Titel\n\nEen **vette** en *schuine* zin met `code`.")).toBe(
      "<h2>Titel</h2><p>Een <strong>vette</strong> en <em>schuine</em> zin met <code>code</code>.</p>",
    );
    expect(html("- een\n- twee\n  - dieper\n\n1. eerst")).toBe(
      "<ul><li>een</li><li>twee<ul><li>dieper</li></ul></li></ul><ol><li>eerst</li></ol>",
    );
    expect(html("> geciteerd")).toBe("<blockquote><p>geciteerd</p></blockquote>");
    expect(html("```\n<b>niet vet</b>\n```")).toBe(
      '<pre class="md-code"><code>&lt;b&gt;niet vet&lt;/b&gt;</code></pre>',
    );
    expect(html("---")).toBe("<hr/>");
    expect(html("## C#")).toBe("<h3>C#</h3>");
    expect(html("| a | b |\n|---|---|\n| 1 | 2 |")).toContain("<th>a</th><th>b</th>");
  });

  it("HTML in de tekst blijft tekst", () => {
    const out = html('<script>alert(1)</script> <img src=x onerror="alert(1)">');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;");
  });

  it("links alleen naar http, https of mailto", () => {
    expect(html("[site](https://example.nl/a)")).toBe(
      '<p><a href="https://example.nl/a" target="_blank" rel="noopener noreferrer nofollow">site</a></p>',
    );
    expect(html("[mail](mailto:a@example.nl)")).toContain('href="mailto:a@example.nl"');
    for (const bad of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x", "/relatief"]) {
      const out = html(`[klik](${bad})`);
      expect(out, bad).not.toContain("href");
      expect(out).toContain("klik");
    }
    expect(html("<javascript:alert(1)>")).not.toContain("href");
    expect(safeHref(" java\nscript:alert(1)")).toBeNull();
    expect(safeHref("https://x.nl")).toBe("https://x.nl/");
  });

  it("een afbeelding wordt zijn alt-tekst", () => {
    expect(html("![een kat](https://example.nl/kat.png)")).toBe('<p><span class="md-image">een kat</span></p>');
  });

  it("veel losse sterretjes en haken blijven snel", () => {
    const start = performance.now();
    html(`${"*a _b [c ![d `e ".repeat(20_000)}`);
    expect(performance.now() - start).toBeLessThan(1000);
  });
});
