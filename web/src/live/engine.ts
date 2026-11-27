// De speler van live tv: één fMP4-stroom zonder einde van GET /api/v1/iptv/channels/{id}/live,
// met fetch gelezen (zodat het token in de Authorization-kop gaat) en in MediaSource gezet
// (ManagedMediaSource op Safari/iOS). De server zegt de codecs in de kop X-Codecs.
//
// - Begint bij het eerste stuk: de tijd van de video springt naar het begin van wat gebufferd is.
// - Blijft live: loopt de video meer dan LAG_S achter, dan springt hij naar het eind min EDGE_S.
// - Houdt de buffer klein: wat meer dan KEEP_S achter de afspeeltijd ligt, gaat weg.
// - Valt de stroom weg, dan opnieuw verbinden (hooguit RETRIES keer na elkaar, steeds later).

import { api, ApiError } from "../api";
import { mediaSourceClass } from "../player/mse";

export type LiveState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "playing" }
  | { kind: "retrying" }
  | { kind: "ended" }
  | { kind: "unsupported" }
  | { kind: "busy"; channels: string[] }
  | { kind: "failed"; reason: string };

const LAG_S = 12;
const EDGE_S = 3;
const KEEP_S = 30;
const RETRIES = 3;

export function liveUrl(channelId: string): string {
  return `/api/v1/iptv/channels/${encodeURIComponent(channelId)}/live`;
}

function waitEvent(target: EventTarget, name: string): Promise<void> {
  return new Promise((resolve) => target.addEventListener(name, () => resolve(), { once: true }));
}

export class LiveEngine {
  private abort: AbortController | null = null;
  private objectUrl: string | null = null;
  private channel: string | null = null;
  private tries = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly video: HTMLVideoElement,
    private readonly onState: (s: LiveState) => void,
  ) {}

  play(channelId: string): void {
    this.channel = channelId;
    this.tries = 0;
    void this.start(channelId);
  }

  stop(): void {
    this.channel = null;
    this.teardown();
    this.onState({ kind: "idle" });
  }

  private teardown(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
    if (this.objectUrl) {
      this.video.removeAttribute("src");
      this.video.load();
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }
  }

  private retry(channelId: string): void {
    if (this.channel !== channelId) return;
    if (this.tries >= RETRIES) {
      this.onState({ kind: "ended" });
      return;
    }
    this.tries += 1;
    this.onState({ kind: "retrying" });
    this.timer = setTimeout(() => void this.start(channelId), 1000 * 2 ** this.tries);
  }

  private async start(channelId: string): Promise<void> {
    this.teardown();
    const ms = mediaSourceClass();
    if (!ms) {
      this.onState({ kind: "unsupported" });
      return;
    }
    const abort = new AbortController();
    this.abort = abort;
    this.onState({ kind: "connecting" });
    let res: Response;
    try {
      res = await api.rawRequest(liveUrl(channelId), { signal: abort.signal });
    } catch (e) {
      if (abort.signal.aborted) return;
      if (e instanceof ApiError && e.code === "iptv_busy") {
        const channels = (e.params?.channels as string[] | undefined) ?? [];
        this.onState({ kind: "busy", channels });
        return;
      }
      if (e instanceof ApiError && e.status !== 0) {
        this.onState({ kind: "failed", reason: e.message });
        return;
      }
      this.retry(channelId);
      return;
    }
    const codecs = res.headers.get("X-Codecs") || "avc1.640028,mp4a.40.2";
    const mime = `video/mp4; codecs="${codecs}"`;
    let supported = false;
    try {
      supported = ms.ctor.isTypeSupported(mime);
    } catch {
      supported = false;
    }
    if (!supported || !res.body) {
      abort.abort();
      this.onState({ kind: "unsupported" });
      return;
    }
    const source = new ms.ctor();
    if (ms.managed) {
      (this.video as HTMLVideoElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true;
    }
    this.objectUrl = URL.createObjectURL(source);
    this.video.src = this.objectUrl;
    await waitEvent(source, "sourceopen");
    if (abort.signal.aborted) return;
    const buffer = source.addSourceBuffer(mime);
    const reader = res.body.getReader();
    let started = false;
    let lastTrim = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done || abort.signal.aborted) break;
        if (!value?.length) continue;
        await this.append(buffer, value);
        if (abort.signal.aborted) break;
        const ranges = buffer.buffered;
        if (!started && ranges.length > 0) {
          started = true;
          this.tries = 0;
          this.video.currentTime = ranges.start(0);
          void this.video.play().catch(() => {});
          this.onState({ kind: "playing" });
        }
        if (ranges.length > 0) {
          const end = ranges.end(ranges.length - 1);
          if (end - this.video.currentTime > LAG_S && !this.video.paused) {
            this.video.currentTime = end - EDGE_S;
          }
          const now = this.video.currentTime;
          if (now - lastTrim > 10 && now - KEEP_S > ranges.start(0)) {
            lastTrim = now;
            await this.remove(buffer, ranges.start(0), now - KEEP_S);
          }
        }
      }
    } catch {
      // de verbinding viel weg of de buffer weigerde: opnieuw verbinden
    }
    if (abort.signal.aborted || this.abort !== abort) return;
    this.retry(channelId);
  }

  private async append(buffer: SourceBuffer, data: Uint8Array): Promise<void> {
    if (buffer.updating) await waitEvent(buffer, "updateend");
    try {
      buffer.appendBuffer(data as BufferSource);
    } catch (e) {
      if (e instanceof DOMException && e.name === "QuotaExceededError" && buffer.buffered.length > 0) {
        await this.remove(buffer, buffer.buffered.start(0), Math.max(0, this.video.currentTime - 5));
        if (buffer.updating) await waitEvent(buffer, "updateend");
        buffer.appendBuffer(data as BufferSource);
      } else {
        throw e;
      }
    }
    await waitEvent(buffer, "updateend");
  }

  private async remove(buffer: SourceBuffer, from: number, to: number): Promise<void> {
    if (to <= from) return;
    if (buffer.updating) await waitEvent(buffer, "updateend");
    buffer.remove(from, to);
    await waitEvent(buffer, "updateend");
  }
}
