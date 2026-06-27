"""
Distill SFT data for the 135M extractor from a NIM teacher (llama-3.3-70b).

The teacher invents fully-synthetic medical records AND their verbatim field
extraction; we keep only field values that are actually grounded in the generated
document, then emit chat-format pairs whose system/user prompts match what the
135M sees at inference. Fine-tuning on this teaches the small model WHICH fields
matter and to copy values verbatim — the recall gap grammar-constraining can't fix.

    python finetune/gen_sft_data.py --count 200 --out finetune/data/medical_extraction_sft.jsonl

Needs NVIDIA_API_KEY / NIM_API_KEY. Note: the extractor is trained to pull ALL
present fields (identifiers included) — de-identification is the governance layer's
job downstream, not the model's.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backends import extract_system_prompt          # noqa: E402
from governed_extractor import _norm, load_schema    # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

DOC_TYPES = ["discharge summary", "progress note", "emergency department note",
             "radiology report", "outpatient clinic note", "operative note"]
THEMES = ["cardiology", "endocrinology", "pulmonology", "orthopedics",
          "nephrology", "infectious disease", "oncology", "gastroenterology"]


def _grounded(value, text: str) -> bool:
    ntext = _norm(text)
    vals = value if isinstance(value, list) else [value]
    return any(_norm(v) and _norm(v) in ntext for v in vals if str(v).strip())


def gen_one(client, model, fields, doc_type, theme, temperature):
    sys_msg = (
        "You generate fully-synthetic medical-record training data. No real people. "
        "Return ONLY JSON: {\"document\": \"<record text>\", \"fields\": {<name>: <verbatim value>}}. "
        "Every value in fields MUST appear verbatim in document. Omit fields not present. "
        "Use only these field names: " + ", ".join(fields) + "."
    )
    user = (
        f"Write a realistic but synthetic {doc_type} in {theme}. Vary the patient, dates, "
        f"medications, and findings. Include most clinical fields and usually some identifiers; "
        f"occasionally leave a few fields out. Then extract the fields."
    )
    last = None
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=900, temperature=temperature,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": user}],
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as exc:  # rate limits / transient 5xx / partial JSON
            last = exc
            time.sleep(min(2 ** attempt, 20))
    raise last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--out", default=str(HERE / "data" / "medical_extraction_sft.sample.jsonl"))
    ap.add_argument("--model", default="meta/llama-3.3-70b-instruct")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from openai import OpenAI
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NIM_API_KEY")
    if not key:
        sys.exit("NVIDIA_API_KEY / NIM_API_KEY not set")
    client = OpenAI(api_key=key, base_url="https://integrate.api.nvidia.com/v1")

    schema = load_schema(ROOT / "policy" / "medical_extraction.schema.json")
    fields = list(schema["fields"].keys())
    valid = set(fields)
    system_prompt = extract_system_prompt(fields)

    rnd = random.Random(args.seed)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    kept = dropped_field = skipped = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for i in range(args.count):
            doc_type = DOC_TYPES[i % len(DOC_TYPES)]
            theme = THEMES[(i * 3 + rnd.randint(0, 7)) % len(THEMES)]
            try:
                obj = gen_one(client, args.model, fields, doc_type, theme, 0.4 + 0.4 * rnd.random())
            except Exception as exc:
                print(f"  [{i}] teacher error: {str(exc)[:80]}"); skipped += 1; continue

            doc = (obj.get("document") or "").strip()
            raw = obj.get("fields") or {}
            clean = {}
            for k, v in raw.items():
                if k not in valid or v in (None, "", [], {}):
                    continue
                if _grounded(v, doc):
                    clean[k] = v
                else:
                    dropped_field += 1
            if not doc or len(clean) < 2:
                skipped += 1
                print(f"  [{i}] skip ({doc_type}/{theme}): too few grounded fields")
                continue

            example = {"messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"DOCUMENT:\n{doc}\n\nJSON:"},
                {"role": "assistant", "content": json.dumps(clean, ensure_ascii=False)},
            ]}
            fh.write(json.dumps(example, ensure_ascii=False) + "\n")
            kept += 1
            print(f"  [{i}] ok ({doc_type}/{theme}): {len(clean)} fields -> {sorted(clean)}")

    print(f"\nwrote {kept} examples to {out_path}")
    print(f"  ungrounded field values dropped: {dropped_field}")
    print(f"  records skipped: {skipped}")


if __name__ == "__main__":
    main()
