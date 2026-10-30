// Native HLS (Keuze 32): video.src = master.m3u8, voor Safari en iOS zonder MMS (later
// misschien webOS). De speler gebruikt dan SrcEngine uit direct.ts met het adres van de
// playlist; dit bestand zegt alleen of de browser HLS zelf speelt.

let cached: boolean | null = null;

export function nativeHls(): boolean {
  if (cached !== null) return cached;
  try {
    cached = document.createElement("video").canPlayType("application/vnd.apple.mpegurl") !== "";
  } catch {
    cached = false;
  }
  return cached;
}
