"""TemporalChainExtractor — turns the (tracks, motions, impacts)
triple into a single ordered event chain.

Output shape:

  [
    {"t": 0.00,  "type": "appear",        "subjects": ["t0"]},
    {"t": 0.10,  "type": "motion_start",  "subjects": ["t0"], "motion": "downward"},
    {"t": 0.45,  "type": "contact",       "subjects": ["t0", "t1"]},
    {"t": 0.46,  "type": "motion_change", "subjects": ["t0"], "motion": "static"},
  ]

This is the human-readable event chain that surfaces in the
EventToken's video payload. It's intentionally tiny — no NN, no
LLM — so it's deterministic and signable.

The concept doc's example ("reach → grip → cup tilt → fall → call
parent") becomes a sequence of these typed events. AXIOM emits the
events; downstream code translates them into product-specific
narrative (in the kid-AI toy case, into the toy's status update).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

from axiom_signing import derive_key

from .impact import ImpactReport
from .motion import MotionReport
from .object_tracker import ObjectTrackReport, Track

TEMPORAL_KEY_NS = b"axiom-video-temporal-v1"


@dataclass(frozen=True)
class TemporalEvent:
    t:        float                   # seconds since clip start
    type:     str                     # appear / disappear / motion_start /
                                       # motion_change / motion_stop / contact
    subjects: tuple[str, ...]
    motion:   Optional[str] = None    # set when type relates to motion


@dataclass(frozen=True)
class TemporalChainReport:
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
    def signed(cls, *, payload: dict,
               confidence: float = 1.0) -> "TemporalChainReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))

    @property
    def events(self) -> list[TemporalEvent]:
        return [
            TemporalEvent(
                t=e["t"], type=e["type"],
                subjects=tuple(e["subjects"]),
                motion=e.get("motion"),
            )
            for e in self.payload.get("events", [])
        ]


class TemporalChainExtractor:
    """Composes track / motion / impact into a single typed event sequence."""

    def extract(
        self,
        track_report: ObjectTrackReport,
        motion_report: MotionReport,
        impact_report: ImpactReport,
    ) -> TemporalChainReport:
        tracks = track_report.tracks
        fps = float(track_report.payload.get("fps", 30.0)) or 30.0
        motion_by_id = {m.id: m for m in motion_report.motions}

        raw: list[TemporalEvent] = []

        # 1. appear / disappear from each track's first + last frame
        for track in tracks:
            raw.append(TemporalEvent(
                t=round(track.first_frame / fps, 3),
                type="appear",
                subjects=(track.id,),
            ))
            if track.last_frame > track.first_frame:
                # Only emit disappear when we have evidence of an end
                last_frame = track.last_frame
                raw.append(TemporalEvent(
                    t=round((last_frame + 1) / fps, 3),
                    type="disappear",
                    subjects=(track.id,),
                ))

        # 2. motion events — one per track that left "static"
        for track in tracks:
            m = motion_by_id.get(track.id)
            if m is None or m.motion_class == "static":
                continue
            # motion_start fires one frame after appear
            raw.append(TemporalEvent(
                t=round((track.first_frame + 1) / fps, 3),
                type="motion_start",
                subjects=(track.id,),
                motion=m.motion_class,
            ))

        # 3. impact events — contact + deceleration both surface
        for event in impact_report.events:
            raw.append(TemporalEvent(
                t=round(event.frame_index / fps, 3),
                type=event.impact_type,
                subjects=event.track_ids,
            ))

        # Sort by time then by canonical type-priority so events at
        # the same timestamp have stable order
        order = {"appear": 0, "motion_start": 1, "contact": 2,
                 "deceleration": 3, "motion_change": 4,
                 "motion_stop": 5, "disappear": 6}
        raw.sort(key=lambda e: (e.t, order.get(e.type, 99), e.subjects))

        # Confidence — function of how many events fired vs how many
        # tracks we saw. A clip with no events at all (static scene)
        # still scores 0.5 — we ran but found a quiet scene.
        n_tracks = len(tracks)
        n_events = len(raw)
        if n_tracks == 0:
            conf = 0.0
        elif n_events <= n_tracks:
            conf = 0.5
        else:
            conf = min(1.0, 0.5 + 0.5 * ((n_events - n_tracks) / max(1, n_tracks)))

        payload = {
            "events": [
                {"t": e.t, "type": e.type,
                 "subjects": list(e.subjects),
                 **({"motion": e.motion} if e.motion else {})}
                for e in raw
            ],
            "n_events": n_events,
            "n_subjects": n_tracks,
            "fps": fps,
        }
        return TemporalChainReport.signed(payload=payload,
                                            confidence=round(conf, 4))


def _canonical(r: TemporalChainReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(TEMPORAL_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
