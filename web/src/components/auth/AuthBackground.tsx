"use client";

import { useEffect, useRef } from "react";

/**
 * The moving part of the auth background: two glowing curves that drift across
 * each other in a continuous loop.
 *
 * Everything static — the blue gradient, the mesh grid, the corner glows — is
 * CSS (see `.auth-mesh` in globals.css). Only what has to move is on a canvas,
 * because a canvas is the one thing here that costs CPU every frame.
 *
 * What keeps that cost near zero
 * ------------------------------
 *   * 30fps, not 60. The curves move slowly enough that the difference is
 *     invisible and the frame budget halves.
 *   * Device pixel ratio capped at 1.5. A 3x retina buffer quadruples the fill
 *     cost of the glow passes to draw a blur nobody can resolve.
 *   * ~64 sample points per curve regardless of width, so a 4K monitor does the
 *     same work as a laptop.
 *   * The loop STOPS when the tab is hidden. requestAnimationFrame already
 *     throttles in a background tab, but "stops" is cheaper than "throttled",
 *     and a login page left open in a tab is the normal case.
 *   * prefers-reduced-motion draws exactly one frame and never starts a loop —
 *     the design still reads, it simply holds still.
 */

/** Glow layers, widest and faintest first. Drawn additively, so the overlap of
 *  the two curves brightens on its own rather than needing a third pass. */
const LAYERS = [
  { width: 22, alpha: 0.05 },
  { width: 9, alpha: 0.1 },
  { width: 1.8, alpha: 0.85 },
];

interface Wave {
  /** Vertical centre, as a fraction of height. */
  y: number;
  /** Peak deviation, as a fraction of height. */
  amp: number;
  /** Full sine cycles across the viewport, for each of the two components. */
  freq: [number, number];
  /** Radians per second, per component. Different signs make the two curves
   *  cross rather than travel together. */
  speed: [number, number];
  /** left → middle → right stops of the stroke gradient. */
  stops: [string, string, string];
}

const WAVES: Wave[] = [
  {
    y: 0.44,
    amp: 0.1,
    freq: [1.15, 2.3],
    speed: [0.26, -0.17],
    stops: ["#38bdf8", "#e0f2fe", "#818cf8"],
  },
  {
    y: 0.58,
    amp: 0.13,
    freq: [0.85, 1.9],
    speed: [-0.19, 0.23],
    stops: ["#6366f1", "#a5f3fc", "#22d3ee"],
  },
];

const SAMPLES = 64;
const MAX_DPR = 1.5;
const FRAME_MS = 1000 / 30;

export function AuthBackground() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    // CSS pixels. The context is scaled by the DPR so everything below can be
    // written in the units the layout is in.
    let width = 0;
    let height = 0;
    let frame = 0;
    let last = 0;

    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function resize() {
      const rect = canvas!.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      width = rect.width;
      height = rect.height;
      canvas!.width = Math.max(1, Math.round(width * dpr));
      canvas!.height = Math.max(1, Math.round(height * dpr));
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function drawWave(wave: Wave, seconds: number) {
      const mid = height * wave.y;
      // Amplitude is clamped so a short viewport (a phone in landscape) does
      // not flatten the curves into two straight lines.
      const amp = Math.max(24, height * wave.amp);

      ctx!.beginPath();
      for (let i = 0; i <= SAMPLES; i++) {
        const t = i / SAMPLES;
        const x = t * width;
        const y =
          mid +
          Math.sin(t * Math.PI * 2 * wave.freq[0] + seconds * wave.speed[0]) *
            amp +
          // Half-amplitude second harmonic: this is what stops the curve
          // reading as a plain sine wave and gives it the loose, drifting
          // shape the design is after.
          Math.sin(t * Math.PI * 2 * wave.freq[1] + seconds * wave.speed[1]) *
            amp *
            0.45;
        if (i === 0) ctx!.moveTo(x, y);
        else ctx!.lineTo(x, y);
      }

      const gradient = ctx!.createLinearGradient(0, 0, width, 0);
      gradient.addColorStop(0, wave.stops[0]);
      gradient.addColorStop(0.5, wave.stops[1]);
      gradient.addColorStop(1, wave.stops[2]);

      ctx!.strokeStyle = gradient;
      ctx!.lineCap = "round";
      ctx!.lineJoin = "round";
      for (const layer of LAYERS) {
        ctx!.globalAlpha = layer.alpha;
        ctx!.lineWidth = layer.width;
        ctx!.stroke();
      }
      ctx!.globalAlpha = 1;
    }

    function render(seconds: number) {
      ctx!.clearRect(0, 0, width, height);
      // Additive: where the two curves cross they add up to a brighter core,
      // which is the whole point of having two of them.
      ctx!.globalCompositeOperation = "lighter";
      for (const wave of WAVES) drawWave(wave, seconds);
      ctx!.globalCompositeOperation = "source-over";
    }

    function loop(now: number) {
      frame = requestAnimationFrame(loop);
      if (now - last < FRAME_MS) return;
      last = now;
      render(now / 1000);
    }

    function start() {
      if (reduced || frame) return;
      last = 0;
      frame = requestAnimationFrame(loop);
    }

    function stop() {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    }

    function onVisibility() {
      if (document.hidden) stop();
      else start();
    }

    resize();
    // One frame either way, so a reduced-motion visitor still gets the design
    // rather than an empty rectangle.
    render(0);
    start();

    // ResizeObserver rather than window.resize: the canvas is stretched to its
    // parent, and on mobile the parent changes height when the URL bar hides
    // without the window ever firing a resize.
    const observer = new ResizeObserver(() => {
      resize();
      render(performance.now() / 1000);
    });
    observer.observe(canvas);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}
