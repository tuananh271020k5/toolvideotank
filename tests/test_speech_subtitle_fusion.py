"""Regression tests for speech_subtitle_fusion and elastic dubbing timing."""
import os
import sys
import tempfile
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

from services import speech_subtitle_fusion as fusion_mod  # noqa: E402
from services.speech_subtitle_fusion import _Fusion  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _w(start, end, text, prob=0.9):
    return {"start": start, "end": end, "text": text, "prob": prob}


def _speech(words, vad, mode="full"):
    return SimpleNamespace(
        audio=None, vad=vad, words=words, asr_segments=[], model=object(),
        is_gpu=False, mode=mode, language="zh", report={},
    )


def _run_fusion(ocr, words, vad, rescan_text=""):
    fusion = _Fusion(video_path="", ocr_region="bottom", speech=_speech(words, vad))
    fusion._rescan_ocr = lambda s, e: rescan_text
    fusion._targeted_asr_batch = lambda pieces: [("", []) for _ in pieces]
    return fusion.run(ocr)


def test_ocr_cue_is_retimed_to_voice():
    ocr = [{"start": 1.0, "end": 3.5, "text": "你今天去哪里"}]
    words = [_w(1.2, 1.5, "你"), _w(1.5, 1.9, "今天"), _w(1.9, 2.2, "去"), _w(2.2, 2.8, "哪里")]
    cues, ctx = _run_fusion(ocr, words, [(1.15, 2.85)])
    assert len(cues) == 1
    assert cues[0]["text"] == "你今天去哪里"  # OCR text wins
    assert abs(cues[0]["start"] - 1.2) < 1e-6 and abs(cues[0]["end"] - 2.8) < 1e-6
    assert cues[0]["timing_source"] == "asr_words"


def test_missed_sentence_is_filled_from_asr():
    ocr = [{"start": 1.0, "end": 2.0, "text": "你好"}, {"start": 6.0, "end": 7.0, "text": "再见"}]
    words = [
        _w(1.1, 1.8, "你好"),
        _w(3.0, 3.4, "我们"), _w(3.4, 3.9, "快走"), _w(3.9, 4.3, "吧"),
        _w(6.1, 6.8, "再见"),
    ]
    cues, ctx = _run_fusion(ocr, words, [(1.1, 1.8), (3.0, 4.3), (6.1, 6.8)])
    texts = [c["text"] for c in cues]
    assert texts == ["你好", "我们快走吧", "再见"]
    filled = cues[1]
    assert filled["source"] == "asr_fill" and abs(filled["start"] - 3.0) < 1e-6
    assert ctx["report"]["added_from_asr"] == 1
    # Pauses preserved between cues.
    assert cues[0]["pause_after"] > 1.0


def test_missed_sentence_prefers_ocr_rescan_text():
    ocr = [{"start": 1.0, "end": 2.0, "text": "你好"}]
    words = [_w(1.1, 1.8, "你好"), _w(3.0, 3.6, "我门"), _w(3.6, 4.0, "走")]
    cues, ctx = _run_fusion(ocr, words, [(1.1, 1.8), (3.0, 4.0)], rescan_text="我们走")
    assert [c["text"] for c in cues] == ["你好", "我们走"]
    assert cues[1]["source"] == "ocr_rescan"


def test_uncovered_speech_is_reported():
    ocr = [{"start": 1.0, "end": 2.0, "text": "你好"}]
    words = [_w(1.1, 1.8, "你好")]
    cues, ctx = _run_fusion(ocr, words, [(1.1, 1.8), (5.0, 6.5)])
    assert len(cues) == 1
    assert ctx["report"]["uncovered_speech"] == [{"start": 5.0, "end": 6.5}]


def test_padding_word_from_next_sentence_not_swallowed():
    ocr = [{"start": 1.0, "end": 2.0, "text": "你好"}]
    # "快" sits within the 0.35 s pad but shares no character with "你好".
    words = [_w(1.1, 1.8, "你好"), _w(2.1, 2.4, "快"), _w(2.4, 2.9, "跑啊")]
    cues, _ = _run_fusion(ocr, words, [(1.1, 2.9)])
    assert [c["text"] for c in cues] == ["你好", "快跑啊"]
    assert cues[0]["end"] <= cues[1]["start"]


def test_no_overlap_after_finalize():
    cues = _Fusion._finalize([
        {"start": 1.0, "end": 3.0, "text": "a"},
        {"start": 2.5, "end": 4.0, "text": "b"},
    ])
    assert cues[0]["end"] < cues[1]["start"]


def test_snap_keeps_edge_inside_continuous_speech():
    # One VAD interval spans two sentences: start inside speech stays put.
    start, end, snapped = _Fusion._snap_to_vad(5.0, 7.0, [(1.0, 7.1)])
    assert start == 5.0 and abs(end - 7.18) < 1e-6 and snapped
    # Onset slightly before the subtitle appears -> pulled earlier.
    start, end, _ = _Fusion._snap_to_vad(2.0, 3.0, [(1.8, 2.9)])
    assert abs(start - 1.75) < 1e-6 and abs(end - 2.98) < 1e-6


def test_gap_asr_runs_once_and_maps_words_back():
    audio = np.zeros(16000 * 20, dtype=np.float32)
    speech = _speech([], [])
    speech.audio = audio
    fusion = _Fusion(video_path="", ocr_region="bottom", speech=speech)
    calls = []

    def fake_transcribe(model, path, **kwargs):
        calls.append(path)
        # Piece 1 (3.0-4.0) is placed at 0.0 in the joined clip (clip start 2.8),
        # piece 2 (10.0-11.0) at 1.4 + 1.0 = 2.4 (clip start 9.8).
        return [_w(0.3, 0.7, "快走"), _w(2.7, 3.1, "等等"), _w(3.1, 3.4, "我")], []

    original = fusion_mod._transcribe_words
    fusion_mod._transcribe_words = fake_transcribe
    try:
        results = fusion._targeted_asr_batch([(3.0, 4.0), (10.0, 11.0)])
    finally:
        fusion_mod._transcribe_words = original
    assert len(calls) == 1
    assert results[0][0] == "快走" and abs(results[0][1][0]["start"] - 3.1) < 1e-6
    assert results[1][0] == "等等我" and abs(results[1][1][0]["start"] - 10.1) < 1e-6


def test_rescan_windows_are_merged():
    fusion = _Fusion(video_path="x.mp4", ocr_region="bottom", speech=_speech([], []))
    scanned = []

    def fake_scan(start, end):
        scanned.append((start, end))
        fusion._rescan_windows.append((start, end))
        fusion._rescan_segments.append({"start": 3.0, "end": 4.0, "text": "我们走"})

    fusion._scan_window = fake_scan
    fusion._prefetch_rescans([(3.0, 4.0), (5.0, 5.5), (20.0, 21.0)])
    assert len(scanned) == 2  # first two merged (gap < 2 s)
    assert fusion._rescan_ocr(3.0, 4.0) == "我们走"
    assert len(scanned) == 2  # served from cache


# ---------------------------------------------------------------- elastic
def _tone(path, seconds, rate=22050):
    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(data.tobytes())
    return path


def _dur(path):
    with wave.open(path, "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def test_sync_mode_labels():
    from workflows.voice_workflow import VoiceWorkflow
    norm = VoiceWorkflow._normalize_sync_mode
    assert norm("Thông minh") == "elastic"
    assert norm("Ưu tiên Timeline") == "timeline"
    assert norm("Ép khớp thời gian") == "force"
    assert norm("Tắt") == "off"
    assert norm("smart") == "smart"


def test_elastic_timing_fits_within_limits():
    from workflows.voice_workflow import VoiceWorkflow
    wf = VoiceWorkflow.__new__(VoiceWorkflow)
    from services import EngineRuntime
    wf.engine_runtime = EngineRuntime()
    tmp = tempfile.mkdtemp()
    segments = [
        {"start": 0.0, "end": 2.0, "text": "a"},   # 2.4 s voice -> speed up ~1.1x (borrow gap)
        {"start": 2.5, "end": 4.0, "text": "b"},   # 1.3 s voice -> natural
        {"start": 4.2, "end": 5.0, "text": "c"},   # 2.0 s voice -> capped at 1.35x + fade guard
    ]
    wavs = [_tone(os.path.join(tmp, f"{i}.wav"), d) for i, d in enumerate((2.4, 1.3, 2.0))]
    out = wf._apply_elastic_timing(segments=segments, wavs=wavs, tmp_dir=tmp, voice_speed=1.0)
    for seg in segments:
        assert 0.9 <= seg["elastic_speed"] <= 1.35
    assert segments[2]["elastic_speed"] == 1.35
    # Sentence 1 must end before sentence 2 starts, sentence 2 before 3.
    assert segments[0]["start"] + _dur(out[0]) <= segments[1]["start"] + 0.01
    assert segments[1]["start"] + _dur(out[1]) <= segments[2]["start"] + 0.01
    # Tempo smoothing: neighbours differ by at most 0.15 + epsilon.
    assert abs(segments[0]["elastic_speed"] - segments[1]["elastic_speed"]) <= 0.151


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
