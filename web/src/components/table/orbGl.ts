// WebGL renderer for the Crumb Orb (raw WebGL 1, no dependencies).
//
// An animated orb gets its own context that draws straight into its visible canvas (no per-frame
// pixel copies). Still orbs (thumbnails, reduced motion) share one hidden context that draws each
// frame once and copies it into the orb's own 2D canvas, so any number of them stays cheap: browsers
// cap live GL contexts at a handful. The shared context is released shortly after its last user.
import { hexToRgb, PALETTES, type OrbLook, type Rgb } from "./orbLook";

const VERTEX = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

// Domain-warped value-noise clouds on a shaded sphere, a soft limb, film grain and a white tip.
const FRAGMENT = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 vUv;
uniform float uPx;
uniform float uFlow;
uniform float uScale;
uniform float uGrainSeed;
uniform vec3 uDeep;
uniform vec3 uMain;
uniform vec3 uSoft;
uniform vec3 uLight;
uniform vec3 uRim;
uniform float uWarp;
uniform float uGlint;
uniform float uSat;
uniform float uBright;
uniform float uGrain;
uniform float uRipple;
uniform float uSeed;

// Hash without sine (Dave Hoskins): even, uncorrelated values for both the noise lattice and the grain.
float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0); // quintic: no creases under strong contrast
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 4; i++) {
    v += a * noise(p);
    p = m * p;
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float R = 0.9 * uScale;
  vec2 q = p / R;
  float r = length(q);
  float px = 2.0 / (uPx * R);
  float alpha = 1.0 - smoothstep(1.0 - 1.6 * px, 1.0 + 0.4 * px, r);
  if (alpha <= 0.0) {
    gl_FragColor = vec4(0.0);
    return;
  }
  float z = sqrt(max(0.0, 1.0 - r * r));
  vec3 n = vec3(q, z);

  // Wrap the cloud field around the sphere so it bends toward the limb. Low frequencies and a gentle
  // warp keep the clouds big and soft (painterly), not veined like marble.
  vec2 s = q / (0.6 + 0.4 * z);
  vec2 uv = s * 0.72 + vec2(uSeed, uSeed * 0.37);
  float t = uFlow;
  vec2 w1 = vec2(fbm(uv + vec2(0.0, t * 0.08)), fbm(uv + vec2(5.2, 1.3) - vec2(t * 0.07, 0.0)));
  vec2 w2 = vec2(
    fbm(uv + uWarp * 1.25 * w1 + vec2(1.7, 9.2) + t * 0.04),
    fbm(uv + uWarp * 1.25 * w1 + vec2(8.3, 2.8) - t * 0.035)
  );
  float f = fbm(uv * 0.9 + uWarp * 1.35 * w2);

  // Light pools top-left, the deep tone settles bottom-right, soft clouds drift through.
  float g = clamp(0.5 - 0.46 * q.y + 0.22 * q.x, 0.0, 1.0);
  vec3 col = mix(uLight, uMain, smoothstep(0.02, 0.62, g + (f - 0.5) * 1.2));
  col = mix(col, uDeep, smoothstep(0.55, 1.1, g + (w2.x - 0.5) * 1.1) * 0.85);
  col = mix(col, uSoft, smoothstep(0.42, 0.78, w2.y) * 0.6);
  col = mix(col, uLight, smoothstep(0.58, 0.9, f) * 0.45 * (1.0 - g));

  float luma = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(vec3(luma), col, uSat) * uBright;

  // Gentle 3D shading and a soft limb tinted by the rim color.
  vec3 L = normalize(vec3(-0.55, 0.65, 0.75));
  float diff = clamp(dot(n, L), 0.0, 1.0);
  col *= mix(0.8, 1.05, diff);
  float fres = pow(1.0 - z, 2.4);
  col = mix(col, uRim, fres * 0.5);

  // A crumb landed: a bright wave front travels out from the middle.
  if (uRipple > 0.0) {
    float front = uRipple * 1.3;
    float band = exp(-pow((r - front) / 0.16, 2.0)) * (1.0 - uRipple);
    col = mix(col, vec3(1.0), band * 0.4);
  }

  // The white specular tip near the upper left: white on every palette.
  vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
  float nh = max(dot(n, H), 0.0);
  float spec = (pow(nh, 10.0) * 0.38 + pow(nh, 38.0) * 0.85) * uGlint;
  col = mix(col, vec3(1.0), clamp(spec, 0.0, 1.0));

  // Film grain.
  float grain = hash(gl_FragCoord.xy + vec2(uGrainSeed * 91.7, uGrainSeed * 37.3)) - 0.5;
  col += grain * uGrain;

  col = clamp(col, 0.0, 1.0);
  gl_FragColor = vec4(col * alpha, alpha);
}
`;

const UNIFORMS = [
  "uPx",
  "uFlow",
  "uScale",
  "uGrainSeed",
  "uDeep",
  "uMain",
  "uSoft",
  "uLight",
  "uRim",
  "uWarp",
  "uGlint",
  "uSat",
  "uBright",
  "uGrain",
  "uRipple",
  "uSeed",
] as const;
type UniformName = (typeof UNIFORMS)[number];

/** Everything one frame needs. Colors are linear 0..1 RGB, already interpolated by the caller. */
export interface OrbFrame {
  deep: Rgb;
  main: Rgb;
  soft: Rgb;
  light: Rgb;
  rim: Rgb;
  flow: number;
  scale: number;
  warp: number;
  glint: number;
  saturation: number;
  brightness: number;
  grain: number;
  grainSeed: number;
  ripple: number; // 0 = none, else 0..1 progress
  seed: number;
}

export function paletteRgb(look: OrbLook): Pick<OrbFrame, "deep" | "main" | "soft" | "light" | "rim"> {
  const p = PALETTES[look.palette];
  return { deep: hexToRgb(p.deep), main: hexToRgb(p.main), soft: hexToRgb(p.soft), light: hexToRgb(p.light), rim: hexToRgb(p.rim) };
}

export class OrbRenderer {
  private canvas: HTMLCanvasElement;
  private readonly dedicated: boolean;
  private gl: WebGLRenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private buffer: WebGLBuffer | null = null;
  private loc: Partial<Record<UniformName, WebGLUniformLocation | null>> = {};
  private lost = false;

  /** With `target`, draws straight into that visible canvas; without, into a hidden shared one. */
  constructor(target?: HTMLCanvasElement) {
    this.dedicated = Boolean(target);
    this.canvas = target ?? document.createElement("canvas");
    if (!target) {
      this.canvas.width = 2;
      this.canvas.height = 2;
    }
    this.canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.lost = true;
    });
    this.canvas.addEventListener("webglcontextrestored", () => {
      this.lost = false;
      this.init();
    });
  }

  /** Creates the context and program. False when WebGL is unavailable or the shader fails. */
  init(): boolean {
    const opts: WebGLContextAttributes = {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: "low-power",
    };
    const gl = (this.canvas.getContext("webgl", opts) ??
      this.canvas.getContext("experimental-webgl", opts)) as WebGLRenderingContext | null;
    if (!gl) return false;
    const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    if (!vs || !fs) return false;
    const program = gl.createProgram();
    if (!program) return false;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      gl.deleteProgram(program);
      return false;
    }
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.useProgram(program);
    const aPos = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    this.loc = {};
    for (const name of UNIFORMS) this.loc[name] = gl.getUniformLocation(program, name);
    this.gl = gl;
    this.program = program;
    this.buffer = buffer;
    return true;
  }

  /**
   * Draws one frame at `size` x `size` device pixels: into the visible canvas for a dedicated
   * renderer, else into the shared canvas and then copied into `target`. False if nothing was drawn.
   */
  draw(frame: OrbFrame, size: number, target: CanvasRenderingContext2D | null = null): boolean {
    const gl = this.gl;
    if (!gl || this.lost || gl.isContextLost() || size <= 0) return false;
    if (this.dedicated) {
      if (this.canvas.width !== size || this.canvas.height !== size) {
        this.canvas.width = size;
        this.canvas.height = size;
      }
    } else if (this.canvas.width < size || this.canvas.height < size) {
      this.canvas.width = Math.max(this.canvas.width, size);
      this.canvas.height = Math.max(this.canvas.height, size);
    }
    const u = this.loc;
    gl.viewport(0, 0, size, size);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(u.uPx ?? null, size);
    gl.uniform1f(u.uFlow ?? null, frame.flow);
    gl.uniform1f(u.uScale ?? null, frame.scale);
    gl.uniform1f(u.uGrainSeed ?? null, frame.grainSeed);
    gl.uniform3fv(u.uDeep ?? null, frame.deep);
    gl.uniform3fv(u.uMain ?? null, frame.main);
    gl.uniform3fv(u.uSoft ?? null, frame.soft);
    gl.uniform3fv(u.uLight ?? null, frame.light);
    gl.uniform3fv(u.uRim ?? null, frame.rim);
    gl.uniform1f(u.uWarp ?? null, frame.warp);
    gl.uniform1f(u.uGlint ?? null, frame.glint);
    gl.uniform1f(u.uSat ?? null, frame.saturation);
    gl.uniform1f(u.uBright ?? null, frame.brightness);
    gl.uniform1f(u.uGrain ?? null, frame.grain);
    gl.uniform1f(u.uRipple ?? null, frame.ripple);
    gl.uniform1f(u.uSeed ?? null, frame.seed);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (this.dedicated) return true;
    if (!target) return false;
    // GL's origin is bottom-left, so the frame sits in the bottom-left corner of the shared canvas.
    target.clearRect(0, 0, size, size);
    target.drawImage(this.canvas, 0, this.canvas.height - size, size, size, 0, 0, size, size);
    return true;
  }

  dispose(): void {
    const gl = this.gl;
    if (gl && !gl.isContextLost()) {
      if (this.buffer) gl.deleteBuffer(this.buffer);
      if (this.program) gl.deleteProgram(this.program);
      const lose = gl.getExtension("WEBGL_lose_context");
      if (!this.dedicated) lose?.loseContext();
      else {
        // A canvas keeps its context for life, and React may re-run the effect on the same element
        // (StrictMode, a quick remount). Release it only once the canvas has really left the page.
        const canvas = this.canvas;
        window.setTimeout(() => {
          if (!canvas.isConnected && !gl.isContextLost()) lose?.loseContext();
        }, 0);
      }
    }
    this.gl = null;
    this.program = null;
    this.buffer = null;
    if (!this.dedicated) {
      this.canvas.width = 1;
      this.canvas.height = 1;
    }
  }
}

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

// ---- Shared instance with reference counting ----------------------------------------------

const DISPOSE_DELAY_MS = 2_000; // page transitions remount orbs; do not rebuild the context each time

let shared: OrbRenderer | null = null;
let users = 0;
let disposeTimer: number | null = null;
let unsupported = false;

/** Borrow the shared renderer. Null when WebGL is unavailable (the caller shows the CSS orb). */
export function acquireOrbRenderer(): OrbRenderer | null {
  if (unsupported || typeof document === "undefined") return null;
  if (disposeTimer !== null) {
    window.clearTimeout(disposeTimer);
    disposeTimer = null;
  }
  if (!shared) {
    const renderer = new OrbRenderer();
    if (!renderer.init()) {
      renderer.dispose();
      unsupported = true;
      return null;
    }
    shared = renderer;
  }
  users += 1;
  return shared;
}

/** A renderer that owns `canvas` (an animated orb). Null when WebGL is unavailable there. */
export function createDedicatedOrbRenderer(canvas: HTMLCanvasElement): OrbRenderer | null {
  if (unsupported) return null;
  const renderer = new OrbRenderer(canvas);
  if (renderer.init()) return renderer;
  renderer.dispose();
  return null;
}

export function releaseOrbRenderer(): void {
  users = Math.max(0, users - 1);
  if (users > 0 || !shared || disposeTimer !== null) return;
  disposeTimer = window.setTimeout(() => {
    disposeTimer = null;
    if (users === 0 && shared) {
      shared.dispose();
      shared = null;
    }
  }, DISPOSE_DELAY_MS);
}
