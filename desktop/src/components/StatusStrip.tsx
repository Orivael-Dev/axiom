import { useEffect, useState } from "react";
import { motion } from "motion/react";
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

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const time = now.toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const date = now.toLocaleDateString(undefined, {
    weekday: "short", month: "short", day: "numeric",
  });
  return (
    <div className="widget widget--clock" title={now.toString()}>
      <span className="widget__time">{time}</span>
      <span className="widget__sub">{date}</span>
    </div>
  );
}

function WeatherWidget() {
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
    // Prefer the user's location; fall back to the service default on denial.
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => load(pos.coords.latitude, pos.coords.longitude),
        () => load(),
        { timeout: 5000, maximumAge: 600000 },
      );
    } else {
      load();
    }
    const id = setInterval(() => load(wx?.latitude, wx?.longitude), 600000);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return <div className="widget widget--weather muted">weather…</div>;
  if (!wx || !wx.ok || wx.temperature_c == null) {
    return <div className="widget widget--weather muted" title={wx?.error ?? ""}>weather —</div>;
  }
  return (
    <div className="widget widget--weather" title={`${wx.description} · ${wx.timezone ?? ""}`}>
      <span className="widget__wx-icon">{icon(wx.code, wx.is_day ?? true)}</span>
      <span className="widget__temp">{Math.round(wx.temperature_c)}°</span>
      <span className="widget__sub">{wx.description}</span>
    </div>
  );
}

export function StatusStrip() {
  return (
    <motion.div className="statusstrip" variants={fadeSlide} initial="hidden" animate="visible">
      <WeatherWidget />
      <Clock />
    </motion.div>
  );
}
