import { describe, expect, it } from "vitest";
import { allDirs, collect, type EntryLike, fromInput, isPseudoFolder, readAll } from "./walk";

/** Nep-FileSystemEntry: een map geeft zijn kinderen in stukken van hooguit 100, dan []. */
function fileEntry(fullPath: string, size = 1): EntryLike {
  const name = fullPath.split("/").pop() ?? fullPath;
  return {
    isFile: true,
    isDirectory: false,
    name,
    fullPath,
    file: (ok) => ok(new File([new Uint8Array(size)], name)),
  };
}

function dirEntry(fullPath: string, children: EntryLike[], calls: { n: number } = { n: 0 }): EntryLike {
  return {
    isFile: false,
    isDirectory: true,
    name: fullPath.split("/").pop() ?? fullPath,
    fullPath,
    createReader: () => {
      let at = 0;
      return {
        readEntries: (ok) => {
          calls.n++;
          const part = children.slice(at, at + 100);
          at += part.length;
          setTimeout(() => ok(part), 0);
        },
      };
    },
  };
}

describe("walk", () => {
  it("leest een map met meer dan 100 entries helemaal uit (readEntries tot [])", async () => {
    const calls = { n: 0 };
    const kids = Array.from({ length: 250 }, (_, i) => fileEntry(`/Groot/f${i}.txt`));
    const all = await readAll(dirEntry("/Groot", kids, calls));
    expect(all).toHaveLength(250);
    expect(calls.n).toBe(4); // 100 + 100 + 50 + []
  });

  it("een drop met mappen: paden, lege mappen en losse bestanden", async () => {
    const tree = dirEntry("/Vakantie", [
      fileEntry("/Vakantie/a.jpg"),
      dirEntry("/Vakantie/2024", [fileEntry("/Vakantie/2024/b.jpg"), dirEntry("/Vakantie/2024/Leeg", [])]),
      dirEntry("/Vakantie/Ook leeg", []),
    ]);
    const los = new File(["x"], "los.txt");
    const out = await collect([tree, fileEntry("/los.txt")], [new File([], "Vakantie"), los]);
    expect(out.files.map((f) => f.path).sort()).toEqual(["Vakantie/2024/b.jpg", "Vakantie/a.jpg", "los.txt"]);
    expect(out.dirs).toEqual(["Vakantie", "Vakantie/2024", "Vakantie/Ook leeg", "Vakantie/2024/Leeg"]);
    expect(out.rejected).toEqual([]);
  });

  it("zonder entries: de bestanden zelf, en een map als 'bestand' wordt geweigerd", async () => {
    const real = new File(["hallo"], "a.txt", { type: "text/plain" });
    const empty = new File([], "leeg.txt"); // echt leeg bestand: leesbaar
    const pseudo = new File([], "Map");
    // Een map in dt.files: lezen mislukt
    Object.defineProperty(pseudo, "slice", {
      value: () => ({ arrayBuffer: () => Promise.reject(new Error("NotReadableError")) }),
    });
    const out = await collect([null, null, null], [real, empty, pseudo]);
    expect(out.files.map((f) => f.path)).toEqual(["a.txt", "leeg.txt"]);
    expect(out.rejected).toEqual(["Map"]);
    expect(out.dirs).toEqual([]);
    expect(await isPseudoFolder(real)).toBe(false);
  });

  it("een bestand dat niet te lezen is, valt af", async () => {
    const broken: EntryLike = {
      isFile: true,
      isDirectory: false,
      name: "weg.txt",
      fullPath: "/M/weg.txt",
      file: (_ok, err) => err?.(new Error("NotFoundError")),
    };
    const out = await collect([dirEntry("/M", [broken, fileEntry("/M/ok.txt")])], []);
    expect(out.files.map((f) => f.path)).toEqual(["M/ok.txt"]);
    expect(out.dirs).toEqual(["M"]);
  });

  it("fromInput (Map uploaden) gebruikt webkitRelativePath", () => {
    const f = (path: string) => {
      const file = new File(["x"], path.split("/").pop() ?? path);
      Object.defineProperty(file, "webkitRelativePath", { value: path });
      return file;
    };
    const out = fromInput([f("Foto's/2024/a.jpg"), f("Foto's/b.jpg"), new File(["y"], "los.txt")]);
    expect(out.files.map((x) => x.path)).toEqual(["Foto's/2024/a.jpg", "Foto's/b.jpg", "los.txt"]);
    expect(out.dirs).toEqual(["Foto's", "Foto's/2024"]);
  });

  it("allDirs: elke tussenliggende map één keer, ondiep eerst", () => {
    expect(allDirs(["a/b/c/x.txt", "a/y.txt", "z.txt"], ["q/r"])).toEqual(["a", "q", "a/b", "q/r", "a/b/c"]);
  });
});
