"""Unit tests kiểm tra tính năng mặt nạ che động và ôm sát phụ đề Trung cho luồng Tạo 1 giọng."""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, "ui"))

from app.models.subtitle_segment import SubtitleSegment
from app.services.video_export_service import (
    VideoExportService,
    SubtitleRenderConfig,
    VideoExportRequest,
    generate_ass_script,
)


def test_single_voice_tight_blur_mask_sizing(tmp_path):
    """Kiểm tra khi khung OCR vẽ rất rộng (1492x84px trên 1920x1080), dải làm mờ tự động ôm sát chữ Trung thực tế."""
    orig_vid = tmp_path / "test_orig.mp4"
    orig_vid.write_bytes(b"dummy")
    final_aud = tmp_path / "test_aud.wav"
    final_aud.write_bytes(b"dummy")
    out_vid = tmp_path / "test_out.mp4"

    # Segment có bbox chữ Trung thực tế: chiếm khoảng 600px ở giữa màn hình (x từ 0.35 đến 0.65, y từ 0.73 đến 0.77 - cao ~43px)
    segments = [
        SubtitleSegment(
            id=1,
            start=1.0,
            end=4.0,
            text="学校处理完 303 的噶人事情",
            translated_text="Trường xử lý xong chuyện người chết ở phòng 303",
            bbox=[0.35, 0.73, 0.65, 0.77],
        ),
    ]

    # Khung OCR người dùng vẽ rất rộng: w=1492px (0.777), h=84px (0.0778)
    user_rect = (0.11, 0.70, 0.78, 0.08)

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=segments,
        output_video_path=str(out_vid),
        enable_blur_mask=True,
        mask_mode="blur",
        blur_rect=user_rect,
        dynamic_mask=True,  # Yêu cầu 1: có chữ thì che, hết chữ không che
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []

    def mock_popen(cmd, **kwargs):
        for flag in ("-filter_complex_script", "-/filter_complex"):
            if flag in cmd:
                idx = cmd.index(flag)
                p = cmd[idx + 1]
                if os.path.isfile(p):
                    with open(p, "r", encoding="utf-8") as f:
                        captured_filter_script.append(f.read())
        out_vid.write_bytes(b"output")
        m = MagicMock()
        m.returncode = 0
        m.stdout.readline.return_value = b""
        m.stderr.readline.return_value = b""
        m.poll.return_value = 0
        m.wait.return_value = 0
        return m

    with (
        patch("app.services.video_export_service.measure_media_duration", return_value=10.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1920, 1080)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # 1. Yêu cầu 1: Mặt nạ động enable='between(t,1.00,4.00)' - chỉ che khi có câu thoại, lúc khác không che
    assert "between(t,1.00,4.00)" in script

    # 2. Yêu cầu 2: Chiều rộng và chiều cao dải làm mờ bám sát chữ Trung thực tế
    # Chiều rộng khung OCR người dùng vẽ = 0.78 * 1920 = 1498px
    # Chữ Trung thực tế rộng: (0.65 - 0.35) * 1920 = 576px + padding ~ 690px
    # Dải mờ phải ôm sát chữ Trung (< 1100px), KHÔNG được phình to 1500px - 1600px!
    import re
    crop_match = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", script)
    assert crop_match is not None
    crop_w = int(crop_match.group(1))
    crop_h = int(crop_match.group(2))

    # Chiều rộng ôm sát (nhỏ hơn nhiều so với 1498px của khung quét OCR)
    assert crop_w < 1100
    assert crop_w >= 576

    # Chiều cao ôm sát chữ Trung thực tế (chữ cao ~43px, dải mờ ôm sát ~50-60px, KHÔNG phình to > 80px)
    assert crop_h < 75
    assert crop_h >= 40


def test_single_voice_tight_box_badge_sizing():
    """Kiểm tra chế độ Khung đen bo góc (mask_mode='box') ôm sát chữ Trung và tiếng Việt, không bị phóng đại 125% khung OCR."""
    segments = [
        SubtitleSegment(
            id=1,
            start=1.0,
            end=3.0,
            text="学校处理完 303 的噶人事情",
            translated_text="Trường xử lý xong",
            bbox=[0.35, 0.73, 0.65, 0.77],  # Chiều cao chữ Trung thực tế ~43px trên 1080p
        ),
    ]

    cfg = SubtitleRenderConfig(background_box=True, mask_mode="box", font_size=34)
    user_rect = (0.11, 0.70, 0.78, 0.08)  # user_rect cao 8% = 86px trên 1080p

    ass_content, cnt = generate_ass_script(
        segments=segments,
        vid_w=1920,
        vid_h=1080,
        cfg=cfg,
        user_rect=user_rect,
        mask_mode="box",
    )
    assert cnt == 1

    # Kiểm tra dòng BgBadge được tạo
    badge_lines = [l for l in ass_content.splitlines() if l.startswith("Dialogue:") and "BgBadge" in l]
    assert len(badge_lines) == 1

    # Parse tọa độ vẽ rounded rect từ ASS drawing
    # Hình chữ nhật ASS có dạng m X1 Y1 l X2 Y1 ... l X1 Y2 ...
    import re
    pts = re.findall(r'[ml]\s+(\d+)\s+(\d+)', badge_lines[0])
    assert len(pts) >= 4
    xs = [int(p[0]) for p in pts]
    ys = [int(p[1]) for p in pts]
    box_w = max(xs) - min(xs)
    box_h = max(ys) - min(ys)

    # Chiều cao box_h không được bị ép lên 105px (86px * 1.25 cũ), mà phải ôm sát (~50-65px)
    assert box_h < 80
    assert box_h >= 42

    # Chiều rộng box_w vừa khít câu chữ (không bị phình to chiếm hết màn hình)
    assert box_w < 1100
    assert box_w >= 500
