"""AXIOM Colab/Jupyter adapter.

One-stop training-data loader for the AXIOM fine-tune notebooks.
Replaces the brittle `from google.colab import files` + `files.upload()`
pattern in Cell 3 of `axiom_qwen_finetune.ipynb` and
`axiom_tinyllama_finetune.ipynb` with a single function that:

  * Works in Colab AND vanilla Jupyter AND VS Code / IDE kernels
  * Accepts data from local upload, Google Drive, HuggingFace hub,
    raw URL, bundled sample, or any direct path
  * Auto-detects the three input formats AXIOM training data ships
    in ({messages}, {instruction, input, output}, {text})
  * Emits informative errors instead of bare assertions

Public API:

    from notebooks.axiom_colab import load_training_data

    # auto: tries Drive first, falls back to picker, then sample
    examples = load_training_data()

    # explicit sources
    examples = load_training_data("upload")
    examples = load_training_data("drive:/data/train.jsonl")
    examples = load_training_data("hf:tatsu-lab/alpaca#train[:500]")
    examples = load_training_data("https://example.com/data.jsonl")
    examples = load_training_data("sample")
    examples = load_training_data("/local/path.jsonl")

    # output shape — "messages" (default) or "text" (TinyLlama ChatML)
    examples = load_training_data("sample", output_format="text")
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_SYSTEM_PROMPT = (
    "You are axiom-dev. You follow constitutional reasoning — "
    "every response must demonstrate these behaviors:\n"
    "1. CANNOT_MUTATE fields are sacred — if asked to change one, "
    "refuse with the field name and why\n"
    "2. Uncertainty floor is 0.15 — never state confidence below "
    "this, say \"I need clarification on X\"\n"
    "3. Clarification IS completion — asking the right question is "
    "a valid response\n"
    "4. Test-first — write BLOCKED/PASSED tests before implementation\n"
    "5. Measurable constraints — every bound uses >=, <=, ==, not "
    "vague terms\n"
    "6. Sign everything — HMAC-SHA256 on packets, supply chain hash "
    "on files\n"
    "7. Adversarial check — consider what RedAgent would exploit "
    "before shipping\n"
    "8. Bug citations — reference BUG-0XX IDs when you spot known "
    "patterns\n"
    "9. Guard specs — write .axiom files with AGENT/VERSION/"
    "CONSTRAINT/PROCESS/CHECK/SUCCESS\n"
    "10. Show reasoning — include \"because\", constraint references, "
    "confidence bounds"
)

_SAMPLE_PATH = Path(__file__).parent / "sample_training_data.jsonl"
_DRIVE_MOUNT = "/content/drive"


# ── Environment detection ────────────────────────────────────────────


def _in_colab() -> bool:
    return "google.colab" in sys.modules or _can_import("google.colab")


def _can_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


# ── Source resolvers — each returns a Path to a local jsonl file ─────


def _resolve_source(source: Optional[str]) -> Path:
    """Turn a source spec into a local Path to a jsonl file."""
    if source is None:
        return _auto_source()
    if source == "sample":
        return _sample_source()
    if source == "upload":
        return _upload_source()
    if source.startswith("drive:"):
        return _drive_source(source[len("drive:"):])
    if source.startswith("hf:"):
        return _hf_source(source[len("hf:"):])
    if source.startswith(("http://", "https://")):
        return _url_source(source)
    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"training data not found at {path}. "
            f"Use one of: load_training_data(), load_training_data('sample'), "
            f"load_training_data('upload'), load_training_data('drive:/path'), "
            f"load_training_data('hf:owner/dataset'), or pass an existing path."
        )
    return path


def _auto_source() -> Path:
    """Pick the best source for the detected environment."""
    if Path(_DRIVE_MOUNT, "MyDrive", "axiom_training_data.jsonl").exists():
        return Path(_DRIVE_MOUNT, "MyDrive", "axiom_training_data.jsonl")
    if _in_colab():
        return _upload_source()
    if _SAMPLE_PATH.exists():
        print(f"[axiom_colab] no source given — using bundled "
              f"sample at {_SAMPLE_PATH.name}. "
              f"Pass load_training_data('upload') / 'drive:/...' / "
              f"'hf:...' / '/path' to override.")
        return _SAMPLE_PATH
    raise RuntimeError(
        "no training data found — no Drive, not in Colab, "
        "and bundled sample is missing. Specify a source explicitly."
    )


def _sample_source() -> Path:
    if not _SAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"bundled sample missing at {_SAMPLE_PATH}. "
            f"Reinstall axiom or pass an explicit source."
        )
    return _SAMPLE_PATH


def _upload_source() -> Path:
    """Colab file picker. Falls back to a clear error outside Colab."""
    if not _in_colab():
        raise RuntimeError(
            "load_training_data('upload') only works inside Google Colab. "
            "In Jupyter / IDE notebooks, pass a path directly: "
            "load_training_data('/path/to/data.jsonl')."
        )
    from google.colab import files  # type: ignore
    print("Upload one .jsonl file (messages / instruction-output / "
          "ChatML-text format all accepted)...")
    uploaded = files.upload()
    if not uploaded:
        raise RuntimeError(
            "upload cancelled — no file received. "
            "Re-run the cell and pick a file, or use "
            "load_training_data('sample') to test the rest of "
            "the notebook."
        )
    name = next(iter(uploaded))
    return Path(name).resolve()


def _drive_source(path: str) -> Path:
    """Mount Drive (if needed) and resolve a path inside it."""
    if not _in_colab():
        raise RuntimeError(
            "load_training_data('drive:...') requires Google Colab."
        )
    from google.colab import drive  # type: ignore
    if not Path(_DRIVE_MOUNT).exists() or not any(Path(_DRIVE_MOUNT).iterdir()):
        drive.mount(_DRIVE_MOUNT)
    p = path.lstrip("/")
    candidates = [
        Path(_DRIVE_MOUNT) / p,
        Path(_DRIVE_MOUNT) / "MyDrive" / p,
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"drive path not found. Looked in: "
        f"{', '.join(str(c) for c in candidates)}"
    )


def _hf_source(spec: str) -> Path:
    """Download a HuggingFace hub dataset to a local jsonl file."""
    if not _can_import("datasets"):
        raise RuntimeError(
            "hf source requires `datasets` — install with "
            "`pip install datasets`."
        )
    from datasets import load_dataset  # type: ignore
    if "#" in spec:
        repo, split = spec.split("#", 1)
    else:
        repo, split = spec, "train"
    print(f"[axiom_colab] loading hf:{repo} split={split}...")
    ds = load_dataset(repo, split=split)
    out = Path("/tmp") / f"hf_{repo.replace('/', '_')}_{split}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for row in ds:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def _url_source(url: str) -> Path:
    """Fetch a raw .jsonl URL to a tempfile."""
    out = Path("/tmp") / f"url_{abs(hash(url))}.jsonl"
    print(f"[axiom_colab] fetching {url}...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        out.write_bytes(resp.read())
    return out


# ── Format detection + normalisation ─────────────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    """Strict JSONL read with per-line error reporting."""
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path}:{lineno}: malformed JSON ({e.msg}). "
                    f"Line content: {line[:120]}"
                ) from None
    if not rows:
        raise ValueError(f"{path}: file is empty (no JSON lines).")
    return rows


def _detect_shape(row: dict) -> str:
    if "messages" in row and isinstance(row["messages"], list):
        return "messages"
    if "text" in row and isinstance(row["text"], str) \
            and "<|im_start|>" in row["text"]:
        return "chatml_text"
    if "instruction" in row or "input" in row \
            or "output" in row or "response" in row:
        return "raw"
    raise ValueError(
        f"unrecognised row shape — keys: {sorted(row.keys())[:8]}. "
        f"Expected one of: {{messages}}, {{instruction, input, output}}, "
        f"or {{text: '<|im_start|>...'}}"
    )


def _raw_to_messages(ex: dict, system_prompt: str) -> list[dict]:
    user_msg = ex.get("instruction", "")
    if ex.get("input"):
        user_msg = f"{user_msg}\n\n{ex['input']}" if user_msg else ex["input"]
    assistant = ex.get("output") or ex.get("response") or ""
    return [
        {"role": "system",    "content": system_prompt},
        {"role": "user",      "content": user_msg},
        {"role": "assistant", "content": assistant},
    ]


def _chatml_text_to_messages(text: str) -> list[dict]:
    msgs: list[dict] = []
    for block in re.split(r"<\|im_start\|>", text):
        block = block.strip()
        if not block:
            continue
        block = block.replace("<|im_end|>", "").strip()
        try:
            nl = block.index("\n")
        except ValueError:
            continue
        role = block[:nl].strip()
        content = block[nl + 1:].strip()
        msgs.append({"role": role, "content": content})
    return msgs


def _messages_to_chatml_text(messages: Iterable[dict]) -> str:
    parts = []
    for m in messages:
        parts.append(
            f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>"
        )
    return "\n".join(parts)


# ── Public API ───────────────────────────────────────────────────────


def load_training_data(
    source: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    output_format: str = "messages",
    dedupe: bool = True,
    min_output_chars: int = 30,
) -> list[dict]:
    """Load AXIOM training data from any source, return Qwen-ready ChatML.

    See module docstring for source string syntax.

    Args:
      source:           source spec or None for auto-pick
      system_prompt:    override the default constitutional prompt
      output_format:    "messages" (Qwen, default) or "text" (TinyLlama ChatML)
      dedupe:           drop duplicate user prompts (raw format only)
      min_output_chars: drop examples whose assistant output is shorter

    Returns:
      list of {"messages": [...]}  (output_format="messages"), or
      list of {"text": "<|im_start|>..."}  (output_format="text")

    Raises ValueError on malformed JSON, FileNotFoundError on missing
    paths, RuntimeError on environment mismatches.
    """
    if output_format not in ("messages", "text"):
        raise ValueError(
            f"output_format must be 'messages' or 'text', got {output_format!r}"
        )
    prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    path = _resolve_source(source)
    rows = _read_jsonl(path)
    shape = _detect_shape(rows[0])
    print(f"[axiom_colab] {len(rows)} rows from {path.name} "
          f"(detected: {shape})")

    if shape == "messages":
        examples = [{"messages": r["messages"]} for r in rows
                    if r.get("messages")]
    elif shape == "chatml_text":
        examples = [{"messages": _chatml_text_to_messages(r["text"])}
                    for r in rows]
    else:  # raw
        normalised = [r for r in rows
                      if (r.get("output") or r.get("response", "")).strip()
                      and len((r.get("output") or r.get("response") or ""))
                          >= min_output_chars]
        if dedupe:
            seen: set[str] = set()
            kept = []
            for r in normalised:
                key = r.get("instruction", "")
                if key in seen:
                    continue
                seen.add(key)
                kept.append(r)
            normalised = kept
        examples = [{"messages": _raw_to_messages(r, prompt)}
                    for r in normalised]
        print(f"[axiom_colab]   filtered → {len(examples)} examples")

    if not examples:
        raise ValueError(
            f"no usable examples after filtering {path}. "
            f"Check that rows have non-empty output / response fields."
        )

    if output_format == "text":
        examples = [{"text": _messages_to_chatml_text(ex["messages"])}
                    for ex in examples]
    return examples
