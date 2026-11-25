import os
import sys
import subprocess
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("app"))

try:
    from app.models.subtitle_segment import SubtitleSegment
    from app.services.video_export_service import (
        VideoExportService,
        VideoExportRequest,
        SubtitleRenderConfig,
        make_rounded_rect_ass,
        generate_ass_script,
    )
except ImportError:
    from models.subtitle_segment import SubtitleSegment
    from services.video_export_service import (
        VideoExportService,
        VideoExportRequest,
        SubtitleRenderConfig,
        make_rounded_rect_ass,
        generate_ass_script,
    )


def test_make_rounded_rect_ass():
    path = make_rounded_rect_ass(600, 100, 20, 540, 1440)
    assert path.startswith("m ")
    assert " b " in path
    assert " l " in path


def test_generate_ass_script_creates_rounded_badge():
    segments = [
        SubtitleSegment(id=1, start=1.5, end=3.5, text="你好", translated_text="Xin chào bạn"),
    ]
    cfg = SubtitleRenderConfig(enable_blur_mask=True)
    ass_content, cnt = generate_ass_script(segments, 1080, 1920, cfg, target_cy=1440)
    assert cnt == 1
    assert "BgBadge" in ass_content
    assert "SubText" in ass_content
    assert "Xin chào bạn" in ass_content
    # Kiểm tra vẽ hộp bo góc vector
    assert r"\p1" in ass_content
    assert r"\p0" in ass_content


def test_video_export_builds_ass_badge_filter(tmp_path):
    orig_vid = tmp_path / "test_orig.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_audio.wav"
    final_aud.write_bytes(b"dummy_audio")
    out_vid = tmp_path / "test_out.mp4"

    segments = [
        SubtitleSegment(id=1, start=0.5, end=2.0, text="测试", translated_text="Kiểm tra mặt nạ"),
    ]

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=segments,
        output_video_path=str(out_vid),
        enable_blur_mask=True,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []

    def mock_run(cmd, **kwargs):
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
            script_path = cmd[idx + 1]
            if os.path.isfile(script_path):
                with open(script_path, "r", encoding="utf-8") as f:
                    captured_filter_script.append(f.read())
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra filter áp dụng ASS subtitle với mặt nạ hộp nền bo góc
    assert "subtitles=" in script
    assert ".ass" in script


def test_color_conversion_helpers():
    from app.services.video_export_service import hex_to_ass_color, ass_color_to_hex
    assert hex_to_ass_color("#FFE5DD") == "&H00DDE5FF&"
    assert hex_to_ass_color("#000000") == "&H00000000&"
    assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF&"
    assert hex_to_ass_color("#FFD400") == "&H0000D4FF&"

    assert ass_color_to_hex("&H00DDE5FF&") == "#FFE5DD"
    assert ass_color_to_hex("&H00000000&") == "#000000"
    assert ass_color_to_hex("&H00FFFFFF&") == "#FFFFFF"


def test_generate_ass_script_without_box():
    segments = [
        SubtitleSegment(id=1, start=1.0, end=3.0, text="测试", translated_text="Chữ không nền"),
    ]
    cfg = SubtitleRenderConfig(background_box=False, badge_text_color="&H0000D4FF&")
    ass_content, cnt = generate_ass_script(segments, 1080, 1920, cfg, target_cy=1440)
    assert cnt == 1
    assert "BgBadge" not in [line.split(",")[3] for line in ass_content.splitlines() if line.startswith("Dialogue:")]
    assert "SubText" in ass_content
    assert "&H0000D4FF&" in ass_content


def test_generate_ass_script_snug_box_dimensions():
    segments = [
        SubtitleSegment(id=1, start=1.0, end=3.0, text="测试", translated_text="Tôn Hiểu Hiểu, khách cần thìa sao không lấy?"),
    ]
    cfg = SubtitleRenderConfig(background_box=True, font_size=34)
    ass_content, cnt = generate_ass_script(segments, 1080, 1920, cfg, target_cy=1440)
    assert cnt == 1
    # Kiểm tra có vẽ BgBadge và SubText
    assert r"\p1" in ass_content
    assert "BgBadge" in ass_content
    assert "SubText" in ass_content


def test_generate_ass_script_blur_mask_mode():
    """Kiểm tra chế độ Mặt nạ làm mờ ngang (Ảnh 1): Không vẽ BgBadge hộp đen, chữ trắng viền đen nổi bật."""
    segments = [
        SubtitleSegment(id=1, start=1.0, end=3.0, text="测试", translated_text="Dòng chữ trên nền mờ"),
    ]
    cfg = SubtitleRenderConfig(mask_mode="blur")
    ass_content, cnt = generate_ass_script(segments, 1080, 1920, cfg, target_cy=1440, mask_mode="blur")
    assert cnt == 1
    # Không tạo BgBadge đen ASS vì đã có dải mờ video
    assert "BgBadge" not in [line.split(",")[3] for line in ass_content.splitlines() if line.startswith("Dialogue:")]
    assert "SubText" in ass_content
    # Chữ màu trắng
    assert r"\c&H00FFFFFF&" in ass_content
    # Viền đen dày dặn sắc nét (ít nhất 4px)
    assert r"\3c&H00000000&" in ass_content
    assert r"\bord4" in ass_content or r"\bord5" in ass_content or r"\bord6" in ass_content


def test_user_rect_locks_target_cy_and_expands_box_height():
    """Kiểm tra fix lỗi Lệch Dưới (Ảnh 2): target_cy được giữ cố định theo user_rect, không bị bbox ngẫu nhiên đè."""
    # Vùng OCR ở 72% chiều cao video
    user_rect = (0.1, 0.70, 0.8, 0.04)  # cy = 0.72 * 1920 = 1382px, h = 0.04 * 1920 = 76.8px
    expected_cy = int(round((0.70 + 0.04 / 2.0) * 1920))
    # Segment có bbox lệch ở 85%
    segments = [
        SubtitleSegment(id=1, start=1.0, end=3.0, text="爸妈做的一切", translated_text="Mọi điều cha mẹ làm", bbox=[0.1, 0.80, 0.9, 0.90]),
    ]
    cfg = SubtitleRenderConfig(mask_mode="box")
    ass_content, cnt = generate_ass_script(
        segments,
        1080,
        1920,
        cfg,
        target_cy=expected_cy,
        user_rect=user_rect,
        mask_mode="box",
    )
    assert cnt == 1
    # Tọa độ SubText phải ở đúng expected_cy (1382px), KHÔNG bị nhảy xuống 85% (~1632px)
    assert f"\\pos(540,{expected_cy})" in ass_content
    # BgBadge phải được vẽ ở đúng expected_cy
    assert "BgBadge" in ass_content


def test_video_export_applies_blur_mask_strip_filter(tmp_path):
    """Kiểm tra xuất video với mask_mode='blur' (Ảnh 1) áp dụng dải mờ FFmpeg boxblur."""
    orig_vid = tmp_path / "test_orig2.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_audio2.wav"
    final_aud.write_bytes(b"dummy_audio")
    out_vid = tmp_path / "test_out2.mp4"

    segments = [
        SubtitleSegment(id=1, start=0.5, end=2.0, text="测试", translated_text="Kiểm tra dải mờ"),
    ]

    user_rect = (0.1, 0.70, 0.8, 0.05)
    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=segments,
        output_video_path=str(out_vid),
        enable_blur_mask=True,
        mask_mode="blur",
        blur_rect=user_rect,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra có filter dải làm mờ ngang boxblur
    assert "boxblur=" in script
    assert "crop=" in script
    assert "overlay=" in script
    assert "subtitles=" in script


def test_video_export_frame_border_overlay(tmp_path):
    """Kiểm tra tính năng đè Khung Viền Video trang trí từ đầu tới cuối video."""
    orig_vid = tmp_path / "test_orig3.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_audio3.wav"
    final_aud.write_bytes(b"dummy_audio")
    border_img = tmp_path / "border.png"
    border_img.write_bytes(b"dummy_border_image")
    out_vid = tmp_path / "test_out3.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[],
        output_video_path=str(out_vid),
        enable_frame_border=True,
        frame_border_top_path=str(border_img),
        frame_border_opacity=0.9,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []
    captured_cmd = []

    def mock_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert str(border_img) in captured_cmd
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra filter viền được scale giữ nguyên aspect ratio (1080:-2) và overlay sát mép trên
    assert "[2:v]format=rgba,scale=1080:-2" in script
    assert "colorchannelmixer=aa=0.90[top_border]" in script
    assert "[top_border]overlay=0:0:repeatlast=1" in script


def test_video_export_top_and_bottom_frame_borders(tmp_path):
    """Kiểm tra áp dụng đồng thời cả Viền Trên và Viền Dưới với 2 ảnh riêng biệt."""
    orig_vid = tmp_path / "test_orig_dual.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_audio_dual.wav"
    final_aud.write_bytes(b"dummy_audio")
    top_border = tmp_path / "top_border.png"
    top_border.write_bytes(b"top_border_image")
    bottom_border = tmp_path / "bottom_border.png"
    bottom_border.write_bytes(b"bottom_border_image")
    out_vid = tmp_path / "test_out_dual.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[],
        output_video_path=str(out_vid),
        enable_frame_border=True,
        frame_border_top_path=str(top_border),
        frame_border_bottom_path=str(bottom_border),
        frame_border_opacity=0.85,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []
    captured_cmd = []

    def mock_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert str(top_border) in captured_cmd
    assert str(bottom_border) in captured_cmd
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra viền trên overlay tại 0:0, viền dưới overlay tại 0:H-h
    assert "[top_border]overlay=0:0:repeatlast=1[v_bordered_top]" in script
    assert "[bottom_border]overlay=0:H-h:repeatlast=1[v_bordered_bot]" in script


def test_video_export_frame_border_with_brand_logo_topmost(tmp_path):
    """Kiểm tra khi đồng thời bật Khung Viền và Logo Thương hiệu: Logo PHẢI đè lên trên Khung Viền."""
    orig_vid = tmp_path / "test_orig4.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_audio4.wav"
    final_aud.write_bytes(b"dummy_audio")
    border_img = tmp_path / "border.png"
    border_img.write_bytes(b"dummy_border_image")
    logo_img = tmp_path / "logo.png"
    logo_img.write_bytes(b"dummy_logo_image")
    out_vid = tmp_path / "test_out4.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[],
        output_video_path=str(out_vid),
        enable_frame_border=True,
        frame_border_top_path=str(border_img),
        frame_border_opacity=1.0,
        enable_brand_logo=True,
        brand_logo_path=str(logo_img),
        brand_logo_position="top_left",
        brand_logo_size_percent=15,
        brand_logo_opacity=1.0,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []
    captured_cmd = []

    def mock_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    # Kiểm tra cả 2 file input đều có trong lệnh FFmpeg theo đúng thứ tự
    border_idx = captured_cmd.index(str(border_img))
    logo_idx = captured_cmd.index(str(logo_img))
    assert border_idx < logo_idx, "Ảnh khung viền phải là input trước, sau đó tới ảnh logo"

    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra phân bổ index stream [2:v] cho border, [3:v] cho logo
    assert "[2:v]format=rgba,scale=1080:-2" in script
    assert "[3:v]format=rgba,colorchannelmixer=aa=1.00" in script

    # Kiểm tra thứ tự overlay: Khung Viền overlay trước, sau đó Logo overlay lên trên lớp đã có viền
    border_pos = script.find("[top_border]overlay=0:0:repeatlast=1[v_bordered_top]")
    logo_pos = script.find("[brand_logo]overlay=")
    assert border_pos != -1
    assert logo_pos != -1
    assert border_pos < logo_pos, "Khung Viền phải được overlay trước, sau đó Logo thương hiệu đè lên trên cùng!"


def test_video_export_video_zoom_capcut_style(tmp_path):
    """Kiểm tra tính năng Thu Phóng Video (Zoom từ tâm kiểu CapCut 100% - 150%)."""
    orig_vid = tmp_path / "test_zoom.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_zoom_audio.wav"
    final_aud.write_bytes(b"dummy_audio")
    out_vid = tmp_path / "test_zoom_out.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[],
        output_video_path=str(out_vid),
        video_scale_percent=115,  # Zoom 115% như CapCut
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []

    def mock_popen(cmd, **kwargs):
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Video 1080x1920 zoom 115% -> scale 1242x2208, sau đó center crop về 1080x1920
    assert "scale=1242:2208,crop=1080:1920" in script
    assert "[v_zoomed]" in script


def test_video_export_custom_border_heights(tmp_path):
    """Kiểm tra điều chỉnh Độ Dày (Chiều cao px) của Viền Trên và Viền Dưới."""
    orig_vid = tmp_path / "test_h.mp4"
    orig_vid.write_bytes(b"dummy_video")
    final_aud = tmp_path / "test_h_audio.wav"
    final_aud.write_bytes(b"dummy_audio")
    top_border = tmp_path / "top.png"
    top_border.write_bytes(b"dummy_top")
    bot_border = tmp_path / "bot.png"
    bot_border.write_bytes(b"dummy_bot")
    out_vid = tmp_path / "test_h_out.mp4"

    req = VideoExportRequest(
        original_video_path=str(orig_vid),
        final_audio_path=str(final_aud),
        segments=[],
        output_video_path=str(out_vid),
        enable_frame_border=True,
        frame_border_top_path=str(top_border),
        frame_border_bottom_path=str(bot_border),
        frame_border_top_height=80,
        frame_border_bottom_height=120,
    )

    svc = VideoExportService(temp_dir=str(tmp_path / "temp"))
    captured_filter_script = []

    def mock_popen(cmd, **kwargs):
        if "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
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
        patch("app.services.video_export_service.measure_media_duration", return_value=5.0),
        patch("app.services.video_export_service.probe_video_dimensions", return_value=(1080, 1920)),
        patch("subprocess.Popen", side_effect=mock_popen),
    ):
        res = svc.export_video(req)

    assert res.status == "success"
    assert len(captured_filter_script) == 1
    script = captured_filter_script[0]

    # Kiểm tra viền trên scale thành 1080:80, viền dưới scale thành 1080:120
    assert "scale=1080:80" in script
    assert "scale=1080:120" in script
    assert "[top_border]overlay=0:0:repeatlast=1" in script
    assert "[bottom_border]overlay=0:H-h:repeatlast=1" in script


def test_smart_dual_zone_bbox_anchoring_top_and_bottom():
    """Kiểm tra thuật toán tổng quát: Dòng chữ ở đâu (Đỉnh hay Đáy) thì BgBadge và SubText neo chính xác ở đó."""
    seg_top = SubtitleSegment(
        id=1,
        start=0.0,
        end=2.0,
        text="我居然被一条鱼给下了咒",
        translated_text="Tôi bị một con cá ếm bùa",
        bbox=[0.14, 0.12, 0.86, 0.18],  # Đỉnh (tâm ~15% chiều cao)
    )
    seg_bot = SubtitleSegment(
        id=2,
        start=2.5,
        end=4.5,
        text="怎么办",
        translated_text="Làm sao bây giờ",
        bbox=[0.14, 0.72, 0.86, 0.78],  # Đáy (tâm ~75% chiều cao)
    )
    cfg = SubtitleRenderConfig(background_box=True, mask_mode="box")

    # Khi không có user_rect (chế độ Smart OCR tự động quét đa vùng)
    ass_content, cnt = generate_ass_script([seg_top, seg_bot], 1080, 1920, cfg)
    assert cnt == 2

    # Tâm Y câu đỉnh: round(0.15 * 1920) = 288px
    assert r"\pos(540,288)" in ass_content
    # Tâm Y câu đáy: round(0.75 * 1920) = 1440px
    assert r"\pos(540,1440)" in ass_content

    # Cả hai câu đều có BgBadge bo góc riêng biệt xuất hiện và biến mất theo đúng thời gian
    badge_lines = [l for l in ass_content.splitlines() if l.startswith("Dialogue:") and "BgBadge" in l]
    assert len(badge_lines) == 2
    assert "0:00:00.00,0:00:02.00,BgBadge" in badge_lines[0]
    assert "0:00:02.50,0:00:04.50,BgBadge" in badge_lines[1]




