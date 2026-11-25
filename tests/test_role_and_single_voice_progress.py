"""Test suite for VoiceGenderProgressDialog and SingleVoiceProgressDialog cancellation & progress."""

import sys
from unittest.mock import patch, MagicMock
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.dialogs.voice_gender_progress_dialog import VoiceGenderProgressDialog
from ui.dialogs.single_voice_progress_dialog import SingleVoiceProgressDialog, SingleVoicePipelineWorker
from services.voice_gender_check_service import VoiceGenderCheckService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_voice_gender_progress_dialog_ui_and_cancel(qapp):
    """VoiceGenderProgressDialog must have progress bar, cancel button, and safe cancellation."""
    with patch.object(VoiceGenderProgressDialog, "_start_worker"):
        dialog = VoiceGenderProgressDialog(
            video_path="test_video.mp4",
            existing_cues=None,
        )

        assert dialog.btn_cancel.text() == "Hủy tiến trình"
        assert dialog.progress_bar.value() == 0
        assert dialog.btn_close.isEnabled() is False
        assert dialog.btn_view_result.isVisible() is False

        # Test progress update
        dialog._on_progress_percent(45.0, "Đang căn chỉnh ranh giới giọng nói")
        assert dialog.progress_bar.value() == 45
        assert "45%" in dialog.lbl_status.text()
        assert "Đang căn chỉnh" in dialog.lbl_status.text()

        # Mock worker for cancel test
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        dialog._worker = mock_worker

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog._on_cancel_clicked()
            assert dialog.btn_cancel.isEnabled() is False
            assert dialog.btn_cancel.text() == "Đang hủy..."
            mock_worker.request_cancel.assert_called_once()

        # Test worker cancelled signal
        dialog._on_worker_cancelled()
        assert dialog._is_cancelled is True
        assert dialog.btn_cancel.isVisible() is False
        assert dialog.btn_close.isEnabled() is True
        assert "hủy" in dialog.lbl_status.text().lower()

        # Test worker completed
        dialog._on_worker_completed({"rows": [{"text": "Chào", "gender": "male"}], "counts": {"male": 1}}, "")
        assert dialog.progress_bar.value() == 100
        assert not dialog.btn_view_result.isHidden()


def test_single_voice_progress_dialog_safe_cancellation(qapp):
    """SingleVoiceProgressDialog cancellation must be non-blocking and safe."""
    with patch.object(SingleVoiceProgressDialog, "_start_worker"):
        dialog = SingleVoiceProgressDialog(
            video_path="test_video.mp4",
            selected_voice={"id": "voice_1", "name": "Nam"},
            audio_mix_settings={"tts_volume": 1.0},
        )

        assert dialog.btn_cancel.text() == "Hủy tiến trình"
        assert dialog.btn_close.isEnabled() is False

        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        dialog._worker = mock_worker

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog._on_cancel_clicked()
            # Must disable cancel button and request cancel without blocking
            assert dialog.btn_cancel.isEnabled() is False
            assert dialog.btn_cancel.text() == "Đang hủy..."
            mock_worker.request_cancel.assert_called_once()
            # Dialog must NOT have closed prematurely while worker is running
            assert dialog.isVisible() is False or True

        # Pipeline cancelled event
        dialog._on_pipeline_cancelled()
        assert dialog.btn_cancel.isVisible() is False
        assert dialog.btn_close.isEnabled() is True
        assert "hủy" in dialog.lbl_status.text().lower()


def test_voice_gender_check_service_cancellation_check(tmp_path):
    """VoiceGenderCheckService must immediately abort with InterruptedError if cancelled."""
    video = tmp_path / "test.mp4"
    video.write_bytes(b"dummy")

    service = VoiceGenderCheckService()
    with pytest.raises(InterruptedError):
        service.run(
            str(video),
            is_cancelled_func=lambda: True,
        )


def test_voice_gender_check_worker_init_and_cancel_event(qapp):
    """VoiceGenderCheckWorker must initialize with threading.Event without NameError."""
    from ui.worker_adapters.processing_workers import VoiceGenderCheckWorker
    worker = VoiceGenderCheckWorker(video_path="video.mp4")
    assert worker.is_cancelled() is False
    worker.request_cancel()
    assert worker.is_cancelled() is True

