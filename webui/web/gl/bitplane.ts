// The bit field seen from above: lanes of square bits that fill the canvas, larger and brighter where taller.
export type PlaneOptions = { lanes: number; rowsPerLane?: number; laneGap?: number; minCell?: number };

type Rgb = [number, number, number];

function hex(value: string): Rgb {
  const text = value.trim().replace("#", "");
  const full = text.length === 3 ? text.replace(/./g, "$&$&") : text.slice(0, 6);
  const number = Number.parseInt(full, 16);
  if (Number.isNaN(number)) return [128, 128, 128];
  return [(number >> 16) & 255, (number >> 8) & 255, number & 255];
}

const mix = (a: Rgb, b: Rgb, t: number): Rgb => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export class BitPlane {
  private ctx: CanvasRenderingContext2D;
  private options: Required<PlaneOptions>;
  private levels: number[];
  private shown: number[];
  private hot: number[];
  private colors = { low: hex("#ddd"), high: hex("#2747ff"), hot: hex("#e8462a"), bg: hex("#fff") };
  private frame = 0;
  private visible = true;
  private started = performance.now();
  private last = 0;
  private wave: { start: number; duration: number } | null = null;
  private layoutListeners = new Set<(centers: number[]) => void>();
  private cleanup: (() => void)[] = [];
  private grid = { cols: 0, cell: 0, x0: 0, y0: 0, centers: [] as number[] };

  constructor(private canvas: HTMLCanvasElement, options: PlaneOptions) {
    this.options = { rowsPerLane: 3, laneGap: 8, minCell: 9, ...options };
    this.levels = new Array(options.lanes).fill(0.1);
    this.shown = new Array(options.lanes).fill(0);
    this.hot = new Array(options.lanes).fill(0);
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("Canvas 2D is unavailable");
    this.ctx = ctx;
    this.readTheme();
    const themeWatch = new MutationObserver(() => this.readTheme());
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const seen = new IntersectionObserver(([entry]) => {
      this.visible = Boolean(entry?.isIntersecting);
      if (this.visible) this.loop();
    });
    seen.observe(canvas);
    this.cleanup.push(() => themeWatch.disconnect(), () => seen.disconnect());
    this.loop();
  }

  readTheme(): void {
    const style = getComputedStyle(document.documentElement);
    this.colors = {
      low: hex(style.getPropertyValue("--bit-low")),
      high: hex(style.getPropertyValue("--bit-high")),
      hot: hex(style.getPropertyValue("--bit-hot")),
      bg: hex(style.getPropertyValue("--bg")),
    };
  }

  /** Each lane's level from 0 to 1, and optionally how hot it runs. */
  set(levels: number[], hot: number[] = []): void {
    for (let i = 0; i < this.options.lanes; i++) {
      this.levels[i] = levels[i] ?? 0;
      this.hot[i] = hot[i] ?? 0;
    }
  }

  sweep(duration = 1800): void {
    this.wave = { start: performance.now(), duration };
  }

  /** Calls back with each lane's vertical center in CSS pixels whenever the layout changes. */
  onLayout(listener: (centers: number[]) => void): () => void {
    this.layoutListeners.add(listener);
    if (this.grid.centers.length) listener(this.grid.centers);
    return () => this.layoutListeners.delete(listener);
  }

  private resize(): boolean {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    if (!width || !height) return false;
    if (this.canvas.width !== Math.round(width * ratio) || this.canvas.height !== Math.round(height * ratio)) {
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
    }
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const { lanes, rowsPerLane, laneGap, minCell } = this.options;
    const cell = Math.max(minCell, Math.floor((height - laneGap * (lanes - 1)) / (lanes * rowsPerLane)));
    const cols = Math.floor(width / cell);
    const used = lanes * rowsPerLane * cell + laneGap * (lanes - 1);
    const grid = { cols, cell, x0: (width - cols * cell) / 2, y0: Math.max(0, (height - used) / 2), centers: [] as number[] };
    for (let lane = 0; lane < lanes; lane++) {
      grid.centers.push(grid.y0 + lane * (rowsPerLane * cell + laneGap) + (rowsPerLane * cell) / 2);
    }
    const moved = grid.cell !== this.grid.cell || grid.cols !== this.grid.cols || grid.y0 !== this.grid.y0;
    this.grid = grid;
    if (moved) for (const listener of this.layoutListeners) listener(grid.centers);
    return true;
  }

  private loop = (): void => {
    cancelAnimationFrame(this.frame);
    if (!this.visible) return;
    const now = performance.now();
    // 30 frames a second is plenty for a slow swell, and halves the work.
    if (now - this.last >= 32) {
      this.draw(now);
      this.last = now;
    }
    this.frame = requestAnimationFrame(this.loop);
  };

  private draw(now: number): void {
    if (!this.resize()) return;
    const { ctx, colors } = this;
    const { lanes, rowsPerLane, laneGap } = this.options;
    const { cols, cell, x0, y0 } = this.grid;
    const still = reduced();
    const t = still ? 0 : (now - this.started) / 1000;
    for (let i = 0; i < lanes; i++) this.shown[i]! += (this.levels[i]! - this.shown[i]!) * (still ? 1 : 0.06);

    let pulseX = -1e9;
    let pulse = 0;
    if (this.wave) {
      const p = (now - this.wave.start) / this.wave.duration;
      if (p >= 1.25) this.wave = null;
      else {
        pulseX = (1 - Math.pow(1 - Math.min(1, p), 3)) * cols;
        pulse = Math.min(1, (1.25 - p) * 3);
      }
    }

    ctx.fillStyle = `rgb(${colors.bg.join(",")})`;
    ctx.fillRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
    for (let lane = 0; lane < lanes; lane++) {
      const level = this.shown[lane]!;
      const speed = 0.35 + (lane % 5) * 0.09;
      const freq = 0.16 + (lane % 7) * 0.025;
      for (let row = 0; row < rowsPerLane; row++) {
        const y = y0 + lane * (rowsPerLane * cell + laneGap) + row * cell;
        const phase = lane * 1.7 + row * 0.55;
        for (let col = 0; col < cols; col++) {
          const swell = 0.5 + 0.5 * Math.sin(col * freq + t * speed + phase);
          const ripple = 0.5 + 0.5 * Math.sin(col * 0.61 - t * 1.3 + lane + row);
          const v = Math.min(1, level * (0.35 + 0.55 * swell + 0.1 * ripple) * 1.6);
          const glow = pulse * Math.exp(-((col - pulseX) ** 2) / 18);
          // Quantize like stacked bits, then let taller bits read larger and brighter from above.
          const q = Math.ceil(v * 8) / 8;
          let rgb = mix(colors.low, colors.high, Math.min(1, q * 1.05));
          if (this.hot[lane]) rgb = mix(rgb, colors.hot, this.hot[lane]! * q);
          if (glow > 0.01) rgb = mix(rgb, colors.high, glow * 0.8);
          const size = cell * (0.5 + 0.38 * q + 0.1 * glow);
          const inset = (cell - size) / 2;
          ctx.fillStyle = `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`;
          ctx.fillRect(x0 + col * cell + inset, y + inset, size, size);
        }
      }
    }
  }

  destroy(): void {
    cancelAnimationFrame(this.frame);
    for (const step of this.cleanup) step();
  }
}
