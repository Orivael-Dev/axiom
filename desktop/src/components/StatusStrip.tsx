import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";
import type { Weather } from "../types";

// WMO weather-code → emoji. Day/night swap for clear-ish codes.
function icon(code: number | undefined, isDay: boolean): string {
  if (code == null) return "·";
  if (code === 0) return isDay ? "☀️" : "🌙";
  if (code <= 2) return isDay ? "🌤️" : "☁️";
  if (code === 3) return "☁️";
  if (code <= 48) return "🌫️";
  if (code <= 57) return "🌦️";
  if (code <= 67) return "🌧️";
  if (code <= 77) return "❄️";
  if (code <= 82) return "🌧️";
  if (code <= 86) return "🌨️";
  return "⛈️";
}

function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function useWeather(): { wx: Weather | null; loading: boolean } {
  const [wx, setWx] = useState<Weather | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    async function load(lat?: number, lon?: number) {
      try {
        const w = await api.weather(lat, lon);
        if (alive) setWx(w);
      } catch {
        if (alive) setWx({ ok: false, latitude: 0, longitude: 0 });
      } finally {
        if (alive) setLoading(false);
      }
    }
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => load(pos.coords.latitude, pos.coords.longitude),
        () => load(),
        { timeout: 5000, maximumAge: 600000 },
      );
    } else {
      load();
    }
    const id = setInterval(() => load(), 600000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return { wx, loading };
}

export function StatusStrip() {
  const now = useClock();
  const { wx, loading } = useWeather();
  const [expanded, setExpanded] = useState(false);

  // Esc closes; lock body scroll while the overlay is up.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [expanded]);

  const time = now.toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const date = now.toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric",
  });
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const hasWx = !!(wx && wx.ok && wx.temperature_c != null);

  // Fullscreen overlay — portalled to <body> so no transformed / max-width
  // ancestor clips it. position:fixed + inset:0 then truly fills the viewport.
  const overlay = (
    <AnimatePresence>
      {expanded && (
        <motion.div
          className="widget-overlay"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={() => setExpanded(false)}
          role="dialog" aria-modal="true"
        >
          <motion.div
            className="widget-overlay__card"
            initial={{ scale: 0.92, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="widget-overlay__close" onClick={() => setExpanded(false)}
                    aria-label="Close">×</button>

            <div className="widget-overlay__clock">{time}</div>
            <div className="widget-overlay__date">{date}</div>
            <div className="widget-overlay__tz">{tz}</div>

            <div className="widget-overlay__wx">
              {hasWx ? (
                <>
                  <span className="widget-overlay__wx-icon">{icon(wx!.code, wx!.is_day ?? true)}</span>
                  <div className="widget-overlay__wx-body">
                    <span className="widget-overlay__temp">{Math.round(wx!.temperature_c!)}°</span>
                    <span className="widget-overlay__desc">{wx!.description}</span>
                    <span className="widget-overlay__meta">
                      wind {Math.round(wx!.wind_kph ?? 0)} kph · {wx!.timezone ?? tz}
                    </span>
                  </div>
                </>
              ) : (
                <span className="muted">weather unavailable</span>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return (
    <>
      <motion.div className="statusstrip" variants={fadeSlide} initial="hidden" animate="visible">
        <button type="button" className="widget widget--weather"
                onClick={() => setExpanded(true)} title="Expand">
          {loading ? (
            <span className="muted">weather…</span>
          ) : hasWx ? (
            <>
              <span className="widget__wx-icon">{icon(wx!.code, wx!.is_day ?? true)}</span>
              <span className="widget__temp">{Math.round(wx!.temperature_c!)}°</span>
              <span className="widget__sub">{wx!.description}</span>
            </>
          ) : (
            <span className="muted" title={wx?.error ?? ""}>weather —</span>
          )}
        </button>

        <button type="button" className="widget widget--clock"
                onClick={() => setExpanded(true)} title="Expand">
          <span className="widget__time">{time}</span>
          <span className="widget__sub">
            {now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
          </span>
        </button>
      </motion.div>

      {createPortal(overlay, document.body)}
    </>
  );
}
