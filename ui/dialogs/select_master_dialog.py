"""Dialog for selecting an approved Master Dataset before starting Pipeline B."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QBrush, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.master_dataset import MasterDataset
from app.services.master_validator import validate_master_dataset, MasterValidationResult
from app.services.master_dataset_service import MasterDatasetService
from app.services.video_export_service import SubtitleRenderConfig
from app.services.voice_mapping_service import (
    VoiceMappingService,
    VoiceMappingConfig,
    VoiceAssignment,
)
from ui.widgets.subtitle_style_group import SubtitleStyleGroupBox

logger = logging.getLogger(__name__)


def _load_available_voices() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Tự động nạp toàn bộ danh sách giọng TTS hiện có trên hệ thống,

    loại bỏ các vế mở ngoặc và loại bỏ các giọng CapCut.
    Luôn đặt 'Cô gái hoạt ngôn' và 'Thanh niên tự tin' ở vị trí đầu tiên.
    """

    females: list[dict[str, str]] = [
        {"id": "vieneu_cogai_moi", "name": "Cô Gái Mới (VieNeu AI)", "provider": "vieneu"},
        {"id": "vieneu_cogaihoatbat", "name": "Cô gái hoạt ngôn", "provider": "vieneu"},
    ]
    males: list[dict[str, str]] = [
        {"id": "vieneu_thanhnientutin", "name": "Thanh niên tự tin", "provider": "vieneu"},
    ]
    seen_ids = {"vieneu_cogaihoatbat", "vieneu_thanhnientutin"}

    # Nạp từ models/piper/voices.json (gộp bản người dùng tải + bản đi kèm app)
    from app.runtime_paths import load_piper_voice_manifest, models_path
    manifest_items = load_piper_voice_manifest("piper")
    manifest_path = models_path("piper", "voices.json")
    if not manifest_items and not os.path.isfile(manifest_path):
        manifest_path = os.path.join(os.getcwd(), "models", "piper", "voices.json")
    if manifest_items or os.path.isfile(manifest_path):
        try:
            if manifest_items:
                data = manifest_items
            else:
                with open(manifest_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    raw_name = str(item.get("name", "") or "").strip()
                    if not raw_name:
                        continue
                    audio_path = str(item.get("audio_path", "") or "").replace("\\", "/").strip()
                    v_id = os.path.splitext(os.path.basename(audio_path))[0]
                    if not v_id or v_id in seen_ids:
                        continue
                    if "capcut" in v_id.lower() or "capcut" in raw_name.lower():
                        continue

                    # Bỏ các vế sau trong ngoặc đơn
                    clean_name = re.sub(r"\s*\([^)]*\)", "", raw_name).strip()
                    if v_id == "ngochuyennew":
                        clean_name = "Ngọc Huyền New"
                    elif v_id == "yannew":
                        clean_name = "Yan New"

                    gender = str(item.get("gender", "") or "").lower().strip()
                    entry = {"id": v_id, "name": clean_name, "provider": "piper"}
                    seen_ids.add(v_id)
                    if gender == "male":
                        males.append(entry)
                    elif gender == "female":
                        females.append(entry)
        except Exception:
            pass

    # Bổ sung các giọng Edge nếu chưa có
    if "vi-VN-NamMinhNeural" not in seen_ids:
        males.append({"id": "vi-VN-NamMinhNeural", "name": "Nam Minh", "provider": "edge"})
        seen_ids.add("vi-VN-NamMinhNeural")
    if "vi-VN-HoaiMyNeural" not in seen_ids:
        females.append({"id": "vi-VN-HoaiMyNeural", "name": "Hoài My", "provider": "edge"})
        seen_ids.add("vi-VN-HoaiMyNeural")

    # Giữ giọng mặc định VieNeu ở vị trí đầu tiên [0], các giọng còn lại sắp xếp A-Z
    males_sorted = [males[0]] + sorted(males[1:], key=lambda x: x["name"].lower())
    females_sorted = [females[0]] + sorted(females[1:], key=lambda x: x["name"].lower())

    return females_sorted, males_sorted


AVAILABLE_FEMALE_VOICES, AVAILABLE_MALE_VOICES = _load_available_voices()


def _format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds or 0.0) * 1000)))
    total_sec, ms = divmod(total_ms, 1000)
    minutes, sec = divmod(total_sec, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{ms // 100:01d}"
    return f"{minutes:02d}:{sec:02d}.{ms // 100:01d}"


class SelectMasterDialog(QDialog):
    """Hộp thoại duyệt và chọn bản Master Data (Single Source of Truth) đã kiểm duyệt

    từ Pipeline A để đưa vào luồng tạo video của Pipeline B.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        workspace_root: str = "",
        video_path: str = "",
        project_root: str = "",
        voice_config: VoiceMappingConfig | None = None,
        initial_orig_volume: int = 50,
        initial_tts_volume: int = 100,
    ):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.project_root = project_root
        self.voice_config = voice_config or VoiceMappingConfig()
        self.voice_mapper = VoiceMappingService(self.voice_config)
        self.initial_orig_volume = max(0, min(100, int(initial_orig_volume)))
        self.initial_tts_volume = max(0, min(200, int(initial_tts_volume)))

        self.datasets: list[MasterDataset] = []
        self.selected_dataset: MasterDataset | None = None
        self.current_validation: MasterValidationResult | None = None

        self.setWindowTitle("Chọn bản phụ đề đã kiểm duyệt (Master Data) - Pipeline B")
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            target_w = max(1180, min(1380, int(geo.width() * 0.88)))
            target_h = max(780, min(920, int(geo.height() * 0.90)))
            self.resize(target_w, target_h)
        else:
            self.resize(1280, 840)
        self.setMinimumSize(1080, 650)

        self._setup_ui()
        self._apply_dark_style()
        self._load_datasets()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        # Header giới thiệu
        header_layout = QHBoxLayout()
        title_label = QLabel("🎬 Chọn bản Master Data đã kiểm duyệt để tạo video")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        root_layout.addLayout(header_layout)

        # Splitter chính chia 2 cột: Danh sách bên trái, Chi tiết bên phải
        splitter = QSplitter(Qt.Horizontal)

        # --- CỘT BÊN TRÁI: Danh sách các bản Master Data ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        left_label = QLabel("Danh sách bản lưu hoàn chỉnh:")
        left_label.setStyleSheet("font-weight: bold; color: #cfd6df;")
        left_layout.addWidget(left_label)

        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(180)
        self.list_widget.currentRowChanged.connect(self._on_dataset_selected)
        left_layout.addWidget(self.list_widget)

        # --- KHUNG CHỌN GIỌNG ĐỌC LỒNG TIẾNG (TTS) ---
        voice_select_group = QGroupBox("Chọn giọng đọc lồng tiếng (TTS)")
        voice_select_layout = QVBoxLayout(voice_select_group)
        voice_select_layout.setSpacing(6)
        voice_select_layout.setContentsMargins(10, 10, 10, 10)

        # Hàng Giọng Nữ
        row_female = QHBoxLayout()
        row_female.setSpacing(6)
        lbl_female = QLabel("👩 Giọng Nữ:")
        lbl_female.setStyleSheet("font-size: 12px; font-weight: bold; color: #ff8f87;")
        lbl_female.setMinimumWidth(75)
        row_female.addWidget(lbl_female)

        self.combo_female_voice = QComboBox()
        def_female_id = "ngochuyen"
        sel_female_idx = 0
        for idx, v in enumerate(AVAILABLE_FEMALE_VOICES):
            self.combo_female_voice.addItem(v["name"], v)
            if v["id"] == def_female_id:
                sel_female_idx = idx
        self.combo_female_voice.setCurrentIndex(sel_female_idx)
        self.combo_female_voice.currentIndexChanged.connect(self._on_voice_selection_changed)
        row_female.addWidget(self.combo_female_voice, 1)
        voice_select_layout.addLayout(row_female)

        # Hàng Giọng Nam
        row_male = QHBoxLayout()
        row_male.setSpacing(6)
        lbl_male = QLabel("👨 Giọng Nam:")
        lbl_male.setStyleSheet("font-size: 12px; font-weight: bold; color: #8dcfff;")
        lbl_male.setMinimumWidth(75)
        row_male.addWidget(lbl_male)

        self.combo_male_voice = QComboBox()
        def_male_id = "adam1"
        sel_male_idx = 0
        for idx, v in enumerate(AVAILABLE_MALE_VOICES):
            self.combo_male_voice.addItem(v["name"], v)
            if v["id"] == def_male_id:
                sel_male_idx = idx
        self.combo_male_voice.setCurrentIndex(sel_male_idx)
        self.combo_male_voice.currentIndexChanged.connect(self._on_voice_selection_changed)
        row_male.addWidget(self.combo_male_voice, 1)
        voice_select_layout.addLayout(row_male)

        # Cập nhật ngay cấu hình mặc định ban đầu sang Ngọc Huyền & Adam
        self._on_voice_selection_changed()

        lbl_voice_hint = QLabel("Tự động gán cho toàn bộ câu thoại nhân vật Nam và Nữ.")
        lbl_voice_hint.setStyleSheet("color: #a0b0c4; font-size: 11px;")
        voice_select_layout.addWidget(lbl_voice_hint)

        left_layout.addWidget(voice_select_group)

        # --- KHUNG HÒA TRỘN ÂM THANH (AUDIO MIX) ---
        audio_mix_group = QGroupBox("Hòa trộn âm thanh (Audio Mix)")
        audio_mix_layout = QVBoxLayout(audio_mix_group)
        audio_mix_layout.setSpacing(6)
        audio_mix_layout.setContentsMargins(10, 10, 10, 10)

        lbl_preset = QLabel("Mẫu trộn nhanh")
        lbl_preset.setStyleSheet("color: #ff9840; font-weight: bold; font-size: 12px;")
        audio_mix_layout.addWidget(lbl_preset)

        self.audio_mix_preset_combo = QComboBox()
        self.audio_mix_preset_combo.addItem("Cân bằng (50/50)", {"orig": 50, "tts": 100})
        self.audio_mix_preset_combo.addItem("Ưu tiên lồng tiếng (20/100)", {"orig": 20, "tts": 100})
        self.audio_mix_preset_combo.addItem("Chỉ nghe lồng tiếng (0/100)", {"orig": 0, "tts": 100})
        self.audio_mix_preset_combo.addItem("Ưu tiên âm thanh gốc (80/80)", {"orig": 80, "tts": 80})
        self.audio_mix_preset_combo.addItem("Chỉ nghe âm thanh gốc (100/0)", {"orig": 100, "tts": 0})
        self.audio_mix_preset_combo.addItem("Tùy chỉnh", None)
        self.audio_mix_preset_combo.currentIndexChanged.connect(self._on_audio_preset_changed)
        audio_mix_layout.addWidget(self.audio_mix_preset_combo)

        lbl_hint = QLabel("Phím tắt nhanh cho tỷ lệ Âm thanh gốc / Lồng tiếng TTS. Thanh trượt bên dưới điều khiển chi tiết.")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #a0b0c4; font-size: 11px; line-height: 1.2;")
        audio_mix_layout.addWidget(lbl_hint)

        lbl_track_title = QLabel("Âm lượng từng kênh")
        lbl_track_title.setStyleSheet("font-weight: bold; font-size: 12px; color: #e0e6ef; margin-top: 4px;")
        audio_mix_layout.addWidget(lbl_track_title)

        # Row A1 Video gốc
        row_orig = QHBoxLayout()
        row_orig.setSpacing(6)
        lbl_orig = QLabel("A1 Video gốc")
        lbl_orig.setStyleSheet("font-size: 12px; color: #e0e6ef;")
        lbl_orig.setMinimumWidth(85)
        row_orig.addWidget(lbl_orig)

        self.slider_orig_vol = QSlider(Qt.Horizontal)
        self.slider_orig_vol.setRange(0, 100)
        self.slider_orig_vol.setValue(self.initial_orig_volume)
        self.slider_orig_vol.setTickInterval(25)
        self.slider_orig_vol.setTickPosition(QSlider.TicksBelow)
        self.slider_orig_vol.valueChanged.connect(self._on_orig_slider_changed)
        row_orig.addWidget(self.slider_orig_vol, 1)

        self.lbl_orig_vol_val = QLabel(f"{self.initial_orig_volume}%")
        self.lbl_orig_vol_val.setMinimumWidth(38)
        self.lbl_orig_vol_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_orig_vol_val.setStyleSheet("color: #ff9840; font-weight: bold; font-size: 12px;")
        row_orig.addWidget(self.lbl_orig_vol_val)
        audio_mix_layout.addLayout(row_orig)

        # Row TS1 Giọng đọc AI
        row_tts = QHBoxLayout()
        row_tts.setSpacing(6)
        lbl_tts = QLabel("TS1 Giọng đọc AI")
        lbl_tts.setStyleSheet("font-size: 12px; color: #e0e6ef;")
        lbl_tts.setMinimumWidth(85)
        row_tts.addWidget(lbl_tts)

        self.slider_tts_vol = QSlider(Qt.Horizontal)
        self.slider_tts_vol.setRange(0, 200)
        self.slider_tts_vol.setValue(self.initial_tts_volume)
        self.slider_tts_vol.setTickInterval(50)
        self.slider_tts_vol.setTickPosition(QSlider.TicksBelow)
        self.slider_tts_vol.valueChanged.connect(self._on_tts_slider_changed)
        row_tts.addWidget(self.slider_tts_vol, 1)

        self.lbl_tts_vol_val = QLabel(f"{self.initial_tts_volume}%")
        self.lbl_tts_vol_val.setMinimumWidth(38)
        self.lbl_tts_vol_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_tts_vol_val.setStyleSheet("color: #6db8ff; font-weight: bold; font-size: 12px;")
        row_tts.addWidget(self.lbl_tts_vol_val)
        audio_mix_layout.addLayout(row_tts)

        self._sync_initial_preset_selection()
        left_layout.addWidget(audio_mix_group)

        # --- KHUNG KIỂU DÁNG PHỤ ĐỀ & HỘP NỀN ---
        self.sub_style_group = SubtitleStyleGroupBox(self, parent_window=self.parent())
        left_layout.addWidget(self.sub_style_group)

        # --- KHUNG LOGO THƯƠNG HIỆU & HÌNH MỜ (WATERMARK) ---
        branding_box = QGroupBox("Logo Thương hiệu & Watermark bản quyền")
        branding_layout = QVBoxLayout(branding_box)
        branding_layout.setSpacing(10)
        branding_layout.setContentsMargins(10, 12, 10, 10)

        # === PHẦN A: LOGO THƯƠNG HIỆU ===
        self.chk_enable_logo = QCheckBox("Bật hiển thị Logo thương hiệu xuyên suốt video")
        self.chk_enable_logo.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_logo)

        self.logo_container = QWidget()
        logo_c_layout = QVBoxLayout(self.logo_container)
        logo_c_layout.setContentsMargins(14, 0, 0, 4)
        logo_c_layout.setSpacing(8)

        # Hàng 1: Chọn file ảnh & thumbnail preview
        logo_file_row = QHBoxLayout()
        logo_file_row.setSpacing(8)
        lbl_l_file = QLabel("Ảnh Logo:")
        lbl_l_file.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        lbl_l_file.setMinimumWidth(65)
        logo_file_row.addWidget(lbl_l_file)

        self.txt_logo_path = QLineEdit()
        self.txt_logo_path.setPlaceholderText("Chọn ảnh logo (.png, .jpg, .webp)...")
        self.txt_logo_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        logo_file_row.addWidget(self.txt_logo_path, 1)

        self.btn_browse_logo = QPushButton("📁 Chọn ảnh...")
        self.btn_browse_logo.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; color: #fff; }
        """)
        self.btn_browse_logo.clicked.connect(self._browse_logo_file)
        logo_file_row.addWidget(self.btn_browse_logo)

        self.lbl_logo_preview = QLabel()
        self.lbl_logo_preview.setFixedSize(38, 38)
        self.lbl_logo_preview.setAlignment(Qt.AlignCenter)
        self.lbl_logo_preview.setStyleSheet("border: 1px dashed #344862; border-radius: 6px; background-color: #16202e; color: #64748b; font-size: 9px;")
        self.lbl_logo_preview.setText("Logo")
        logo_file_row.addWidget(self.lbl_logo_preview)
        logo_c_layout.addLayout(logo_file_row)

        # Hàng 2: Vị trí logo & kích thước & độ mờ
        logo_opt_row = QHBoxLayout()
        logo_opt_row.setSpacing(10)

        lbl_l_pos = QLabel("Vị trí:")
        lbl_l_pos.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        logo_opt_row.addWidget(lbl_l_pos)

        self.combo_logo_pos = QComboBox()
        self.combo_logo_pos.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        self.combo_logo_pos.addItem("Góc trên trái (Top-Left) ★ Khuyên dùng", "top_left")
        self.combo_logo_pos.addItem("Góc trên phải (Top-Right)", "top_right")
        self.combo_logo_pos.addItem("Góc dưới trái (Bottom-Left)", "bottom_left")
        self.combo_logo_pos.addItem("Góc dưới phải (Bottom-Right)", "bottom_right")
        self.combo_logo_pos.addItem("Chính giữa (Center)", "center")
        self.combo_logo_pos.addItem("Góc trên giữa (Top-Center)", "top_center")
        logo_opt_row.addWidget(self.combo_logo_pos, 1)

        self.btn_grab_logo_rect = QPushButton("📐 Lấy từ khung [Đặt Logo]")
        self.btn_grab_logo_rect.setToolTip("Lấy tọa độ vùng chọn từ khung viền vàng [Đặt Logo] trên màn hình Preview")
        self.btn_grab_logo_rect.setStyleSheet("""
            QPushButton {
                background-color: #2e1d08; color: #f59e0b;
                border: 1px solid #d97706; border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #451a03; color: #fbbf24; }
        """)
        self.btn_grab_logo_rect.clicked.connect(self._grab_logo_preview_rect)
        logo_opt_row.addWidget(self.btn_grab_logo_rect)

        self.lbl_logo_custom_info = QLabel("")
        self.lbl_logo_custom_info.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: 600;")
        logo_opt_row.addWidget(self.lbl_logo_custom_info)

        lbl_l_size = QLabel("Kích thước:")
        lbl_l_size.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        logo_opt_row.addWidget(lbl_l_size)

        self.slider_logo_size = QSlider(Qt.Horizontal)
        self.slider_logo_size.setRange(5, 35)
        self.slider_logo_size.setValue(15)
        self.lbl_logo_size_val = QLabel("15%")
        self.lbl_logo_size_val.setMinimumWidth(32)
        self.lbl_logo_size_val.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 11px;")
        self.slider_logo_size.valueChanged.connect(lambda v: self.lbl_logo_size_val.setText(f"{v}%"))
        logo_opt_row.addWidget(self.slider_logo_size)
        logo_opt_row.addWidget(self.lbl_logo_size_val)

        lbl_l_op = QLabel("Độ mờ:")
        lbl_l_op.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        logo_opt_row.addWidget(lbl_l_op)

        self.slider_logo_opacity = QSlider(Qt.Horizontal)
        self.slider_logo_opacity.setRange(10, 100)
        self.slider_logo_opacity.setValue(100)
        self.lbl_logo_opacity_val = QLabel("100%")
        self.lbl_logo_opacity_val.setMinimumWidth(36)
        self.lbl_logo_opacity_val.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        self.slider_logo_opacity.valueChanged.connect(lambda v: self.lbl_logo_opacity_val.setText(f"{v}%"))
        logo_opt_row.addWidget(self.slider_logo_opacity)
        logo_opt_row.addWidget(self.lbl_logo_opacity_val)

        logo_c_layout.addLayout(logo_opt_row)
        branding_layout.addWidget(self.logo_container)

        self.chk_enable_logo.toggled.connect(self.logo_container.setEnabled)
        self.txt_logo_path.textChanged.connect(self._update_logo_preview)

        # Phân cách
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep)

        # === PHẦN B: KHUNG VIỀN VIDEO TRANG TRÍ (FRAME BORDER) ===
        self.chk_enable_frame_border = QCheckBox("🖼️ Khung viền video (Che viền trên/dưới & che logo cũ)")
        self.chk_enable_frame_border.setStyleSheet("color: #c084fc; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_frame_border)

        self.border_container = QWidget()
        border_c_layout = QVBoxLayout(self.border_container)
        border_c_layout.setContentsMargins(14, 0, 0, 4)
        border_c_layout.setSpacing(6)

        # Hàng 1: Viền trên
        border_top_row = QHBoxLayout()
        border_top_row.setSpacing(6)
        lbl_b_top = QLabel("Viền trên:")
        lbl_b_top.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        lbl_b_top.setMinimumWidth(65)
        border_top_row.addWidget(lbl_b_top)

        self.txt_frame_border_top_path = QLineEdit()
        self.txt_frame_border_top_path.setPlaceholderText("Chọn ảnh viền trên (.png, .jpg)...")
        self.txt_frame_border_top_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        self.txt_frame_border_top_path.textChanged.connect(self._update_frame_border_top_preview)
        border_top_row.addWidget(self.txt_frame_border_top_path, 1)

        self.btn_browse_frame_border_top = QPushButton("📁 Chọn ảnh trên...")
        self.btn_browse_frame_border_top.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #a78bfa;
                border: 1px solid #6d28d9; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #4c1d95; color: #fff; }
        """)
        self.btn_browse_frame_border_top.clicked.connect(self._browse_frame_border_top_file)
        border_top_row.addWidget(self.btn_browse_frame_border_top)

        self.btn_clear_frame_border_top = QPushButton("🗑️ Xóa")
        self.btn_clear_frame_border_top.setToolTip("Xóa ảnh viền trên (nếu bạn chỉ muốn dùng viền dưới)")
        self.btn_clear_frame_border_top.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f87171;
                border: 1px solid #7f1d1d; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #7f1d1d; color: #fecaca; border-color: #dc2626; }
        """)
        self.btn_clear_frame_border_top.clicked.connect(self._clear_frame_border_top)
        border_top_row.addWidget(self.btn_clear_frame_border_top)

        self.lbl_frame_border_top_preview = QLabel()
        self.lbl_frame_border_top_preview.setFixedSize(38, 38)
        self.lbl_frame_border_top_preview.setAlignment(Qt.AlignCenter)
        self.lbl_frame_border_top_preview.setStyleSheet("border: 1px dashed #6d28d9; border-radius: 6px; background-color: #16202e; color: #8b5cf6; font-size: 9px;")
        self.lbl_frame_border_top_preview.setText("Trên")
        border_top_row.addWidget(self.lbl_frame_border_top_preview)
        border_c_layout.addLayout(border_top_row)

        # Hàng 2: Viền dưới
        border_bot_row = QHBoxLayout()
        border_bot_row.setSpacing(6)
        lbl_b_bot = QLabel("Viền dưới:")
        lbl_b_bot.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        lbl_b_bot.setMinimumWidth(65)
        border_bot_row.addWidget(lbl_b_bot)

        self.txt_frame_border_bottom_path = QLineEdit()
        self.txt_frame_border_bottom_path.setPlaceholderText("Chọn ảnh viền dưới (.png, .jpg)...")
        self.txt_frame_border_bottom_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        self.txt_frame_border_bottom_path.textChanged.connect(self._update_frame_border_bottom_preview)
        border_bot_row.addWidget(self.txt_frame_border_bottom_path, 1)

        self.btn_browse_frame_border_bot = QPushButton("📁 Chọn ảnh dưới...")
        self.btn_browse_frame_border_bot.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #a78bfa;
                border: 1px solid #6d28d9; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #4c1d95; color: #fff; }
        """)
        self.btn_browse_frame_border_bot.clicked.connect(self._browse_frame_border_bottom_file)
        border_bot_row.addWidget(self.btn_browse_frame_border_bot)

        self.btn_copy_border_top = QPushButton("📋 Dùng ảnh trên")
        self.btn_copy_border_top.setToolTip("Sao chép ảnh viền trên xuống viền dưới")
        self.btn_copy_border_top.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 6px; padding: 5px 8px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background-color: #0369a1; color: #fff; }
        """)
        self.btn_copy_border_top.clicked.connect(self._copy_top_border_to_bottom)
        border_bot_row.addWidget(self.btn_copy_border_top)

        self.btn_clear_frame_border_bot = QPushButton("🗑️ Xóa")
        self.btn_clear_frame_border_bot.setToolTip("Xóa ảnh viền dưới (nếu bạn chỉ muốn dùng viền trên)")
        self.btn_clear_frame_border_bot.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f87171;
                border: 1px solid #7f1d1d; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #7f1d1d; color: #fecaca; border-color: #dc2626; }
        """)
        self.btn_clear_frame_border_bot.clicked.connect(self._clear_frame_border_bottom)
        border_bot_row.addWidget(self.btn_clear_frame_border_bot)

        self.lbl_frame_border_bot_preview = QLabel()
        self.lbl_frame_border_bot_preview.setFixedSize(38, 38)
        self.lbl_frame_border_bot_preview.setAlignment(Qt.AlignCenter)
        self.lbl_frame_border_bot_preview.setStyleSheet("border: 1px dashed #6d28d9; border-radius: 6px; background-color: #16202e; color: #8b5cf6; font-size: 9px;")
        self.lbl_frame_border_bot_preview.setText("Dưới")
        border_bot_row.addWidget(self.lbl_frame_border_bot_preview)
        border_c_layout.addLayout(border_bot_row)

        # Hàng 3: Tùy chỉnh Độ dày viền (Chiều cao px)
        border_height_row = QHBoxLayout()
        border_height_row.setSpacing(10)
        lbl_top_h = QLabel("Độ dày viền trên:")
        lbl_top_h.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        self.spin_frame_border_top_height = QSpinBox()
        self.spin_frame_border_top_height.setRange(0, 400)
        self.spin_frame_border_top_height.setSingleStep(10)
        self.spin_frame_border_top_height.setValue(0)
        self.spin_frame_border_top_height.setSpecialValueText("Tự động (theo ảnh)")
        self.spin_frame_border_top_height.setSuffix(" px")
        self.spin_frame_border_top_height.setStyleSheet("background-color: #1a2738; color: #a78bfa; border: 1px solid #6d28d9; border-radius: 4px; padding: 3px 6px; font-size: 11px; font-weight: bold;")

        lbl_bot_h = QLabel("Độ dày viền dưới:")
        lbl_bot_h.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        self.spin_frame_border_bottom_height = QSpinBox()
        self.spin_frame_border_bottom_height.setRange(0, 400)
        self.spin_frame_border_bottom_height.setSingleStep(10)
        self.spin_frame_border_bottom_height.setValue(0)
        self.spin_frame_border_bottom_height.setSpecialValueText("Tự động (theo ảnh)")
        self.spin_frame_border_bottom_height.setSuffix(" px")
        self.spin_frame_border_bottom_height.setStyleSheet("background-color: #1a2738; color: #a78bfa; border: 1px solid #6d28d9; border-radius: 4px; padding: 3px 6px; font-size: 11px; font-weight: bold;")

        border_height_row.addWidget(lbl_top_h)
        border_height_row.addWidget(self.spin_frame_border_top_height)
        border_height_row.addSpacing(15)
        border_height_row.addWidget(lbl_bot_h)
        border_height_row.addWidget(self.spin_frame_border_bottom_height)
        border_height_row.addStretch()
        border_c_layout.addLayout(border_height_row)

        border_opt_row = QHBoxLayout()
        border_opt_row.setSpacing(10)
        lbl_b_hint = QLabel("💡 Nhập độ dày px nếu muốn viền dày hơn để che sạch mặt nạ/sub cũ; Logo của bạn sẽ đè lên trên viền.")
        lbl_b_hint.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        border_opt_row.addWidget(lbl_b_hint, 1)

        lbl_b_op = QLabel("Độ mờ:")
        lbl_b_op.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        border_opt_row.addWidget(lbl_b_op)

        self.slider_frame_border_opacity = QSlider(Qt.Horizontal)
        self.slider_frame_border_opacity.setRange(10, 100)
        self.slider_frame_border_opacity.setValue(100)
        self.lbl_frame_border_opacity_val = QLabel("100%")
        self.lbl_frame_border_opacity_val.setMinimumWidth(36)
        self.lbl_frame_border_opacity_val.setStyleSheet("color: #a78bfa; font-weight: bold; font-size: 11px;")
        self.slider_frame_border_opacity.valueChanged.connect(lambda v: self.lbl_frame_border_opacity_val.setText(f"{v}%"))
        border_opt_row.addWidget(self.slider_frame_border_opacity)
        border_opt_row.addWidget(self.lbl_frame_border_opacity_val)
        border_c_layout.addLayout(border_opt_row)

        self.border_container.setEnabled(False)
        self.chk_enable_frame_border.toggled.connect(self.border_container.setEnabled)
        self.txt_frame_border_top_path.textChanged.connect(self._update_frame_border_top_preview)
        self.txt_frame_border_bottom_path.textChanged.connect(self._update_frame_border_bottom_preview)
        branding_layout.addWidget(self.border_container)

        sep_zoom = QFrame()
        sep_zoom.setFrameShape(QFrame.HLine)
        sep_zoom.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep_zoom)

        # === PHẦN: THU PHÓNG VIDEO KIỂU CAPCUT (SCALE & CENTER CROP) ===
        zoom_box = QWidget()
        zoom_layout = QVBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(0, 2, 0, 2)
        zoom_layout.setSpacing(4)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)
        lbl_zoom = QLabel("🔍 Thu phóng video (Tỷ lệ Zoom như CapCut):")
        lbl_zoom.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        zoom_row.addWidget(lbl_zoom)

        self.slider_video_zoom = QSlider(Qt.Horizontal)
        self.slider_video_zoom.setRange(100, 150)
        self.slider_video_zoom.setValue(100)
        self.lbl_video_zoom_val = QLabel("100% (Gốc)")
        self.lbl_video_zoom_val.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px; min-width: 90px;")
        self.slider_video_zoom.valueChanged.connect(self._on_video_zoom_changed)
        zoom_row.addWidget(self.slider_video_zoom, 1)
        zoom_row.addWidget(self.lbl_video_zoom_val)

        self.btn_reset_zoom = QPushButton("↺ 100%")
        self.btn_reset_zoom.setToolTip("Khôi phục tỷ lệ gốc 100%")
        self.btn_reset_zoom.setStyleSheet("background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 3px 8px; font-size: 10px; font-weight: 600;")
        self.btn_reset_zoom.clicked.connect(lambda: self.slider_video_zoom.setValue(100))
        zoom_row.addWidget(self.btn_reset_zoom)
        zoom_layout.addLayout(zoom_row)

        lbl_zoom_hint = QLabel("💡 Phóng to video từ tâm (gợi ý 110% - 118%) giúp đẩy lùi mép video, logo cũ và phụ đề gốc ra ngoài màn hình như CapCut.")
        lbl_zoom_hint.setStyleSheet("color: #64748b; font-size: 10px; font-style: italic;")
        zoom_layout.addWidget(lbl_zoom_hint)
        branding_layout.addWidget(zoom_box)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep2)

        # === PHẦN C: HÌNH MỜ CHỮ (WATERMARK) ===
        self.chk_enable_watermark = QCheckBox("Bật hình mờ chữ (Watermark chống reup) xuyên suốt video")
        self.chk_enable_watermark.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_watermark)

        self.wm_container = QWidget()
        wm_c_layout = QVBoxLayout(self.wm_container)
        wm_c_layout.setContentsMargins(14, 0, 0, 0)
        wm_c_layout.setSpacing(8)

        wm_row1 = QHBoxLayout()
        wm_row1.setSpacing(8)
        lbl_wm_text = QLabel("Chữ Watermark:")
        lbl_wm_text.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        lbl_wm_text.setMinimumWidth(90)
        wm_row1.addWidget(lbl_wm_text)

        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("Ví dụ: Nhím Review, Tên Kênh...")
        self.txt_watermark.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        wm_row1.addWidget(self.txt_watermark, 1)

        lbl_wm_pos = QLabel("Vị trí:")
        lbl_wm_pos.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_row1.addWidget(lbl_wm_pos)

        self.combo_watermark_pos = QComboBox()
        self.combo_watermark_pos.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        self.combo_watermark_pos.addItem("Chính giữa (Center) ★ Khuyên dùng", "center")
        self.combo_watermark_pos.addItem("Gần giữa dưới (trên phụ đề)", "center_bottom")
        self.combo_watermark_pos.addItem("Góc trên trái (Top-Left)", "top_left")
        self.combo_watermark_pos.addItem("Góc trên phải (Top-Right)", "top_right")
        self.combo_watermark_pos.addItem("Góc dưới trái (Bottom-Left)", "bottom_left")
        self.combo_watermark_pos.addItem("Góc dưới phải (Bottom-Right)", "bottom_right")
        wm_row1.addWidget(self.combo_watermark_pos, 1)

        self.btn_grab_wm_placement_rect = QPushButton("📐 Lấy từ khung [Đặt Watermark]")
        self.btn_grab_wm_placement_rect.setToolTip("Lấy tọa độ vùng chọn từ khung viền tím [Đặt Watermark] trên màn hình Preview")
        self.btn_grab_wm_placement_rect.setStyleSheet("""
            QPushButton {
                background-color: #26113b; color: #c084fc;
                border: 1px solid #9333ea; border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #3b0764; color: #e9d5ff; }
        """)
        self.btn_grab_wm_placement_rect.clicked.connect(self._grab_wm_placement_rect)
        wm_row1.addWidget(self.btn_grab_wm_placement_rect)

        self.lbl_wm_custom_info = QLabel("")
        self.lbl_wm_custom_info.setStyleSheet("color: #c084fc; font-size: 11px; font-weight: 600;")
        wm_row1.addWidget(self.lbl_wm_custom_info)
        wm_c_layout.addLayout(wm_row1)

        wm_row2 = QHBoxLayout()
        wm_row2.setSpacing(10)
        lbl_wm_size = QLabel("Độ to:")
        lbl_wm_size.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_row2.addWidget(lbl_wm_size)

        self.slider_watermark_size = QSlider(Qt.Horizontal)
        self.slider_watermark_size.setRange(18, 80)
        self.slider_watermark_size.setValue(36)
        self.lbl_watermark_size_val = QLabel("36px")
        self.lbl_watermark_size_val.setMinimumWidth(36)
        self.lbl_watermark_size_val.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 11px;")
        self.slider_watermark_size.valueChanged.connect(lambda v: self.lbl_watermark_size_val.setText(f"{v}px"))
        wm_row2.addWidget(self.slider_watermark_size, 1)
        wm_row2.addWidget(self.lbl_watermark_size_val)

        lbl_wm_op = QLabel("Độ mờ:")
        lbl_wm_op.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_row2.addWidget(lbl_wm_op)

        self.slider_watermark_opacity = QSlider(Qt.Horizontal)
        self.slider_watermark_opacity.setRange(10, 100)
        self.slider_watermark_opacity.setValue(35)
        self.lbl_watermark_opacity_val = QLabel("35%")
        self.lbl_watermark_opacity_val.setMinimumWidth(36)
        self.lbl_watermark_opacity_val.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        self.slider_watermark_opacity.valueChanged.connect(lambda v: self.lbl_watermark_opacity_val.setText(f"{v}%"))
        wm_row2.addWidget(self.slider_watermark_opacity, 1)
        wm_row2.addWidget(self.lbl_watermark_opacity_val)

        wm_c_layout.addLayout(wm_row2)
        branding_layout.addWidget(self.wm_container)

        self.chk_enable_watermark.toggled.connect(self.wm_container.setEnabled)
        left_layout.addWidget(branding_box)

        # Nạp cài đặt branding đã lưu
        self._load_branding_settings()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("background: transparent; border: none;")
        left_scroll.setWidget(left_widget)
        splitter.addWidget(left_scroll)

        # --- CỘT BÊN PHẢI: Chi tiết & Xem trước bản chọn ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(10)

        # Khung thông tin tổng quan
        info_group = QGroupBox("Thông tin bản ghi & Video gốc")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(4)

        self.lbl_video_title = QLabel("Tên video: --")
        self.lbl_video_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #58a6ff;")
        self.lbl_video_path = QLabel("Đường dẫn: --")
        self.lbl_video_path.setWordWrap(True)
        self.lbl_video_path.setStyleSheet("color: #8b949e; font-size: 11px;")

        self.lbl_meta = QLabel("Thời gian tạo: -- | Tổng số câu: -- | Thời lượng: --")
        self.lbl_meta.setStyleSheet("color: #c9d1d9; font-size: 12px;")

        info_layout.addWidget(self.lbl_video_title)
        info_layout.addWidget(self.lbl_video_path)
        info_layout.addWidget(self.lbl_meta)
        right_layout.addWidget(info_group)

        # Khung Pre-flight Status Banner
        self.status_banner = QFrame()
        self.status_banner.setFrameShape(QFrame.StyledPanel)
        banner_layout = QVBoxLayout(self.status_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_status_title = QLabel("Trạng thái tiền kiểm tra (Pre-flight)")
        self.lbl_status_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.lbl_status_details = QLabel("Đang kiểm tra dữ liệu...")
        self.lbl_status_details.setWordWrap(True)
        self.lbl_status_details.setStyleSheet("font-size: 11px;")
        banner_layout.addWidget(self.lbl_status_title)
        banner_layout.addWidget(self.lbl_status_details)
        right_layout.addWidget(self.status_banner)

        # Khung Ánh xạ Giọng đọc (Voice Mapping Summary)
        self.lbl_voice_summary = QLabel("Ánh xạ giọng TTS: --")
        self.lbl_voice_summary.setStyleSheet("color: #7ee787; font-size: 11px; padding: 4px;")
        right_layout.addWidget(self.lbl_voice_summary)

        # Bảng xem trước phân vai & câu thoại
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(5)
        self.preview_table.setHorizontalHeaderLabels([
            "Câu", "Thời gian", "Thoại gốc (OCR)", "Dịch tiếng Việt", "Giọng lồng tiếng (TTS)"
        ])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(False)
        right_layout.addWidget(self.preview_table)

        splitter.addWidget(right_widget)
        splitter.setSizes([520, 760])
        root_layout.addWidget(splitter)

        # --- DÒNG NÚT BẤM DƯỚI CÙNG ---
        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("Làm mới danh sách")
        self.btn_refresh.clicked.connect(self._load_datasets)
        btn_layout.addWidget(self.btn_refresh)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Đóng")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton("Sử dụng bản này để tạo video ➔")
        self.btn_confirm.setStyleSheet(
            "background-color: #238636; color: #ffffff; font-weight: bold; padding: 8px 18px; border-radius: 6px;"
        )
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self._on_confirm_clicked)
        btn_layout.addWidget(self.btn_confirm)

        root_layout.addLayout(btn_layout)

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #141c2a;
                color: #e8edf5;
            }
            QLabel {
                color: #e0e6ef;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #3a4a62;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 12px;
                color: #ff9840;
                font-weight: bold;
                font-size: 12px;
                background-color: #1a2332;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                color: #ff9840;
            }
            QListWidget {
                background-color: #1e2a3a;
                border: 1px solid #3a4a62;
                border-radius: 6px;
                color: #e8edf5;
                padding: 4px;
                font-size: 12px;
            }
            QListWidget::item {
                border-bottom: 1px solid #2a3a4f;
                padding: 8px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QListWidget::item:hover {
                background-color: #253550;
            }
            QTableWidget {
                background-color: #1e2a3a;
                border: 1px solid #3a4a62;
                border-radius: 6px;
                gridline-color: #2a3a4f;
                color: #f0f4fa;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #253550;
                color: #e0e6ef;
                padding: 6px;
                border: 1px solid #3a4a62;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton {
                background-color: #253550;
                border: 1px solid #3a4a62;
                border-radius: 6px;
                color: #e8edf5;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2e4268;
                border-color: #4a6a8f;
            }
            QPushButton:disabled {
                background-color: #1a2332;
                color: #556677;
                border-color: #2a3a4f;
            }
            QComboBox {
                background-color: #1e2a3a;
                border: 1px solid #3a4a62;
                border-radius: 6px;
                color: #f0f4fa;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            QComboBox:hover {
                border-color: #ff9840;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e2a3a;
                border: 1px solid #3a4a62;
                selection-background-color: #ff7b1a;
                selection-color: #ffffff;
                color: #f0f4fa;
                font-size: 12px;
                padding: 4px;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #253550;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #ff7b1a;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 2px solid #ff7b1a;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QSplitter::handle {
                background-color: #3a4a62;
                width: 2px;
            }
        """)

    def _load_datasets(self):
        """Nạp danh sách Master Dataset từ tệp lịch sử cụ thể hoặc quét toàn bộ."""
        self.list_widget.clear()
        self.datasets.clear()

        # 1. Nạp theo video hiện tại nếu có
        if self.video_path or self.project_root:
            self.datasets = MasterDatasetService.list_datasets(
                self.workspace_root, video_path=self.video_path, project_root=self.project_root
            )

        # 2. Nếu chưa có, quét toàn bộ workspace để người dùng có thể chọn bất kỳ bản nào
        if not self.datasets and self.workspace_root:
            self.datasets = MasterDatasetService.find_all_available_masters(self.workspace_root)

        if not self.datasets:
            item = QListWidgetItem("Không có bản Master Data nào đã lưu")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            self._clear_details()
            return

        for ds in self.datasets:
            counts = ds.counts
            video_name = os.path.basename(ds.video_path) if ds.video_path else ds.title
            date_str = ds.created_at[:19].replace("T", " ") if ds.created_at else "Chưa rõ"
            item_text = (
                f"🎥 {video_name}\n"
                f"📅 {date_str} | Tổng {counts['total']} câu\n"
                f"♂ {counts['male']} | ♀ {counts['female']} | ❓ {counts['unknown']}"
            )
            item = QListWidgetItem(item_text)
            self.list_widget.addItem(item)

        self.list_widget.setCurrentRow(0)

    def _clear_details(self):
        self.lbl_video_title.setText("Tên video: --")
        self.lbl_video_path.setText("Đường dẫn: --")
        self.lbl_meta.setText("Thời gian tạo: -- | Tổng số câu: -- | Thời lượng: --")
        self.lbl_status_title.setText("Chưa chọn bản ghi")
        self.lbl_status_details.setText("")
        self.status_banner.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 6px;")
        self.lbl_voice_summary.setText("Ánh xạ giọng TTS: --")
        self.preview_table.setRowCount(0)
        self.btn_confirm.setEnabled(False)

    def _on_dataset_selected(self, row: int):
        if row < 0 or row >= len(self.datasets):
            self._clear_details()
            return

        dataset = self.datasets[row]
        self.selected_dataset = dataset

        # 1. Hiển thị thông tin cơ bản
        video_name = os.path.basename(dataset.video_path) if dataset.video_path else dataset.title
        self.lbl_video_title.setText(f"Tên video: {video_name}")
        self.lbl_video_path.setText(f"Đường dẫn: {dataset.video_path or '(Chưa gắn tệp video)'}")

        counts = dataset.counts
        dur_str = _format_timestamp(dataset.duration)
        date_str = dataset.created_at[:19].replace("T", " ") if dataset.created_at else ""
        self.lbl_meta.setText(
            f"📅 Tạo lúc: {date_str} | ⏱️ Thời lượng: ~{dur_str} | "
            f"📊 Tổng: {counts['total']} câu (Nam: {counts['male']}, Nữ: {counts['female']}, "
            f"Không xác định: {counts['unknown']})"
        )

        # 2. Chạy Pre-flight Validator (STEP 1)
        # Nếu video_path không tồn tại cục bộ, kiểm tra xem có tệp video_path trên máy người dùng không
        validation = validate_master_dataset(dataset, check_video_file=bool(dataset.video_path))
        self.current_validation = validation

        if validation.is_valid:
            if validation.warnings:
                self.status_banner.setStyleSheet(
                    "background-color: #261c0e; border: 1px solid #d29922; border-radius: 6px;"
                )
                self.lbl_status_title.setStyleSheet("font-weight: bold; color: #d29922;")
                self.lbl_status_title.setText("⚠️ ĐỦ ĐIỀU KIỆN TẠO VIDEO (Có cảnh báo cần lưu ý)")
                warning_texts = [f"• {w.message}" for w in validation.warnings[:3]]
                self.lbl_status_details.setText("\n".join(warning_texts))
            else:
                self.status_banner.setStyleSheet(
                    "background-color: #0d2818; border: 1px solid #238636; border-radius: 6px;"
                )
                self.lbl_status_title.setStyleSheet("font-weight: bold; color: #3fb950;")
                self.lbl_status_title.setText("✅ DỮ LIỆU HOÀN TOÀN HỢP LỆ (Sẵn sàng chạy Pipeline B)")
                self.lbl_status_details.setText(validation.format_summary())
        else:
            self.status_banner.setStyleSheet(
                "background-color: #2b1115; border: 1px solid #da3633; border-radius: 6px;"
            )
            self.lbl_status_title.setStyleSheet("font-weight: bold; color: #f85149;")
            self.lbl_status_title.setText(f"❌ KHÔNG HỢP LỆ ({len(validation.errors)} lỗi nghiêm trọng)")
            err_texts = [f"• {e.message}" for e in validation.errors[:3]]
            self.lbl_status_details.setText("\n".join(err_texts))

        # 3. Nạp dữ liệu cơ bản vào Preview Table
        self.preview_table.setRowCount(len(dataset.segments))
        for r_idx, seg in enumerate(dataset.segments):
            # Cột 0: ID
            item_id = QTableWidgetItem(str(seg.id))
            item_id.setTextAlignment(Qt.AlignCenter)

            # Cột 1: Thời gian
            time_str = f"{_format_timestamp(seg.start)} -> {_format_timestamp(seg.end)}"
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignCenter)

            # Cột 2: Gốc
            item_orig = QTableWidgetItem(seg.text)

            # Cột 3: Dịch
            item_trans = QTableWidgetItem(seg.translated_text)

            self.preview_table.setItem(r_idx, 0, item_id)
            self.preview_table.setItem(r_idx, 1, item_time)
            self.preview_table.setItem(r_idx, 2, item_orig)
            self.preview_table.setItem(r_idx, 3, item_trans)

        # 4. Ánh xạ và hiển thị giọng đọc TTS theo cấu hình hiện tại
        self._refresh_voice_preview()

        # 5. Kích hoạt nút bấm xác nhận nếu có thể tiếp tục
        self.btn_confirm.setEnabled(validation.can_proceed)

    def _refresh_voice_preview(self):
        """Cập nhật phân bổ giọng đọc và cột Giọng TTS khi người dùng đổi giọng hoặc đổi dataset."""
        if not self.selected_dataset:
            return

        dataset = self.selected_dataset
        mapping = self.voice_mapper.map_dataset(dataset)
        summary = self.voice_mapper.get_summary_mapping(dataset)
        # Thống kê phân bổ giọng đọc sạch (không có vế sau)
        voice_counts: dict[str, int] = {}
        for seg in dataset.segments:
            v = mapping.get(seg.id)
            if v is not None:
                clean_name = v.name.split(" (")[0]
                voice_counts[clean_name] = voice_counts.get(clean_name, 0) + 1

        voice_lines = []
        for voice_name, count in voice_counts.items():
            clean_vn = re.sub(r"\s*\([^)]*\)", "", voice_name).strip()
            voice_lines.append(f"🗣️ {clean_vn}: {count} câu")
        if summary.get("skipped", {}).get("unknown", 0) > 0:
            voice_lines.append(f"❓ Không xác định: {summary['skipped']['unknown']} câu (Chỉ hiện phụ đề)")
        if summary.get("skipped", {}).get("none", 0) > 0:
            voice_lines.append(f"🔇 Không lời: {summary['skipped']['none']} câu")

        self.lbl_voice_summary.setText(" | ".join(voice_lines))

        for r_idx, seg in enumerate(dataset.segments):
            voice = mapping.get(seg.id)
            if voice:
                clean_voice_name = re.sub(r"\s*\([^)]*\)", "", voice.name).strip()
                voice_text = f"🗣️ {clean_voice_name}"
                item_voice = QTableWidgetItem(voice_text)
                if voice.gender == "male":
                    item_voice.setForeground(QBrush(QColor("#79c0ff")))
                else:
                    item_voice.setForeground(QBrush(QColor("#ff7b72")))
            else:
                if seg.gender == "unknown":
                    item_voice = QTableWidgetItem("⚠️ Chỉ hiện phụ đề (Không TTS)")
                    item_voice.setForeground(QBrush(QColor("#e3b341")))
                elif seg.gender == "none" or seg.is_noise:
                    item_voice = QTableWidgetItem("🔇 Bỏ qua")
                    item_voice.setForeground(QBrush(QColor("#8b949e")))
                else:
                    item_voice = QTableWidgetItem("❌ Chưa dịch")
                    item_voice.setForeground(QBrush(QColor("#f85149")))

            item_voice.setTextAlignment(Qt.AlignCenter)
            self.preview_table.setItem(r_idx, 4, item_voice)

    def _on_voice_selection_changed(self):
        """Xử lý khi người dùng chọn giọng Nam hoặc Nữ khác từ dropdown."""
        male_data = self.combo_male_voice.currentData()
        if male_data and isinstance(male_data, dict):
            clean_name = male_data["name"].split(" (")[0]
            self.voice_config.default_male_voice = VoiceAssignment(
                voice_id=male_data["id"],
                name=clean_name,
                provider=male_data.get("provider", "vieneu"),
                gender="male",
            )

        female_data = self.combo_female_voice.currentData()
        if female_data and isinstance(female_data, dict):
            clean_name = female_data["name"].split(" (")[0]
            self.voice_config.default_female_voice = VoiceAssignment(
                voice_id=female_data["id"],
                name=clean_name,
                provider=female_data.get("provider", "vieneu"),
                gender="female",
            )

        self.voice_mapper = VoiceMappingService(self.voice_config)
        self._refresh_voice_preview()

    def get_voice_mapping_config(self) -> VoiceMappingConfig:
        """Trả về cấu hình giọng đọc TTS đã được người dùng tùy chỉnh."""
        return self.voice_config

    def _on_confirm_clicked(self):
        if not self.selected_dataset:
            return
        if self.current_validation and not self.current_validation.can_proceed:
            QMessageBox.critical(
                self,
                "Không thể tiếp tục",
                "Bản ghi Master Data này đang có lỗi nghiêm trọng chưa được giải quyết trong Pipeline A.\n"
                "Vui lòng quay lại kiểm tra trước khi render video.",
            )
            return
        # Lưu lại cấu hình Logo Thương hiệu & Watermark
        self._save_branding_settings()
        # Cập nhật cấu hình giọng đọc vào các segment của Master Data
        if self.selected_dataset:
            self.voice_mapper.map_dataset(self.selected_dataset)
        self.accept()

    def get_selected_dataset(self) -> MasterDataset | None:
        return self.selected_dataset

    def _sync_initial_preset_selection(self):
        curr_orig = self.slider_orig_vol.value()
        curr_tts = self.slider_tts_vol.value()
        matched = False
        for idx in range(self.audio_mix_preset_combo.count()):
            data = self.audio_mix_preset_combo.itemData(idx)
            if data and isinstance(data, dict):
                if data.get("orig") == curr_orig and data.get("tts") == curr_tts:
                    self.audio_mix_preset_combo.blockSignals(True)
                    self.audio_mix_preset_combo.setCurrentIndex(idx)
                    self.audio_mix_preset_combo.blockSignals(False)
                    matched = True
                    break
        if not matched:
            self.audio_mix_preset_combo.blockSignals(True)
            self.audio_mix_preset_combo.setCurrentIndex(self.audio_mix_preset_combo.count() - 1)
            self.audio_mix_preset_combo.blockSignals(False)

    def _on_audio_preset_changed(self, index: int):
        data = self.audio_mix_preset_combo.itemData(index)
        if data and isinstance(data, dict):
            self.slider_orig_vol.blockSignals(True)
            self.slider_tts_vol.blockSignals(True)
            self.slider_orig_vol.setValue(data.get("orig", 50))
            self.slider_tts_vol.setValue(data.get("tts", 100))
            self.lbl_orig_vol_val.setText(f"{self.slider_orig_vol.value()}%")
            self.lbl_tts_vol_val.setText(f"{self.slider_tts_vol.value()}%")
            self.slider_orig_vol.blockSignals(False)
            self.slider_tts_vol.blockSignals(False)

    def _on_orig_slider_changed(self, val: int):
        self.lbl_orig_vol_val.setText(f"{val}%")
        self._check_audio_mix_preset_custom()

    def _on_tts_slider_changed(self, val: int):
        self.lbl_tts_vol_val.setText(f"{val}%")
        self._check_audio_mix_preset_custom()

    def _check_audio_mix_preset_custom(self):
        curr_orig = self.slider_orig_vol.value()
        curr_tts = self.slider_tts_vol.value()
        matched = False
        for idx in range(self.audio_mix_preset_combo.count()):
            data = self.audio_mix_preset_combo.itemData(idx)
            if data and isinstance(data, dict):
                if data.get("orig") == curr_orig and data.get("tts") == curr_tts:
                    self.audio_mix_preset_combo.blockSignals(True)
                    self.audio_mix_preset_combo.setCurrentIndex(idx)
                    self.audio_mix_preset_combo.blockSignals(False)
                    matched = True
                    break
        if not matched:
            self.audio_mix_preset_combo.blockSignals(True)
            self.audio_mix_preset_combo.setCurrentIndex(self.audio_mix_preset_combo.count() - 1)
            self.audio_mix_preset_combo.blockSignals(False)

    def get_audio_mix_settings(self) -> dict[str, Any]:
        """Trả về cấu hình hòa trộn âm thanh do người dùng thiết lập."""
        return {
            "original_volume": float(self.slider_orig_vol.value()) / 100.0,
            "tts_volume": float(self.slider_tts_vol.value()) / 100.0,
            "original_percent": self.slider_orig_vol.value(),
            "tts_percent": self.slider_tts_vol.value(),
            "preset": self.audio_mix_preset_combo.currentText(),
        }

    def get_subtitle_config(self) -> SubtitleRenderConfig:
        """Trả về cấu hình kiểu dáng phụ đề do người dùng chọn."""
        if hasattr(self, "sub_style_group"):
            return self.sub_style_group.get_subtitle_render_config()
        return SubtitleRenderConfig()

    def _browse_logo_file(self):
        """Mở hộp thoại chọn file ảnh logo thương hiệu."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file ảnh Logo Thương hiệu",
            os.path.dirname(self.txt_logo_path.text().strip()) if self.txt_logo_path.text().strip() else "",
            "File hình ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả file (*.*)",
        )
        if path:
            self.txt_logo_path.setText(os.path.normpath(path))
            self.chk_enable_logo.setChecked(True)
            self._update_logo_preview(path)

    def _update_logo_preview(self, path: str = ""):
        """Cập nhật ảnh thumbnail xem trước của Logo."""
        img_path = (path or self.txt_logo_path.text()).strip()
        if img_path and os.path.isfile(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                scaled = pix.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lbl_logo_preview.setPixmap(scaled)
                self.lbl_logo_preview.setText("")
                self.lbl_logo_preview.setToolTip(f"Logo: {os.path.basename(img_path)}")
                return
        self.lbl_logo_preview.clear()
        self.lbl_logo_preview.setText("Logo")
        self.lbl_logo_preview.setToolTip("Chưa chọn file ảnh")

    def _browse_frame_border_top_file(self):
        """Mở hộp thoại chọn file ảnh viền trên."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file ảnh Viền Trên Video",
            os.path.dirname(self.txt_frame_border_top_path.text().strip()) if self.txt_frame_border_top_path.text().strip() else "",
            "File hình ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả file (*.*)",
        )
        if path:
            self.txt_frame_border_top_path.setText(os.path.normpath(path))
            self.chk_enable_frame_border.setChecked(True)
            self._update_frame_border_top_preview(path)

    def _browse_frame_border_bottom_file(self):
        """Mở hộp thoại chọn file ảnh viền dưới."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file ảnh Viền Dưới Video",
            os.path.dirname(self.txt_frame_border_bottom_path.text().strip()) if self.txt_frame_border_bottom_path.text().strip() else "",
            "File hình ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả file (*.*)",
        )
        if path:
            self.txt_frame_border_bottom_path.setText(os.path.normpath(path))
            self.chk_enable_frame_border.setChecked(True)
            self._update_frame_border_bottom_preview(path)

    def _copy_top_border_to_bottom(self):
        """Sao chép đường dẫn ảnh viền trên xuống viền dưới."""
        top_p = self.txt_frame_border_top_path.text().strip()
        if top_p:
            self.txt_frame_border_bottom_path.setText(top_p)
            self._update_frame_border_bottom_preview(top_p)

    def _clear_frame_border_top(self):
        """Xóa ảnh viền trên để chỉ dùng viền dưới hoặc tắt viền trên."""
        self.txt_frame_border_top_path.clear()
        self._update_frame_border_top_preview("")

    def _clear_frame_border_bottom(self):
        """Xóa ảnh viền dưới để chỉ dùng viền trên hoặc tắt viền dưới."""
        self.txt_frame_border_bottom_path.clear()
        self._update_frame_border_bottom_preview("")

    def _update_frame_border_top_preview(self, path: str = ""):
        """Cập nhật ảnh thumbnail xem trước của Viền Trên."""
        img_path = (path or self.txt_frame_border_top_path.text()).strip()
        if img_path and os.path.isfile(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                scaled = pix.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lbl_frame_border_top_preview.setPixmap(scaled)
                self.lbl_frame_border_top_preview.setText("")
                self.lbl_frame_border_top_preview.setToolTip(f"Viền trên: {os.path.basename(img_path)}")
                return
        self.lbl_frame_border_top_preview.clear()
        self.lbl_frame_border_top_preview.setText("Trên")
        self.lbl_frame_border_top_preview.setToolTip("Chưa chọn file ảnh")

    def _update_frame_border_bottom_preview(self, path: str = ""):
        """Cập nhật ảnh thumbnail xem trước của Viền Dưới."""
        img_path = (path or self.txt_frame_border_bottom_path.text()).strip()
        if img_path and os.path.isfile(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                scaled = pix.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lbl_frame_border_bot_preview.setPixmap(scaled)
                self.lbl_frame_border_bot_preview.setText("")
                self.lbl_frame_border_bot_preview.setToolTip(f"Viền dưới: {os.path.basename(img_path)}")
                return
        self.lbl_frame_border_bot_preview.clear()
        self.lbl_frame_border_bot_preview.setText("Dưới")
        self.lbl_frame_border_bot_preview.setToolTip("Chưa chọn file ảnh")

    def _on_video_zoom_changed(self, val: int):
        if val == 100:
            self.lbl_video_zoom_val.setText("100% (Gốc)")
        elif 110 <= val <= 120:
            self.lbl_video_zoom_val.setText(f"{val}% (Gợi ý)")
        else:
            self.lbl_video_zoom_val.setText(f"{val}%")

    def _load_branding_settings(self):
        """Khôi phục các cài đặt Logo, Khung Viền & Watermark đã lưu từ trước."""
        settings = QSettings("TankVideo", "TankVideoStudio")
        # Brand Logo
        enable_logo = settings.value("branding/enable_logo", False, type=bool)
        logo_path = settings.value("branding/logo_path", "", type=str)
        logo_pos = settings.value("branding/logo_pos", "top_left", type=str)
        logo_size = settings.value("branding/logo_size", 15, type=int)
        logo_op = settings.value("branding/logo_opacity", 100, type=int)

        self.chk_enable_logo.setChecked(enable_logo)
        self.logo_container.setEnabled(enable_logo)
        if logo_path:
            self.txt_logo_path.setText(logo_path)
            self._update_logo_preview(logo_path)

        pos_idx = self.combo_logo_pos.findData(logo_pos)
        if pos_idx >= 0:
            self.combo_logo_pos.setCurrentIndex(pos_idx)

        # Tự động nhận diện khung Đặt Logo từ Preview
        self._logo_custom_rect = None
        p = self.parent()
        if p and hasattr(p, "logo_placement_overlay"):
            lp_ov = getattr(p, "logo_placement_overlay", None)
            if lp_ov and (getattr(lp_ov, "_user_customized", False) or getattr(lp_ov, "_requested_visible", False)):
                lpr = lp_ov.get_norm_rect()
                if lpr and len(lpr) == 4 and lpr[2] > 0.01:
                    self._logo_custom_rect = lpr
                    idx_custom = self.combo_logo_pos.findData("custom_rect")
                    if idx_custom >= 0:
                        self.combo_logo_pos.setCurrentIndex(idx_custom)
                    self.lbl_logo_custom_info.setText(f"📐 x={lpr[0]*100:.1f}%, y={lpr[1]*100:.1f}%, w={lpr[2]*100:.1f}%, h={lpr[3]*100:.1f}%")
        if self._logo_custom_rect is None:
            saved_lp = settings.value("branding/logo_custom_rect", "", type=str)
            if saved_lp:
                try:
                    parts = [float(x) for x in saved_lp.split(",")]
                    if len(parts) == 4:
                        self._logo_custom_rect = tuple(parts)
                        if logo_pos == "custom_rect":
                            self.lbl_logo_custom_info.setText(f"📐 x={parts[0]*100:.1f}%, y={parts[1]*100:.1f}%, w={parts[2]*100:.1f}%, h={parts[3]*100:.1f}%")
                except Exception:
                    pass

        self.slider_logo_size.setValue(max(5, min(35, logo_size)))
        self.slider_logo_opacity.setValue(max(10, min(100, logo_op)))

        # Frame Border (Viền Trên & Viền Dưới)
        enable_border = settings.value("branding/enable_frame_border", False, type=bool)
        top_val = settings.value("branding/frame_border_top_path", None)
        bot_val = settings.value("branding/frame_border_bottom_path", None)
        border_legacy = settings.value("branding/frame_border_path", "", type=str)
        if top_val is None and border_legacy:
            border_top = border_legacy
        else:
            border_top = str(top_val or "").strip()
        border_bot = str(bot_val or "").strip()
        border_op = settings.value("branding/frame_border_opacity", 100, type=int)
        top_h = settings.value("branding/frame_border_top_height", 0, type=int)
        bot_h = settings.value("branding/frame_border_bottom_height", 0, type=int)

        self.chk_enable_frame_border.setChecked(enable_border)
        self.border_container.setEnabled(enable_border)
        if border_top:
            self.txt_frame_border_top_path.setText(border_top)
            self._update_frame_border_top_preview(border_top)
        else:
            self.txt_frame_border_top_path.clear()
            self._update_frame_border_top_preview("")
        if border_bot:
            self.txt_frame_border_bottom_path.setText(border_bot)
            self._update_frame_border_bottom_preview(border_bot)
        else:
            self.txt_frame_border_bottom_path.clear()
            self._update_frame_border_bottom_preview("")
        self.slider_frame_border_opacity.setValue(max(10, min(100, border_op)))
        self.spin_frame_border_top_height.setValue(max(0, min(400, top_h)))
        self.spin_frame_border_bottom_height.setValue(max(0, min(400, bot_h)))

        # Thu phóng video (Zoom kiểu CapCut)
        video_zoom = settings.value("branding/video_scale_percent", 100, type=int)
        self.slider_video_zoom.setValue(max(100, min(150, video_zoom)))
        self._on_video_zoom_changed(self.slider_video_zoom.value())

        # Watermark
        enable_wm = settings.value("branding/enable_watermark", False, type=bool)
        wm_text = settings.value("branding/watermark_text", "", type=str)
        wm_pos = settings.value("branding/watermark_pos", "center", type=str)
        wm_size = settings.value("branding/watermark_size", 36, type=int)
        wm_op = settings.value("branding/watermark_opacity", 35, type=int)

        self.chk_enable_watermark.setChecked(enable_wm)
        self.wm_container.setEnabled(enable_wm)
        if wm_text:
            self.txt_watermark.setText(wm_text)

        wm_pos_idx = self.combo_watermark_pos.findData(wm_pos)
        if wm_pos_idx >= 0:
            self.combo_watermark_pos.setCurrentIndex(wm_pos_idx)

        # Tự động nhận diện khung Đặt Watermark từ Preview
        self._watermark_custom_rect = None
        if p and hasattr(p, "watermark_placement_overlay"):
            wmp_ov = getattr(p, "watermark_placement_overlay", None)
            if wmp_ov and (getattr(wmp_ov, "_user_customized", False) or getattr(wmp_ov, "_requested_visible", False)):
                wmpr = wmp_ov.get_norm_rect()
                if wmpr and len(wmpr) == 4 and wmpr[2] > 0.01:
                    self._watermark_custom_rect = wmpr
                    idx_custom_wm = self.combo_watermark_pos.findData("custom_rect")
                    if idx_custom_wm >= 0:
                        self.combo_watermark_pos.setCurrentIndex(idx_custom_wm)
                    self.lbl_wm_custom_info.setText(f"📐 x={wmpr[0]*100:.1f}%, y={wmpr[1]*100:.1f}%, w={wmpr[2]*100:.1f}%, h={wmpr[3]*100:.1f}%")
        if self._watermark_custom_rect is None:
            saved_wmp = settings.value("branding/watermark_custom_rect", "", type=str)
            if saved_wmp:
                try:
                    parts = [float(x) for x in saved_wmp.split(",")]
                    if len(parts) == 4:
                        self._watermark_custom_rect = tuple(parts)
                        if wm_pos == "custom_rect":
                            self.lbl_wm_custom_info.setText(f"📐 x={parts[0]*100:.1f}%, y={parts[1]*100:.1f}%, w={parts[2]*100:.1f}%, h={parts[3]*100:.1f}%")
                except Exception:
                    pass

        self.slider_watermark_size.setValue(max(18, min(80, wm_size)))
        self.slider_watermark_opacity.setValue(max(10, min(100, wm_op)))

    def _save_branding_settings(self):
        """Lưu lại cài đặt Logo, Khung Viền & Watermark để tiện dùng cho những lần sau."""
        settings = QSettings("TankVideo", "TankVideoStudio")
        # Brand Logo
        settings.setValue("branding/enable_logo", self.chk_enable_logo.isChecked())
        settings.setValue("branding/logo_path", self.txt_logo_path.text().strip())
        settings.setValue("branding/logo_pos", self.combo_logo_pos.currentData() or "top_left")
        if getattr(self, "_logo_custom_rect", None):
            lr = self._logo_custom_rect
            settings.setValue("branding/logo_custom_rect", f"{lr[0]:.4f},{lr[1]:.4f},{lr[2]:.4f},{lr[3]:.4f}")
        settings.setValue("branding/logo_size", self.slider_logo_size.value())
        settings.setValue("branding/logo_opacity", self.slider_logo_opacity.value())

        # Frame Border (Viền Trên & Viền Dưới)
        settings.setValue("branding/enable_frame_border", self.chk_enable_frame_border.isChecked())
        settings.setValue("branding/frame_border_top_path", self.txt_frame_border_top_path.text().strip())
        settings.setValue("branding/frame_border_bottom_path", self.txt_frame_border_bottom_path.text().strip())
        settings.setValue("branding/frame_border_path", self.txt_frame_border_top_path.text().strip() or self.txt_frame_border_bottom_path.text().strip())
        settings.setValue("branding/frame_border_opacity", self.slider_frame_border_opacity.value())
        settings.setValue("branding/frame_border_top_height", self.spin_frame_border_top_height.value())
        settings.setValue("branding/frame_border_bottom_height", self.spin_frame_border_bottom_height.value())

        # Thu phóng video (Zoom kiểu CapCut)
        settings.setValue("branding/video_scale_percent", self.slider_video_zoom.value())

        # Watermark
        settings.setValue("branding/enable_watermark", self.chk_enable_watermark.isChecked())
        settings.setValue("branding/watermark_text", self.txt_watermark.text().strip())
        settings.setValue("branding/watermark_pos", self.combo_watermark_pos.currentData() or "center")
        if getattr(self, "_watermark_custom_rect", None):
            wmr = self._watermark_custom_rect
            settings.setValue("branding/watermark_custom_rect", f"{wmr[0]:.4f},{wmr[1]:.4f},{wmr[2]:.4f},{wmr[3]:.4f}")
        settings.setValue("branding/watermark_size", self.slider_watermark_size.value())
        settings.setValue("branding/watermark_opacity", self.slider_watermark_opacity.value())

    def _grab_logo_preview_rect(self):
        """Lấy tọa độ vùng chọn từ khung Đặt Logo trên màn hình Preview."""
        p = self.parent()
        rect = None
        if p and hasattr(p, "logo_placement_overlay"):
            lp_ov = getattr(p, "logo_placement_overlay", None)
            if lp_ov and hasattr(lp_ov, "get_norm_rect"):
                rect = lp_ov.get_norm_rect()
                if hasattr(p, "toggle_logo_placement_overlay") and not getattr(lp_ov, "_requested_visible", False):
                    p.toggle_logo_placement_overlay(True)
        if rect and len(rect) == 4 and rect[2] > 0.01:
            self._logo_custom_rect = rect
            idx = self.combo_logo_pos.findData("custom_rect")
            if idx >= 0:
                self.combo_logo_pos.setCurrentIndex(idx)
            self.lbl_logo_custom_info.setText(f"📐 x={rect[0]*100:.1f}%, y={rect[1]*100:.1f}%, w={rect[2]*100:.1f}%, h={rect[3]*100:.1f}%")
            self.chk_enable_logo.setChecked(True)
            self.logo_container.setEnabled(True)

    def _grab_wm_placement_rect(self):
        """Lấy tọa độ vùng chọn từ khung Đặt Watermark trên màn hình Preview."""
        p = self.parent()
        rect = None
        if p and hasattr(p, "watermark_placement_overlay"):
            wmp_ov = getattr(p, "watermark_placement_overlay", None)
            if wmp_ov and hasattr(wmp_ov, "get_norm_rect"):
                rect = wmp_ov.get_norm_rect()
                if hasattr(p, "toggle_watermark_placement_overlay") and not getattr(wmp_ov, "_requested_visible", False):
                    p.toggle_watermark_placement_overlay(True)
        if rect and len(rect) == 4 and rect[2] > 0.01:
            self._watermark_custom_rect = rect
            idx = self.combo_watermark_pos.findData("custom_rect")
            if idx >= 0:
                self.combo_watermark_pos.setCurrentIndex(idx)
            self.lbl_wm_custom_info.setText(f"📐 x={rect[0]*100:.1f}%, y={rect[1]*100:.1f}%, w={rect[2]*100:.1f}%, h={rect[3]*100:.1f}%")
            self.chk_enable_watermark.setChecked(True)
            self.wm_container.setEnabled(True)

    def get_brand_logo_settings(self) -> dict[str, Any]:
        """Trả về cấu hình Logo thương hiệu được thiết lập."""
        pos = str(self.combo_logo_pos.currentData() or "top_left")
        return {
            "enable": self.chk_enable_logo.isChecked() and bool(self.txt_logo_path.text().strip()),
            "path": self.txt_logo_path.text().strip(),
            "position": pos,
            "size_percent": int(self.slider_logo_size.value()),
            "opacity": float(self.slider_logo_opacity.value()) / 100.0,
            "custom_rect": getattr(self, "_logo_custom_rect", None) if pos == "custom_rect" else None,
        }

    def get_frame_border_settings(self) -> dict[str, Any]:
        """Trả về cấu hình Khung viền video trang trí được thiết lập."""
        top_p = self.txt_frame_border_top_path.text().strip()
        bot_p = self.txt_frame_border_bottom_path.text().strip()
        return {
            "enable": self.chk_enable_frame_border.isChecked() and bool(top_p or bot_p),
            "top_path": top_p,
            "bottom_path": bot_p,
            "path": top_p or bot_p,
            "opacity": float(self.slider_frame_border_opacity.value()) / 100.0,
            "top_height": int(self.spin_frame_border_top_height.value()),
            "bottom_height": int(self.spin_frame_border_bottom_height.value()),
        }

    def get_video_scale_percent(self) -> int:
        """Trả về tỷ lệ thu phóng video kiểu CapCut (100% - 150%)."""
        return int(self.slider_video_zoom.value())

    def get_watermark_settings(self) -> dict[str, Any]:
        """Trả về cấu hình Hình mờ chữ (Watermark) được thiết lập."""
        pos = str(self.combo_watermark_pos.currentData() or "center")
        return {
            "enable": self.chk_enable_watermark.isChecked() and bool(self.txt_watermark.text().strip()),
            "text": self.txt_watermark.text().strip(),
            "position": pos,
            "font_size": int(self.slider_watermark_size.value()),
            "opacity": float(self.slider_watermark_opacity.value()) / 100.0,
            "color": "#FFFFFF",
            "custom_rect": getattr(self, "_watermark_custom_rect", None) if pos == "custom_rect" else None,
        }


