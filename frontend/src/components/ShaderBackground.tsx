import { useEffect, useRef } from "react";
import * as THREE from "three";

/**
 * Full-viewport WebGL plane running a domain-warped fbm noise shader.
 *
 * The visual: deep violet plasma flowing slowly. The cursor distorts a small
 * region around it, pulling the flow toward (and slightly through) the pointer.
 * On click, a violet shockwave ripples outward briefly.
 *
 * Why a single full-screen quad and not three.js geometry: nothing about this
 * effect needs vertex shading. We render a 2-triangle quad and do all the work
 * in the fragment shader. Cheaper, simpler, no scene graph.
 *
 * Performance: rendered at devicePixelRatio capped at 1.5 (4K screens at full
 * DPR will saturate even a 4060 with this many fbm samples). Pauses when the
 * tab is hidden (visibilitychange listener).
 */

const VERT = /* glsl */ `
  void main() {
    gl_Position = vec4(position, 1.0);
  }
`;

const FRAG = /* glsl */ `
  precision highp float;

  uniform vec2  u_resolution;
  uniform float u_time;
  uniform vec2  u_mouse;       // 0..1, normalized
  uniform float u_mouseStrength;
  uniform float u_clickTime;   // wall-clock seconds of last click (0 if none)

  // hash + value-noise + fbm. Standard. No textures.
  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }
  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
  }
  float fbm(vec2 p) {
    float v = 0.0;
    float amp = 0.5;
    for (int i = 0; i < 5; i++) {
      v += amp * noise(p);
      p *= 2.02;
      amp *= 0.5;
    }
    return v;
  }

  void main() {
    vec2 res = u_resolution;
    vec2 uv = gl_FragCoord.xy / res;
    vec2 p = uv;
    p.x *= res.x / res.y;

    // Domain warp toward the cursor — pulls the flow into the pointer's
    // neighbourhood without affecting the rest of the screen.
    vec2 m = u_mouse;
    m.x *= res.x / res.y;
    vec2 toMouse = p - m;
    float d = length(toMouse);
    float pull = exp(-d * 4.5) * u_mouseStrength;
    p -= normalize(toMouse + 1e-4) * pull * 0.18;

    // Slow time. Two layers of fbm at different speeds = parallax flow.
    float t = u_time * 0.05;
    vec2 q = vec2(
      fbm(p * 1.3 + vec2(t, -t * 0.7)),
      fbm(p * 1.3 + vec2(-t * 0.6, t))
    );
    float n = fbm(p * 2.0 + q * 1.6 + t);

    // Violet ramp. Three colors stops, blended by noise.
    vec3 c0 = vec3(0.039, 0.024, 0.071);  // base   #0A0612
    vec3 c1 = vec3(0.075, 0.012, 0.231);  // deep   #13033b
    vec3 c2 = vec3(0.486, 0.227, 0.929);  // strong #7C3AED
    vec3 col = mix(c0, c1, smoothstep(0.25, 0.55, n));
    col = mix(col, c2, smoothstep(0.62, 0.85, n) * 0.55);

    // Cursor halo — soft violet bloom centered on the pointer.
    float halo = exp(-d * 3.5) * 0.35 * u_mouseStrength;
    col += vec3(0.46, 0.32, 0.95) * halo;

    // Click shockwave — a short-lived ring expanding from the cursor.
    float since = u_time - u_clickTime;
    if (u_clickTime > 0.0 && since < 1.2) {
      float r = since * 1.4;
      float ring = exp(-pow(d - r, 2.0) * 80.0) * exp(-since * 2.0);
      col += vec3(0.65, 0.45, 1.0) * ring * 0.9;
    }

    // Vignette — keeps the eye centered.
    float vig = smoothstep(1.2, 0.4, length(uv - 0.5));
    col *= 0.65 + 0.35 * vig;

    // Slight grain to break up the gradient banding on dark monitors.
    float g = (hash(gl_FragCoord.xy + u_time) - 0.5) * 0.025;
    col += g;

    gl_FragColor = vec4(col, 1.0);
  }
`;

export function ShaderBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      alpha: false,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    const geom = new THREE.PlaneGeometry(2, 2);
    const uniforms = {
      u_resolution: { value: new THREE.Vector2(1, 1) },
      u_time: { value: 0 },
      u_mouse: { value: new THREE.Vector2(0.5, 0.5) },
      u_mouseStrength: { value: 0 },
      u_clickTime: { value: 0 },
    };
    const mat = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms,
    });
    const mesh = new THREE.Mesh(geom, mat);
    scene.add(mesh);

    const resize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      renderer.setSize(w, h, false);
      uniforms.u_resolution.value.set(w, h);
    };
    resize();
    window.addEventListener("resize", resize);

    // Mouse uniforms — also rAF-decoupled so we don't update GL state per move.
    let targetX = 0.5;
    let targetY = 0.5;
    let mouseStrengthTarget = 0;
    const onMove = (e: MouseEvent) => {
      targetX = e.clientX / window.innerWidth;
      targetY = 1 - e.clientY / window.innerHeight;
      mouseStrengthTarget = 1;
    };
    const onLeave = () => {
      mouseStrengthTarget = 0;
    };
    const onClick = (e: MouseEvent) => {
      targetX = e.clientX / window.innerWidth;
      targetY = 1 - e.clientY / window.innerHeight;
      uniforms.u_clickTime.value = performance.now() / 1000;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseleave", onLeave);
    window.addEventListener("click", onClick);

    let raf = 0;
    let running = true;
    const start = performance.now();
    const tick = () => {
      if (!running) return;
      const now = (performance.now() - start) / 1000;
      uniforms.u_time.value = now;
      // ease the cursor toward target so flicks don't snap the flow
      const m = uniforms.u_mouse.value;
      m.x += (targetX - m.x) * 0.08;
      m.y += (targetY - m.y) * 0.08;
      uniforms.u_mouseStrength.value +=
        (mouseStrengthTarget - uniforms.u_mouseStrength.value) * 0.06;
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    tick();

    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        tick();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("click", onClick);
      document.removeEventListener("visibilitychange", onVisibility);
      geom.dispose();
      mat.dispose();
      renderer.dispose();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 -z-10 w-screen h-screen pointer-events-none"
      aria-hidden="true"
    />
  );
}
