// Upscaling in de speler (fase 11, stap 5a): het beeld van de <video> gaat via WebGL2 naar een
// <canvas> erboven, op de echte pixels van het scherm. Twee stappen, eigen shaders:
//
// 1. Vergroten met een Catmull-Rom-filter (bicubisch, 4×4 pixels): scherper dan het bilineaire
//    vergroten van de browser, zonder de ringen van lanczos-3.
// 2. Verscherpen op het contrast (zoals CAS): per pixel kijkt de shader naar de buren; waar het
//    contrast al hoog is, verscherpt hij minder (geen halo's rond letters en randen).
//
// Het werkt op het toestel van de kijker: geen server, geen GPU in het cluster. Alleen als het
// beeld echt groter getoond wordt dan het is (`worth`), met WebGL2, en het stopt vanzelf als
// WebGL faalt (dan toont de <video> zich weer). De ondertitels toont de speler dan zelf boven
// het canvas (Player.tsx), want het canvas ligt over de <video>.

/** Vanaf zoveel keer groter heeft upscaling zin. */
export const MIN_SCALE = 1.15;
/** Hooguit zoveel pixels op het canvas (4K). */
export const MAX_PIXELS = 3840 * 2160;
/** 0 = niet verscherpen, 1 = het meest. */
export const SHARPNESS = 0.6;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Waar het beeld komt in een vak van w × h (object-fit: contain), in hele pixels. */
export function containRect(videoW: number, videoH: number, w: number, h: number): Rect {
  if (videoW <= 0 || videoH <= 0 || w <= 0 || h <= 0) return { x: 0, y: 0, w: 0, h: 0 };
  const scale = Math.min(w / videoW, h / videoH);
  const rw = Math.round(videoW * scale);
  const rh = Math.round(videoH * scale);
  return { x: Math.floor((w - rw) / 2), y: Math.floor((h - rh) / 2), w: rw, h: rh };
}

/** De grootte van het canvas in pixels: de CSS-grootte maal devicePixelRatio, hooguit MAX_PIXELS. */
export function canvasSize(cssW: number, cssH: number, dpr: number): { w: number; h: number } {
  let w = Math.max(1, Math.round(cssW * dpr));
  let h = Math.max(1, Math.round(cssH * dpr));
  const over = (w * h) / MAX_PIXELS;
  if (over > 1) {
    const f = Math.sqrt(over);
    w = Math.floor(w / f);
    h = Math.floor(h / f);
  }
  return { w, h };
}

/** Heeft upscaling zin? Het beeld wordt minstens MIN_SCALE keer groter getoond. */
export function worth(videoW: number, videoH: number, w: number, h: number): boolean {
  const r = containRect(videoW, videoH, w, h);
  return videoW > 0 && r.w / videoW >= MIN_SCALE;
}

const VERTEX = `#version 300 es
in vec2 pos;
out vec2 uv;
void main() {
  uv = pos * 0.5 + 0.5;
  gl_Position = vec4(pos, 0.0, 1.0);
}`;

// Catmull-Rom in twee richtingen, 16 texelFetch's (geen afhankelijkheid van het filter)
const UPSCALE = `#version 300 es
precision highp float;
uniform sampler2D src;
uniform vec2 srcSize;
in vec2 uv;
out vec4 color;
vec4 cr(float t) {
  float t2 = t * t, t3 = t2 * t;
  return vec4(
    -0.5 * t3 + t2 - 0.5 * t,
    1.5 * t3 - 2.5 * t2 + 1.0,
    -1.5 * t3 + 2.0 * t2 + 0.5 * t,
    0.5 * t3 - 0.5 * t2);
}
vec3 at(ivec2 p) {
  return texelFetch(src, clamp(p, ivec2(0), ivec2(srcSize) - 1), 0).rgb;
}
void main() {
  vec2 p = vec2(uv.x, 1.0 - uv.y) * srcSize - 0.5;
  vec2 f = fract(p);
  ivec2 b = ivec2(floor(p));
  vec4 wx = cr(f.x), wy = cr(f.y);
  vec3 sum = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    vec3 row = at(b + ivec2(-1, j - 1)) * wx.x + at(b + ivec2(0, j - 1)) * wx.y
             + at(b + ivec2(1, j - 1)) * wx.z + at(b + ivec2(2, j - 1)) * wx.w;
    sum += row * wy[j];
  }
  color = vec4(clamp(sum, 0.0, 1.0), 1.0);
}`;

// Verscherpen naar het contrast van de buren (kruis van vier)
const SHARPEN = `#version 300 es
precision highp float;
uniform sampler2D src;
uniform float sharpness;
uniform vec2 origin;
in vec2 uv;
out vec4 color;
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy - origin);
  ivec2 hi = textureSize(src, 0) - 1;
  vec3 c = texelFetch(src, p, 0).rgb;
  vec3 n = texelFetch(src, clamp(p + ivec2(0, 1), ivec2(0), hi), 0).rgb;
  vec3 s = texelFetch(src, clamp(p - ivec2(0, 1), ivec2(0), hi), 0).rgb;
  vec3 e = texelFetch(src, clamp(p + ivec2(1, 0), ivec2(0), hi), 0).rgb;
  vec3 w = texelFetch(src, clamp(p - ivec2(1, 0), ivec2(0), hi), 0).rgb;
  vec3 mn = min(c, min(min(n, s), min(e, w)));
  vec3 mx = max(c, max(max(n, s), max(e, w)));
  vec3 amp = sqrt(clamp(min(mn, 1.0 - mx) / max(mx, 1e-4), 0.0, 1.0));
  vec3 wgt = -amp / mix(8.0, 5.0, sharpness);
  vec3 outc = (c + (n + s + e + w) * wgt) / (1.0 + 4.0 * wgt);
  color = vec4(clamp(outc, 0.0, 1.0), 1.0);
}`;

function compile(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram {
  const prog = gl.createProgram();
  for (const [type, text] of [
    [gl.VERTEX_SHADER, vs],
    [gl.FRAGMENT_SHADER, fs],
  ] as const) {
    const sh = gl.createShader(type);
    if (!sh) throw new Error("shader");
    gl.shaderSource(sh, text);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) ?? "shader");
    gl.attachShader(prog, sh);
  }
  gl.bindAttribLocation(prog, 0, "pos");
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) ?? "link");
  return prog;
}

function texture(gl: WebGL2RenderingContext): WebGLTexture {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return tex;
}

/** Eén upscaler op een canvas. draw() per beeld; false als WebGL het opgaf. */
export class Upscaler {
  private gl: WebGL2RenderingContext;
  private up: WebGLProgram;
  private sharp: WebGLProgram;
  private srcTex: WebGLTexture;
  private midTex: WebGLTexture;
  private fbo: WebGLFramebuffer;
  private mid = { w: 0, h: 0 };
  lost = false;

  private constructor(gl: WebGL2RenderingContext) {
    this.gl = gl;
    this.up = compile(gl, VERTEX, UPSCALE);
    this.sharp = compile(gl, VERTEX, SHARPEN);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.srcTex = texture(gl);
    this.midTex = texture(gl);
    const fbo = gl.createFramebuffer();
    if (!fbo) throw new Error("framebuffer");
    this.fbo = fbo;
    gl.canvas.addEventListener?.("webglcontextlost", () => {
      this.lost = true;
    });
  }

  /** Een upscaler, of null zonder WebGL2 (of als de shaders niet willen). */
  static create(canvas: HTMLCanvasElement): Upscaler | null {
    try {
      const gl = canvas.getContext("webgl2", {
        alpha: false,
        antialias: false,
        depth: false,
        powerPreference: "high-performance",
        preserveDrawingBuffer: false,
      });
      return gl ? new Upscaler(gl) : null;
    } catch {
      return null;
    }
  }

  /** Het huidige beeld van de video, vergroot en verscherpt, in rect op het canvas. */
  draw(video: HTMLVideoElement, rect: Rect, sharpness = SHARPNESS): boolean {
    const gl = this.gl;
    if (this.lost || gl.isContextLost()) return false;
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!vw || !vh || rect.w <= 0 || rect.h <= 0) return true;
    try {
      gl.bindTexture(gl.TEXTURE_2D, this.srcTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      // Stap 1 naar de tussentextuur van rect.w × rect.h
      if (this.mid.w !== rect.w || this.mid.h !== rect.h) {
        gl.bindTexture(gl.TEXTURE_2D, this.midTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, rect.w, rect.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.midTex, 0);
        this.mid = { w: rect.w, h: rect.h };
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
      gl.viewport(0, 0, rect.w, rect.h);
      gl.useProgram(this.up);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.srcTex);
      gl.uniform1i(gl.getUniformLocation(this.up, "src"), 0);
      gl.uniform2f(gl.getUniformLocation(this.up, "srcSize"), vw, vh);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      // Stap 2 naar het canvas (zwart eromheen)
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      // WebGL telt y van onder: het vak van boven gemeten omrekenen
      const bottom = gl.drawingBufferHeight - rect.y - rect.h;
      gl.viewport(rect.x, bottom, rect.w, rect.h);
      gl.useProgram(this.sharp);
      gl.uniform2f(gl.getUniformLocation(this.sharp, "origin"), rect.x, bottom);
      gl.bindTexture(gl.TEXTURE_2D, this.midTex);
      gl.uniform1i(gl.getUniformLocation(this.sharp, "src"), 0);
      gl.uniform1f(gl.getUniformLocation(this.sharp, "sharpness"), sharpness);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return gl.getError() === gl.NO_ERROR;
    } catch {
      return false;
    }
  }

  dispose(): void {
    const gl = this.gl;
    gl.deleteTexture(this.srcTex);
    gl.deleteTexture(this.midTex);
    gl.deleteFramebuffer(this.fbo);
    gl.deleteProgram(this.up);
    gl.deleteProgram(this.sharp);
  }
}
