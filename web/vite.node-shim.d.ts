// De paar Node-functies die vite.config.ts gebruikt, getypeerd zonder @types/node (geen nieuwe
// afhankelijkheden, B19). vite.config.ts haalt dit binnen met een triple-slash-verwijzing.

declare const process: { env: Record<string, string | undefined> };

declare module "node:crypto" {
  interface Hash {
    update(data: string | Uint8Array): Hash;
    digest(encoding: "hex"): string;
  }
  export function createHash(algorithm: string): Hash;
}

declare module "node:fs" {
  interface Stats {
    isDirectory(): boolean;
    isFile(): boolean;
    size: number;
  }
  export function existsSync(path: string): boolean;
  export function readdirSync(path: string): string[];
  export function readFileSync(path: string): Uint8Array;
  export function readFileSync(path: string, encoding: "utf8"): string;
  export function writeFileSync(path: string, data: string | Uint8Array): void;
  export function statSync(path: string): Stats;
  export function mkdirSync(path: string, options?: { recursive?: boolean }): void;
  export function rmSync(path: string, options?: { recursive?: boolean; force?: boolean }): void;
}

declare module "node:path" {
  export function dirname(path: string): string;
  export function join(...parts: string[]): string;
  export function resolve(...parts: string[]): string;
  export function relative(from: string, to: string): string;
}

declare module "node:zlib" {
  export const constants: { BROTLI_PARAM_QUALITY: number; BROTLI_PARAM_SIZE_HINT: number };
  export function brotliCompressSync(data: Uint8Array, options?: { params?: Record<number, number> }): Uint8Array;
  export function gzipSync(data: Uint8Array, options?: { level?: number }): Uint8Array;
}
