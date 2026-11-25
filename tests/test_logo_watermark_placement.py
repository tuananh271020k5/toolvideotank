"""Unit test kiểm tra tính năng kéo chọn vị trí khung Logo và Watermark trực quan."""

import os
import sys

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, "ui"))

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QRectF

from ui.views.preview_panel import LogoPlacementOverlay, WatermarkPlacementOverlay
from app.services.video_export_service import (
    VideoExportService,
    SubtitleRenderConfig,
    VideoExportRequest,
    generate_ass_script,
)
from app.models.subtitle_segment import SubtitleSegment


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_logo_placement_overlay(qapp):
    overlay = LogoPlacementOverlay()
    assert overlay._norm.width() > 0
    assert overlay._norm.height() > 0

    # Đặt tọa độ tùy chọn
    overlay.set_norm_rect(0.75, 0.35, 0.18, 0.08)
    norm = overlay.get_norm_rect()
    assert abs(norm[0] - 0.75) < 1e-4
    assert abs(norm[1] - 0.35) < 1e-4
    assert abs(norm[2] - 0.18) < 1e-4
    assert abs(norm[3] - 0.08) < 1e-4
    assert overlay._user_customized is True


def test_watermark_placement_overlay(qapp):
    overlay = WatermarkPlacementOverlay()
    assert overlay._norm.width() > 0
    assert overlay._norm.height() > 0

    # Đặt tọa độ tùy chọn
    overlay.set_norm_rect(0.60, 0.70, 0.30, 0.05)
    norm = overlay.get_norm_rect()
    assert abs(norm[0] - 0.60) < 1e-4
    assert abs(norm[1] - 0.70) < 1e-4
    assert abs(norm[2] - 0.30) < 1e-4
    assert abs(norm[3] - 0.05) < 1e-4
    assert overlay._user_customized is True


def test_watermark_ass_custom_rect_pos():
    svc = VideoExportService()
    cfg = SubtitleRenderConfig(
        enable_watermark=True,
        watermark_text="tanksohai",
        watermark_position="custom_rect",
        watermark_custom_rect=(0.60, 0.80, 0.30, 0.06),
    )
    segments = [
        SubtitleSegment(
            id=1,
            start=1.0,
            end=3.0,
            text="Xin chào",
            translated_text="Xin chào",
        )
    ]
    # Canvas 1080x1920 (9:16)
    ass_content, cnt = generate_ass_script(
        segments=segments,
        vid_w=1080,
        vid_h=1920,
        cfg=cfg,
        duration=10.0,
    )
    assert cnt >= 1
    # cx = 1080 * (0.60 + 0.15) = 810
    # cy = 1920 * (0.80 + 0.03) = 1594 (hoặc 1593.6 -> 1594)
    assert r"\pos(810,1594)\an5" in ass_content
    assert "tanksohai" in ass_content


def test_single_voice_setup_dialog_placement(qapp):
    from PySide6.QtWidgets import QWidget
    from ui.dialogs.single_voice_setup_dialog import SingleVoiceSetupDialog

    parent = QWidget()
    parent.logo_placement_overlay = LogoPlacementOverlay()
    parent.logo_placement_overlay.set_norm_rect(0.72, 0.30, 0.15, 0.08)
    parent.logo_placement_overlay._requested_visible = True

    parent.watermark_placement_overlay = WatermarkPlacementOverlay()
    parent.watermark_placement_overlay.set_norm_rect(0.65, 0.75, 0.25, 0.04)
    parent.watermark_placement_overlay._requested_visible = True

    dlg = SingleVoiceSetupDialog(parent=parent)
    # Tự động nhận diện custom_rect từ parent overlay
    assert dlg.combo_logo_pos.currentData() == "custom_rect"
    assert dlg._logo_custom_rect == (0.72, 0.30, 0.15, 0.08)
    assert dlg.combo_watermark_pos.currentData() == "custom_rect"
    assert dlg._watermark_custom_rect == (0.65, 0.75, 0.25, 0.04)

    # Đặt logo path và bật watermark
    dlg.txt_logo_path.setText("test_logo.png")
    dlg.txt_watermark.setText("nhim_review")

    logo_settings = dlg.get_brand_logo_settings()
    assert logo_settings["position"] == "custom_rect"
    assert logo_settings["custom_rect"] == (0.72, 0.30, 0.15, 0.08)

    wm_settings = dlg.get_watermark_settings()
    assert wm_settings["position"] == "custom_rect"
    assert wm_settings["custom_rect"] == (0.65, 0.75, 0.25, 0.04)

