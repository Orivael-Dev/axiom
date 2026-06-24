"""axiom_groove_tts.py — Pure formant-synthesis TTS (Klatt-style)

Pipeline:
  Text → G2P → PhonemeSpec[] → KlattSynth (cascade resonators) → WAV

No neural weights. Voiced pulse train or noise source filtered through a
cascade of 4 second-order resonators (F1–F4). Output is synthetic / robotic
(1980s Klatt style) but fully offline, zero external model weights.

Built-in mini phoneme dictionary covers ~150 common English words.
Install `pronouncing` for full coverage: pip install pronouncing

Dependencies (stdlib only for synthesis + built-in G2P):
  pip install numpy           # required
  pip install pronouncing     # optional — full-vocabulary G2P
  pip install sounddevice     # optional — --play flag

Usage:
  python3 axiom_groove_tts.py "hello world" -o hello.wav
  python3 axiom_groove_tts.py "hello world" --play
  python3 axiom_groove_tts.py --phonemes "HH AH L OW"
  python3 axiom_groove_tts.py "test speech" --f0 100 --fs 22050
"""

import sys
import math
import wave
import struct
import argparse
import re
from dataclasses import dataclass, field

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

# ── Phoneme specification ─────────────────────────────────────────────────────

@dataclass
class PhonemeSpec:
    voiced:      bool         # True = pulse source, False = noise source
    closure_ms:  int          # silence before burst (stops only)
    formants:    list         # [F1, F2, F3, F4] Hz
    bandwidths:  list         # [BW1, BW2, BW3, BW4] Hz
    noise_mix:   float        # 0=pure voiced, 1=pure noise
    duration_ms: int          # nominal duration


# ── ARPABET phoneme table ─────────────────────────────────────────────────────
# Formant values from Peterson & Barney (1952), Klatt (1980), Stevens (1998).
# Format: (voiced, closure_ms, [F1,F2,F3,F4], [BW1,BW2,BW3,BW4], noise_mix, dur_ms)

def _p(voiced, closure_ms, f1, f2, f3, f4, bw1, bw2, bw3, bw4, noise_mix, dur):
    return PhonemeSpec(voiced, closure_ms, [f1,f2,f3,f4], [bw1,bw2,bw3,bw4], noise_mix, dur)

PHONEME_TABLE: dict[str, PhonemeSpec] = {
    # ── Vowels (Peterson & Barney 1952 male averages) ──────────────────────
    "AA": _p(True,  0, 730, 1090, 2440, 3500,  60,  90, 150, 200, 0.00, 120),  # father
    "AE": _p(True,  0, 660, 1720, 2410, 3500,  60,  90, 150, 200, 0.00, 120),  # cat
    "AH": _p(True,  0, 520, 1190, 2390, 3500,  60,  90, 150, 200, 0.00, 100),  # but/schwa
    "AO": _p(True,  0, 570,  840, 2410, 3500,  60,  90, 150, 200, 0.00, 120),  # thought
    "AW": _p(True,  0, 730, 1090, 2440, 3500,  60,  90, 150, 200, 0.00, 150),  # how (onset)
    "AX": _p(True,  0, 520, 1190, 2390, 3500,  60,  90, 150, 200, 0.00,  60),  # unstressed schwa
    "AY": _p(True,  0, 730, 1090, 2440, 3500,  60,  90, 150, 200, 0.00, 150),  # hide (onset)
    "EH": _p(True,  0, 530, 1840, 2480, 3500,  60,  90, 150, 200, 0.00, 110),  # bed
    "ER": _p(True,  0, 490, 1350, 1690, 3500,  70, 100, 180, 220, 0.00, 130),  # bird (low F3)
    "EY": _p(True,  0, 400, 2000, 2550, 3500,  60,  90, 150, 200, 0.00, 130),  # face
    "IH": _p(True,  0, 390, 1990, 2550, 3500,  60,  90, 150, 200, 0.00,  90),  # bit
    "IY": _p(True,  0, 270, 2290, 3010, 3500,  60,  90, 150, 200, 0.00, 120),  # feet
    "OW": _p(True,  0, 450, 1000, 2400, 3500,  60,  90, 150, 200, 0.00, 130),  # goat
    "OY": _p(True,  0, 570,  840, 2410, 3500,  60,  90, 150, 200, 0.00, 150),  # boy (onset)
    "UH": _p(True,  0, 440, 1020, 2240, 3500,  60,  90, 150, 200, 0.00,  90),  # book
    "UW": _p(True,  0, 300,  870, 2240, 3500,  60,  90, 150, 200, 0.00, 120),  # food
    # ── Nasals ─────────────────────────────────────────────────────────────
    "M":  _p(True,  0, 250, 1000, 2200, 3500, 150, 200, 250, 300, 0.00,  80),
    "N":  _p(True,  0, 250, 1700, 2500, 3500, 150, 200, 250, 300, 0.00,  70),
    "NG": _p(True,  0, 250, 2000, 2500, 3500, 150, 200, 250, 300, 0.00,  80),
    # ── Voiced stops ───────────────────────────────────────────────────────
    "B":  _p(True,  60,  200,  800, 2000, 3200, 150, 200, 250, 300, 0.10,  80),
    "D":  _p(True,  70,  200, 1700, 2600, 3500, 150, 200, 250, 300, 0.10,  70),
    "G":  _p(True,  80,  200, 2200, 2800, 3500, 150, 200, 250, 300, 0.10,  80),
    # ── Unvoiced stops ─────────────────────────────────────────────────────
    "P":  _p(False, 90,  200,  800, 2000, 3200, 200, 250, 300, 350, 0.80,  80),
    "T":  _p(False,100,  200, 1700, 2600, 3500, 200, 250, 300, 350, 0.70,  70),
    "K":  _p(False,110,  200, 2200, 2800, 3500, 200, 250, 300, 350, 0.75,  80),
    # ── Fricatives (voiced) ────────────────────────────────────────────────
    "V":  _p(True,  0,  200, 1000, 2200, 3200, 300, 350, 400, 450, 0.50,  80),
    "DH": _p(True,  0,  200, 1400, 2200, 3500, 300, 350, 400, 450, 0.30,  60),
    "Z":  _p(True,  0,  200, 1700, 4800, 6500, 300, 350, 400, 450, 0.60,  80),
    "ZH": _p(True,  0,  200, 1800, 3200, 5000, 300, 350, 400, 450, 0.50,  80),
    # ── Fricatives (unvoiced) ──────────────────────────────────────────────
    "F":  _p(False, 0,  200, 1000, 2200, 3200, 300, 350, 400, 450, 0.90,  80),
    "TH": _p(False, 0,  200, 1400, 2200, 3500, 300, 350, 400, 450, 0.85,  70),
    "S":  _p(False, 0,  200, 1700, 4800, 6500, 300, 350, 400, 450, 0.95,  80),
    "SH": _p(False, 0,  200, 1800, 3200, 5000, 300, 350, 400, 450, 0.90,  80),
    "HH": _p(False, 0,  520, 1190, 2390, 3500, 200, 250, 300, 350, 0.70,  60),
    # ── Affricates ─────────────────────────────────────────────────────────
    "CH": _p(False, 30, 200, 1800, 3200, 5000, 250, 300, 350, 400, 0.80,  90),
    "JH": _p(True,  20, 200, 1800, 3200, 5000, 250, 300, 350, 400, 0.50,  80),
    # ── Approximants ───────────────────────────────────────────────────────
    "W":  _p(True,  0,  350,  800, 2200, 3500,  80, 100, 150, 200, 0.00,  70),
    "Y":  _p(True,  0,  280, 2100, 2700, 3500,  80, 100, 150, 200, 0.00,  70),
    "L":  _p(True,  0,  360, 1000, 2800, 3500,  80, 100, 150, 200, 0.00,  70),
    "R":  _p(True,  0,  430, 1100, 1600, 3500,  80, 100, 150, 200, 0.00,  70),
    # ── Silence ────────────────────────────────────────────────────────────
    "SIL":_p(False, 0,  500, 1500, 2500, 3500,  60,  90, 150, 200, 0.00, 150),
    "SP": _p(False, 0,  500, 1500, 2500, 3500,  60,  90, 150, 200, 0.00,  60),
}

STRESS_RE = re.compile(r'[012]$')

def _strip_stress(phone: str) -> str:
    return STRESS_RE.sub('', phone)


# ── Built-in mini G2P dictionary (~150 common English words) ─────────────────
# ARPABET without stress markers.

MINI_DICT: dict[str, list[str]] = {
    "a":       ["AH"],
    "about":   ["AH","B","AW","T"],
    "after":   ["AE","F","T","ER"],
    "all":     ["AO","L"],
    "also":    ["AO","L","S","OW"],
    "an":      ["AE","N"],
    "and":     ["AE","N","D"],
    "are":     ["AA","R"],
    "as":      ["AE","Z"],
    "at":      ["AE","T"],
    "axiom":   ["AE","K","S","IY","AH","M"],
    "back":    ["B","AE","K"],
    "be":      ["B","IY"],
    "been":    ["B","IH","N"],
    "before":  ["B","IH","F","AO","R"],
    "but":     ["B","AH","T"],
    "by":      ["B","AY"],
    "can":     ["K","AE","N"],
    "come":    ["K","AH","M"],
    "could":   ["K","UH","D"],
    "day":     ["D","EY"],
    "do":      ["D","UW"],
    "down":    ["D","AW","N"],
    "eight":   ["EY","T"],
    "even":    ["IY","V","AH","N"],
    "find":    ["F","AY","N","D"],
    "first":   ["F","ER","S","T"],
    "five":    ["F","AY","V"],
    "for":     ["F","AO","R"],
    "formant": ["F","AO","R","M","AH","N","T"],
    "four":    ["F","AO","R"],
    "from":    ["F","R","AH","M"],
    "get":     ["G","EH","T"],
    "give":    ["G","IH","V"],
    "go":      ["G","OW"],
    "good":    ["G","UH","D"],
    "groove":  ["G","R","UW","V"],
    "had":     ["HH","AE","D"],
    "has":     ["HH","AE","Z"],
    "have":    ["HH","AE","V"],
    "he":      ["HH","IY"],
    "help":    ["HH","EH","L","P"],
    "her":     ["HH","ER"],
    "here":    ["HH","IH","R"],
    "him":     ["HH","IH","M"],
    "his":     ["HH","IH","Z"],
    "how":     ["HH","AW"],
    "human":   ["HH","Y","UW","M","AH","N"],
    "i":       ["AY"],
    "if":      ["IH","F"],
    "in":      ["IH","N"],
    "into":    ["IH","N","T","UW"],
    "is":      ["IH","Z"],
    "it":      ["IH","T"],
    "its":     ["IH","T","S"],
    "just":    ["JH","AH","S","T"],
    "know":    ["N","OW"],
    "last":    ["L","AE","S","T"],
    "like":    ["L","AY","K"],
    "long":    ["L","AO","NG"],
    "look":    ["L","UH","K"],
    "make":    ["M","EY","K"],
    "may":     ["M","EY"],
    "more":    ["M","AO","R"],
    "most":    ["M","OW","S","T"],
    "mouth":   ["M","AW","TH"],
    "much":    ["M","AH","CH"],
    "must":    ["M","AH","S","T"],
    "my":      ["M","AY"],
    "need":    ["N","IY","D"],
    "new":     ["N","UW"],
    "nine":    ["N","AY","N"],
    "no":      ["N","OW"],
    "not":     ["N","AO","T"],
    "now":     ["N","AW"],
    "of":      ["AH","V"],
    "old":     ["OW","L","D"],
    "on":      ["AO","N"],
    "one":     ["W","AH","N"],
    "only":    ["OW","N","L","IY"],
    "or":      ["AO","R"],
    "other":   ["AH","DH","ER"],
    "our":     ["AW","ER"],
    "out":     ["AW","T"],
    "over":    ["OW","V","ER"],
    "people":  ["P","IY","P","AH","L"],
    "phoneme": ["F","OW","N","IY","M"],
    "right":   ["R","AY","T"],
    "said":    ["S","EH","D"],
    "say":     ["S","EY"],
    "see":     ["S","IY"],
    "seven":   ["S","EH","V","AH","N"],
    "she":     ["SH","IY"],
    "should":  ["SH","UH","D"],
    "show":    ["SH","OW"],
    "six":     ["S","IH","K","S"],
    "so":      ["S","OW"],
    "some":    ["S","AH","M"],
    "sound":   ["S","AW","N","D"],
    "speak":   ["S","P","IY","K"],
    "speech":  ["S","P","IY","CH"],
    "still":   ["S","T","IH","L"],
    "such":    ["S","AH","CH"],
    "take":    ["T","EY","K"],
    "talk":    ["T","AO","K"],
    "ten":     ["T","EH","N"],
    "test":    ["T","EH","S","T"],
    "than":    ["DH","AE","N"],
    "that":    ["DH","AE","T"],
    "the":     ["DH","AH"],
    "their":   ["DH","EH","R"],
    "them":    ["DH","EH","M"],
    "then":    ["DH","EH","N"],
    "there":   ["DH","EH","R"],
    "they":    ["DH","EY"],
    "think":   ["TH","IH","NG","K"],
    "this":    ["DH","IH","S"],
    "three":   ["TH","R","IY"],
    "time":    ["T","AY","M"],
    "to":      ["T","UW"],
    "told":    ["T","OW","L","D"],
    "tongue":  ["T","AH","NG"],
    "two":     ["T","UW"],
    "up":      ["AH","P"],
    "use":     ["Y","UW","Z"],
    "very":    ["V","EH","R","IY"],
    "voice":   ["V","OY","S"],
    "vowel":   ["V","AW","AH","L"],
    "was":     ["W","AH","Z"],
    "way":     ["W","EY"],
    "we":      ["W","IY"],
    "well":    ["W","EH","L"],
    "what":    ["W","AH","T"],
    "when":    ["W","EH","N"],
    "which":   ["W","IH","CH"],
    "who":     ["HH","UW"],
    "will":    ["W","IH","L"],
    "with":    ["W","IH","DH"],
    "word":    ["W","ER","D"],
    "work":    ["W","ER","K"],
    "world":   ["W","ER","L","D"],
    "would":   ["W","UH","D"],
    "yes":     ["Y","EH","S"],
    "you":     ["Y","UW"],
    "your":    ["Y","AO","R"],
    "hello":   ["HH","AH","L","OW"],
}


# ── G2P ───────────────────────────────────────────────────────────────────────

# Basic letter-to-sound rules for unknown words (approximate)
_L2S_MULTI: list[tuple[str, list[str]]] = [
    ("tion",  ["SH","AH","N"]),   ("ck",   ["K"]),
    ("ph",    ["F"]),              ("kn",   ["N"]),
    ("wr",    ["R"]),              ("gn",   ["N"]),
    ("wh",    ["W"]),              ("th",   ["TH"]),
    ("sh",    ["SH"]),             ("ch",   ["CH"]),
    ("ng",    ["NG"]),             ("oo",   ["UW"]),
    ("ee",    ["IY"]),             ("ea",   ["IY"]),
    ("ai",    ["EY"]),             ("ay",   ["EY"]),
    ("oa",    ["OW"]),             ("ou",   ["AW"]),
    ("ow",    ["OW"]),             ("igh",  ["AY"]),
    ("ie",    ["AY"]),             ("ue",   ["UW"]),
    ("ui",    ["UW"]),             ("au",   ["AO"]),
    ("aw",    ["AO"]),
]
_L2S_SINGLE: dict[str, list[str]] = {
    "a":"AE","b":"B","c":"K","d":"D","e":"EH","f":"F","g":"G",
    "h":"HH","i":"IH","j":"JH","k":"K","l":"L","m":"M","n":"N",
    "o":"OW","p":"P","q":"K","r":"R","s":"S","t":"T","u":"AH",
    "v":"V","w":"W","x":"K S","y":"Y","z":"Z",
}


def _l2s(word: str) -> list[str]:
    """Basic letter-to-sound fallback — approximate but phonetically motivated."""
    result: list[str] = []
    w = word.lower()
    i = 0
    while i < len(w):
        matched = False
        for pattern, phones in _L2S_MULTI:
            if w[i:].startswith(pattern):
                result.extend(phones)
                i += len(pattern)
                matched = True
                break
        if not matched:
            ch = w[i]
            if ch in _L2S_SINGLE:
                result.extend(_L2S_SINGLE[ch].split())
            i += 1
    return result


def text_to_phonemes(text: str, use_pronouncing: bool = True) -> list[str]:
    """Convert text to ARPABET phoneme list (stress markers stripped).

    Priority:
      1. `pronouncing` library (CMU dict, full vocabulary)
      2. Built-in MINI_DICT (~150 common words)
      3. Letter-to-sound rules (approximate fallback)
    """
    pronouncing_mod = None
    if use_pronouncing:
        try:
            import pronouncing as pm
            pronouncing_mod = pm
        except ImportError:
            pass

    words = re.sub(r"[^\w\s'-]", "", text.lower()).split()
    phones: list[str] = []

    for word in words:
        word = word.strip("'-")
        if not word:
            continue

        if pronouncing_mod:
            matches = pronouncing_mod.phones_for_word(word)
            if matches:
                raw = matches[0].split()
                phones.extend(_strip_stress(p) for p in raw)
                phones.append("SP")
                continue

        if word in MINI_DICT:
            phones.extend(MINI_DICT[word])
        else:
            phones.extend(_l2s(word))

        phones.append("SP")

    return phones


# ── SRD Dither ───────────────────────────────────────────────────────────────
#
# Stochastic Residual Dithering applied to formant synthesis.
# Mirrors the SRD quantization concept: noise proportional to the residual
# between the ideal formant trajectory and the actual voiced output.
# Applied along three axes: formant frequency, F0 amplitude (shimmer),
# and phoneme duration — each scaled by a transition_factor that is higher
# at phoneme boundaries (where acoustic residual is largest) and lower in
# stable vowel nuclei.

@dataclass
class SRDDither:
    """Stochastic Residual Dithering for formant synthesis.

    formant_jitter   : ±σ fractional variation in F1–F4 per phoneme.
                       Doubled at phoneme boundaries (transition zone).
    f0_shimmer       : ±σ fractional F0 period jitter per pitch pulse.
    duration_jitter  : ±σ fractional variation in phoneme duration.
    transition_frac  : fraction of phoneme treated as high-variance transition.
    """
    formant_jitter:   float = 0.025   # ±2.5% formant variation
    f0_shimmer:       float = 0.03    # ±3% F0 period jitter
    duration_jitter:  float = 0.08    # ±8% phoneme duration
    transition_frac:  float = 0.25    # first/last 25% of phoneme = boundary zone

    def perturb_formants(self, formants: list, rng,
                         at_boundary: bool = False) -> list:
        """Apply stochastic perturbation — 2× at boundaries."""
        scale = self.formant_jitter * (2.0 if at_boundary else 1.0)
        return [max(50.0, f * (1.0 + rng.uniform(-scale, scale)))
                for f in formants]

    def perturb_duration(self, duration_ms: int, rng) -> int:
        d = self.duration_jitter
        return max(20, int(duration_ms * (1.0 + rng.uniform(-d, d))))


# Preset dither levels
DITHER_OFF    = SRDDither(0.0,   0.01, 0.0)   # no formant/duration dither
DITHER_LOW    = SRDDither(0.015, 0.02, 0.05)
DITHER_MED    = SRDDither(0.025, 0.03, 0.08)  # default — natural speech variation
DITHER_HIGH   = SRDDither(0.045, 0.05, 0.15)  # expressive / emotional


# ── Emotion profiles & Speech Trace ──────────────────────────────────────────
#
# EmotionProfile maps an emotional state to:
#   constitutional_distance  how far speech planning lags behind text (0–1)
#   f0_variation             F0 range multiplier (excited = wider pitch)
#   rate_scale               speaking rate (>1 = slower, <1 = faster)
#   dither                   which SRD preset to use
#
# SpeechTrace models the human cognitive lookahead buffer:
#   - Speaker plans ~300ms ahead while articulating current phoneme
#   - When constitutional_distance is HIGH, the buffer drains faster than
#     it fills → hesitation tokens inserted before content words
#   - Groove agent's articulator displacement feeds the CD:
#     precise articulation (high displacement) → deliberate → lower CD
#     lazy/neutral articulation → casual → higher CD

@dataclass
class EmotionProfile:
    name:                    str
    constitutional_distance: float   # 0=fluent, 1=max hesitation
    f0_variation:            float   # F0 range multiplier
    rate_scale:              float   # >1 slower, <1 faster
    dither:                  SRDDither = field(default_factory=lambda: DITHER_MED)


EMOTION_PROFILES: dict[str, EmotionProfile] = {
    "neutral":   EmotionProfile("neutral",   0.08, 1.0, 1.00, DITHER_MED),
    "excited":   EmotionProfile("excited",   0.05, 2.0, 0.85, DITHER_HIGH),
    "calm":      EmotionProfile("calm",      0.05, 0.6, 1.20, DITHER_LOW),
    "uncertain": EmotionProfile("uncertain", 0.40, 1.2, 1.15, DITHER_MED),
    "deep":      EmotionProfile("deep",      0.60, 0.8, 1.35, DITHER_LOW),
    "emotional": EmotionProfile("emotional", 0.35, 1.8, 1.10, DITHER_HIGH),
}

# Function words — low cognitive load, never hesitate before these
_FUNCTION_WORDS = frozenset({
    "the","a","an","and","or","but","in","on","at","to","of","for",
    "is","are","was","were","be","been","it","this","that","he","she",
    "we","they","i","my","your","his","her","its","our","their",
})

# "uh" filler → phonemes
_UH_FILLER = ["AH", "SP"]        # "uh"
_UM_FILLER = ["AH", "M", "SP"]   # "um"


class SpeechTrace:
    """Cognitive lookahead buffer — models thinking-before-speaking.

    Inserts hesitation tokens (uh / um / pause) before content words
    when constitutional_distance + word complexity exceeds a threshold.
    Also models phoneme repetition (stutter) on very high cognitive load.

    constitutional_distance flows from:
      - EmotionProfile (emotional state)
      - groove_displacement (from GrooveAgent.ArticulatorState.displacement):
        high precision → deliberate speech → lower effective CD
    """

    def __init__(self, profile: EmotionProfile,
                 groove_displacement: float = 0.0):
        self.profile = profile
        # Groove depth modulates CD: high displacement = precise = calmer
        precision_bonus = min(groove_displacement * 0.15, 0.25)
        self.cd = max(0.0, profile.constitutional_distance - precision_bonus)
        self._load = 0.0   # running EWMA cognitive load
        self._rng  = __import__('random').Random(42)

    def process_words(self, words: list[str]) -> list[str]:
        """Return modified word list with hesitation tokens injected."""
        out: list[str] = []
        self._load = 0.0
        for word in words:
            w = word.lower().strip("'-")
            if not w:
                continue
            load = self._word_load(w)
            # EWMA update: recent words matter more
            self._load = self._load * 0.65 + load * 0.35
            if self._is_content(w) and self._load > (1.0 - self.cd):
                out.extend(self._hesitation(load))
            out.append(word)
        return out

    def _word_load(self, word: str) -> float:
        """Cognitive load: length × rarity × constitutional_distance."""
        len_load    = min(len(word) / 9.0, 1.0)
        rarity_load = 0.25 if word in MINI_DICT else 0.75
        return (len_load * 0.35 + rarity_load * 0.65) * (1.0 + self.cd)

    def _is_content(self, word: str) -> bool:
        return word not in _FUNCTION_WORDS

    def _hesitation(self, load: float) -> list[str]:
        """Choose hesitation type based on load and CD."""
        r = self._rng.random()
        if load > 1.4 and self.cd > 0.45:
            # Very high load + deep CD: stutter (first-sound repetition)
            # represented as extra SP before the word — phoneme-level
            # repetition is handled in phoneme_sequence_for_word() below
            return ["__STUTTER__"]
        elif r < 0.45:
            return ["__UH__"]
        elif r < 0.75:
            return ["__UM__"]
        else:
            return ["__PAUSE__"]

    @property
    def rate_scale(self) -> float:
        return self.profile.rate_scale

    @property
    def f0_variation(self) -> float:
        return self.profile.f0_variation


def apply_trace_to_phonemes(word_phonemes: list[tuple[str, list[str]]],
                             trace: SpeechTrace) -> list[str]:
    """Expand a (word, phonemes) list through the trace model.

    word_phonemes: [(word, [ARPABET, ...]), ...]
    Returns flat ARPABET list with hesitation tokens resolved.
    """
    words = [w for w, _ in word_phonemes]
    phone_map = {w: p for w, p in word_phonemes}

    modified_words = trace.process_words(words)
    result: list[str] = []
    _stutter_next = False   # flag: next content word gets first-phoneme repetition

    for token in modified_words:
        if token == "__UH__":
            result.extend(_UH_FILLER)
        elif token == "__UM__":
            result.extend(_UM_FILLER)
        elif token == "__PAUSE__":
            result.extend(["SIL"])
        elif token == "__STUTTER__":
            _stutter_next = True   # tag — applied when next word arrives
        elif token in phone_map:
            phones = phone_map[token]
            if _stutter_next and phones:
                # Repeat first phoneme with a brief gap before continuing
                result.extend([phones[0], "SP"])
                _stutter_next = False
            result.extend(phones)
            result.append("SP")
        else:
            _stutter_next = False

    return result


def text_to_word_phonemes(text: str,
                           use_pronouncing: bool = True) -> list[tuple[str, list[str]]]:
    """Like text_to_phonemes but returns [(word, [phones])] pairs."""
    pm = None
    if use_pronouncing:
        try:
            import pronouncing
            pm = pronouncing
        except ImportError:
            pass

    words = re.sub(r"[^\w\s'-]", "", text.lower()).split()
    result = []
    for word in words:
        word = word.strip("'-")
        if not word:
            continue
        if pm:
            matches = pm.phones_for_word(word)
            if matches:
                phones = [_strip_stress(p) for p in matches[0].split()]
                result.append((word, phones))
                continue
        if word in MINI_DICT:
            result.append((word, list(MINI_DICT[word])))
        else:
            result.append((word, _l2s(word)))
    return result


# ── Klatt cascade synthesizer ─────────────────────────────────────────────────

class KlattSynth:
    """Cascade formant synthesizer (Klatt 1980 architecture, simplified).

    Voiced source: pulse train at F0 Hz with ±1% period jitter.
    Unvoiced source: Gaussian noise.
    Cascade: 4 second-order resonators (F1→F2→F3→F4) applied in series.
    Resonator state carries across phoneme boundaries — smooth transitions.
    """

    def __init__(self, fs: int = 16000, f0: float = 120.0,
                 dither: "SRDDither | None" = None,
                 f0_variation: float = 1.0):
        self.fs           = fs
        self.f0           = f0
        self.dither       = dither or DITHER_MED
        self.f0_variation = f0_variation  # from EmotionProfile
        self._rng = None   # seeded in synthesize()

    def _build_source(self,
                      sequence: list[tuple[PhonemeSpec, int]]) -> "np.ndarray":
        """Build the source signal for the full phoneme sequence."""
        total = sum(n for _, n in sequence)
        source = np.zeros(total)
        rng = self._rng
        pos = 0
        phase = 0.0   # fractional phase within one F0 period

        for spec, n in sequence:
            n_closure = min(int(self.fs * spec.closure_ms / 1000), n)
            n_active  = n - n_closure

            if n_active <= 0:
                pos += n
                continue

            # Voiced component (pulse train)
            voiced = np.zeros(n_active)
            if spec.voiced:
                period = self.fs / self.f0
                for k in range(n_active):
                    phase += 1.0
                    # F0 shimmer: SRD dither on pitch period + emotion F0 variation
                    shimmer = self.dither.f0_shimmer * self.f0_variation
                    jitter = 1.0 + rng.uniform(-shimmer, shimmer)
                    if phase >= period * jitter:
                        phase -= period * jitter
                        voiced[k] = 1.0

            # Noise component
            noise = rng.standard_normal(n_active) if spec.noise_mix > 0 else np.zeros(n_active)

            mix = (1.0 - spec.noise_mix) * voiced + spec.noise_mix * noise

            # 5ms onset ramp to avoid click
            ramp = min(int(self.fs * 0.005), n_active)
            if ramp > 0:
                mix[:ramp] *= np.linspace(0, 1, ramp)

            source[pos + n_closure: pos + n] = mix
            pos += n

        return source

    def _cascade_resonators(self,
                             source: "np.ndarray",
                             sequence: list[tuple[PhonemeSpec, int]]) -> "np.ndarray":
        """Apply 4 formant resonators in cascade.

        Coefficients update at phoneme boundaries; state carries across
        boundaries so transitions are smooth (resonator ring time ~80ms
        at BW=60Hz acts as a natural smoothing filter).
        """
        output = source.copy()
        n_formants = 4

        for fi in range(n_formants):
            pos = 0
            p1, p2 = 0.0, 0.0   # resonator state

            for spec, n in sequence:
                freq = min(spec.formants[fi], self.fs * 0.45)   # cap below Nyquist
                bw   = spec.bandwidths[fi]
                r    = math.exp(-math.pi * bw   / self.fs)
                th   = 2.0 * math.pi * freq / self.fs
                a1   = 2.0 * r * math.cos(th)
                a2   = -(r ** 2)

                for j in range(pos, pos + n):
                    y = output[j] + a1 * p1 + a2 * p2
                    p2, p1 = p1, y
                    output[j] = y

                pos += n

        return output

    def synthesize(self, phonemes: list[str],
                   rate_scale: float = 1.0) -> "np.ndarray":
        """Synthesize audio from an ARPABET phoneme list.

        rate_scale: speaking rate from EmotionProfile (>1 = slower)
        Returns a float64 array normalised to [-1, 1].
        """
        self._rng = np.random.default_rng(0)
        drng = __import__('numpy').random.default_rng(1)   # separate seed for dither

        # Build (spec, n_samples) pairs with SRD dither applied
        sequence: list[tuple[PhonemeSpec, int]] = []
        n_phones = len(phonemes)
        for idx, phone in enumerate(phonemes):
            phone = _strip_stress(phone)
            spec  = PHONEME_TABLE.get(phone, PHONEME_TABLE["AH"])

            # Duration: base × rate_scale × duration dither
            base_dur = int(spec.duration_ms * rate_scale)
            dur = self.dither.perturb_duration(base_dur, drng)

            # Formant dither: higher at boundaries (first and last phonemes, or SP)
            at_boundary = (idx == 0 or idx == n_phones - 1
                           or phonemes[max(0,idx-1)] in ("SP","SIL")
                           or phone in ("SP","SIL"))
            dithered_formants = self.dither.perturb_formants(
                spec.formants, drng, at_boundary=at_boundary)

            # Build a per-phoneme spec with dithered formants
            dithered_spec = PhonemeSpec(
                voiced      = spec.voiced,
                closure_ms  = spec.closure_ms,
                formants    = dithered_formants,
                bandwidths  = spec.bandwidths,
                noise_mix   = spec.noise_mix,
                duration_ms = dur,
            )
            n = max(1, int(self.fs * dur / 1000))
            sequence.append((dithered_spec, n))

        source = self._build_source(sequence)
        audio  = self._cascade_resonators(source, sequence)

        # Normalise
        mx = np.max(np.abs(audio))
        if mx > 0:
            audio /= mx
        audio *= 0.9
        return audio


# ── WAV output ────────────────────────────────────────────────────────────────

def save_wav(audio: "np.ndarray", path: str, fs: int) -> None:
    """Write float64 audio array to a 16-bit PCM mono WAV file."""
    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(struct.pack(f'<{len(pcm)}h', *pcm))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Axiom Groove TTS — pure formant synthesis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 axiom_groove_tts.py "hello world" -o hello.wav
  python3 axiom_groove_tts.py "hello world" --play
  python3 axiom_groove_tts.py --phonemes "HH AH L OW"
  python3 axiom_groove_tts.py "test speech" --f0 100
  python3 axiom_groove_tts.py "test speech" --fs 22050 -o out.wav
""",
    )
    p.add_argument("text",        nargs="?",  default="",
                   help="Text to synthesize")
    p.add_argument("-o", "--out", default="groove_tts_out.wav",
                   help="Output WAV path (default groove_tts_out.wav)")
    p.add_argument("--phonemes",  default="",
                   help="Direct ARPABET input, e.g. 'HH AH L OW'")
    p.add_argument("--f0",  type=float, default=120.0,
                   help="Fundamental frequency Hz (default 120 — baritone)")
    p.add_argument("--fs",  type=int,   default=16000,
                   help="Sample rate Hz (default 16000)")
    p.add_argument("--play", action="store_true",
                   help="Play audio via sounddevice after synthesis")
    p.add_argument("--emotion", default="neutral",
                   choices=list(EMOTION_PROFILES),
                   help="Emotion profile → constitutional distance + dither + rate")
    p.add_argument("--groove-displacement", type=float, default=0.0, metavar="D",
                   help="GrooveAgent articulator displacement (0–1.2). Higher = "
                        "more precise speech → lower effective constitutional distance")
    p.add_argument("--dither", default="auto",
                   choices=["off","low","med","high","auto"],
                   help="SRD dither level (auto = from emotion profile)")
    p.add_argument("--list-phonemes", action="store_true",
                   help="Print ARPABET table and exit")
    args = p.parse_args()

    if args.list_phonemes:
        print("\n  ARPABET phoneme table:\n")
        for name, spec in sorted(PHONEME_TABLE.items()):
            src = "voiced" if spec.voiced else "noise "
            dur = f"{spec.duration_ms}ms"
            f12 = f"F1={spec.formants[0]:4d} F2={spec.formants[1]:4d}"
            print(f"  {name:<5} {src}  {dur:6}  {f12}")
        return 0

    if not _NUMPY:
        print("  numpy required:  pip install numpy")
        return 1

    # Resolve emotion + dither
    emotion   = EMOTION_PROFILES[args.emotion]
    dither_map = {"off": DITHER_OFF, "low": DITHER_LOW,
                  "med": DITHER_MED, "high": DITHER_HIGH,
                  "auto": emotion.dither}
    dither = dither_map[args.dither]
    trace  = SpeechTrace(emotion, groove_displacement=args.groove_displacement)

    print("═" * 60)
    print("  AXIOM Groove TTS  |  Klatt + SRD dither + Speech Trace")
    print(f"  F0={args.f0:.0f}Hz  |  fs={args.fs}Hz  |  emotion={args.emotion}")
    print(f"  CD={trace.cd:.2f}  |  rate×{emotion.rate_scale:.2f}  |  "
          f"dither F={dither.formant_jitter:.3f} D={dither.duration_jitter:.2f}")
    print("═" * 60)

    if args.phonemes:
        phonemes = args.phonemes.upper().split()
        print(f"  Phonemes : {' '.join(phonemes)}")
        known = [ph for ph in phonemes if _strip_stress(ph) in PHONEME_TABLE]
    elif args.text:
        word_phones = text_to_word_phonemes(args.text)
        phonemes = apply_trace_to_phonemes(word_phones, trace)
        print(f"  Text     : {args.text!r}")
        print(f"  Trace    : {' '.join(phonemes)}")
        known = [ph for ph in phonemes if _strip_stress(ph) in PHONEME_TABLE]
    else:
        print("  Provide text or --phonemes.  Use --help for examples.")
        return 1

    unknown = [ph for ph in phonemes if _strip_stress(ph) not in PHONEME_TABLE]
    if unknown:
        print(f"  Skipped  : {unknown}")

    if not known:
        print("  No recognisable phonemes — aborting.")
        return 1

    synth = KlattSynth(fs=args.fs, f0=args.f0,
                       dither=dither, f0_variation=emotion.f0_variation)
    audio = synth.synthesize(known, rate_scale=emotion.rate_scale)

    dur_s = len(audio) / args.fs
    save_wav(audio, args.out, args.fs)
    print(f"  Wrote    : {args.out}  ({dur_s:.2f}s, {len(audio)} samples)")

    if args.play:
        try:
            import sounddevice as sd
            sd.play(audio, args.fs)
            sd.wait()
        except ImportError:
            print("  --play requires sounddevice:  pip install sounddevice")
        except Exception as e:
            print(f"  Play error: {e}")

    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
