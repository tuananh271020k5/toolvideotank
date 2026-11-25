"""Unit tests for Dual OCR scanning, independent dynamic blur masks, and subtitle positioning."""

import os
from unittest.mock import MagicMock, patch
import pytest

from app.models.subtitle_segment import SubtitleSegment
from app.services.video_export_service import (
    SubtitleRenderConfig,
    VideoExportRequest,
    VideoExportService,
    generate_ass_script,
)
from app.services.subtitle_merge_service import SubtitleMergeService


def test_subtitle_segment_dual_ocr_fields():
    seg1 = SubtitleSegment(id=1, start=0.0, end=2.0, text="Bottom text", ocr_box_index=1, position="bottom")
    assert seg1.ocr_box_index == 1
    assert seg1.position == "bottom"

    seg2 = SubtitleSegment.from_dict({
        "id": 2,
        "start": 2.5,
        "end": 4.5,
        "text": "Top text",
        "ocr_box_index": 2,
        "position": "top",
    })
    assert seg2.ocr_box_index == 2
    assert seg2.position == "top"


def test_sanitize_cue_timings_preserves_dual_boxes():
    # Box 1 and Box 2 can overlap in time without truncating each other
    cues = [
        {"id": 1, "start": 1.0, "end": 4.0, "text": "Khung đáy", "ocr_box_index": 1, "position": "bottom"},
        {"id": 2, "start": 2.0, "end": 3.5, "text": "Khung đỉnh", "ocr_box_index": 2, "position": "top"},
    ]
    cleaned = SubtitleMergeService.sanitize_cue_timings(cues)
    assert len(cleaned) == 2
    # Seg 1 should NOT be truncated by Seg 2 since they are in different boxes
    assert cleaned[0]["end"] == 4.0
    assert cleaned[1]["end"] == 3.5


def test_generate_ass_script_dual_ocr_rects():
    vid_w, vid_h = 1080, 1920
    rect1 = (0.08, 0.70, 0.84, 0.10)  # Center Y = (0.70 + 0.05) * 1920 = 1440
    rect2 = (0.08, 0.10, 0.84, 0.08)  # Center Y = (0.10 + 0.04) * 1920 = 269 (rounded: 269)

    seg_bot = SubtitleSegment(
        id=1,
        start=0.0,
        end=2.0,
        text="Câu thoại ở đáy",
        translated_text="Câu thoại ở đáy",
        ocr_box_index=1,
        position="bottom",
    )
    seg_top = SubtitleSegment(
        id=2,
        start=2.5,
        end=5.0,
        text="Câu thoại ở đỉnh",
        translated_text="Câu thoại ở đỉnh",
        ocr_box_index=2,
        position="top",
    )

    cfg = SubtitleRenderConfig(background_box=True, mask_mode="blur")
    ass_content, cnt = generate_ass_script(
        [seg_bot, seg_top],
        vid_w=vid_w,
        vid_h=vid_h,
        cfg=cfg,
        user_rect=rect1,
        user_rect_2=rect2,
        mask_mode="blur",
        dynamic_mask=True,
    )
    assert cnt == 2
    # seg_bot positioned at cy=1440
    assert r"\pos(540,1440)" in ass_content
    # seg_top positioned at cy=269
    assert r"\pos(540,269)" in ass_content


def test_export_video_dual_dynamic_blur_filter_script(tmp_path):
    svc = VideoExportService(temp_dir=str(tmp_path))

    rect1 = (0.08, 0.70, 0.84, 0.10)
    rect2 = (0.08, 0.09, 0.84, 0.08)

    seg_bot = SubtitleSegment(
        id=1,
        start=1.0,
        end=3.0,
        text="Bottom cue",
        translated_text="Bottom cue vi",
        ocr_box_index=1,
        position="bottom",
    )
    seg_top = SubtitleSegment(
        id=2,
        start=4.0,
        end=6.0,
        text="Top cue",
        translated_text="Top cue vi",
        ocr_box_index=2,
        position="top",
    )

    orig_vid = tmp_path / "in.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "in.wav"
    final_aud.write_bytes(b"dummy_audio")
    out_vid = tmp_path / "out.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[seg_bot, seg_top],
        output_video_path=str(out_vid),
        enable_blur_mask=True,
        mask_mode="blur",
        blur_rect=rect1,
        blur_rect_2=rect2,
        dynamic_mask=True,
    )

    captured_filter_script = []

    def mock_popen(cmd, **kwargs):
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
            script_path = cmd[idx + 1]
            if os.path.isfile(script_path):
                with open(script_path, "r", encoding="utf-8") as f:
                    captured_filter_script.append(f.read())
        elif "-/filter_complex" in cmd:
            idx = cmd.index("-/filter_complex")
            script_path = cmd[idx + 1]
            if os.path.isfile(script_path):
                with open(script_path, "r", encoding="utf-8") as f:
                    captured_filter_script.append(f.read())
        out_vid.write_bytes(b"final_video_output")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.return_value = b""
        mock_proc.stderr.readline.return_value = b""
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        return mock_proc

    with (
        patch("app.services.video_export_service.measure_media_duration", return_value=10.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)
        assert res.status == "success"

    assert len(captured_filter_script) == 1
    filter_script = captured_filter_script[0]

    # Must have bottom blur crop with enable expression between 1.00 and 3.00
    assert "v_crop_strip" in filter_script
    assert "between(t,1.00,3.00)" in filter_script
    # Must have top blur crop with enable expression between 4.00 and 6.00
    assert "v_crop_top" in filter_script
    assert "between(t,4.00,6.00)" in filter_script
    # Both crops use boxblur
    assert "boxblur=" in filter_script
