import { describe, expect, it } from "vitest";
import { withoutInlineHandlers } from "./loadApi";

// Een element met attributen, zonder DOM (de tests draaien in Node)
class Element {
  attrs: Record<string, string> = {};
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
}
class Frame extends Element {}

describe("withoutInlineHandlers", () => {
  it("laat on…-attributen weg terwijl DocEditor zijn iframe maakt, en de rest niet", () => {
    const frame = withoutInlineHandlers(() => {
      const f = new Frame();
      f.setAttribute("onmousewheel", "");
      f.setAttribute("ONclick", "x()");
      f.setAttribute("allow", "autoplay");
      f.setAttribute("allowfullscreen", "");
      return f;
    }, Frame.prototype);
    expect(frame.attrs).toEqual({ allow: "autoplay", allowfullscreen: "" });
  });

  it("zet setAttribute daarna terug zoals het was", () => {
    withoutInlineHandlers(() => undefined, Frame.prototype);
    expect(Object.getOwnPropertyDescriptor(Frame.prototype, "setAttribute")).toBeUndefined();
    const f = new Frame();
    f.setAttribute("onmousewheel", "");
    expect(f.attrs).toEqual({ onmousewheel: "" });
  });

  it("ook als make een fout gooit", () => {
    expect(() =>
      withoutInlineHandlers(() => {
        throw new Error("api.js kapot");
      }, Frame.prototype),
    ).toThrow("api.js kapot");
    expect(Object.getOwnPropertyDescriptor(Frame.prototype, "setAttribute")).toBeUndefined();
  });

  it("een eigen setAttribute komt terug", () => {
    class Own extends Element {}
    const mine = function (this: Element, name: string, value: string) {
      this.attrs[`eigen-${name}`] = value;
    };
    Object.defineProperty(Own.prototype, "setAttribute", { value: mine, configurable: true, writable: true });
    const f = withoutInlineHandlers(() => {
      const o = new Own();
      o.setAttribute("onload", "y()");
      o.setAttribute("name", "frameEditor");
      return o;
    }, Own.prototype);
    expect(f.attrs).toEqual({ "eigen-name": "frameEditor" });
    expect(Own.prototype.setAttribute).toBe(mine);
  });
});
