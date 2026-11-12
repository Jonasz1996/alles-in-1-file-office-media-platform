import { afterEach, describe, expect, it, vi } from "vitest";
import type { ServerEvent } from "../events";
import { type NoticesModule, officeListener } from "./listener";

afterEach(() => {
  vi.restoreAllMocks();
});

const ev = (type: string): ServerEvent => ({ type, data: { key: type } });

describe("de luisteraar van App.tsx (Keuze 33)", () => {
  it("laadt niets zonder gebeurtenis office.*", async () => {
    const load = vi.fn(async (): Promise<NoticesModule> => ({ handle: vi.fn() }));
    const listen = officeListener(load);
    listen(ev("node.changed"));
    listen(ev("officex"));
    listen(ev("share.created"));
    await Promise.resolve();
    expect(load).not.toHaveBeenCalled();
  });

  it("laadt de meldingen één keer, en geeft elke gebeurtenis office.* in volgorde door", async () => {
    const handled: string[] = [];
    const load = vi.fn(async (): Promise<NoticesModule> => ({ handle: (e) => handled.push(e.type) }));
    const listen = officeListener(load);
    listen(ev("office.unsaved"));
    listen(ev("node.changed"));
    listen(ev("office.recovered"));
    await vi.waitFor(() => expect(handled).toEqual(["office.unsaved", "office.recovered"]));
    listen(ev("office.presence"));
    await vi.waitFor(() => expect(handled).toHaveLength(3));
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("lukt het laden niet, dan bij de volgende gebeurtenis opnieuw (geen onafgehandelde fout)", async () => {
    const handle = vi.fn();
    const load = vi
      .fn<() => Promise<NoticesModule>>()
      .mockRejectedValueOnce(new Error("geen verbinding"))
      .mockResolvedValue({ handle });
    const listen = officeListener(load);
    listen(ev("office.unsaved"));
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    listen(ev("office.recovered"));
    await vi.waitFor(() => expect(handle).toHaveBeenCalledTimes(1));
    expect(handle.mock.calls[0][0].type).toBe("office.recovered");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("een fout in één melding houdt de volgende niet tegen", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const seen: string[] = [];
    const listen = officeListener(async () => ({
      handle: (e) => {
        seen.push(e.type);
        if (e.type === "office.unsaved") throw new Error("kapot");
      },
    }));
    listen(ev("office.unsaved"));
    listen(ev("office.recovered"));
    await vi.waitFor(() => expect(seen).toEqual(["office.unsaved", "office.recovered"]));
    expect(error).toHaveBeenCalledTimes(1);
  });
});
