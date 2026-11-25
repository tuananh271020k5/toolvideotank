"""Gender AI Subprocess Worker.

Runs GenderDetector in a separate Python process that has PyTorch,
SpeechBrain, and Transformers installed (e.g. .venv).
Can be called from a frozen PyInstaller bundle where PyTorch is excluded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Gender AI Inference Worker")
    parser.add_argument("--input", "-i", required=True, help="Path to input JSON file")
    parser.add_argument("--output", "-o", required=True, help="Path to output JSON file")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    if not os.path.isfile(input_path):
        print(f"[GenderWorker] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    audio_path = str(data.get("audio_path", "")).strip()
    attempts = list(data.get("attempts", []) or [])
    package_dir = str(data.get("package_dir", "")).strip()
    accept_confidence = float(data.get("accept_confidence", 0.50))

    if not audio_path or not os.path.isfile(audio_path):
        print(f"[GenderWorker] Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    if package_dir and os.path.isdir(package_dir):
        if package_dir not in sys.path:
            sys.path.insert(0, package_dir)

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        from gender_detector import GenderDetector
    except ImportError as e:
        print(f"[GenderWorker] Cannot import GenderDetector: {e}", file=sys.stderr)
        sys.exit(2)

    pkg_path = Path(package_dir)
    classifier_path = pkg_path / "gender_classifier.joblib"
    runtime_models = pkg_path / "runtime_models"

    try:
        import torch
        torch.set_num_threads(min(8, os.cpu_count() or 4))
    except Exception:
        pass

    detector = GenderDetector(
        gender_model_path=classifier_path,
        cache_dir=runtime_models,
        device="cpu",
        min_gender_confidence=accept_confidence,
    )

    raw_results = detector.detect_segments(audio_path, attempts)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_results or [], f, ensure_ascii=False, indent=2)

    print(f"[GenderWorker] Successfully processed {len(raw_results or [])} segments.")
    sys.exit(0)


if __name__ == "__main__":
    main()
