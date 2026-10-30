import { beforeEach, describe, expect, it } from "vitest";
import { mayLoad, resetImageGateForTests, scrolledForTests, waitToLoad } from "./imageGate";

beforeEach(() => resetImageGateForTests());

describe("thumbnails tijdens het scrollen", () => {
  it("zonder scrollen meteen; een adres dat al mocht, mag daarna altijd", () => {
    expect(mayLoad("/a")).toBe(true);
    scrolledForTests();
    expect(mayLoad("/a")).toBe(true);
    expect(mayLoad("/b")).toBe(false);
  });

  it("wachten: de toestemming komt (hier zonder frames: meteen)", () => {
    scrolledForTests();
    const got: string[] = [];
    waitToLoad("/c", () => got.push("c"));
    expect(got).toEqual(["c"]);
    expect(mayLoad("/c")).toBe(true);
  });
});
