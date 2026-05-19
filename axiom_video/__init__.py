"""Axiom Video — modular, signed video-topology detectors.

The differentiator vs traditional VLM (Gemini 2.5 / GPT-4o video /
Llava-Next / Qwen2-VL): those return one monolithic understanding
output. AXIOM Video returns four independent signed verdicts that
compose with text / audio / physics through the event-token
Coordinator.

## Pipeline

    scene_graph (objects, bboxes, frame_t)
            │
            ▼
    [1] ObjectTracker          signed under axiom-video-objects-v1
            │
            ▼
    [2] MotionClassifier       signed under axiom-video-motion-v1
            │
            ▼
    [3] ImpactDetector         signed under axiom-video-impact-v1
            │
            ▼
    [4] TemporalChainExtractor signed under axiom-video-temporal-v1

Each detector takes upstream signed reports as inputs + emits its
own signed report. Same modular pattern as audio:material →
audio:tempo → audio:vad → audio:voice.

## Phase A scope

This Phase A module operates on **scene graphs** — structured input
of `{frame_index, objects: [{id, label, bbox, ...}]}` per frame.
Real-world frame ingestion (YOLO / Detectron / OpenCV) is a future
concern; Phase A locks in the detector ladder + synthetic harness
+ acceptance gates so the modular architecture is testable today.

The same separation audio uses: detectors operate on extracted
features, not raw waveforms. Customers bring their own object
detector; AXIOM provides the audit + composition layer.
"""
from __future__ import annotations

from .color_watcher import ColorReport, ColorWatcher, classify_color
from .impact import ImpactDetector, ImpactReport
from .motion import MotionClassifier, MotionReport
from .object_tracker import ObjectTracker, ObjectTrackReport
from .scene import Object, Scene, SceneGraph
from .temporal_chain import TemporalChainExtractor, TemporalChainReport
from .time_keeper import TimeKeeper, TimeKeeperReport

__all__ = [
    "ColorReport",
    "ColorWatcher",
    "ImpactDetector",
    "ImpactReport",
    "MotionClassifier",
    "MotionReport",
    "Object",
    "ObjectTracker",
    "ObjectTrackReport",
    "Scene",
    "SceneGraph",
    "TemporalChainExtractor",
    "TemporalChainReport",
    "TimeKeeper",
    "TimeKeeperReport",
    "classify_color",
]
