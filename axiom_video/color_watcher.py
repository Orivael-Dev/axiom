"""ColorWatcher — classifies dominant color per object + flags shifts.

Per the user's framing: colors ARE points in space (RGB / HSV / Lab
are 3-D embeddings). Classification = partitioning that space.

This Phase A implementation uses the HSV cylinder. Hue is angular
(0-360°), so we partition into 6 named hue regions:

  red         330-30°
  orange      30-90°
  green       90-150°
  cyan        150-210°
  blue        210-270°
  magenta     270-330°

Saturation + Value layered on top:

  very low saturation (S < 0.15)            → "gray" (override hue)
  low value         (V < 0.20)              → "dark_<hue>"
  high value + low-mid saturation           → "pale_<hue>"
  otherwise                                  → "<hue>"

This produces ~18 distinct color labels — enough to be useful, few
enough to be deterministic across test fixtures.

## Input contract

Per the rest of axiom_video: scene-graph-agnostic. The customer's
upstream object detector populates `Object.extras["color"]` with
a `(r, g, b)` tuple in 0-255. AXIOM consumes that tuple — it does
NOT sample pixels itself. Phase B would add a pixel-sampling
ingester; Phase A keeps the detector boundary clean.

Objects without `extras["color"]` are skipped silently (the
ColorReport's `n_uncolored` field surfaces the count so callers
can detect under-instrumentation).

## Color-shift events

Per track, we compare each frame's color to the track's median
color. A frame whose color is more than `shift_threshold` away
(Euclidean in normalized HSV space) emits a `color_event` —
useful for blush detection, brake-light state, traffic-light
transitions, etc.
"""
from __future__ import annotations

import colorsys
import hashlib
import hmac
import json
from dataclasses import dataclass

from axiom_signing import derive_key

from .scene import SceneGraph

COLOR_KEY_NS = b"axiom-video-color-v1"


# ─── Color naming ───────────────────────────────────────────────────────


HUE_REGIONS: tuple[tuple[float, float, str], ...] = (
    # (start_deg, end_deg, name) — first match wins; red wraps
    (330.0, 360.0, "red"),
    (0.0,    30.0, "red"),
    (30.0,   90.0, "orange"),
    (90.0,  150.0, "green"),
    (150.0, 210.0, "cyan"),
    (210.0, 270.0, "blue"),
    (270.0, 330.0, "magenta"),
)

GRAY_SATURATION_MAX  = 0.15
DARK_VALUE_MAX       = 0.20
PALE_SATURATION_MAX  = 0.50
PALE_VALUE_MIN       = 0.80


def _hue_name(h_deg: float) -> str:
    for start, end, name in HUE_REGIONS:
        if start <= h_deg < end:
            return name
    return "red"   # wraparound safety


def classify_color(rgb: tuple[int, int, int]) -> tuple[str, tuple[float, float, float]]:
    """Return (label, (h_deg, s, v)) for a given (r,g,b) in 0-255.

    Public helper so callers can compute labels without instantiating
    a ColorWatcher. Deterministic — no random sampling.
    """
    r, g, b = [c / 255.0 for c in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h_deg = h * 360.0
    if s < GRAY_SATURATION_MAX:
        # Saturation too low to be a hue. Use value to pick gray shade.
        if v < DARK_VALUE_MAX:
            label = "black"
        elif v > 0.85:
            label = "white"
        else:
            label = "gray"
        return label, (h_deg, s, v)
    hue = _hue_name(h_deg)
    if v < DARK_VALUE_MAX:
        return f"dark_{hue}", (h_deg, s, v)
    if v > PALE_VALUE_MIN and s < PALE_SATURATION_MAX:
        return f"pale_{hue}", (h_deg, s, v)
    return hue, (h_deg, s, v)


# ─── Report dataclass ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ColorEvent:
    frame_index: int
    track_id:    str
    from_label:  str
    to_label:    str


@dataclass(frozen=True)
class TrackColor:
    id:                str
    label:             str
    dominant_color:    str
    rgb_mean:          tuple[float, float, float]
    hsv_mean:          tuple[float, float, float]
    saturation_class:  str           # vivid | muted | gray
    brightness_class:  str           # bright | mid | dark
    stable:            bool
    n_observations:    int


@dataclass(frozen=True)
class ColorReport:
    payload:    dict
    confidence: float = 1.0
    signature:  str = ""

    def to_dict(self) -> dict:
        return {
            "payload": self.payload,
            "confidence": self.confidence,
            "signature": self.signature,
        }

    @classmethod
    def signed(cls, *, payload: dict, confidence: float = 1.0
              ) -> "ColorReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))


class ColorWatcher:
    """Watches `Object.extras["color"]` across a scene graph.

    `shift_threshold` is Euclidean distance in normalized HSV space
    (each axis 0..1; hue wraps but we use linear distance for
    simplicity — works for the dominant-shift detection target).

    `min_observations` filters tracks with too little color data
    from the dominant-color rollup (still scanned for shifts).
    """

    def __init__(
        self,
        *,
        shift_threshold:   float = 0.30,
        min_observations:  int   = 2,
    ) -> None:
        self.shift_threshold = shift_threshold
        self.min_observations = min_observations

    def watch(self, sg: SceneGraph) -> ColorReport:
        # Collect per-track (frame_index, rgb) observations
        by_track: dict[str, list[tuple[int, str, tuple[int, int, int]]]] = {}
        n_uncolored = 0
        for scene in sg.scenes:
            for obj in scene.objects:
                rgb = obj.extras.get("color") if obj.extras else None
                if rgb is None or len(rgb) != 3:
                    n_uncolored += 1
                    continue
                rgb_t = tuple(int(c) for c in rgb)
                by_track.setdefault(obj.id, []).append(
                    (scene.frame_index, obj.label, rgb_t),
                )

        track_colors: list[TrackColor] = []
        color_events: list[ColorEvent] = []

        for tid, obs in by_track.items():
            if len(obs) < self.min_observations:
                continue
            # Mean RGB across observations
            r_mean = sum(o[2][0] for o in obs) / len(obs)
            g_mean = sum(o[2][1] for o in obs) / len(obs)
            b_mean = sum(o[2][2] for o in obs) / len(obs)
            dom_label, (h_deg, s, v) = classify_color(
                (int(r_mean), int(g_mean), int(b_mean)),
            )

            # Per-observation labels — detect shifts
            obs_labels = []
            for frame_idx, _, rgb in obs:
                obs_label, _ = classify_color(rgb)
                obs_labels.append((frame_idx, obs_label))
            # Shift events: whenever consecutive labels differ
            for (_, prev_lab), (cur_frame, cur_lab) in zip(
                obs_labels, obs_labels[1:]
            ):
                if prev_lab != cur_lab:
                    color_events.append(ColorEvent(
                        frame_index=cur_frame,
                        track_id=tid,
                        from_label=prev_lab,
                        to_label=cur_lab,
                    ))

            stable = all(lab == dom_label for _, lab in obs_labels)
            track_colors.append(TrackColor(
                id=tid,
                label=obs[0][1],
                dominant_color=dom_label,
                rgb_mean=(round(r_mean, 1), round(g_mean, 1), round(b_mean, 1)),
                hsv_mean=(round(h_deg, 2), round(s, 4), round(v, 4)),
                saturation_class=_saturation_class(s),
                brightness_class=_brightness_class(v),
                stable=stable,
                n_observations=len(obs),
            ))

        scene_dominant = _scene_dominant(track_colors)
        # Confidence: high if we have multi-observation tracks; low if
        # everything was uncolored.
        n_colored_tracks = len(track_colors)
        if n_colored_tracks == 0:
            conf = 0.0
        else:
            conf = min(1.0, 0.5 + 0.5 * (n_colored_tracks /
                                           max(1, n_colored_tracks +
                                               (n_uncolored > 0))))

        payload = {
            "n_tracks":              n_colored_tracks,
            "n_uncolored":           n_uncolored,
            "scene_dominant_color":  scene_dominant,
            "tracks": [
                {
                    "id": tc.id, "label": tc.label,
                    "dominant_color":    tc.dominant_color,
                    "rgb_mean":          list(tc.rgb_mean),
                    "hsv_mean":          list(tc.hsv_mean),
                    "saturation_class":  tc.saturation_class,
                    "brightness_class":  tc.brightness_class,
                    "stable":            tc.stable,
                    "n_observations":    tc.n_observations,
                }
                for tc in track_colors
            ],
            "color_events": [
                {"frame_index": e.frame_index,
                 "track_id": e.track_id,
                 "from": e.from_label, "to": e.to_label}
                for e in color_events
            ],
            "n_color_events": len(color_events),
        }
        return ColorReport.signed(payload=payload, confidence=round(conf, 4))


# ─── classifiers ────────────────────────────────────────────────────────


def _saturation_class(s: float) -> str:
    if s < GRAY_SATURATION_MAX:
        return "gray"
    if s < 0.50:
        return "muted"
    return "vivid"


def _brightness_class(v: float) -> str:
    if v < DARK_VALUE_MAX:
        return "dark"
    if v > 0.75:
        return "bright"
    return "mid"


def _scene_dominant(track_colors: list[TrackColor]) -> str:
    if not track_colors:
        return "none"
    counts: dict[str, int] = {}
    for tc in track_colors:
        counts[tc.dominant_color] = counts.get(tc.dominant_color, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


# ─── signing ────────────────────────────────────────────────────────────


def _canonical(r: ColorReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(COLOR_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
