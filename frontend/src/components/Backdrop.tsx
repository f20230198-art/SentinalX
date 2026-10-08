import { motion, useReducedMotion, useScroll, useTransform, type MotionValue } from "framer-motion";

/* ----------------------------------------------------------------------- *
 * Page backdrop — a few large, calm shapes that give the paper ground depth.
 *
 *   back   two big tonal waves, one with an echo line along its edge
 *   front  two halftone circles and two thick rings
 *
 * Fewer, bigger elements on purpose: small scattered marks read as noise
 * behind dense data. Layers move at different speeds (and slightly sideways)
 * on scroll — the parallax. Still under prefers-reduced-motion.
 * ----------------------------------------------------------------------- */

// Tone-on-tone greens: each shape is a lightness step of the page ground.
const WAVE = "#163024";
const WAVE_HI = "#1b3a2c";
const LINE = "#355e4b";
const DOT = "#315747";
const RED = "#ff6a5c";

function useLayer(scrollY: MotionValue<number>, dy: number, dx: number, still: boolean) {
  const y = useTransform(scrollY, (v) => (still ? 0 : -v * dy));
  const x = useTransform(scrollY, (v) => (still ? 0 : Math.sin(v / 900) * dx));
  return { x, y };
}

export function Backdrop() {
  const still = !!useReducedMotion();
  const { scrollY } = useScroll();
  const back = useLayer(scrollY, 0.04, 24, still);
  const front = useLayer(scrollY, 0.14, -40, still);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <motion.svg style={back} className="absolute -inset-x-[5%] -top-[10%] h-[130%] w-[110%]" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">
        <path d="M0 300 C 200 340 320 460 360 600 S 560 860 820 900 S 1060 1000 1100 1000 L0 1000 Z" fill={WAVE} />
        <path d="M0 262 C 220 304 344 440 392 578 S 600 830 860 868 S 1100 970 1150 1000" fill="none" stroke={LINE} strokeWidth="1.5" />
        <path d="M1600 640 C 1420 700 1300 820 1120 840 S 900 960 860 1000 L1600 1000 Z" fill={WAVE_HI} />
        <path d="M1600 610 C 1400 672 1280 790 1100 812 S 870 940 820 1000" fill="none" stroke={LINE} strokeWidth="1.5" />
        <path d="M900 0 C 940 130 1060 190 1170 170 S 1340 270 1400 390 S 1540 520 1600 500 L1600 0 Z" fill={WAVE_HI} />
      </motion.svg>

      <motion.svg style={front} className="absolute -inset-x-[5%] top-0 h-[140%] w-[110%]" viewBox="0 0 1600 1400" preserveAspectRatio="xMidYMin slice">
        <defs>
          <pattern id="bd-dots" width="20" height="20" patternUnits="userSpaceOnUse">
            <circle cx="2" cy="2" r="2.2" fill={DOT} />
          </pattern>
        </defs>
        <circle cx="1330" cy="-20" r="130" fill="url(#bd-dots)" />
        <circle cx="1480" cy="860" r="150" fill="url(#bd-dots)" />
        <circle cx="40" cy="520" r="46" fill="none" stroke={WAVE_HI} strokeWidth="12" />
        <circle cx="1120" cy="1080" r="40" fill="none" stroke={RED} strokeOpacity="0.35" strokeWidth="11" />
      </motion.svg>
    </div>
  );
}
