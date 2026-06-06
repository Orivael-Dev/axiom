// Shared motion tokens — eased, no bounce ("ease in and out, no jerk").
import type { Variants, Transition } from "motion/react";

export const EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1];
export const EASE_IN_OUT: [number, number, number, number] = [0.65, 0, 0.35, 1];

// Smooth FLIP reflow when the workspace reassembles (no overshoot).
export const layoutTransition: Transition = { duration: 0.42, ease: EASE_IN_OUT };

export const gridVariants: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};

export const panelVariants: Variants = {
  hidden: { opacity: 0, y: 8, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.34, ease: EASE_OUT } },
  exit: { opacity: 0, y: 6, scale: 0.98, transition: { duration: 0.2, ease: EASE_IN_OUT } },
};

export const fadeSlide: Variants = {
  hidden: { opacity: 0, y: -6 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: EASE_OUT } },
};

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}
