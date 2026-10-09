// A quiet, interactive "care network" drawn on a 2D canvas: points (people and their care
// team) joined by faint links, drifting slowly; links near the pointer brighten and the
// nearest points lean gently toward it. Used behind the landing hero and the sign-in panel
// only, never on working pages.
//
// Why a plain canvas (not WebGL, WebGPU or a Spline scene): it needs no library and no
// download, draws a few dozen points cheaply, and degrades to nothing. Budget:
// - at most 46 points (22 on small or touch screens), pixel ratio capped at 1.5;
// - draws only while on screen and the tab is visible (IntersectionObserver +
//   visibilitychange); stops completely otherwise;
// - reduced motion: one still frame, no loop, no pointer response (also when the setting
//   changes while the page is open);
// - colours come from the theme tokens and update when the theme changes.
import { useEffect, useRef } from "react";
import { isTouch, onReducedMotionChange, reducedMotion } from "./motion";

type Node = { x: number; y: number; vx: number; vy: number; r: number };

const LINK = 118;
const POINTER = 150;

function readColours(el: Element) {
  const css = getComputedStyle(el);
  return {
    node: css.getPropertyValue("--ambient-node").trim() || css.getPropertyValue("--primary").trim(),
    link: css.getPropertyValue("--ambient-link").trim() || css.getPropertyValue("--line-strong").trim(),
  };
}

export function CareNetwork({ className, density = 1 }: { className?: string; density?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = canvas?.parentElement;
    if (!canvas || !host) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let still = reducedMotion();
    const touch = isTouch();
    let width = 0;
    let height = 0;
    let nodes: Node[] = [];
    let colours = readColours(canvas);
    const pointer = { x: -9999, y: -9999, active: false };
    let frame = 0;
    let running = false;
    let visible = false;

    const seed = () => {
      const area = width * height;
      const max = touch || width < 640 ? 22 : 46;
      const count = Math.max(10, Math.min(max, Math.round((area / 16000) * density)));
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 0.18,
        r: 1.2 + Math.random() * 1.8,
      }));
    };

    const resize = () => {
      const rect = host.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const dpr = Math.min(1.5, window.devicePixelRatio || 1);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!nodes.length) seed();
      draw();
    };

    const draw = () => {
      ctx.clearRect(0, 0, width, height);
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 > LINK * LINK) continue;
          const near = pointer.active && Math.hypot((a.x + b.x) / 2 - pointer.x, (a.y + b.y) / 2 - pointer.y) < POINTER;
          ctx.globalAlpha = (1 - Math.sqrt(d2) / LINK) * (near ? 0.75 : 0.32);
          ctx.strokeStyle = near ? colours.node : colours.link;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
      ctx.fillStyle = colours.node;
      for (const n of nodes) {
        const near = pointer.active && Math.hypot(n.x - pointer.x, n.y - pointer.y) < POINTER;
        ctx.globalAlpha = near ? 0.95 : 0.55;
        ctx.beginPath();
        ctx.arc(n.x, n.y, near ? n.r + 1 : n.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    const step = () => {
      for (const n of nodes) {
        if (pointer.active) {
          const dx = pointer.x - n.x;
          const dy = pointer.y - n.y;
          const d = Math.hypot(dx, dy);
          if (d < POINTER && d > 1) {
            n.vx += (dx / d) * 0.012;
            n.vy += (dy / d) * 0.012;
          }
        }
        n.vx *= 0.985;
        n.vy *= 0.985;
        // A floor on drift so the network never freezes; a ceiling so it never rushes.
        const speed = Math.hypot(n.vx, n.vy);
        if (speed < 0.05) {
          n.vx += (Math.random() - 0.5) * 0.04;
          n.vy += (Math.random() - 0.5) * 0.04;
        } else if (speed > 0.9) {
          n.vx *= 0.9 / speed;
          n.vy *= 0.9 / speed;
        }
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < -10) n.x = width + 10;
        if (n.x > width + 10) n.x = -10;
        if (n.y < -10) n.y = height + 10;
        if (n.y > height + 10) n.y = -10;
      }
      draw();
      frame = requestAnimationFrame(step);
    };

    const start = () => {
      if (still || running || !visible || document.hidden) return;
      running = true;
      frame = requestAnimationFrame(step);
    };
    const stop = () => {
      running = false;
      cancelAnimationFrame(frame);
    };

    const onMove = (e: PointerEvent) => {
      if (still) return;
      const rect = host.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
      pointer.active = true;
    };
    const onLeave = () => {
      pointer.active = false;
    };
    const onVisibility = () => (document.hidden ? stop() : start());

    resize();
    const ro = new ResizeObserver(() => resize());
    ro.observe(host);
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible) start();
      else stop();
    });
    io.observe(host);
    const themeWatch = new MutationObserver(() => {
      colours = readColours(canvas);
      draw();
    });
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const offMotion = onReducedMotionChange((reduce) => {
      still = reduce;
      pointer.active = false;
      if (reduce) {
        stop();
        draw();
      } else start();
    });
    if (!touch) {
      host.addEventListener("pointermove", onMove);
      host.addEventListener("pointerleave", onLeave);
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      offMotion();
      ro.disconnect();
      io.disconnect();
      themeWatch.disconnect();
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [density]);

  return <canvas ref={canvasRef} aria-hidden className={className ?? "pointer-events-none absolute inset-0"} />;
}
