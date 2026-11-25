"""Voice Gender Check & AI Role Assignment Progress Dialog.

Displays real-time multi-stage visual progress, percentage, step tracking,
log box, and safe cooperative cancellation for AI Role Assignment.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.worker_adapters.processing_workers import VoiceGenderCheckWorker


class VoiceGenderProgressDialog(QDialog):
    """Hộp thoại trực quan hiển thị tiến độ phân vai AI và hỗ trợ hủy tiến trình an toàn."""

    result_ready = Signal(object, str)  # (result_dict, error_msg)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        video_path: str = "",
        existing_cues: list[dict[str, Any]] | None = None,
        ocr_rect: tuple[float, float, float, float] | None = None,
    ):
        super().__init__(parent)
        self.video_path = video_path
        self.existing_cues = existing_cues
        self.ocr_rect = ocr_rect

        self._worker: VoiceGenderCheckWorker | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str = ""
        self._is_cancelled: bool = False
        self._close_requested: bool = False
        self._start_time: float = time.time()

        self.setWindowTitle("Tiến trình Phân vai AI - TankVideo")
        self.resize(750, 520)
        self.setMinimumSize(650, 450)

        self._setup_ui()
        self._apply_dark_style()
        self._start_worker()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        # Header Title
        header_title = QLabel("🎙️ TIẾN TRÌNH PHÂN VAI NHÂN VẬT AI - TANKVIDEO")
        h_font = QFont()
        h_font.setPointSize(12)
        h_font.setBold(True)
        header_title.setFont(h_font)
        header_title.setStyleSheet("color: #c084fc;")
        root_layout.addWidget(header_title)

        video_name = os.path.basename(self.video_path) if self.video_path else "Video"
        has_reuse = bool(self.existing_cues and len(self.existing_cues) > 0)
        mode_desc = "Tái sử dụng phụ đề giao diện" if has_reuse else "Quét OCR phụ đề tự động"
        lbl_info = QLabel(f"🎥 Video: {video_name} | ⚙️ Phương thức: {mode_desc}")
        lbl_info.setStyleSheet("color: #8b949e; font-size: 11px;")
        root_layout.addWidget(lbl_info)

        # 4 Step Visual Stepper
        steps_frame = QFrame()
        steps_frame.setStyleSheet(
            "background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 6px;"
        )
        steps_layout = QHBoxLayout(steps_frame)
        steps_layout.setContentsMargins(8, 8, 8, 8)
        steps_layout.setSpacing(6)

        self.lbl_step1 = QLabel("1. 🎵 Tách âm & OCR\n[🔄 Đang chạy...]")
        self.lbl_step1.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 10px;")
        self.lbl_step1.setAlignment(Qt.AlignCenter)

        self.lbl_step2 = QLabel("2. 🎙️ ASR & VAD\n[⏳ Chờ xử lý]")
        self.lbl_step2.setStyleSheet("color: #8b949e; font-weight: bold; font-size: 10px;")
        self.lbl_step2.setAlignment(Qt.AlignCenter)

        self.lbl_step3 = QLabel("3. 🧠 Gender AI\n[⏳ Chờ xử lý]")
        self.lbl_step3.setStyleSheet("color: #8b949e; font-weight: bold; font-size: 10px;")
        self.lbl_step3.setAlignment(Qt.AlignCenter)

        self.lbl_step4 = QLabel("4. 🤖 Dịch & Phân vai\n[⏳ Chờ xử lý]")
        self.lbl_step4.setStyleSheet("color: #8b949e; font-weight: bold; font-size: 10px;")
        self.lbl_step4.setAlignment(Qt.AlignCenter)

        steps_layout.addWidget(self.lbl_step1, 1)
        steps_layout.addWidget(self.lbl_step2, 1)
        steps_layout.addWidget(self.lbl_step3, 1)
        steps_layout.addWidget(self.lbl_step4, 1)
        root_layout.addWidget(steps_frame)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #21262d;
                color: #ffffff;
                border: 1px solid #30363d;
                border-radius: 6px;
                text-align: center;
                height: 22px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #3b82f6);
                border-radius: 5px;
            }
        """)
        root_layout.addWidget(self.progress_bar)

        # Status Label
        self.lbl_status = QLabel("Đang khởi tạo tiến trình phân vai AI...")
        self.lbl_status.setStyleSheet("color: #e6edf3; font-weight: bold; font-size: 12px;")
        root_layout.addWidget(self.lbl_status)

        # Real-time Log Box
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 6px;
                color: #c9d1d9;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
        """)
        root_layout.addWidget(self.log_box)

        # Button Controls
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Hủy tiến trình")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()

        self.btn_view_result = QPushButton("📋 Xem kết quả phân vai")
        self.btn_view_result.setObjectName("primaryBtn")
        self.btn_view_result.setVisible(False)
        self.btn_view_result.clicked.connect(self._on_view_result_clicked)
        btn_layout.addWidget(self.btn_view_result)

        self.btn_close = QPushButton("Đóng")
        self.btn_close.clicked.connect(self.accept)
        self.btn_close.setEnabled(False)
        btn_layout.addWidget(self.btn_close)

        root_layout.addLayout(btn_layout)

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #121316; color: #e6edf3; }
            QLabel { background-color: transparent; }
            QPushButton {
                background-color: #21262d; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 6px; padding: 6px 14px; font-weight: 600;
            }
            QPushButton:hover { background-color: #30363d; }
            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #6d28d9);
                color: #ffffff; border: 1px solid #a855f7;
            }
            QPushButton#primaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9333ea, stop:1 #7c3aed);
            }
        """)

    def _start_worker(self):
        self._worker = VoiceGenderCheckWorker(
            video_path=self.video_path,
            existing_cues=self.existing_cues,
            ocr_rect=self.ocr_rect,
            parent=None,
        )

        self._worker.progress_percent.connect(self._on_progress_percent)
        self._worker.log_emitted.connect(self._on_log_emitted)
        self._worker.completed.connect(self._on_worker_completed)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        self._worker.finished.connect(self._on_worker_finished)

        self._start_time = time.time()
        self.log_box.append("🚀 BẮT ĐẦU TIẾN TRÌNH PHÂN VAI NHÂN VẬT AI")
        self.log_box.append(f"📁 Video: {os.path.basename(self.video_path)}")
        self.log_box.append("-" * 50)
        self._worker.start(QThread.Priority.HighestPriority)

    def _on_progress_percent(self, pct: float, message: str):
        val = max(0, min(100, int(round(pct))))
        self.progress_bar.setValue(val)
        self.lbl_status.setText(f"{message} ({val}%)")

        # Cập nhật hiển thị bước
        if val < 20:
            self.lbl_step1.setText("1. 🎵 Tách âm & OCR\n[🔄 Đang chạy]")
            self.lbl_step1.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 10px;")
        elif val < 45:
            self.lbl_step1.setText("1. 🎵 Tách âm & OCR\n[✅ Xong]")
            self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step2.setText("2. 🎙️ ASR & VAD\n[🔄 Đang phân tích]")
            self.lbl_step2.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 10px;")
        elif val < 65:
            self.lbl_step1.setText("1. 🎵 Tách âm & OCR\n[✅ Xong]")
            self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step2.setText("2. 🎙️ ASR & VAD\n[✅ Xong]")
            self.lbl_step2.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step3.setText("3. 🧠 Gender AI\n[🔄 Đang nhận diện]")
            self.lbl_step3.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 10px;")
        else:
            self.lbl_step1.setText("1. 🎵 Tách âm & OCR\n[✅ Xong]")
            self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step2.setText("2. 🎙️ ASR & VAD\n[✅ Xong]")
            self.lbl_step2.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step3.setText("3. 🧠 Gender AI\n[✅ Xong]")
            self.lbl_step3.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
            self.lbl_step4.setText("4. 🤖 Dịch & Phân vai\n[🔄 Đang dịch ngữ cảnh]")
            self.lbl_step4.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 10px;")

    def _on_log_emitted(self, msg: str):
        self.log_box.append(msg)

    def _on_worker_completed(self, result: dict[str, Any], err_msg: str):
        self._last_result = result
        self._last_error = err_msg

        elapsed = max(0.1, time.time() - getattr(self, "_start_time", time.time()))
        s_int = int(round(elapsed))
        dur_str = f"{s_int}s" if s_int < 60 else f"{s_int // 60}m {s_int % 60:02d}s"

        if err_msg:
            self.lbl_status.setText(f"❌ Thất bại: {err_msg}")
            self.lbl_status.setStyleSheet("color: #f85149; font-weight: bold; font-size: 12px;")
            self.btn_cancel.setVisible(False)
            self.btn_close.setEnabled(True)
            self.result_ready.emit({}, err_msg)
            QMessageBox.critical(self, "Lỗi phân vai AI", f"Không thể hoàn thành phân vai AI:\n\n{err_msg}")
            return

        rows = list(result.get("rows") or [])
        counts = dict(result.get("counts") or {})
        m_cnt = counts.get("male", 0)
        f_cnt = counts.get("female", 0)
        u_cnt = counts.get("unknown", 0)

        self.lbl_step1.setText("1. 🎵 Tách âm & OCR\n[✅ Xong]")
        self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
        self.lbl_step2.setText("2. 🎙️ ASR & VAD\n[✅ Xong]")
        self.lbl_step2.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
        self.lbl_step3.setText("3. 🧠 Gender AI\n[✅ Xong]")
        self.lbl_step3.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")
        self.lbl_step4.setText("4. 🤖 Dịch & Phân vai\n[✅ Hoàn thành]")
        self.lbl_step4.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 10px;")

        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"🎉 Hoàn tất phân vai: {len(rows)} câu thoại ({m_cnt} Nam, {f_cnt} Nữ) trong {dur_str}!")
        self.lbl_status.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 13px;")

        self.log_box.append("\n" + "=" * 50)
        self.log_box.append(f"⏱️ THỜI GIAN HOÀN THÀNH: {dur_str} ({elapsed:.1f}s)")
        self.log_box.append(f"📊 KẾT QUẢ PHÂN VAI: {len(rows)} câu thoại | ♂️ Nam: {m_cnt} | ♀️ Nữ: {f_cnt} | ❓ Khác: {u_cnt}")
        self.log_box.append("=" * 50)

        self.btn_cancel.setVisible(False)
        self.btn_view_result.setVisible(True)
        self.btn_close.setEnabled(True)

        self.result_ready.emit(result, "")

    def _on_worker_cancelled(self):
        self._is_cancelled = True
        self.lbl_status.setText("⏹️ Đã dừng và hủy tiến trình phân vai AI.")
        self.lbl_status.setStyleSheet("color: #d29922; font-weight: bold; font-size: 12px;")
        self.log_box.append("\n" + "=" * 50)
        self.log_box.append("⏹️ TIẾN TRÌNH PHÂN VAI AI ĐÃ ĐƯỢC DỪNG VÀ HỦY AN TOÀN.")
        self.log_box.append("=" * 50)
        self.btn_cancel.setVisible(False)
        self.btn_close.setEnabled(True)
        if self._close_requested:
            super().reject()

    def _on_worker_finished(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if self._close_requested:
            super().reject()

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            ans = QMessageBox.question(
                self,
                "Hủy tiến trình",
                "Bạn có chắc muốn hủy tiến trình phân vai AI này không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans == QMessageBox.Yes:
                self.btn_cancel.setEnabled(False)
                self.btn_cancel.setText("Đang hủy...")
                self.lbl_status.setText("⏹️ Đang dừng tiến trình an toàn...")
                self.lbl_status.setStyleSheet("color: #d29922; font-weight: bold; font-size: 12px;")
                self.log_box.append("⏹️ Người dùng yêu cầu hủy tiến trình (đang dừng an toàn tại checkpoint)...")
                self._worker.request_cancel()

    def _on_view_result_clicked(self):
        self.accept()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
            self._close_requested = True
            self._on_cancel_clicked()
        else:
            event.accept()
            super().closeEvent(event)

    def reject(self):
        if self._worker and self._worker.isRunning():
            self._close_requested = True
            self._on_cancel_clicked()
            return
        super().reject()
