import os
import pytest
from PySide6.QtWidgets import QApplication
from ui.dialogs.single_voice_setup_dialog import SingleVoiceSetupDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    yield app


def test_asr_mode_toggle_and_auto_grab_video_duration(qapp, monkeypatch):
    dialog = SingleVoiceSetupDialog(video_path="dummy_sample.mp4")

    # Mock get_video_duration_seconds để trả về 197.37 giây
    monkeypatch.setattr(dialog, "get_video_duration_seconds", lambda: 197.37)

    # Ban đầu ở chế độ range
    dialog.radio_asr_range.setChecked(True)
    assert dialog.radio_asr_range.isChecked() is True
    assert not dialog.btn_add_asr_range.isHidden()

    # Khi người dùng click vào "Quét toàn bộ video"
    dialog.radio_asr_all.setChecked(True)
    assert dialog.radio_asr_all.isChecked() is True

    # Kiểm tra Đoạn 1 đã tự động bắt giây đầu = 0.0 và giây cuối = 197.37
    assert len(dialog._asr_range_rows) >= 1
    row1 = dialog._asr_range_rows[0]
    assert row1.spin_start.value() == 0.0
    assert row1.spin_end.value() == 197.37

    # Nút thêm đoạn phải bị ẩn đi để không tạo các đoạn rác
    assert dialog.btn_add_asr_range.isHidden()
    assert row1.spin_start.isReadOnly() is True
    assert row1.spin_end.isReadOnly() is True

    # Kiểm tra settings trả về cho Worker
    settings = dialog.get_asr_supplement_settings()
    assert settings["mode"] == "all"
    assert settings["ranges"] == [(0.0, 197.37)]

    # Khi người dùng click lại vào "Quét theo khoảng thời gian chỉ định"
    dialog.radio_asr_range.setChecked(True)
    assert not dialog.btn_add_asr_range.isHidden()
    assert row1.spin_start.isReadOnly() is False
    assert row1.spin_end.isReadOnly() is False
