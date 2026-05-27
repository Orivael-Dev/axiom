"""ImpactDetector — flags collisions / contact / fracture events.

Operates on motion + track output. An impact event fires when:

  1. A moving track's velocity drops sharply (deceleration > threshold)
     OR

  2. Two tracks' bounding boxes overlap (IoU > contact_iou) AND
     at least one of them had non-trivial velocity in the immediately
     preceding frames

Each event carries: frame_index, the participating track IDs,
impact_type ("deceleration" or "contact"), and rough magnitude.

The output is consumed by TemporalChainExtractor to build the event
chain ("ball → fall → contact_floor → bounce").
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from axiom_signing import derive_key

from .motion import MotionReport
from .object_tracker import ObjectTrackReport, Track
from .scene import iou

IMPACT_KEY_NS = b"axiom-video-impact-v1"


@dataclass(frozen=True)
class ImpactEvent:
    frame_index:    int
    track_ids:      tuple[str, ...]      # 1 element for deceleration, 2 for contact
    impact_type:    str                   # "deceleration" | "contact"
    magnitude:      float                 # 0..1, higher = more violent


@dataclass(frozen=True)
class ImpactReport:
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
    def signed(cls, *, payload: dict, confidence: float = 1.0) -> "ImpactReport":
        unsigned = cls(payload=payload, confidence=confidence)
        sig = _sign(_canonical(unsigned))
        return cls(payload=payload, confidence=confidence, signature=sig)

    def verify(self) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _sign(_canonical(self)))

    @property
    def events(self) -> list[ImpactEvent]:
        return [
            ImpactEvent(
                frame_index=e["frame_index"],
                track_ids=tuple(e["track_ids"]),
                impact_type=e["impact_type"],
                magnitude=e["magnitude"],
            )
            for e in self.payload.get("events", [])
        ]


class ImpactDetector:
    """Detects deceleration + contact events across tracks.

    `decel_threshold` is in normalized-velocity-per-frame; default
    0.01 means a track's per-frame velocity must drop by ≥1% of
    frame size for the event to fire.

    `contact_iou` is the bbox overlap above which two tracks count
    as touching.

    `precontact_velocity` ensures the contact event doesn't fire for
    two static objects that happen to be near each other in every
    frame — at least one must have been moving immediately before.
    """

    def __init__(
        self,
        *,
        decel_threshold:     float = 0.01,
        contact_iou:         float = 0.05,
        precontact_velocity: float = 0.003,
    ) -> None:
        self.decel_threshold = decel_threshold
        self.contact_iou = contact_iou
        self.precontact_velocity = precontact_velocity

    def detect(
        self,
        track_report: ObjectTrackReport,
        motion_report: MotionReport,
    ) -> ImpactReport:
        tracks = track_report.tracks
        events: list[ImpactEvent] = []

        # 1. Deceleration events — per-track scan of velocity time series
        for track in tracks:
            events.extend(self._decel_events(track))

        # 2. Contact events — per-frame scan for IoU-overlapping pairs
        events.extend(self._contact_events(tracks))

        # Deduplicate: if the same frame_index + track set produces
        # both a decel and a contact event, prefer contact.
        events = _dedupe_events(events)

        # Confidence: 1.0 if events found, 0.5 if not (we ran the scan
        # but found nothing — that's still a useful signal).
        conf = 1.0 if events else 0.5

        payload = {
            "events": [
                {
                    "frame_index": e.frame_index,
                    "track_ids": list(e.track_ids),
                    "impact_type": e.impact_type,
                    "magnitude": round(e.magnitude, 4),
                }
                for e in sorted(events, key=lambda x: x.frame_index)
            ],
            "n_events": len(events),
        }
        return ImpactReport.signed(payload=payload, confidence=conf)

    def _decel_events(self, track: Track) -> list[ImpactEvent]:
        if len(track.points) < 3:
            return []
        out = []
        # Per-frame velocity magnitude
        v_mags = []
        for prev, cur in zip(track.points, track.points[1:]):
            gap = max(1, cur.frame_index - prev.frame_index)
            pcx = (prev.bbox[0] + prev.bbox[2]) / 2
            pcy = (prev.bbox[1] + prev.bbox[3]) / 2
            ccx = (cur.bbox[0]  + cur.bbox[2])  / 2
            ccy = (cur.bbox[1]  + cur.bbox[3])  / 2
            vx = (ccx - pcx) / gap
            vy = (ccy - pcy) / gap
            v_mags.append(((vx*vx + vy*vy) ** 0.5, cur.frame_index))

        # Find largest single-step drop in velocity magnitude
        for (prev_v, _), (cur_v, frame_idx) in zip(v_mags, v_mags[1:]):
            drop = prev_v - cur_v
            if drop >= self.decel_threshold:
                magnitude = min(1.0, drop / 0.05)   # normalize to 0..1
                out.append(ImpactEvent(
                    frame_index=frame_idx,
                    track_ids=(track.id,),
                    impact_type="deceleration",
                    magnitude=magnitude,
                ))
        return out

    def _contact_events(self, tracks: list[Track]) -> list[ImpactEvent]:
        if len(tracks) < 2:
            return []

        # Build (frame_index → {track_id: bbox}) lookup
        by_frame: dict[int, dict[str, tuple[float,float,float,float]]] = {}
        prev_bbox: dict[str, tuple[int, tuple[float,float,float,float]]] = {}
        for track in tracks:
            for p in track.points:
                by_frame.setdefault(p.frame_index, {})[track.id] = p.bbox

        events: list[ImpactEvent] = []
        already_seen_pairs: set[tuple[str, str]] = set()

        for frame_idx in sorted(by_frame):
            bboxes = by_frame[frame_idx]
            ids = list(bboxes.keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    pair = tuple(sorted((a, b)))
                    if iou(bboxes[a], bboxes[b]) < self.contact_iou:
                        continue
                    # Avoid duplicate firing while the contact persists
                    if pair in already_seen_pairs:
                        continue
                    # Pre-contact velocity check: was at least one
                    # moving in the immediately preceding frame?
                    pre_v = self._precontact_velocity_for(
                        a, frame_idx, by_frame
                    ) + self._precontact_velocity_for(
                        b, frame_idx, by_frame
                    )
                    if pre_v < self.precontact_velocity:
                        continue
                    magnitude = min(1.0, pre_v / 0.05)
                    events.append(ImpactEvent(
                        frame_index=frame_idx,
                        track_ids=pair,
                        impact_type="contact",
                        magnitude=magnitude,
                    ))
                    already_seen_pairs.add(pair)
        return events

    def _precontact_velocity_for(
        self,
        track_id: str,
        frame_idx: int,
        by_frame: dict[int, dict[str, tuple[float,float,float,float]]],
    ) -> float:
        """Return magnitude of velocity of track_id between (frame_idx-1)
        and frame_idx. 0 if no prior frame for that track."""
        if frame_idx - 1 not in by_frame:
            return 0.0
        prev_bb = by_frame[frame_idx - 1].get(track_id)
        cur_bb  = by_frame[frame_idx].get(track_id)
        if prev_bb is None or cur_bb is None:
            return 0.0
        pcx = (prev_bb[0] + prev_bb[2]) / 2
        pcy = (prev_bb[1] + prev_bb[3]) / 2
        ccx = (cur_bb[0]  + cur_bb[2])  / 2
        ccy = (cur_bb[1]  + cur_bb[3])  / 2
        return ((ccx - pcx) ** 2 + (ccy - pcy) ** 2) ** 0.5


def _dedupe_events(events: list[ImpactEvent]) -> list[ImpactEvent]:
    seen: dict[tuple, ImpactEvent] = {}
    for e in events:
        key = (e.frame_index, tuple(sorted(e.track_ids)))
        # Prefer contact over deceleration if both fire on same frame+IDs
        if key in seen:
            if e.impact_type == "contact":
                seen[key] = e
            continue
        seen[key] = e
    return list(seen.values())


def _canonical(r: ImpactReport) -> bytes:
    d = r.to_dict()
    d.pop("signature", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(derive_key(IMPACT_KEY_NS), payload,
                    hashlib.sha256).hexdigest()
