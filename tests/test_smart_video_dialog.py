import pytest
import os
from PySide6.QtWidgets import QApplication
from ui.dialogs.smart_video_dialog import SmartVideoDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_smart_video_dialog_single_voice(qapp):
    dialog = SmartVideoDialog(
        video_path="test_video.mp4",
        workspace_root=os.getcwd(),
    )
    dialog.show()

    # Kiểm tra Tab 2: Giọng đọc AI duy nhất cho toàn bộ video
    assert hasattr(dialog, "single_combo")
    assert dialog.single_combo.count() > 0
    assert dialog.single_combo.currentData() is not None

    # Kiểm tra Slider âm lượng
    assert dialog.orig_slider.value() == 40
    assert dialog.tts_slider.value() == 100

    # Kiểm tra Mặt nạ động
    assert dialog.mask_dyn_blur_rb.isChecked()

    dialog.close()
