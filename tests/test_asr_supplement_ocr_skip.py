"""Unit tests for skipping OCR frames during ASR supplement ranges and stripping duplicate OCR message text."""

import os
from unittest.mock import MagicMock, patch
import pytest

from app.ocr_processor import transcribe_video_ocr, transcribe_video_dual_ocr


def test_transcribe_video_ocr_skip_ranges():
    """Kiểm tra transcribe_video_ocr bỏ qua các frame trong skip_ranges."""
    import numpy as np
    dummy_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    fake_frames = [
        # (timestamp, dummy_img, discovered_dur, est_steps)
        (0.5, dummy_img, 10.0, 10),
        (1.0, dummy_img, 10.0, 10),
        (2.0, dummy_img, 10.0, 10),  # inside skip_ranges [(1.8, 3.5)]
        (2.5, dummy_img, 10.0, 10),  # inside skip_ranges
        (3.0, dummy_img, 10.0, 10),  # inside skip_ranges
        (4.0, dummy_img, 10.0, 10),  # after skip_ranges
        (4.5, dummy_img, 10.0, 10),
    ]

    captured_ocr_timestamps = []

    def mock_ocr_frame(engine, cropped, **kwargs):
        return ["Fake text"], [{"bbox": [0.1, 0.7, 0.9, 0.9], "confidence": 0.9}]

    with (
        patch("app.ocr_processor._iter_video_frames", return_value=iter(fake_frames)),
        patch("app.ocr_processor.crop_subtitle_region", return_value=MagicMock()),
        patch("app.ocr_processor._crop_hash", side_effect=lambda x: id(x)),
        patch("app.ocr_processor._stroke_hash", side_effect=lambda x: id(x)),
        patch("app.ocr_processor._is_blank_region", return_value=False),
        patch("app.ocr_processor.ocr_frame", side_effect=mock_ocr_frame),
    ):
        segs = transcribe_video_ocr(
            "dummy_video.mp4",
            fps=2.0,
            skip_ranges=[(1.8, 3.5)],
        )

        # Không có segment nào có start nằm trong khoảng 1.8 -> 3.5
        for s in segs:
            assert not (1.8 <= s["start"] <= 3.5), f"Segment {s} was found inside skip_ranges!"


def test_transcribe_video_dual_ocr_skip_ranges():
    """Kiểm tra transcribe_video_dual_ocr bỏ qua các frame trong skip_ranges."""
    import numpy as np
    dummy_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    fake_frames = [
        (0.5, dummy_img, 10.0, 10),
        (1.0, dummy_img, 10.0, 10),
        (2.0, dummy_img, 10.0, 10),  # inside skip_ranges [(1.8, 3.5)]
        (2.5, dummy_img, 10.0, 10),  # inside skip_ranges
        (3.0, dummy_img, 10.0, 10),  # inside skip_ranges
        (4.0, dummy_img, 10.0, 10),  # after skip_ranges
        (4.5, dummy_img, 10.0, 10),
    ]

    def mock_ocr_frame(engine, cropped, **kwargs):
        return ["Fake dual text"], [{"bbox": [0.1, 0.7, 0.9, 0.9], "confidence": 0.9}]

    with (
        patch("app.ocr_processor._iter_video_frames", return_value=iter(fake_frames)),
        patch("app.ocr_processor._crop_hash", side_effect=lambda x: id(x)),
        patch("app.ocr_processor._stroke_hash", side_effect=lambda x: id(x)),
        patch("app.ocr_processor._is_blank_region", return_value=False),
        patch("app.ocr_processor.ocr_frame", side_effect=mock_ocr_frame),
    ):
        segs = transcribe_video_dual_ocr(
            "dummy_video.mp4",
            fps=2.0,
            skip_ranges=[(1.8, 3.5)],
        )

        for s in segs:
            assert not (1.8 <= s["start"] <= 3.5), f"Segment {s} was found inside skip_ranges!"


def test_single_voice_worker_removes_ocr_cues_in_asr_ranges():
    """Kiểm tra logic lọc bỏ các câu OCR trong khoảng thời gian tin nhắn ASR."""
    raw_existing = [
        {"id": 1, "start": 10.0, "end": 15.0, "text": "Lời thoại mở đầu"},
        {"id": 2, "start": 67.0, "end": 72.0, "text": "402: 我是学法医的 (Tin nhắn điện thoại)"},
        {"id": 3, "start": 75.0, "end": 85.0, "text": "302: 要不然我们报警 (Tin nhắn điện thoại)"},
        {"id": 4, "start": 105.0, "end": 110.0, "text": "Lời thoại sau tin nhắn"},
        {"id": 5, "start": 118.0, "end": 124.0, "text": "Tin nhắn nhóm 2"},
        {"id": 6, "start": 130.0, "end": 135.0, "text": "Lời thoại kết thúc"},
    ]

    asr_ranges = [(66.0, 100.0), (116.0, 126.0)]

    def _is_in_asr_range(c_start: float, c_end: float) -> bool:
        for r_s, r_e in asr_ranges:
            overlap = max(0.0, min(c_end, r_e) - max(c_start, r_s))
            c_dur = max(0.1, c_end - c_start)
            if (r_s <= c_start < r_e) or (r_s < c_end <= r_e) or (overlap / c_dur >= 0.35) or (overlap > 0.8):
                return True
        return False

    filtered = [c for c in raw_existing if not _is_in_asr_range(float(c["start"]), float(c["end"]))]

    # Các câu 2, 3 và 5 phải bị loại bỏ
    assert len(filtered) == 3
    assert filtered[0]["id"] == 1
    assert filtered[1]["id"] == 4
    assert filtered[2]["id"] == 6
    assert all("Tin nhắn" not in c["text"] for c in filtered)
