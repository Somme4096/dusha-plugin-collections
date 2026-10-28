// A field of square bits in 3D: one instanced cube per cell, its height a value from 0 to 1, quantized into
// stacked steps so it reads as bits rather than bars. Values ease toward their targets, a pulse can sweep
// across the columns, and the colors follow the page's theme tokens.
import { lookAt, multiply, perspective } from "./mat4";

export type FieldOptions = {
  cols: number;
  rows: number;
  spacing?: number;
  size?: number;
  maxHeight?: number;
  step?: number;
  yaw?: number;
  pitch?: number;
  zoom?: number;
  sway?: number;
  ease?: number;
  /** Moves the camera's focus toward the front row, as a fraction of the field's depth. */
  front?: number;
  /** Fits the camera so the field (plus `margin` rows in front, for labels) spans this much of the frame. */
  fill?: number;
  margin?: number;
};

const VERTEX = `#version 300 es
precision highp float;
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_normal;
layout(location=2) in vec2 a_cell;
layout(location=3) in float a_value;
layout(location=4) in float a_tone;
uniform mat4 u_matrix;
uniform mat4 u_view;
uniform vec2 u_grid;
uniform float u_spacing;
uniform float u_size;
uniform float u_maxH;
uniform float u_step;
uniform vec4 u_clear;
out vec3 v_normal;
out float v_y;
out float v_value;
out float v_tone;
out float v_depth;
out float v_col;
void main() {
  float h = max(a_value, 0.0) * u_maxH;
  vec2 base = vec2((a_cell.x - (u_grid.x - 1.0) * 0.5) * u_spacing, (a_cell.y - (u_grid.y - 1.0) * 0.5) * u_spacing);
  // Flatten columns whose top would land inside the clear rectangle (in NDC), so text there stays legible.
  if (u_clear.z > u_clear.x) {
    vec4 top = u_matrix * vec4(base.x, h, base.y, 1.0);
    vec2 ndc = top.xy / top.w;
    vec2 outside = max(vec2(u_clear.x, u_clear.y) - ndc, ndc - vec2(u_clear.z, u_clear.w));
    float d = max(outside.x, outside.y);
    h *= mix(0.04, 1.0, smoothstep(0.0, 0.18, d));
  }
  h = max(u_step * 0.35, ceil(h / u_step - 0.001) * u_step);
  vec3 world = vec3(base.x + a_pos.x * u_size, a_pos.y * h, base.y + a_pos.z * u_size);
  vec4 view = u_view * vec4(world, 1.0);
  v_depth = -view.z;
  v_normal = a_normal;
  v_y = world.y;
  v_value = a_value;
  v_tone = a_tone;
  v_col = a_cell.x;
  gl_Position = u_matrix * vec4(world, 1.0);
}`;

const FRAGMENT = `#version 300 es
precision highp float;
in vec3 v_normal;
in float v_y;
in float v_value;
in float v_tone;
in float v_depth;
in float v_col;
uniform vec3 u_low;
uniform vec3 u_high;
uniform vec3 u_hot;
uniform vec3 u_bg;
uniform float u_step;
uniform vec2 u_pulse;
uniform vec2 u_fog;
uniform float u_dark;
out vec4 color;
void main() {
  float v = clamp(v_value, 0.0, 1.0);
  vec3 base = mix(u_low, u_high, smoothstep(0.02, 0.95, v));
  base = mix(base, u_hot, clamp(v_tone, 0.0, 1.0) * 0.9);
  float glow = exp(-pow(v_col - u_pulse.x, 2.0) / 6.0) * u_pulse.y;
  base = mix(base, u_high, glow * 0.75);
  vec3 light = normalize(vec3(0.45, 1.0, 0.65));
  float shade = 0.6 + 0.4 * max(dot(v_normal, light), 0.0);
  if (v_normal.y > 0.5) shade = 1.0;
  if (abs(v_normal.y) < 0.5) {
    float f = v_y / u_step;
    float w = fwidth(f);
    float groove = 1.0 - smoothstep(0.0, max(w * 1.2, 0.05), fract(f));
    groove *= 1.0 - smoothstep(0.25, 0.6, w);
    shade *= 1.0 - groove * mix(0.22, 0.4, u_dark);
  }
  vec3 lit = base * shade;
  float fog = smoothstep(u_fog.x, u_fog.y, v_depth);
  color = vec4(mix(lit, u_bg, fog * 0.85), 1.0);
}`;

function cube(): Float32Array {
  // 6 faces x 2 triangles x 3 vertices, each vertex position then normal. y runs 0..1, x and z -0.5..0.5.
  const faces: [number[], number[][]][] = [
    [[0, 1, 0], [[-0.5, 1, -0.5], [-0.5, 1, 0.5], [0.5, 1, 0.5], [0.5, 1, -0.5]]],
    [[0, 0, 1], [[-0.5, 0, 0.5], [0.5, 0, 0.5], [0.5, 1, 0.5], [-0.5, 1, 0.5]]],
    [[0, 0, -1], [[0.5, 0, -0.5], [-0.5, 0, -0.5], [-0.5, 1, -0.5], [0.5, 1, -0.5]]],
    [[1, 0, 0], [[0.5, 0, 0.5], [0.5, 0, -0.5], [0.5, 1, -0.5], [0.5, 1, 0.5]]],
    [[-1, 0, 0], [[-0.5, 0, -0.5], [-0.5, 0, 0.5], [-0.5, 1, 0.5], [-0.5, 1, -0.5]]],
  ];
  const out: number[] = [];
  for (const [normal, [a, b, c, d]] of faces) {
    for (const vertex of [a, b, c, a, c, d]) out.push(...vertex!, ...normal);
  }
  return new Float32Array(out);
}

function hex(value: string): [number, number, number] {
  const text = value.trim().replace("#", "");
  const full = text.length === 3 ? text.replace(/./g, "$&$&") : text.slice(0, 6);
  const number = Number.parseInt(full, 16);
  if (Number.isNaN(number)) return [0.5, 0.5, 0.5];
  return [((number >> 16) & 255) / 255, ((number >> 8) & 255) / 255, (number & 255) / 255];
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? "shader");
  return shader;
}

const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export class BitField {
  readonly cols: number;
  readonly rows: number;
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};
  private valueBuffer: WebGLBuffer;
  private toneBuffer: WebGLBuffer;
  private current: Float32Array;
  private target: Float32Array;
  private tones: Float32Array;
  private toneTarget: Float32Array;
  private options: Required<FieldOptions>;
  private colors = { low: hex("#ccc"), high: hex("#2747ff"), hot: hex("#e8462a"), bg: hex("#fff"), dark: 0 };
  private frame = 0;
  private visible = true;
  private started = performance.now();
  private last = performance.now();
  private pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  private wave: { from: number; to: number; start: number; duration: number } | null = null;
  private cleanup: (() => void)[] = [];
  private matrix: Float32Array = new Float32Array(16);
  private frameListeners = new Set<() => void>();
  private fitScale = 1;
  private shift = { x: 0, y: 0 };
  private clearRect: [number, number, number, number] = [0, 0, 0, 0];

  constructor(private canvas: HTMLCanvasElement, options: FieldOptions) {
    this.options = {
      spacing: 1,
      size: 0.8,
      maxHeight: 4,
      step: 0.5,
      yaw: 0.6,
      pitch: 0.62,
      zoom: 1,
      sway: 0.08,
      ease: 0.08,
      front: 0,
      fill: 0,
      margin: 0,
      ...options,
    };
    this.cols = options.cols;
    this.rows = options.rows;
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "low-power" });
    if (!gl) throw new Error("WebGL2 is unavailable");
    this.gl = gl;

    const program = gl.createProgram()!;
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "link");
    this.program = program;
    for (const name of [
      "u_matrix", "u_view", "u_grid", "u_spacing", "u_size", "u_maxH", "u_step", "u_low", "u_high", "u_hot",
      "u_bg", "u_pulse", "u_fog", "u_dark", "u_clear",
    ]) {
      this.uniforms[name] = gl.getUniformLocation(program, name);
    }

    const count = this.cols * this.rows;
    this.current = new Float32Array(count);
    this.target = new Float32Array(count);
    this.tones = new Float32Array(count);
    this.toneTarget = new Float32Array(count);

    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const geometry = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, geometry);
    gl.bufferData(gl.ARRAY_BUFFER, cube(), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);

    const cells = new Float32Array(count * 2);
    for (let row = 0; row < this.rows; row++) {
      for (let col = 0; col < this.cols; col++) {
        cells.set([col, row], (row * this.cols + col) * 2);
      }
    }
    const cellBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cellBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, cells, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(2);
    gl.vertexAttribPointer(2, 2, gl.FLOAT, false, 0, 0);
    gl.vertexAttribDivisor(2, 1);

    this.valueBuffer = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.valueBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, this.current, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(3);
    gl.vertexAttribPointer(3, 1, gl.FLOAT, false, 0, 0);
    gl.vertexAttribDivisor(3, 1);

    this.toneBuffer = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.toneBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, this.tones, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(4);
    gl.vertexAttribPointer(4, 1, gl.FLOAT, false, 0, 0);
    gl.vertexAttribDivisor(4, 1);

    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.CULL_FACE);

    this.readTheme();
    const themeWatch = new MutationObserver(() => this.readTheme());
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const seen = new IntersectionObserver(([entry]) => {
      this.visible = Boolean(entry?.isIntersecting);
      if (this.visible) this.loop();
    });
    seen.observe(canvas);
    const move = (event: PointerEvent) => {
      const box = canvas.getBoundingClientRect();
      this.pointer.tx = ((event.clientX - box.left) / box.width - 0.5) * 2;
      this.pointer.ty = ((event.clientY - box.top) / box.height - 0.5) * 2;
    };
    const leave = () => {
      this.pointer.tx = 0;
      this.pointer.ty = 0;
    };
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerleave", leave);
    const lost = (event: Event) => {
      event.preventDefault();
      cancelAnimationFrame(this.frame);
    };
    canvas.addEventListener("webglcontextlost", lost);
    this.cleanup.push(
      () => themeWatch.disconnect(),
      () => seen.disconnect(),
      () => canvas.removeEventListener("pointermove", move),
      () => canvas.removeEventListener("pointerleave", leave),
      () => canvas.removeEventListener("webglcontextlost", lost),
    );
    this.loop();
  }

  readTheme(): void {
    const style = getComputedStyle(document.documentElement);
    this.colors = {
      low: hex(style.getPropertyValue("--bit-low")),
      high: hex(style.getPropertyValue("--bit-high")),
      hot: hex(style.getPropertyValue("--bit-hot")),
      bg: hex(style.getPropertyValue("--bg")),
      dark: document.documentElement.dataset.theme === "dark" ? 1 : 0,
    };
  }

  /** Sets every cell's target, row-major from the back row to the front. */
  set(values: ArrayLike<number>, tones?: ArrayLike<number>): void {
    for (let i = 0; i < this.target.length; i++) {
      this.target[i] = values[i] ?? 0;
      this.toneTarget[i] = tones?.[i] ?? 0;
    }
  }

  /** Scrolls history one row back and grows a new front row from the floor. */
  push(row: ArrayLike<number>, tones?: ArrayLike<number>): void {
    const { cols } = this;
    for (const array of [this.current, this.target, this.tones, this.toneTarget]) {
      array.copyWithin(0, cols);
    }
    const front = (this.rows - 1) * cols;
    for (let col = 0; col < cols; col++) {
      this.current[front + col] = 0;
      this.target[front + col] = row[col] ?? 0;
      this.tones[front + col] = tones?.[col] ?? 0;
      this.toneTarget[front + col] = tones?.[col] ?? 0;
    }
  }

  /** Sweeps a highlight across the columns, from one column to another. */
  sweep(from = 0, to = this.cols - 1, duration = 1400): void {
    this.wave = { from, to, start: performance.now(), duration };
  }

  private resize(): boolean {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.round(this.canvas.clientWidth * ratio);
    const height = Math.round(this.canvas.clientHeight * ratio);
    if (width === 0 || height === 0) return false;
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    return true;
  }

  private loop = (): void => {
    cancelAnimationFrame(this.frame);
    if (!this.visible || document.hidden) {
      if (!document.hidden) return;
      this.frame = requestAnimationFrame(this.loop);
      return;
    }
    this.draw();
    this.frame = requestAnimationFrame(this.loop);
  };

  private draw(): void {
    if (!this.resize()) return;
    const { gl, options } = this;
    const now = performance.now();
    const still = reduced();
    const dt = Math.min(64, now - this.last) / 16.7;
    this.last = now;
    const ease = still ? 1 : 1 - Math.pow(1 - options.ease, dt);
    for (let i = 0; i < this.current.length; i++) {
      this.current[i]! += (this.target[i]! - this.current[i]!) * ease;
      this.tones[i]! += (this.toneTarget[i]! - this.tones[i]!) * ease;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.valueBuffer);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.current);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.toneBuffer);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.tones);

    const time = (now - this.started) / 1000;
    this.pointer.x += (this.pointer.tx - this.pointer.x) * 0.04 * dt;
    this.pointer.y += (this.pointer.ty - this.pointer.y) * 0.04 * dt;
    const sway = still ? 0 : Math.sin(time * 0.11) * options.sway;
    const yaw = options.yaw + sway + this.pointer.x * 0.14;
    const pitch = Math.min(1.35, Math.max(0.2, options.pitch - this.pointer.y * 0.06));

    const width = this.cols * options.spacing;
    const depth = this.rows * options.spacing;
    const aspect = this.canvas.width / this.canvas.height;
    const fov = 0.5;
    const radius = Math.hypot(width, depth, options.maxHeight) / 2;
    const fit = radius / Math.sin(fov / 2) / Math.min(1, aspect * 0.9);
    const distance = (fit / options.zoom) * 0.8 * this.fitScale;
    const eye: [number, number, number] = [
      Math.sin(yaw) * Math.cos(pitch) * distance,
      Math.sin(pitch) * distance,
      Math.cos(yaw) * Math.cos(pitch) * distance,
    ];
    const focus: [number, number, number] = [0, options.maxHeight * 0.18, depth * options.front];
    const view = lookAt([eye[0], eye[1], eye[2] + focus[2]], focus, [0, 1, 0]);
    const raw = multiply(perspective(fov, aspect, 0.1, distance * 4), view);
    if (options.fill) this.fit(raw, still ? 1 : 0.12 * dt);
    // Re-center on screen: shift clip x and y by the fitted offset, times w.
    const matrix = multiply(
      new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, this.shift.x, this.shift.y, 0, 1]),
      raw,
    );
    this.matrix = matrix;

    let pulse: [number, number] = [-100, 0];
    if (this.wave) {
      const t = (now - this.wave.start) / this.wave.duration;
      if (t >= 1.25) this.wave = null;
      else {
        const eased = 1 - Math.pow(1 - Math.min(1, t), 3);
        pulse = [this.wave.from + (this.wave.to - this.wave.from) * eased, Math.min(1, (1.25 - t) * 3)];
      }
    }

    const { low, high, hot, bg, dark } = this.colors;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(bg[0], bg[1], bg[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.program);
    const u = this.uniforms;
    gl.uniformMatrix4fv(u.u_matrix!, false, matrix);
    gl.uniformMatrix4fv(u.u_view!, false, view);
    gl.uniform2f(u.u_grid!, this.cols, this.rows);
    gl.uniform1f(u.u_spacing!, options.spacing);
    gl.uniform1f(u.u_size!, options.size);
    gl.uniform1f(u.u_maxH!, options.maxHeight);
    gl.uniform1f(u.u_step!, options.step);
    gl.uniform3fv(u.u_low!, low);
    gl.uniform3fv(u.u_high!, high);
    gl.uniform3fv(u.u_hot!, hot);
    gl.uniform3fv(u.u_bg!, bg);
    gl.uniform2fv(u.u_pulse!, pulse);
    gl.uniform2f(u.u_fog!, distance * 0.85, distance * 1.6);
    gl.uniform1f(u.u_dark!, dark);
    gl.uniform4fv(u.u_clear!, this.clearRect);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 30, this.cols * this.rows);
    for (const listener of this.frameListeners) listener();
  }

  /** Projects the field's bounding box and eases distance and offset until it fills `options.fill`. */
  private fit(matrix: Float32Array, rate: number): void {
    const { spacing, maxHeight, margin, fill } = this.options;
    const halfW = (this.cols / 2) * spacing;
    const back = -(this.rows / 2) * spacing;
    const front = (this.rows / 2 + margin) * spacing;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const x of [-halfW, halfW]) {
      for (const z of [back, front]) {
        for (const y of [0, maxHeight * 0.7]) {
          const cx = matrix[0]! * x + matrix[4]! * y + matrix[8]! * z + matrix[12]!;
          const cy = matrix[1]! * x + matrix[5]! * y + matrix[9]! * z + matrix[13]!;
          const cw = matrix[3]! * x + matrix[7]! * y + matrix[11]! * z + matrix[15]!;
          const w = Math.max(cw, 0.01);
          minX = Math.min(minX, cx / w);
          maxX = Math.max(maxX, cx / w);
          minY = Math.min(minY, cy / w);
          maxY = Math.max(maxY, cy / w);
        }
      }
    }
    const extent = Math.max((maxX - minX) / 2, (maxY - minY) / 2);
    const k = Math.min(1, rate);
    const scale = Math.min(1.25, Math.max(0.8, Math.pow(extent / fill, 0.5)));
    this.fitScale = Math.min(6, Math.max(0.2, this.fitScale * (1 + (scale - 1) * k)));
    this.shift.x += (-(minX + maxX) / 2 - this.shift.x) * k;
    this.shift.y += (-(minY + maxY) / 2 - this.shift.y) * k;
  }

  /** Keeps bits low under a rectangle of the canvas, in CSS pixels, such as text set over the field. */
  clear(rect: { left: number; top: number; right: number; bottom: number } | null): void {
    if (!rect) {
      this.clearRect = [0, 0, 0, 0];
      return;
    }
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.clearRect = [(rect.left / w) * 2 - 1, 1 - (rect.bottom / h) * 2, (rect.right / w) * 2 - 1, 1 - (rect.top / h) * 2];
  }

  /** Where a cell's floor (or a height above it, 0..1) lands on the canvas, in CSS pixels. */
  project(col: number, row: number, height = 0): { x: number; y: number } {
    const { spacing, maxHeight } = this.options;
    const world = [
      (col - (this.cols - 1) / 2) * spacing,
      height * maxHeight,
      (row - (this.rows - 1) / 2) * spacing,
      1,
    ];
    const m = this.matrix;
    const clip = [0, 1, 2, 3].map((r) => m[r]! * world[0]! + m[4 + r]! * world[1]! + m[8 + r]! * world[2]! + m[12 + r]! * world[3]!);
    const w = clip[3] || 1;
    return {
      x: ((clip[0]! / w + 1) / 2) * this.canvas.clientWidth,
      y: ((1 - clip[1]! / w) / 2) * this.canvas.clientHeight,
    };
  }

  /** Runs after every drawn frame, for anything that follows the camera. */
  onFrame(listener: () => void): () => void {
    this.frameListeners.add(listener);
    return () => this.frameListeners.delete(listener);
  }

  destroy(): void {
    cancelAnimationFrame(this.frame);
    for (const step of this.cleanup) step();
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
  }
}
