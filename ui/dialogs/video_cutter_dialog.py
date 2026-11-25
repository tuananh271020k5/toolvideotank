"""Video Cutter & Splitter Dialog - Giao diện cắt video ngắn (Shorts / TikTok / Reels) & chia video.

Hỗ trợ:
1. Cắt clip ngắn (ví dụ: cắt 1 phút, 2 phút từ video 5 phút làm YouTube Shorts).
2. Tự động chia cả video thành nhiều phần nhỏ (Part 001, Part 002...) theo thời lượng tùy chọn.
3. Chế độ Siêu tốc (Stream Copy - không nén lại, 100% giữ gốc trong 1 giây) và chế độ Khung hình chuẩn xác (Re-encode).
4. Preset 1-Click chuẩn YouTube Shorts (60s), TikTok/Reels (30s, 90s, 120s...).
5. Bố cục QScrollArea thích ứng với mọi kích thước màn hình, không bị che khuất hay đè chữ.
"""

import math
import os
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.video_cutter_service import (
    calculate_split_ranges,
    cut_single_clip,
    format_seconds,
    format_seconds_detailed,
    probe_video_details,
    sanitize_filename,
    split_video_into_parts,
)


# ====================================================================== #
#  Worker Thread cho thao tác cắt video không giật lag                   #
# ====================================================================== #
class VideoCutterWorker(QThread):
    progress_changed = Signal(float, str)
    finished_success = Signal(list)  # list các file đã tạo
    failed_error = Signal(str)

    def __init__(
        self,
        task_type: str,  # "clip" hoặc "split"
        video_path: str,
        output_path: str,
        start_sec: float = 0.0,
        duration_sec: float = 60.0,
        segment_sec: float = 60.0,
        mode: str = "copy",
        custom_base_name: str = "",
        merge_remainder: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.task_type = task_type
        self.video_path = video_path
        self.output_path = output_path
        self.start_sec = start_sec
        self.duration_sec = duration_sec
        self.segment_sec = segment_sec
        self.mode = mode
        self.custom_base_name = custom_base_name
        self.merge_remainder = merge_remainder
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self.task_type == "clip":
                def on_prog(pct, msg):
                    self.progress_changed.emit(pct, msg)

                ok, err = cut_single_clip(
                    video_path=self.video_path,
                    output_path=self.output_path,
                    start_sec=self.start_sec,
                    duration_sec=self.duration_sec,
                    mode=self.mode,
                    progress_cb=on_prog,
                )
                if self._is_cancelled:
                    self.failed_error.emit("Đã hủy tiến trình cắt video.")
                    return

                if ok:
                    self.finished_success.emit([self.output_path])
                else:
                    self.failed_error.emit(err or "Không thể cắt đoạn video.")

            elif self.task_type == "split":
                def on_prog(pct, msg):
                    self.progress_changed.emit(pct, msg)

                def is_canc():
                    return self._is_cancelled

                files, err = split_video_into_parts(
                    video_path=self.video_path,
                    output_dir=self.output_path,
                    segment_seconds=self.segment_sec,
                    mode=self.mode,
                    custom_base_name=self.custom_base_name,
                    merge_remainder=self.merge_remainder,
                    progress_cb=on_prog,
                    is_cancelled_cb=is_canc,
                )
                if self._is_cancelled:
                    self.failed_error.emit("Đã hủy tiến trình chia video.")
                    return

                if files:
                    self.finished_success.emit(files)
                else:
                    self.failed_error.emit(err or "Không thể chia video thành các phần.")

        except Exception as exc:
            self.failed_error.emit(f"Ngoại lệ khi xử lý video: {exc}")


# ====================================================================== #
#  Giao diện Dialog Cắt Video Ngắn                                       #
# ====================================================================== #
class VideoCutterDialog(QDialog):
    """Hộp thoại cắt video ngắn (Shorts / TikTok / Reels) & chia video đa năng."""

    _BG = "#0b0f19"
    _CARD = "#131a29"
    _CARD_BORDER = "#1f293d"
    _CYAN = "#06b6d4"
    _CYAN_HOVER = "#22d3ee"
    _GREEN = "#10b981"
    _ORANGE = "#f59e0b"
    _ROSE = "#f43f5e"
    _TEXT = "#f8fafc"
    _TEXT_MUTED = "#94a3b8"

    def __init__(self, parent=None, video_path: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("✂️ CẮT VIDEO NGẮN (SHORTS / TIKTOK) & CHIA VIDEO")
        self.setMinimumSize(780, 560)

        # Tính kích thước hợp lý dựa trên độ phân giải màn hình
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            target_w = min(880, avail.width() - 40)
            target_h = min(800, avail.height() - 50)
            self.resize(target_w, target_h)
        else:
            self.resize(880, 780)

        self.current_video_path = video_path or ""
        self.video_info = {}
        self.worker: Optional[VideoCutterWorker] = None
        self.last_output_files: List[str] = []

        self._apply_styling()
        self._init_ui()

        if self.current_video_path and os.path.isfile(self.current_video_path):
            self.path_input.setText(self.current_video_path)
            self._load_video_info(self.current_video_path)

    def _apply_styling(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self._BG};
                color: {self._TEXT};
            }}
            QWidget {{
                color: {self._TEXT};
                font-family: "Segoe UI", "Inter", -apple-system, sans-serif;
            }}
            QTabWidget::pane {{
                border: 1px solid {self._CARD_BORDER};
                background: {self._CARD};
                border-radius: 10px;
                top: -1px;
            }}
            QTabBar::tab {{
                background: #0f172a;
                color: {self._TEXT_MUTED};
                border: 1px solid {self._CARD_BORDER};
                border-bottom: none;
                padding: 10px 22px;
                font-size: 13px;
                font-weight: 700;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
            }}
            QTabBar::tab:selected {{
                background: {self._CARD};
                color: {self._CYAN};
                border-bottom: 2px solid {self._CYAN};
            }}
            QTabBar::tab:hover:!selected {{
                background: #1e293b;
                color: #e2e8f0;
            }}
            QLineEdit, QSpinBox {{
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f8fafc;
                font-size: 13px;
                min-height: 24px;
            }}
            QLineEdit:focus, QSpinBox:focus {{
                border: 1px solid {self._CYAN};
            }}
            QProgressBar {{
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 6px;
                text-align: center;
                color: #f8fafc;
                font-weight: bold;
                height: 18px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {self._CYAN}, stop:1 #3b82f6);
                border-radius: 5px;
            }}
            QRadioButton {{
                spacing: 8px;
                font-size: 12px;
                color: #e2e8f0;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}
            QRadioButton::indicator:checked {{
                background-color: {self._CYAN};
                border: 2px solid #0f172a;
                border-radius: 8px;
            }}
            QCheckBox {{
                spacing: 8px;
                font-size: 12px;
                color: {self._TEXT_MUTED};
            }}
            QCheckBox::indicator:checked {{
                background-color: {self._CYAN};
                border: 1px solid {self._CYAN};
                border-radius: 3px;
            }}
            QSlider::groove:horizontal {{
                border: 1px solid #334155;
                height: 6px;
                background: #0f172a;
                border-radius: 3px;
            }}
            QSlider::sub-page:horizontal {{
                background: {self._CYAN};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                border: 2px solid {self._CYAN};
                width: 16px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 8px;
            }}
            QScrollBar:vertical {{
                background: #090e18; width: 6px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #334155; min-height: 24px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {self._CYAN}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(8)

        # 1. Header (Cố định ở đỉnh)
        header_box = QHBoxLayout()
        header_box.setSpacing(10)

        icon_lbl = QLabel("✂️")
        icon_lbl.setStyleSheet("font-size: 26px;")
        header_box.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("CẮT VIDEO NGẮN (SHORTS / TIKTOK / REELS) & CHIA VIDEO")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {self._CYAN}; letter-spacing: 0.5px;")
        subtitle_lbl = QLabel("Cắt trích đoạn 1-2 phút chuẩn YouTube Shorts hoặc tự động chia video dài cực nhanh mà không nén lại.")
        subtitle_lbl.setStyleSheet(f"font-size: 11px; color: {self._TEXT_MUTED};")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(subtitle_lbl)

        header_box.addLayout(title_vbox)
        header_box.addStretch()
        root_layout.addLayout(header_box)

        # 2. Scroll Area bao trọn nội dung chính để không bao giờ bị cắt che
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        main_layout = QVBoxLayout(scroll_widget)
        main_layout.setContentsMargins(0, 2, 4, 2)
        main_layout.setSpacing(8)

        # --- 2.1 Khung chọn Video nguồn ---
        source_frame = QFrame()
        source_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self._CARD};
                border: 1px solid {self._CARD_BORDER};
                border-radius: 8px;
            }}
        """)
        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(10, 8, 10, 8)
        source_layout.setSpacing(6)

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        src_label = QLabel("🎬 Video nguồn:")
        src_label.setStyleSheet("font-weight: 700; font-size: 13px; color: #e2e8f0;")
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Chọn hoặc kéo thả tệp video vào đây...")
        self.path_input.setReadOnly(True)

        self.browse_btn = QPushButton("📂 Chọn video...")
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e293b; color: {self._TEXT}; font-weight: 700; font-size: 12px;
                border-radius: 6px; border: 1px solid #334155; padding: 6px 16px; min-height: 28px;
            }}
            QPushButton:hover {{ background: #334155; color: {self._CYAN}; border-color: {self._CYAN}; }}
        """)
        self.browse_btn.clicked.connect(self._on_browse_video)

        file_row.addWidget(src_label)
        file_row.addWidget(self.path_input, 1)
        file_row.addWidget(self.browse_btn)
        source_layout.addLayout(file_row)

        # Thẻ thông tin chi tiết video
        self.info_box = QFrame()
        self.info_box.setStyleSheet("""
            QFrame {
                background: #090e18;
                border: 1px solid #1e293b;
                border-radius: 6px;
            }
        """)
        info_layout = QHBoxLayout(self.info_box)
        info_layout.setContentsMargins(10, 6, 10, 6)
        info_layout.setSpacing(16)

        self.info_duration_lbl = QLabel("⏱️ Thời lượng: --:--")
        self.info_duration_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #38bdf8;")
        self.info_res_lbl = QLabel("📐 Độ phân giải: -- x --")
        self.info_res_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.info_ratio_lbl = QLabel("📱 Tỷ lệ: --")
        self.info_ratio_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.info_size_lbl = QLabel("💾 Dung lượng: -- MB")
        self.info_size_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")

        info_layout.addWidget(self.info_duration_lbl)
        info_layout.addWidget(self.info_res_lbl)
        info_layout.addWidget(self.info_ratio_lbl)
        info_layout.addWidget(self.info_size_lbl)
        info_layout.addStretch()

        source_layout.addWidget(self.info_box)
        main_layout.addWidget(source_frame)

        # --- 2.2 Tab Widget: Chia thành các Part (Mặc định) vs Cắt 1 đoạn lẻ ---
        self.tabs = QTabWidget()
        self.tab_split = QWidget()
        self.tab_clip = QWidget()

        self._setup_tab_split()
        self._setup_tab_clip()

        self.tabs.addTab(self.tab_split, "📦 Tự động chia video thành các Part (Khuyên dùng)")
        self.tabs.addTab(self.tab_clip, "✂️ Cắt 1 đoạn lẻ (Chỉ lấy 1 clip)")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tabs)

        # --- 2.3 Tùy chọn xuất (Output & Tên file & Mode) ---
        out_frame = QFrame()
        out_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self._CARD};
                border: 1px solid {self._CARD_BORDER};
                border-radius: 8px;
            }}
        """)
        out_layout = QVBoxLayout(out_frame)
        out_layout.setContentsMargins(10, 8, 10, 8)
        out_layout.setSpacing(6)

        # Hàng 1: Đổi tên video xuất (Tùy chọn)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel("🏷️ Đổi tên video:")
        name_lbl.setStyleSheet("font-weight: 700; font-size: 12px; color: #cbd5e1; min-width: 95px;")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Để trống nếu giữ nguyên tên video gốc...")
        self.name_input.textChanged.connect(self._on_name_input_changed)

        self.reset_name_btn = QPushButton("↺ Tên gốc")
        self.reset_name_btn.setToolTip("Xóa tên tùy chỉnh để lấy lại tên video gốc")
        self.reset_name_btn.setCursor(Qt.PointingHandCursor)
        self.reset_name_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e293b; color: {self._TEXT_MUTED}; font-size: 11px; font-weight: 600;
                border-radius: 5px; border: 1px solid #334155; padding: 5px 12px; min-height: 24px;
            }}
            QPushButton:hover {{ background: #334155; color: {self._CYAN}; }}
        """)
        self.reset_name_btn.clicked.connect(self._on_reset_name_clicked)

        name_row.addWidget(name_lbl)
        name_row.addWidget(self.name_input, 1)
        name_row.addWidget(self.reset_name_btn)
        out_layout.addLayout(name_row)

        # Hàng 2: Thư mục lưu
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_lbl = QLabel("📁 Thư mục lưu:")
        out_lbl.setStyleSheet("font-weight: 700; font-size: 12px; color: #cbd5e1; min-width: 95px;")
        self.out_dir_input = QLineEdit()
        self.out_dir_input.setPlaceholderText("Mặc định lưu cùng thư mục với video nguồn")
        self.browse_out_btn = QPushButton("Chọn...")
        self.browse_out_btn.setCursor(Qt.PointingHandCursor)
        self.browse_out_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e293b; color: {self._TEXT}; font-size: 11px; font-weight: 600;
                border-radius: 5px; border: 1px solid #334155; padding: 5px 12px; min-height: 24px;
            }}
            QPushButton:hover {{ background: #334155; color: {self._CYAN}; }}
        """)
        self.browse_out_btn.clicked.connect(self._on_browse_out_dir)

        out_row.addWidget(out_lbl)
        out_row.addWidget(self.out_dir_input, 1)
        out_row.addWidget(self.browse_out_btn)
        out_layout.addLayout(out_row)

        # Chế độ cắt (Xếp dọc 2 dòng rõ ràng, không bao giờ bị cắt chữ)
        mode_box = QVBoxLayout()
        mode_box.setSpacing(4)

        lbl_mode_title = QLabel("⚙️ Phương thức xử lý:")
        lbl_mode_title.setStyleSheet("font-weight: 700; font-size: 12px; color: #cbd5e1;")
        mode_box.addWidget(lbl_mode_title)

        self.mode_group = QButtonGroup(self)
        self.mode_copy_rb = QRadioButton("⚡ Siêu tốc (Stream Copy) — Cắt tức thì trong 1 giây, giữ 100% chất lượng gốc không nén lại (Khuyên dùng)")
        self.mode_copy_rb.setChecked(True)
        self.mode_copy_rb.setToolTip("Khuyên dùng: Cực nhanh, giữ nguyên toàn bộ độ phân giải và chất lượng video gốc.")

        self.mode_reencode_rb = QRadioButton("🎯 Chuẩn xác từng khung hình (Re-encode) — Khớp chính xác từng mili-giây (Dành cho video bị lệch keyframe)")
        self.mode_reencode_rb.setToolTip("Dành cho trường hợp muốn cắt chuẩn xác từng mili-giây hoặc video bị lỗi keyframe.")

        self.mode_group.addButton(self.mode_copy_rb)
        self.mode_group.addButton(self.mode_reencode_rb)

        mode_box.addWidget(self.mode_copy_rb)
        mode_box.addWidget(self.mode_reencode_rb)
        out_layout.addLayout(mode_box)

        self.auto_open_cb = QCheckBox("Tự động mở thư mục chứa video khi cắt xong")
        self.auto_open_cb.setChecked(True)
        out_layout.addWidget(self.auto_open_cb)

        main_layout.addWidget(out_frame)

        scroll.setWidget(scroll_widget)
        root_layout.addWidget(scroll, 1)

        # 3. Bottom Bar Cố định (Tiến trình + Các nút bấm điều khiển)
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet(f"""
            QFrame {{
                background: {self._CARD};
                border: 1px solid {self._CARD_BORDER};
                border-radius: 8px;
            }}
        """)
        bottom_layout = QVBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(10, 6, 10, 6)
        bottom_layout.setSpacing(4)

        # Thanh tiến trình và nhãn trạng thái
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.status_lbl = QLabel("Sẵn sàng.")
        self.status_lbl.setStyleSheet(f"font-size: 11px; color: {self._TEXT_MUTED};")
        self.status_lbl.setAlignment(Qt.AlignCenter)

        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addWidget(self.status_lbl)

        # Hàng nút điều khiển
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.open_folder_btn = QPushButton("📂 Mở thư mục đích")
        self.open_folder_btn.setCursor(Qt.PointingHandCursor)
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e293b; color: {self._CYAN}; font-weight: 700; font-size: 12px;
                border-radius: 6px; border: 1px solid {self._CYAN}; padding: 7px 16px; min-height: 32px;
            }}
            QPushButton:hover {{ background: #334155; }}
        """)
        self.open_folder_btn.clicked.connect(self._on_open_dest_folder)
        btn_row.addWidget(self.open_folder_btn)

        btn_row.addStretch()

        self.close_btn = QPushButton("Đóng")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1e293b; color: {self._TEXT_MUTED}; font-weight: 600; font-size: 13px;
                border-radius: 6px; border: 1px solid #334155; padding: 7px 20px; min-height: 32px;
            }}
            QPushButton:hover {{ background: #334155; color: #ffffff; }}
        """)
        self.close_btn.clicked.connect(self._on_close_clicked)
        btn_row.addWidget(self.close_btn)

        self.start_btn = QPushButton("🚀 BẮT ĐẦU CẮT VIDEO")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {self._CYAN}, stop:1 #2563eb);
                color: #ffffff; font-weight: 800; font-size: 13px;
                border-radius: 6px; border: none; padding: 8px 24px; min-height: 32px;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {self._CYAN_HOVER}, stop:1 #3b82f6);
            }}
            QPushButton:disabled {{
                background: #334155; color: #64748b;
            }}
        """)
        self.start_btn.clicked.connect(self._on_start_cutting)
        btn_row.addWidget(self.start_btn)

        bottom_layout.addLayout(btn_row)
        root_layout.addWidget(bottom_frame)

    # ------------------------------------------------------------------ #
    #  Tab 1: Cắt đoạn ngắn                                              #
    # ------------------------------------------------------------------ #
    def _setup_tab_clip(self):
        layout = QVBoxLayout(self.tab_clip)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # Lưu ý chế độ cắt lẻ
        hint_card = QFrame()
        hint_card.setStyleSheet("background: #0f172a; border-left: 3px solid #f59e0b; border-radius: 4px;")
        hint_lay = QHBoxLayout(hint_card)
        hint_lay.setContentsMargins(10, 6, 10, 6)
        hint_lbl = QLabel("💡 Lưu ý: Chế độ này chỉ trích xuất duy nhất 1 đoạn. Nếu muốn tự động chia cả video thành Part 1, Part 2... vui lòng chọn Tab '📦 Tự động chia video thành các Part' ở trên.")
        hint_lbl.setStyleSheet("font-size: 11px; color: #cbd5e1;")
        hint_lbl.setWordWrap(True)
        hint_lay.addWidget(hint_lbl)
        layout.addWidget(hint_card)

        # Hàng 1: Thời điểm bắt đầu
        start_row = QHBoxLayout()
        start_row.setSpacing(8)

        lbl_start = QLabel("📍 Bắt đầu từ:")
        lbl_start.setStyleSheet("font-weight: 700; font-size: 13px; color: #e2e8f0; min-width: 100px;")
        start_row.addWidget(lbl_start)

        self.start_m_spin = QSpinBox()
        self.start_m_spin.setRange(0, 9999)
        self.start_m_spin.setSuffix(" phút")
        self.start_m_spin.setValue(0)
        self.start_m_spin.valueChanged.connect(self._update_clip_summary)

        self.start_s_spin = QSpinBox()
        self.start_s_spin.setRange(0, 59)
        self.start_s_spin.setSuffix(" giây")
        self.start_s_spin.setValue(0)
        self.start_s_spin.valueChanged.connect(self._update_clip_summary)

        start_row.addWidget(self.start_m_spin)
        start_row.addWidget(self.start_s_spin)

        # Quick preset buttons cho Start
        quick_0_btn = QPushButton("Đầu video (00:00)")
        quick_0_btn.setCursor(Qt.PointingHandCursor)
        quick_0_btn.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 11px; font-weight: 600; border: 1px solid #334155; border-radius: 4px; padding: 4px 10px; min-height: 26px;")
        quick_0_btn.clicked.connect(lambda: self._set_start_sec(0))
        start_row.addWidget(quick_0_btn)

        quick_30_btn = QPushButton("00:30")
        quick_30_btn.setCursor(Qt.PointingHandCursor)
        quick_30_btn.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 11px; font-weight: 600; border: 1px solid #334155; border-radius: 4px; padding: 4px 10px; min-height: 26px;")
        quick_30_btn.clicked.connect(lambda: self._set_start_sec(30))
        start_row.addWidget(quick_30_btn)

        quick_60_btn = QPushButton("01:00")
        quick_60_btn.setCursor(Qt.PointingHandCursor)
        quick_60_btn.setStyleSheet("background: #1e293b; color: #94a3b8; font-size: 11px; font-weight: 600; border: 1px solid #334155; border-radius: 4px; padding: 4px 10px; min-height: 26px;")
        quick_60_btn.clicked.connect(lambda: self._set_start_sec(60))
        start_row.addWidget(quick_60_btn)

        start_row.addStretch()
        layout.addLayout(start_row)

        # Slider chọn điểm bắt đầu
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, 300)
        self.start_slider.setValue(0)
        self.start_slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.start_slider)

        # Hàng 2: Thời lượng đoạn cắt (Preset 1-Click chuẩn Shorts)
        dur_vbox = QVBoxLayout()
        dur_vbox.setSpacing(6)

        lbl_dur = QLabel("⏱️ Thời lượng đoạn cắt (Chọn nhanh chuẩn Shorts / Reels):")
        lbl_dur.setStyleSheet("font-weight: 700; font-size: 13px; color: #e2e8f0;")
        dur_vbox.addWidget(lbl_dur)

        presets_grid = QGridLayout()
        presets_grid.setSpacing(6)

        preset_configs = [
            ("⚡ 30 Giây", 30, "#1e293b"),
            ("🔥 60s (Shorts)", 60, "#0e7490"),
            ("⚡ 90s (1.5p)", 90, "#1e293b"),
            ("⚡ 120s (2p)", 120, "#1e293b"),
            ("⚡ 3 Phút", 180, "#1e293b"),
            ("⚡ 5 Phút", 300, "#1e293b"),
        ]

        for col, (text, sec, bg) in enumerate(preset_configs):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg}; color: #f8fafc; font-weight: 700; font-size: 11px;
                    border-radius: 6px; border: 1px solid #475569; padding: 6px 6px;
                }}
                QPushButton:hover {{ background: {self._CYAN}; color: #0b0f19; border-color: {self._CYAN}; }}
            """)
            btn.clicked.connect(lambda checked=False, s=sec: self._set_duration_sec(s))
            presets_grid.addWidget(btn, 0, col)

        dur_vbox.addLayout(presets_grid)
        layout.addLayout(dur_vbox)

        # Hàng kết hợp: Tùy chỉnh thời lượng & Đánh số Part
        combined_row = QHBoxLayout()
        combined_row.setSpacing(10)

        lbl_custom = QLabel("Tùy chỉnh thời lượng:")
        lbl_custom.setStyleSheet("font-size: 12px; color: #94a3b8;")
        combined_row.addWidget(lbl_custom)

        self.dur_m_spin = QSpinBox()
        self.dur_m_spin.setRange(0, 9999)
        self.dur_m_spin.setSuffix(" phút")
        self.dur_m_spin.setValue(1)
        self.dur_m_spin.valueChanged.connect(self._update_clip_summary)

        self.dur_s_spin = QSpinBox()
        self.dur_s_spin.setRange(0, 59)
        self.dur_s_spin.setSuffix(" giây")
        self.dur_s_spin.setValue(0)
        self.dur_s_spin.valueChanged.connect(self._update_clip_summary)

        combined_row.addWidget(self.dur_m_spin)
        combined_row.addWidget(self.dur_s_spin)

        # Phân tách nhẹ
        combined_row.addSpacing(14)

        lbl_part = QLabel("🏷️ Đánh số Part:")
        lbl_part.setStyleSheet("font-weight: 700; font-size: 12px; color: #e2e8f0;")
        combined_row.addWidget(lbl_part)

        self.part_spin = QSpinBox()
        self.part_spin.setRange(1, 9999)
        self.part_spin.setPrefix("Part ")
        self.part_spin.setValue(1)
        self.part_spin.setMinimumHeight(26)
        self.part_spin.valueChanged.connect(self._update_clip_summary)
        combined_row.addWidget(self.part_spin)

        self.auto_inc_part_cb = QCheckBox("Tự tăng Part (Part 01 ➔ 02...)")
        self.auto_inc_part_cb.setChecked(True)
        self.auto_inc_part_cb.setToolTip("Tự động tăng số Part và chuyển mốc bắt đầu sang đoạn tiếp theo sau mỗi lần cắt")
        self.auto_inc_part_cb.setStyleSheet(f"font-size: 11px; color: {self._CYAN}; font-weight: 600;")
        combined_row.addWidget(self.auto_inc_part_cb)

        combined_row.addStretch()
        layout.addLayout(combined_row)

        # Khung tóm tắt đoạn cắt trực quan
        self.clip_summary_card = QFrame()
        self.clip_summary_card.setObjectName("clipSummaryCard")
        self.clip_summary_card.setStyleSheet(f"""
            QFrame#clipSummaryCard {{
                background: #090e18;
                border: 1px solid {self._CYAN};
                border-radius: 8px;
            }}
            QFrame#clipSummaryCard QLabel {{
                border: none;
                background: transparent;
            }}
        """)
        sum_layout = QVBoxLayout(self.clip_summary_card)
        sum_layout.setContentsMargins(12, 8, 12, 8)
        sum_layout.setSpacing(3)

        self.clip_summary_title = QLabel("✂️ THÔNG TIN ĐOẠN CẮT:")
        self.clip_summary_title.setStyleSheet(f"font-weight: 800; font-size: 12px; color: {self._CYAN};")
        self.clip_summary_text = QLabel("Đoạn cắt từ [00:00] đến [01:00] (Tổng: 1 phút 0 giây)")
        self.clip_summary_text.setStyleSheet("font-size: 13px; font-weight: 700; color: #f8fafc;")
        self.clip_summary_text.setWordWrap(True)

        self.clip_preview_file_lbl = QLabel("📁 Tên tệp xuất dự kiến: --")
        self.clip_preview_file_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: #38bdf8;")
        self.clip_preview_file_lbl.setWordWrap(True)

        self.clip_badge_lbl = QLabel("🔥 Phù hợp: YouTube Shorts (≤ 60s), TikTok, Facebook Reels")
        self.clip_badge_lbl.setStyleSheet(f"font-size: 11px; color: {self._GREEN}; font-weight: 600;")

        sum_layout.addWidget(self.clip_summary_title)
        sum_layout.addWidget(self.clip_summary_text)
        sum_layout.addWidget(self.clip_preview_file_lbl)
        sum_layout.addWidget(self.clip_badge_lbl)
        layout.addWidget(self.clip_summary_card)

    # ------------------------------------------------------------------ #
    #  Tab 2: Chia toàn bộ video thành nhiều phần                        #
    # ------------------------------------------------------------------ #
    def _setup_tab_split(self):
        layout = QVBoxLayout(self.tab_split)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        desc_lbl = QLabel(
            "Tự động chia cả video thành các phần nhỏ liên tiếp (Part 001, Part 002...)\n"
            "Thích hợp khi có video 5-10 phút muốn chia thành từng tập Shorts/Clip 1-2 phút hoặc chia video dài nhiều giờ."
        )
        desc_lbl.setStyleSheet(f"font-size: 12px; color: {self._TEXT_MUTED};")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Chọn thời lượng mỗi phần (Bố trí dạng lưới 2 hàng gọn gàng, có setMinimumHeight)
        dur_vbox = QVBoxLayout()
        dur_vbox.setSpacing(6)

        lbl_split = QLabel("⏱️ Thời lượng mỗi phần (Chọn nhanh):")
        lbl_split.setStyleSheet("font-weight: 700; font-size: 13px; color: #e2e8f0;")
        dur_vbox.addWidget(lbl_split)

        presets_grid = QGridLayout()
        presets_grid.setSpacing(6)

        split_configs = [
            ("60s (1 Phút)", 60, 0, 0),
            ("2 Phút", 120, 0, 1),
            ("3 Phút", 180, 0, 2),
            ("5 Phút", 300, 0, 3),
            ("10 Phút", 600, 1, 0),
            ("15 Phút", 900, 1, 1),
            ("30 Phút", 1800, 1, 2),
            ("60 Phút (1h)", 3600, 1, 3),
        ]

        for text, sec, r, c in split_configs:
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e293b; color: #f8fafc; font-weight: 600; font-size: 11px;
                    border-radius: 6px; border: 1px solid #334155; padding: 6px 10px;
                }
                QPushButton:hover { background: #0891b2; border-color: #0891b2; color: #ffffff; }
            """)
            btn.clicked.connect(lambda checked=False, s=sec: self._set_split_segment_sec(s))
            presets_grid.addWidget(btn, r, c)

        dur_vbox.addLayout(presets_grid)
        layout.addLayout(dur_vbox)

        # Tùy chỉnh giây/phút cho split + Checkbox gộp đoạn thừa
        split_custom_row = QHBoxLayout()
        split_custom_row.setSpacing(10)

        lbl_sp_custom = QLabel("Tùy chỉnh thời lượng mỗi đoạn:")
        lbl_sp_custom.setStyleSheet("font-size: 12px; color: #94a3b8;")
        split_custom_row.addWidget(lbl_sp_custom)

        self.split_m_spin = QSpinBox()
        self.split_m_spin.setRange(0, 9999)
        self.split_m_spin.setSuffix(" phút")
        self.split_m_spin.setValue(2)  # Mặc định 2 phút
        self.split_m_spin.valueChanged.connect(self._update_split_summary)

        self.split_s_spin = QSpinBox()
        self.split_s_spin.setRange(0, 59)
        self.split_s_spin.setSuffix(" giây")
        self.split_s_spin.setValue(0)
        self.split_s_spin.valueChanged.connect(self._update_split_summary)

        split_custom_row.addWidget(self.split_m_spin)
        split_custom_row.addWidget(self.split_s_spin)

        split_custom_row.addSpacing(14)

        self.merge_remainder_cb = QCheckBox("Gộp phần dư cuối vào Part cuối (tránh tạo clip cụt lủn)")
        self.merge_remainder_cb.setChecked(True)
        self.merge_remainder_cb.setStyleSheet(f"font-size: 11px; color: {self._CYAN}; font-weight: 600;")
        self.merge_remainder_cb.setToolTip("Ví dụ: Video 4p30s chia 2p -> Ra Part 1: 2p và Part 2: 2p30s (gộp 30s dư vào Part 2)")
        self.merge_remainder_cb.toggled.connect(self._update_split_summary)
        split_custom_row.addWidget(self.merge_remainder_cb)

        split_custom_row.addStretch()
        layout.addLayout(split_custom_row)

        # Card tóm tắt số phần chia
        self.split_summary_card = QFrame()
        self.split_summary_card.setObjectName("splitSummaryCard")
        self.split_summary_card.setMinimumHeight(60)
        self.split_summary_card.setStyleSheet("""
            QFrame#splitSummaryCard {
                background: #090e18;
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
            QFrame#splitSummaryCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        sp_sum_layout = QVBoxLayout(self.split_summary_card)
        sp_sum_layout.setContentsMargins(12, 8, 12, 8)
        sp_sum_layout.setSpacing(3)

        self.split_summary_title = QLabel("📦 KẾT QUẢ CHIA DỰ KIẾN:")
        self.split_summary_title.setStyleSheet(f"font-weight: 800; font-size: 12px; color: {self._GREEN};")
        self.split_summary_text = QLabel("💡 Chọn video nguồn ở trên để xem ước tính số phần sẽ chia tự động.")
        self.split_summary_text.setStyleSheet("font-size: 13px; font-weight: 700; color: #f8fafc;")
        self.split_summary_text.setWordWrap(True)

        sp_sum_layout.addWidget(self.split_summary_title)
        sp_sum_layout.addWidget(self.split_summary_text)
        layout.addWidget(self.split_summary_card)

    # ------------------------------------------------------------------ #
    #  Logic xử lý & Tương tác                                           #
    # ------------------------------------------------------------------ #
    def _on_browse_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn video cần cắt hoặc chia",
            "",
            "Tệp video (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.ts);;Tất cả tệp (*.*)",
        )
        if file_path:
            self.path_input.setText(file_path)
            self._load_video_info(file_path)

    def _on_browse_out_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục lưu video sau khi cắt",
            self.out_dir_input.text() or (os.path.dirname(self.current_video_path) if self.current_video_path else ""),
        )
        if dir_path:
            self.out_dir_input.setText(dir_path)

    def _on_name_input_changed(self, _text: str):
        self._update_clip_summary()
        self._update_split_summary()

    def _on_reset_name_clicked(self):
        self.name_input.clear()
        self._update_clip_summary()
        self._update_split_summary()

    def _get_effective_base_name(self) -> str:
        """Lấy tên tệp cơ sở: ưu tiên tên người dùng nhập, nếu không có thì lấy tên video nguồn."""
        custom = sanitize_filename(self.name_input.text().strip())
        if custom:
            return custom
        if self.current_video_path:
            raw_base = os.path.splitext(os.path.basename(self.current_video_path))[0]
            clean = sanitize_filename(raw_base)
            if clean:
                return clean
        return "video"

    def _load_video_info(self, video_path: str):
        self.current_video_path = video_path
        if not os.path.isfile(video_path):
            return

        self.status_lbl.setText("Đang phân tích thông số video...")
        QApplication.processEvents()

        self.video_info = probe_video_details(video_path)
        dur = self.video_info.get("duration", 0.0)
        w = self.video_info.get("width", 0)
        h = self.video_info.get("height", 0)
        fps = self.video_info.get("fps", 30.0)
        size_mb = self.video_info.get("size_mb", 0.0)
        ratio = self.video_info.get("aspect_ratio", "")

        # Cập nhật nhãn
        self.info_duration_lbl.setText(f"⏱️ Thời lượng: {format_seconds(dur)} ({format_seconds_detailed(dur)})")
        self.info_res_lbl.setText(f"📐 Độ phân giải: {w} x {h} ({fps} fps)")
        self.info_ratio_lbl.setText(f"📱 Tỷ lệ: {ratio}")
        self.info_size_lbl.setText(f"💾 Dung lượng: {size_mb} MB")

        # Cập nhật slider max
        self.start_slider.blockSignals(True)
        self.start_slider.setRange(0, max(1, int(dur)))
        self.start_slider.setValue(0)
        self.start_slider.blockSignals(False)

        # Cập nhật gợi ý tên video và thư mục lưu mặc định
        raw_name = os.path.splitext(os.path.basename(video_path))[0]
        short_name = (raw_name[:35] + "...") if len(raw_name) > 35 else raw_name
        self.name_input.setPlaceholderText(f"Mặc định theo video gốc: {short_name}")

        if not self.out_dir_input.text():
            self.out_dir_input.setText(os.path.dirname(os.path.abspath(video_path)))

        self._update_clip_summary()
        self._update_split_summary()
        self.status_lbl.setText("Sẵn sàng cắt video.")

    def _set_start_sec(self, seconds: int):
        self.start_m_spin.blockSignals(True)
        self.start_s_spin.blockSignals(True)
        self.start_m_spin.setValue(seconds // 60)
        self.start_s_spin.setValue(seconds % 60)
        self.start_m_spin.blockSignals(False)
        self.start_s_spin.blockSignals(False)

        self.start_slider.blockSignals(True)
        self.start_slider.setValue(seconds)
        self.start_slider.blockSignals(False)

        self._update_clip_summary()

    def _set_duration_sec(self, seconds: int):
        self.dur_m_spin.blockSignals(True)
        self.dur_s_spin.blockSignals(True)
        self.dur_m_spin.setValue(seconds // 60)
        self.dur_s_spin.setValue(seconds % 60)
        self.dur_m_spin.blockSignals(False)
        self.dur_s_spin.blockSignals(False)

        self._update_clip_summary()

    def _set_split_segment_sec(self, seconds: int):
        self.split_m_spin.blockSignals(True)
        self.split_s_spin.blockSignals(True)
        self.split_m_spin.setValue(seconds // 60)
        self.split_s_spin.setValue(seconds % 60)
        self.split_m_spin.blockSignals(False)
        self.split_s_spin.blockSignals(False)

        self._update_split_summary()

    def _on_slider_changed(self, val: int):
        self.start_m_spin.blockSignals(True)
        self.start_s_spin.blockSignals(True)
        self.start_m_spin.setValue(val // 60)
        self.start_s_spin.setValue(val % 60)
        self.start_m_spin.blockSignals(False)
        self.start_s_spin.blockSignals(False)

        self._update_clip_summary()

    def _get_start_sec(self) -> float:
        return float(self.start_m_spin.value() * 60 + self.start_s_spin.value())

    def _get_duration_sec(self) -> float:
        return float(self.dur_m_spin.value() * 60 + self.dur_s_spin.value())

    def _get_split_sec(self) -> float:
        return float(self.split_m_spin.value() * 60 + self.split_s_spin.value())

    def _on_tab_changed(self, idx: int):
        if idx == 0:
            self._update_split_summary()
        else:
            self._update_clip_summary()

    def _update_clip_summary(self):
        start_s = self._get_start_sec()
        dur_s = self._get_duration_sec()
        end_s = start_s + dur_s

        self.start_slider.blockSignals(True)
        self.start_slider.setValue(int(start_s))
        self.start_slider.blockSignals(False)

        total_dur = self.video_info.get("duration", 0.0)
        if total_dur > 0 and end_s > total_dur:
            warning = f" (⚠️ Vượt quá thời lượng video {format_seconds(total_dur)}, sẽ tự lấy đến hết video)"
        else:
            warning = ""

        self.clip_summary_text.setText(
            f"Đoạn cắt từ [{format_seconds(start_s)}] đến [{format_seconds(end_s)}] "
            f"(Tổng: {format_seconds_detailed(dur_s)}){warning}"
        )

        # Hiển thị trước tên file xuất có Part rõ ràng
        base_name = self._get_effective_base_name()
        ext = os.path.splitext(self.current_video_path)[1] if self.current_video_path else ".mp4"
        part_tag = f"Part{self.part_spin.value():02d}"
        preview_filename = f"{base_name}_{part_tag}{ext}"
        self.clip_preview_file_lbl.setText(f"📁 Tên tệp xuất dự kiến: {preview_filename}")

        if dur_s <= 60:
            self.clip_badge_lbl.setText("🔥 Rất tốt: Chuẩn YouTube Shorts (≤ 60s), TikTok, Facebook Reels")
            self.clip_badge_lbl.setStyleSheet(f"font-size: 11px; color: {self._GREEN}; font-weight: 600; background: transparent;")
        elif dur_s <= 180:
            self.clip_badge_lbl.setText("⚡ Phù hợp TikTok (đến 3-10p) hoặc video ngắn Facebook/YouTube")
            self.clip_badge_lbl.setStyleSheet("font-size: 11px; color: #38bdf8; font-weight: 600; background: transparent;")
        else:
            self.clip_badge_lbl.setText("🎬 Phù hợp video thông thường / highlight")
            self.clip_badge_lbl.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: 600; background: transparent;")

        if self.tabs.currentIndex() == 1:
            self.start_btn.setText(f"🚀 CẮT TRÍCH XUẤT 1 ĐOẠN ({part_tag})")

    def _update_split_summary(self):
        seg_s = self._get_split_sec()
        total_dur = self.video_info.get("duration", 0.0)

        if total_dur <= 0:
            self.split_summary_text.setText("💡 Chọn video nguồn ở trên để xem ước tính số phần sẽ chia tự động.")
            if self.tabs.currentIndex() == 0:
                self.start_btn.setText("🚀 BẮT ĐẦU CHIA VIDEO THÀNH CÁC PART")
            return

        if seg_s <= 0:
            self.split_summary_text.setText("⚠️ Thời lượng mỗi đoạn phải lớn hơn 0 giây.")
            return

        merge_rem = self.merge_remainder_cb.isChecked() if hasattr(self, "merge_remainder_cb") else True
        ranges = calculate_split_ranges(total_dur, seg_s, merge_remainder=merge_rem)
        num_parts = len(ranges)
        base_name = self._get_effective_base_name()
        ext = os.path.splitext(self.current_video_path)[1] if self.current_video_path else ".mp4"
        part_end_tag = f"Part{num_parts:02d}" if num_parts < 100 else f"Part{num_parts:03d}"

        parts_detail = []
        for i, (p_start, p_dur) in enumerate(ranges, 1):
            p_end = p_start + p_dur
            parts_detail.append(f"• Part {i:02d}: {format_seconds(p_start)} ➔ {format_seconds(p_end)} ({format_seconds_detailed(p_dur)})")

        if len(parts_detail) <= 4:
            detail_str = "\n".join(parts_detail)
        else:
            detail_str = "\n".join(parts_detail[:2]) + f"\n... ({num_parts - 3} phần ở giữa) ...\n" + parts_detail[-1]

        self.split_summary_text.setText(
            f"🎬 Video dài {format_seconds(total_dur)} ({format_seconds_detailed(total_dur)}) ➔ Tự động chia thành {num_parts} phần (mỗi phần ~{format_seconds_detailed(seg_s)}):\n"
            f"{detail_str}\n"
            f"📁 Tệp xuất: {base_name}_Part01{ext}  ➔  {base_name}_{part_end_tag}{ext}"
        )

        if self.tabs.currentIndex() == 0:
            self.start_btn.setText(f"🚀 BẮT ĐẦU CHIA THÀNH {num_parts} PART")

    # ------------------------------------------------------------------ #
    #  Bắt đầu cắt / chia                                                #
    # ------------------------------------------------------------------ #
    def _on_start_cutting(self):
        if not self.current_video_path or not os.path.isfile(self.current_video_path):
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn một tệp video hợp lệ trước khi bắt đầu.")
            return

        is_split_mode = (self.tabs.currentIndex() == 0)
        mode = "copy" if self.mode_copy_rb.isChecked() else "reencode"
        out_dir = self.out_dir_input.text().strip() or os.path.dirname(os.path.abspath(self.current_video_path))
        os.makedirs(out_dir, exist_ok=True)

        base_name = self._get_effective_base_name()
        ext = os.path.splitext(self.current_video_path)[1] or ".mp4"

        if is_split_mode:
            seg_s = self._get_split_sec()
            if seg_s < 5.0:
                QMessageBox.warning(self, "Thời lượng quá ngắn", "Mỗi phần chia nhỏ tối thiểu phải từ 5 giây trở lên.")
                return

            merge_rem = self.merge_remainder_cb.isChecked() if hasattr(self, "merge_remainder_cb") else True
            self._launch_worker(
                task_type="split",
                video_path=self.current_video_path,
                output_path=out_dir,
                segment_sec=seg_s,
                mode=mode,
                custom_base_name=base_name,
                merge_remainder=merge_rem,
            )
        else:
            start_s = self._get_start_sec()
            dur_s = self._get_duration_sec()

            if dur_s <= 0.5:
                QMessageBox.warning(self, "Thời lượng không hợp lệ", "Thời lượng đoạn cắt phải từ 1 giây trở lên.")
                return

            part_num = self.part_spin.value()
            part_tag = f"Part{part_num:02d}"
            out_name = f"{base_name}_{part_tag}{ext}"
            out_file = os.path.join(out_dir, out_name)

            # Nếu file đã tồn tại thì tự động đánh số tiếp _1, _2... tránh ghi đè làm mất clip của người dùng
            if os.path.isfile(out_file):
                i = 1
                while os.path.isfile(os.path.join(out_dir, f"{base_name}_{part_tag}_{i}{ext}")):
                    i += 1
                out_file = os.path.join(out_dir, f"{base_name}_{part_tag}_{i}{ext}")

            self._launch_worker(
                task_type="clip",
                video_path=self.current_video_path,
                output_path=out_file,
                start_sec=start_s,
                duration_sec=dur_s,
                mode=mode,
            )

    def _launch_worker(self, task_type: str, video_path: str, output_path: str, **kwargs):
        self.start_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setText("Đang chuẩn bị...")
        self.open_folder_btn.setVisible(False)
        self.close_btn.setText("Hủy")

        self.worker = VideoCutterWorker(
            task_type=task_type,
            video_path=video_path,
            output_path=output_path,
            **kwargs,
            parent=self,
        )
        self.worker.progress_changed.connect(self._on_worker_progress)
        self.worker.finished_success.connect(self._on_worker_success)
        self.worker.failed_error.connect(self._on_worker_failed)
        self.worker.start()

    def _on_worker_progress(self, pct: float, msg: str):
        self.progress_bar.setValue(int(pct))
        self.status_lbl.setText(msg)

    def _on_worker_success(self, files: List[str]):
        self.last_output_files = files
        self.progress_bar.setValue(100)
        self.status_lbl.setText(f"🎉 Hoàn tất! Đã tạo thành công {len(files)} tệp video.")
        self.status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {self._GREEN};")

        self.start_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.close_btn.setText("Đóng")
        self.open_folder_btn.setVisible(True)

        # Nếu vừa cắt clip ngắn thành công và bật tự động tăng Part:
        if self.worker and self.worker.task_type == "clip":
            if self.auto_inc_part_cb.isChecked():
                self.part_spin.blockSignals(True)
                self.part_spin.setValue(self.part_spin.value() + 1)
                self.part_spin.blockSignals(False)

                # Tự động nhảy điểm bắt đầu tiếp theo bằng điểm kết thúc vừa cắt
                next_start = int(self.worker.start_sec + self.worker.duration_sec)
                total_dur = self.video_info.get("duration", 0.0)
                if total_dur > 0 and next_start < total_dur:
                    self._set_start_sec(next_start)
                else:
                    self._update_clip_summary()

        if self.auto_open_cb.isChecked() and files:
            self._on_open_dest_folder()

        file_list_str = "\n".join([f"• {os.path.basename(f)}" for f in files[:5]])
        if len(files) > 5:
            file_list_str += f"\n• ...và {len(files) - 5} tệp khác"

        QMessageBox.information(
            self,
            "Cắt video thành công",
            f"Đã xuất thành công {len(files)} tệp video:\n\n{file_list_str}\n\n"
            f"Vị trí lưu: {os.path.dirname(files[0]) if files else ''}",
        )

    def _on_worker_failed(self, err_msg: str):
        self.status_lbl.setText(f"❌ {err_msg}")
        self.status_lbl.setStyleSheet(f"font-size: 12px; color: {self._ROSE};")
        self.start_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.close_btn.setText("Đóng")
        QMessageBox.critical(self, "Lỗi cắt video", f"Không thể hoàn thành thao tác:\n{err_msg}")

    def _on_open_dest_folder(self):
        target_dir = ""
        if self.last_output_files and os.path.exists(self.last_output_files[0]):
            target_file = os.path.abspath(self.last_output_files[0])
            if sys.platform == "win32":
                subprocess.Popen(f'explorer /select,"{target_file}"')
                return
            target_dir = os.path.dirname(target_file)
        else:
            target_dir = self.out_dir_input.text().strip()

        if target_dir and os.path.isdir(target_dir):
            if sys.platform == "win32":
                os.startfile(target_dir)
            else:
                subprocess.Popen(["xdg-open", target_dir])

    def _on_close_clicked(self):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Xác nhận hủy",
                "Tiến trình cắt video đang chạy. Bạn có chắc chắn muốn hủy không?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.worker.cancel()
                self.worker.wait(2000)
                self.reject()
        else:
            self.accept()
