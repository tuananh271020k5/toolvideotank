"""Single Voice Setup Dialog for 1-Voice Full Pipeline Automation.

Allows users to configure 1 voice narrator, audio mixing levels (original volume vs TTS volume),
blur mask for Chinese subtitles, subtitle burning, and DeepSeek contextual translation.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.video_export_service import SubtitleRenderConfig
from ui.dialogs.select_master_dialog import (
    AVAILABLE_FEMALE_VOICES,
    AVAILABLE_MALE_VOICES,
    _load_available_voices,
)
from ui.widgets.subtitle_style_group import SubtitleStyleGroupBox


class AsrRangeRowWidget(QFrame):
    """Một hàng cấu hình khoảng thời gian ASR [Từ ... Đến ...]."""

    def __init__(self, parent_dialog, index: int, start_val: float = 15.0, end_val: float = 35.0, on_delete=None):
        super().__init__()
        self.parent_dialog = parent_dialog
        self.on_delete = on_delete
        self.setStyleSheet("""
            QFrame {
                background-color: #141f2e;
                border: 1px solid #23374d;
                border-radius: 6px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self.lbl_idx = QLabel(f"Đoạn {index}:")
        self.lbl_idx.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        self.lbl_idx.setMinimumWidth(55)
        layout.addWidget(self.lbl_idx)

        lbl_from = QLabel("Từ:")
        lbl_from.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        layout.addWidget(lbl_from)

        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(0.0, 7200.0)
        self.spin_start.setSingleStep(1.0)
        self.spin_start.setValue(start_val)
        self.spin_start.setSuffix(" giây")
        self.spin_start.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 4px; padding: 2px 4px; font-size: 11px;")
        layout.addWidget(self.spin_start)

        lbl_to = QLabel("Đến:")
        lbl_to.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600; margin-left: 4px;")
        layout.addWidget(lbl_to)

        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(0.5, 7200.0)
        self.spin_end.setSingleStep(1.0)
        self.spin_end.setValue(end_val)
        self.spin_end.setSuffix(" giây")
        self.spin_end.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 4px; padding: 2px 4px; font-size: 11px;")
        layout.addWidget(self.spin_end)

        self.btn_grab_start = QPushButton("📍 Lấy giây đầu")
        self.btn_grab_start.setToolTip("Lấy vị trí hiện tại trên video làm mốc bắt đầu đoạn này")
        self.btn_grab_start.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; color: #fff; }
        """)
        self.btn_grab_start.clicked.connect(self._grab_start)
        layout.addWidget(self.btn_grab_start)

        self.btn_grab_end = QPushButton("📍 Lấy giây cuối")
        self.btn_grab_end.setToolTip("Lấy vị trí hiện tại trên video làm mốc kết thúc đoạn này")
        self.btn_grab_end.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; color: #fff; }
        """)
        self.btn_grab_end.clicked.connect(self._grab_end)
        layout.addWidget(self.btn_grab_end)

        self.btn_del = QPushButton("🗑️ Xóa")
        self.btn_del.setToolTip("Xóa khoảng thời gian này")
        self.btn_del.setStyleSheet("""
            QPushButton {
                background-color: #3b1818; color: #f87171;
                border: 1px solid #7f1d1d; border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #7f1d1d; color: #fff; }
        """)
        self.btn_del.clicked.connect(self._on_del)
        layout.addWidget(self.btn_del)

        layout.addStretch()

    def _grab_start(self):
        p = self.parent_dialog.parent()
        if p and hasattr(p, "media_player"):
            pos_ms = p.media_player.position()
            self.spin_start.setValue(round(pos_ms / 1000.0, 1))

    def _grab_end(self):
        p = self.parent_dialog.parent()
        pos_ms = 0
        dur_ms = 0
        if p and hasattr(p, "media_player"):
            try:
                pos_ms = int(p.media_player.position() or 0)
                dur_ms = int(p.media_player.duration() or 0)
            except Exception:
                pass

        if pos_ms > 1000:
            self.spin_end.setValue(round(pos_ms / 1000.0, 1))
        elif dur_ms > 1000:
            self.spin_end.setValue(round(dur_ms / 1000.0, 1))
        else:
            dur_s = self.parent_dialog.get_video_duration_seconds()
            if dur_s > 0:
                self.spin_end.setValue(round(dur_s, 1))

    def _on_del(self):
        if callable(self.on_delete):
            self.on_delete(self)

    def set_index(self, idx: int):
        self.lbl_idx.setText(f"Đoạn {idx}:")


class SingleVoiceSetupDialog(QDialog):
    """Hộp thoại Cài đặt Lồng tiếng 1 giọng & Tạo video tự động."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        video_path: str = "",
        video_paths: list[str] | None = None,
        initial_orig_volume: int = 50,
        initial_tts_volume: int = 100,
        blur_rect: tuple[float, float, float, float] | None = None,
        workspace_root: str = "",
        initial_voice_id: str = "",
        individual_voices: dict[str, dict[str, str]] | None = None,
    ):
        super().__init__(parent)
        self.video_path = video_path
        self.video_paths = [os.path.normpath(p) for p in (video_paths or []) if p]
        if not self.video_path and self.video_paths:
            self.video_path = self.video_paths[0]
        self.workspace_root = workspace_root or os.getcwd()
        self.initial_orig_volume = max(0, min(100, int(initial_orig_volume)))
        self.initial_tts_volume = max(0, min(200, int(initial_tts_volume)))
        self.blur_rect = blur_rect
        if not self.blur_rect and parent:
            for attr in ("ocr_region_overlay", "ocr_overlay"):
                ov = getattr(parent, attr, None)
                if not ov and hasattr(parent, "preview_panel"):
                    ov = getattr(parent.preview_panel, attr, None)
                if ov and hasattr(ov, "get_norm_rect"):
                    r = ov.get_norm_rect()
                    if r and len(r) == 4 and r[2] > 0.01 and r[3] > 0.01:
                        self.blur_rect = tuple(r)
                        break
            if not self.blur_rect and hasattr(parent, "_video_ocr_rects"):
                norm_p = os.path.normpath(self.video_path) if self.video_path else ""
                self.blur_rect = parent._video_ocr_rects.get(norm_p)
            if not self.blur_rect:
                rect_str = os.getenv("OCR_SUBTITLE_RECT", "").strip()
                if rect_str:
                    try:
                        parts = [float(p) for p in rect_str.split(",")]
                        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                            self.blur_rect = tuple(parts)
                    except Exception:
                        pass
        self._watermark_delogo_rect = (0.35, 0.49, 0.30, 0.045)
        self.initial_voice_id = initial_voice_id
        self.individual_voices = individual_voices
        self.start_now = False
        self.save_only = False

        self.setWindowTitle("Cài đặt lồng tiếng (1 giọng) & Tạo video tự động")
        # Tự động tính toán kích thước rộng rãi, vừa vặn độ phân giải màn hình
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            target_w = max(900, min(1020, int(geo.width() * 0.75)))
            target_h = max(780, min(900, int(geo.height() * 0.90)))
            self.resize(target_w, target_h)
        else:
            self.resize(940, 840)
        self.setMinimumSize(850, 600)

        # Cho phép click bên ngoài dialog để đóng (dismiss/cancel)
        self.setWindowFlags(self.windowFlags() | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        self._setup_ui()
        self._apply_dark_style()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(8, 6, 14, 6)
        layout.setSpacing(14)

        # Header Title
        count = len(self.video_paths)
        title_text = f"🎬 CÀI ĐẶT LỒNG TIẾNG (1 GIỌNG) CHO {count} VIDEO ĐÃ CHỌN" if count > 1 else "🎬 CÀI ĐẶT LỒNG TIẾNG (1 GIỌNG) & TẠO VIDEO"
        title_label = QLabel(title_text)
        t_font = QFont()
        t_font.setPointSize(12)
        t_font.setBold(True)
        title_label.setFont(t_font)
        title_label.setStyleSheet("color: #ff7b1a;")
        layout.addWidget(title_label)

        desc_label = QLabel(
            "Quy trình tự động: Quét OCR phụ đề Trung ➔ Dịch chuẩn ngữ cảnh bằng DeepSeek AI ➔ "
            "Lồng tiếng 1 nhân vật ➔ Che mờ chữ Trung gốc và đè phụ đề tiếng Việt lên video."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #9fb3ca; font-size: 12px; line-height: 1.4;")
        layout.addWidget(desc_label)

        if count > 1:
            names = [os.path.basename(p) for p in self.video_paths]
            disp = ", ".join(names[:3]) + (f" ...và {len(names) - 3} video khác" if len(names) > 3 else "")
            v_box = QLabel(f"🎬 Video áp dụng: {disp}")
            v_box.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600; background: #162032; padding: 4px 8px; border-radius: 4px;")
            layout.addWidget(v_box)

        # --- 1. KHUNG CHỌN 1 GIỌNG LỒNG TIẾNG ---
        voice_box = QGroupBox("1. Chọn giọng đọc nhân vật lồng tiếng")
        voice_layout = QVBoxLayout(voice_box)
        voice_layout.setSpacing(8)
        voice_layout.setContentsMargins(12, 14, 12, 12)

        v_row = QHBoxLayout()
        v_lbl = QLabel("🎙️ Giọng thuyết minh / lồng tiếng:")
        v_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #f8fbff;")
        v_row.addWidget(v_lbl)

        self.combo_voice = QComboBox()
        self.combo_voice.setMinimumHeight(34)

        # Nạp động danh sách giọng đọc mới nhất
        try:
            cur_females, cur_males = _load_available_voices()
        except Exception:
            cur_females, cur_males = AVAILABLE_FEMALE_VOICES, AVAILABLE_MALE_VOICES

        # Tổng hợp danh sách giọng đọc
        all_voices = []
        # Tùy chọn Không lồng tiếng (Chỉ tạo phụ đề Subtitle)
        all_voices.append({
            "id": "no_voiceover",
            "name": "🚫 Không lồng tiếng (Chỉ tạo & đè phụ đề Subtitle)",
            "provider": "none",
        })
        # VieNeu VIP
        all_voices.append({"id": "vieneu_thanhnientutin", "name": "👑 Thanh niên tự tin (VieNeu Neural VIP)", "provider": "vieneu"})
        all_voices.append({"id": "vieneu_cogaihoatbat", "name": "👑 Cô gái hoạt ngôn (VieNeu Neural VIP)", "provider": "vieneu"})
        # Piper & Edge
        seen = {"no_voiceover", "vieneu_thanhnientutin", "vieneu_cogaihoatbat"}
        for v in cur_males:
            if v["id"] not in seen:
                seen.add(v["id"])
                prefix = "👨 " if "vieneu" not in v["id"] else ""
                all_voices.append({**v, "name": f"{prefix}{v['name']}"})
        for v in cur_females:
            if v["id"] not in seen:
                seen.add(v["id"])
                prefix = "👩 " if "vieneu" not in v["id"] else ""
                all_voices.append({**v, "name": f"{prefix}{v['name']}"})

        # Kiểm tra xem các video đã chọn có cài giọng riêng từ trước không
        has_individual = False
        indiv_map = getattr(self, "individual_voices", None)
        if indiv_map is None and self.parent() and hasattr(self.parent(), "_video_voice_configs"):
            indiv_map = {}
            for p in self.video_paths:
                cfg = getattr(self.parent(), "_video_voice_configs", {}).get(os.path.normpath(p), {})
                if cfg.get("label") and (cfg.get("voice_name") or cfg.get("voice_mode") == "dual"):
                    indiv_map[os.path.normpath(p)] = cfg

        if len(self.video_paths) > 1 and indiv_map and len(indiv_map) > 0:
            summaries = []
            for p in self.video_paths:
                c = indiv_map.get(os.path.normpath(p), {})
                v_l = c.get("label", "")
                base = os.path.basename(p)
                short_b = base if len(base) <= 12 else base[:10] + "…"
                if v_l:
                    summaries.append(f"{short_b}: {v_l}")
                else:
                    summaries.append(f"{short_b}: Mặc định")
            summ_txt = ", ".join(summaries[:2]) + ("..." if len(summaries) > 2 else "")
            keep_item = {
                "id": "keep_individual",
                "name": f"🎯 Giữ nguyên giọng riêng từng video ({summ_txt})",
                "provider": "individual",
            }
            all_voices.insert(1, keep_item)
            has_individual = True

        def_voice_idx = 1 if len(all_voices) > 1 and all_voices[0]["id"] == "no_voiceover" else 0
        if not has_individual:
            target_v = getattr(self, "initial_voice_id", "").strip()
            for idx, item in enumerate(all_voices):
                if target_v and (item["id"] == target_v or target_v in item["id"]):
                    def_voice_idx = idx
                    break
                elif not target_v and item["id"] in ("adam1", "adam"):
                    def_voice_idx = idx

        for idx, item in enumerate(all_voices):
            self.combo_voice.addItem(item["name"], item)

        self.combo_voice.setCurrentIndex(def_voice_idx)
        v_row.addWidget(self.combo_voice, 1)
        voice_layout.addLayout(v_row)

        # Checkbox Chuyên dụng: Chỉ tạo phụ đề, không lồng tiếng
        self.chk_no_voiceover = QCheckBox("🚫 Tắt lồng tiếng (Chỉ dịch & tạo phụ đề, giữ 100% âm thanh gốc video)")
        self.chk_no_voiceover.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px; margin-top: 4px;")
        voice_layout.addWidget(self.chk_no_voiceover)

        self.voice_hint = QLabel("Giọng này sẽ được dùng để lồng tiếng cho toàn bộ các câu thoại trong video.")
        self.voice_hint.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        voice_layout.addWidget(self.voice_hint)

        self._updating_no_voice_state = False

        def _update_voice_hint():
            if self._updating_no_voice_state:
                return
            cur = self.combo_voice.currentData()
            is_no_voice = isinstance(cur, dict) and cur.get("id") == "no_voiceover"

            if is_no_voice:
                self.voice_hint.setText("💡 Chế độ Chỉ Phụ Đề: Video giữ 100% âm thanh gốc. Hệ thống chỉ quét OCR, dịch phụ đề, che chữ gốc và chèn phụ đề tiếng Việt (bỏ qua TTS, xuất siêu nhanh).")
                self.voice_hint.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
                self._updating_no_voice_state = True
                self.chk_no_voiceover.setChecked(True)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(False)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText("0% (Tắt)")
                self._updating_no_voice_state = False
            elif isinstance(cur, dict) and cur.get("id") == "keep_individual":
                self.voice_hint.setText("💡 Mỗi video trong hàng đợi sẽ được tạo đúng theo giọng đọc bạn đã chọn trên từng thẻ video.")
                self.voice_hint.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
                self._updating_no_voice_state = True
                self.chk_no_voiceover.setChecked(False)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(True)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText(f"{self.slider_tts.value()}%")
                self._updating_no_voice_state = False
            elif len(self.video_paths) > 1:
                self.voice_hint.setText("⚠️ Áp dụng giọng này để thay thế cho toàn bộ các video trong hàng đợi.")
                self.voice_hint.setStyleSheet("color: #FBBF24; font-size: 11px;")
                self._updating_no_voice_state = True
                self.chk_no_voiceover.setChecked(False)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(True)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText(f"{self.slider_tts.value()}%")
                self._updating_no_voice_state = False
            else:
                self.voice_hint.setText("Giọng này sẽ được dùng để lồng tiếng cho toàn bộ các câu thoại trong video.")
                self.voice_hint.setStyleSheet("color: #8ea3bb; font-size: 11px;")
                self._updating_no_voice_state = True
                self.chk_no_voiceover.setChecked(False)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(True)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText(f"{self.slider_tts.value()}%")
                self._updating_no_voice_state = False

        def _on_chk_no_voiceover_toggled(checked: bool):
            if self._updating_no_voice_state:
                return
            self._updating_no_voice_state = True
            if checked:
                # Tìm index của no_voiceover trong combo_voice
                idx = self.combo_voice.findData("no_voiceover")
                if idx < 0:
                    for i in range(self.combo_voice.count()):
                        d = self.combo_voice.itemData(i)
                        if isinstance(d, dict) and d.get("id") == "no_voiceover":
                            idx = i
                            break
                if idx >= 0:
                    self.combo_voice.setCurrentIndex(idx)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(False)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText("0% (Tắt)")
                if hasattr(self, "slider_orig") and self.slider_orig is not None:
                    self.slider_orig.setValue(100)
                self.voice_hint.setText("💡 Chế độ Chỉ Phụ Đề: Video giữ 100% âm thanh gốc. Hệ thống chỉ quét OCR, dịch phụ đề, che chữ gốc và chèn phụ đề tiếng Việt (bỏ qua TTS, xuất siêu nhanh).")
                self.voice_hint.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
            else:
                # Khôi phục lại giọng đọc mặc định nếu đang ở no_voiceover
                cur = self.combo_voice.currentData()
                if isinstance(cur, dict) and cur.get("id") == "no_voiceover":
                    next_idx = 1 if self.combo_voice.count() > 1 else 0
                    self.combo_voice.setCurrentIndex(next_idx)
                if hasattr(self, "slider_tts") and self.slider_tts is not None:
                    self.slider_tts.setEnabled(True)
                if hasattr(self, "lbl_tts_val") and self.lbl_tts_val is not None:
                    self.lbl_tts_val.setText(f"{self.slider_tts.value()}%")
                self._updating_no_voice_state = False
                _update_voice_hint()
                return
            self._updating_no_voice_state = False

        self.chk_no_voiceover.toggled.connect(_on_chk_no_voiceover_toggled)
        self.combo_voice.currentIndexChanged.connect(_update_voice_hint)
        _update_voice_hint()

        # Nếu ban đầu voice_id là no_voiceover thì bật checkbox
        if getattr(self, "initial_voice_id", "") in ("no_voiceover", "none"):
            self.chk_no_voiceover.setChecked(True)

        layout.addWidget(voice_box)

        # --- 2. KHUNG HÒA TRỘN ÂM THANH (AUDIO MIXING) ---
        audio_box = QGroupBox("2. Điều chỉnh âm lượng & Hòa âm")
        audio_layout = QVBoxLayout(audio_box)
        audio_layout.setSpacing(10)
        audio_layout.setContentsMargins(12, 14, 12, 12)

        # Slider Âm lượng gốc
        row_orig = QHBoxLayout()
        lbl_orig = QLabel("Âm lượng âm thanh gốc / Nhạc nền:")
        lbl_orig.setMinimumWidth(210)
        lbl_orig.setStyleSheet("color: #d7e3f4; font-size: 12px;")
        self.slider_orig = QSlider(Qt.Horizontal)
        self.slider_orig.setRange(0, 100)
        self.slider_orig.setValue(self.initial_orig_volume)
        self.lbl_orig_val = QLabel(f"{self.initial_orig_volume}%")
        self.lbl_orig_val.setMinimumWidth(45)
        self.lbl_orig_val.setStyleSheet("color: #8ad7ff; font-weight: bold;")
        self.slider_orig.valueChanged.connect(lambda v: self.lbl_orig_val.setText(f"{v}%"))
        row_orig.addWidget(lbl_orig)
        row_orig.addWidget(self.slider_orig, 1)
        row_orig.addWidget(self.lbl_orig_val)
        audio_layout.addLayout(row_orig)

        # Slider Âm lượng lồng tiếng
        row_tts = QHBoxLayout()
        lbl_tts = QLabel("Âm lượng giọng đọc AI (TTS):")
        lbl_tts.setMinimumWidth(210)
        lbl_tts.setStyleSheet("color: #d7e3f4; font-size: 12px;")
        self.slider_tts = QSlider(Qt.Horizontal)
        self.slider_tts.setRange(0, 150)
        self.slider_tts.setValue(self.initial_tts_volume)
        self.lbl_tts_val = QLabel(f"{self.initial_tts_volume}%")
        self.lbl_tts_val.setMinimumWidth(45)
        self.lbl_tts_val.setStyleSheet("color: #3ddc97; font-weight: bold;")
        self.slider_tts.valueChanged.connect(lambda v: self.lbl_tts_val.setText(f"{v}%"))
        row_tts.addWidget(lbl_tts)
        row_tts.addWidget(self.slider_tts, 1)
        row_tts.addWidget(self.lbl_tts_val)
        audio_layout.addLayout(row_tts)

        audio_hint = QLabel("💡 Tự động kích hoạt Audio Ducking: Nhạc nền sẽ tự giảm nhỏ khi có giọng đọc AI phát ra.")
        audio_hint.setStyleSheet("color: #79c0ff; font-size: 11px;")
        audio_layout.addWidget(audio_hint)

        layout.addWidget(audio_box)

        # --- 3. KHUNG CHE CHỮ TRUNG & ĐÈ PHỤ ĐỀ ---
        visual_box = QGroupBox("3. Xử lý che chữ Trung & Phụ đề Tiếng Việt")
        visual_layout = QVBoxLayout(visual_box)
        visual_layout.setSpacing(8)
        visual_layout.setContentsMargins(12, 14, 12, 12)

        self.chk_blur_mask = QCheckBox("Bật tính năng che chữ Trung gốc (Mặt nạ Động bám sát chữ, chỉ che khi có thoại)")
        self.chk_blur_mask.setChecked(True)
        self.chk_blur_mask.setStyleSheet("color: #f8fbff; font-weight: bold; font-size: 12px;")
        visual_layout.addWidget(self.chk_blur_mask)

        # Lựa chọn kiểu mặt nạ che chữ: Làm mờ ngang (Ảnh 1) hoặc Hộp đen (Ảnh 2)
        mask_row = QHBoxLayout()
        mask_row.setSpacing(8)
        lbl_mask_type = QLabel("Kiểu che chữ Trung:")
        lbl_mask_type.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600; margin-left: 20px;")
        mask_row.addWidget(lbl_mask_type)

        self.combo_mask_mode = QComboBox()
        self.combo_mask_mode.addItem("🌫️ Mặt nạ làm mờ ngang (Ảnh 1 - Đẹp & Khuyên dùng)", "blur")
        self.combo_mask_mode.addItem("🖤 Khung che chữ đen bo góc (Ảnh 2)", "box")
        self.combo_mask_mode.addItem("🚫 Không che chữ gốc (Chỉ đè phụ đề)", "none")
        mask_row.addWidget(self.combo_mask_mode, 1)
        visual_layout.addLayout(mask_row)

        self.lbl_mask_hint = QLabel("💡 Dải làm mờ động: Chỉ che khi có chữ Trung, hết chữ tự động ẩn 100%. Kích thước ôm sát chữ gốc, không to thừa chiếm diện tích.")
        self.lbl_mask_hint.setStyleSheet("color: #38bdf8; font-size: 11px; margin-left: 20px;")
        visual_layout.addWidget(self.lbl_mask_hint)

        def _on_mask_mode_changed(idx):
            mode = self.combo_mask_mode.currentData()
            if mode == "blur":
                self.lbl_mask_hint.setText("💡 Dải làm mờ động: Chỉ che khi có chữ Trung, hết chữ tự động ẩn 100%. Kích thước ôm sát chữ gốc, không to thừa chiếm diện tích.")
                self.lbl_mask_hint.setStyleSheet("color: #38bdf8; font-size: 11px; margin-left: 20px;")
                p_idx = self.sub_style_group.combo_preset.findData("blur_mask")
                if p_idx >= 0:
                    self.sub_style_group.combo_preset.setCurrentIndex(p_idx)
            elif mode == "box":
                self.lbl_mask_hint.setText("💡 Khung đen bo góc động: Chỉ xuất hiện đúng lúc có câu thoại, ôm sát độ dài từng câu để che chữ Trung gốc.")
                self.lbl_mask_hint.setStyleSheet("color: #fbbf24; font-size: 11px; margin-left: 20px;")
                p_idx = self.sub_style_group.combo_preset.findData("youtube_box")
                if p_idx >= 0:
                    self.sub_style_group.combo_preset.setCurrentIndex(p_idx)
            else:
                self.lbl_mask_hint.setText("💡 Chỉ hiển thị phụ đề tiếng Việt, không che nội dung video gốc.")
                self.lbl_mask_hint.setStyleSheet("color: #94a3b8; font-size: 11px; margin-left: 20px;")

        self.combo_mask_mode.currentIndexChanged.connect(_on_mask_mode_changed)

        # Hiển thị thông tin toạ độ khung che chữ Trung
        rect_desc = "Tự động phát hiện theo vùng OCR chân trang"
        if self.blur_rect and len(self.blur_rect) == 4:
            x, y, w, h = self.blur_rect
            rect_desc = f"Tọa độ vùng che: x={x:.2f}, y={y:.2f}, w={w:.2f}, h={h:.2f} (Khớp khung OCR đã vẽ)"
        self.lbl_blur_info = QLabel(f"📐 {rect_desc}")
        self.lbl_blur_info.setStyleSheet("color: #8ea3bb; font-size: 11px; margin-left: 20px;")
        visual_layout.addWidget(self.lbl_blur_info)

        self.chk_burn_subtitles = QCheckBox("Đè phụ đề Tiếng Việt dịch bởi DeepSeek AI lên video")
        self.chk_burn_subtitles.setChecked(True)
        self.chk_burn_subtitles.setStyleSheet("color: #f8fbff; font-weight: bold; font-size: 12px;")
        visual_layout.addWidget(self.chk_burn_subtitles)

        # Trạng thái & Cài đặt DeepSeek API
        ds_row = QHBoxLayout()
        ds_row.setContentsMargins(20, 4, 0, 4)
        ds_row.setSpacing(10)

        self.lbl_ds = QLabel()
        self.btn_config_ai = QPushButton("⚙️ Cài đặt / Đổi Key AI")
        self.btn_config_ai.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; color: #fff; }
        """)
        self.btn_config_ai.clicked.connect(self._open_ai_settings)

        self._refresh_deepseek_status()
        ds_row.addWidget(self.lbl_ds, 1)
        ds_row.addWidget(self.btn_config_ai, 0)
        visual_layout.addLayout(ds_row)

        layout.addWidget(visual_box)

        # --- 4. KHUNG KIỂU DÁNG PHỤ ĐỀ & HỘP NỀN CHE VIDEO ---
        self.sub_style_group = SubtitleStyleGroupBox(self, parent_window=self.parent())
        self.chk_burn_subtitles.toggled.connect(self.sub_style_group.setEnabled)
        layout.addWidget(self.sub_style_group)

        # --- 5. KHUNG LOGO THƯƠNG HIỆU & HÌNH MỜ (WATERMARK) ---
        branding_box = QGroupBox("5. Đóng Dấu Bản Quyền CỦA BẠN (Chèn Thêm Logo / Chữ Watermark)")
        branding_layout = QVBoxLayout(branding_box)
        branding_layout.setSpacing(12)
        branding_layout.setContentsMargins(12, 14, 12, 12)

        lbl_brand_hint = QLabel("💡 Đóng dấu logo hoặc tên kênh của bạn lên video. (Lưu ý: Nếu muốn XÓA logo/watermark có sẵn của video nguồn, hãy dùng Mục 6 bên dưới!)")
        lbl_brand_hint.setStyleSheet("color: #9fb3ca; font-size: 11px;")
        lbl_brand_hint.setWordWrap(True)
        branding_layout.addWidget(lbl_brand_hint)

        # === PHẦN A: LOGO THƯƠNG HIỆU (BRAND LOGO) ===
        self.chk_enable_logo = QCheckBox("Bật hiển thị Logo thương hiệu của bạn xuyên suốt video")
        self.chk_enable_logo.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_logo)

        self.logo_container = QWidget()
        logo_c_layout = QVBoxLayout(self.logo_container)
        logo_c_layout.setContentsMargins(18, 0, 0, 4)
        logo_c_layout.setSpacing(8)

        # Hàng 1: Chọn file ảnh logo & thumbnail xem trước
        logo_file_row = QHBoxLayout()
        logo_file_row.setSpacing(8)
        lbl_l_file = QLabel("Ảnh Logo:")
        lbl_l_file.setStyleSheet("color: #d7e3f4; font-size: 12px; font-weight: 600;")
        lbl_l_file.setMinimumWidth(70)
        logo_file_row.addWidget(lbl_l_file)

        self.txt_logo_path = QLineEdit()
        self.txt_logo_path.setPlaceholderText("Chọn file ảnh logo (.png, .jpg, .webp)...")
        self.txt_logo_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        logo_file_row.addWidget(self.txt_logo_path, 1)

        self.btn_browse_logo = QPushButton("📁 Chọn ảnh...")
        self.btn_browse_logo.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #38bdf8;
                border: 1px solid #334155; border-radius: 6px; padding: 5px 12px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; color: #fff; }
        """)
        self.btn_browse_logo.clicked.connect(self._browse_logo_file)
        logo_file_row.addWidget(self.btn_browse_logo)

        # Thumbnail preview nhỏ
        self.lbl_logo_preview = QLabel()
        self.lbl_logo_preview.setFixedSize(40, 40)
        self.lbl_logo_preview.setAlignment(Qt.AlignCenter)
        self.lbl_logo_preview.setStyleSheet("border: 1px dashed #344862; border-radius: 6px; background-color: #16202e; color: #64748b; font-size: 9px;")
        self.lbl_logo_preview.setText("Logo")
        logo_file_row.addWidget(self.lbl_logo_preview)
        logo_c_layout.addLayout(logo_file_row)

        # Hàng 2: Vị trí logo & Kích thước & Độ mờ
        logo_opt_row = QHBoxLayout()
        logo_opt_row.setSpacing(12)

        lbl_l_pos = QLabel("Vị trí:")
        lbl_l_pos.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        logo_opt_row.addWidget(lbl_l_pos)

        self.combo_logo_pos = QComboBox()
        self.combo_logo_pos.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        self.combo_logo_pos.addItem("Góc trên phải (Top-Right) ★ Đè lên vị trí logo Bilibili/Douyin", "top_right")
        self.combo_logo_pos.addItem("Góc trên trái (Top-Left)", "top_left")
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

        # Phân cách mỏng giữa Logo và Viền
        sep_border = QFrame()
        sep_border.setFrameShape(QFrame.HLine)
        sep_border.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep_border)

        # === PHẦN B: KHUNG VIỀN VIDEO TRANG TRÍ (VIỀN TRÊN & VIỀN DƯỚI) ===
        self.chk_enable_frame_border = QCheckBox("🖼️ Thêm Khung Viền Video Trang Trí (Che viền trên/dưới & che logo video gốc)")
        self.chk_enable_frame_border.setStyleSheet("color: #a78bfa; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_frame_border)

        self.border_container = QWidget()
        border_c_layout = QVBoxLayout(self.border_container)
        border_c_layout.setContentsMargins(18, 0, 0, 0)
        border_c_layout.setSpacing(6)

        # Hàng 1: Viền trên
        border_top_row = QHBoxLayout()
        border_top_row.setSpacing(6)
        lbl_b_top = QLabel("Viền trên:")
        lbl_b_top.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        lbl_b_top.setMinimumWidth(70)
        border_top_row.addWidget(lbl_b_top)

        self.txt_frame_border_top_path = QLineEdit()
        self.txt_frame_border_top_path.setPlaceholderText("Chọn file ảnh viền trên (.png, .jpg)...")
        self.txt_frame_border_top_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        self.txt_frame_border_top_path.textChanged.connect(self._update_frame_border_top_preview)
        border_top_row.addWidget(self.txt_frame_border_top_path, 1)

        self.btn_browse_frame_border_top = QPushButton("📁 Chọn ảnh viền trên...")
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
        lbl_b_bot.setMinimumWidth(70)
        border_bot_row.addWidget(lbl_b_bot)

        self.txt_frame_border_bottom_path = QLineEdit()
        self.txt_frame_border_bottom_path.setPlaceholderText("Chọn file ảnh viền dưới (.png, .jpg)...")
        self.txt_frame_border_bottom_path.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        self.txt_frame_border_bottom_path.textChanged.connect(self._update_frame_border_bottom_preview)
        border_bot_row.addWidget(self.txt_frame_border_bottom_path, 1)

        self.btn_browse_frame_border_bot = QPushButton("📁 Chọn ảnh viền dưới...")
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
        border_opt_row.setSpacing(12)
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

        # Phân cách mỏng
        sep_zoom = QFrame()
        sep_zoom.setFrameShape(QFrame.HLine)
        sep_zoom.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep_zoom)

        # === PHẦN D: THU PHÓNG VIDEO KIỂU CAPCUT (SCALE & CENTER CROP) ===
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

        # Phân cách mỏng giữa Viền và Watermark
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #1e293b; margin: 4px 0;")
        branding_layout.addWidget(sep)

        # === PHẦN C: HÌNH MỜ CHỮ (TEXT WATERMARK) ===
        self.chk_enable_watermark = QCheckBox("✍️ Đóng dấu chữ Watermark CỦA BẠN (Chèn thêm chữ lên video)")
        self.chk_enable_watermark.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        branding_layout.addWidget(self.chk_enable_watermark)

        self.wm_container = QWidget()
        wm_c_layout = QVBoxLayout(self.wm_container)
        wm_c_layout.setContentsMargins(18, 0, 0, 0)
        wm_c_layout.setSpacing(8)

        # Hàng 1: Nhập chữ watermark & Vị trí
        wm_row1 = QHBoxLayout()
        wm_row1.setSpacing(8)

        lbl_wm_text = QLabel("Chữ đóng dấu:")
        lbl_wm_text.setStyleSheet("color: #d7e3f4; font-size: 12px; font-weight: 600;")
        lbl_wm_text.setMinimumWidth(95)
        wm_row1.addWidget(lbl_wm_text)

        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("Ví dụ: Nhím Review, Tên Kênh Của Bạn...")
        self.txt_watermark.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 6px; padding: 5px 8px; font-size: 11px;")
        wm_row1.addWidget(self.txt_watermark, 1)

        lbl_wm_pos = QLabel("Vị trí:")
        lbl_wm_pos.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_row1.addWidget(lbl_wm_pos)

        self.combo_watermark_pos = QComboBox()
        self.combo_watermark_pos.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        self.combo_watermark_pos.addItem("Chính giữa video (Center) ★ Khuyên dùng", "center")
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

        # Hàng 2: Độ to & Độ mờ
        wm_row2 = QHBoxLayout()
        wm_row2.setSpacing(12)

        lbl_wm_size = QLabel("Độ to (Cỡ chữ):")
        lbl_wm_size.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_row2.addWidget(lbl_wm_size)

        self.slider_watermark_size = QSlider(Qt.Horizontal)
        self.slider_watermark_size.setRange(18, 80)
        self.slider_watermark_size.setValue(36)
        self.lbl_watermark_size_val = QLabel("36px")
        self.lbl_watermark_size_val.setMinimumWidth(38)
        self.lbl_watermark_size_val.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 11px;")
        self.slider_watermark_size.valueChanged.connect(lambda v: self.lbl_watermark_size_val.setText(f"{v}px"))
        wm_row2.addWidget(self.slider_watermark_size, 1)
        wm_row2.addWidget(self.lbl_watermark_size_val)

        lbl_wm_op = QLabel("Độ mờ (Độ trong suốt):")
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
        layout.addWidget(branding_box)

        # --- 6. KHUNG XÓA LOGO & WATERMARK GỐC (DELOGO AI) ---
        delogo_box = QGroupBox("6. Xóa / Tẩy Logo & Watermark Video Gốc (Delogo AI)")
        delogo_layout = QVBoxLayout(delogo_box)
        delogo_layout.setSpacing(10)
        delogo_layout.setContentsMargins(12, 14, 12, 12)

        delogo_desc = QLabel(
            "💡 Tẩy sạch logo đài, logo Bilibili, Douyin và chữ bản quyền mờ "
            "trên video nguồn bằng thuật toán nội suy điểm ảnh Delogo và BoxBlur thông minh."
        )
        delogo_desc.setWordWrap(True)
        delogo_desc.setStyleSheet("color: #9fb3ca; font-size: 11px; line-height: 1.4;")
        delogo_layout.addWidget(delogo_desc)

        # 1. Preset: Logo Bilibili / Douyin góc trên phải
        self.chk_delogo_bilibili = QCheckBox("🎯 Tự động xóa Logo Bilibili / Douyin (Góc trên cùng bên phải)")
        self.chk_delogo_bilibili.setStyleSheet("color: #ff9d42; font-weight: bold; font-size: 12px;")
        delogo_layout.addWidget(self.chk_delogo_bilibili)

        lbl_bili_hint = QLabel("    Khử sạch logo 'bilibili' và tên kênh ở góc trên phải bằng thuật toán Delogo nội suy điểm ảnh.")
        lbl_bili_hint.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        delogo_layout.addWidget(lbl_bili_hint)

        # 2. Watermark bản quyền
        self.chk_delogo_watermark = QCheckBox("🥷 Xóa sạch Watermark chữ mờ bản quyền (Không tạo mặt nạ mờ)")
        self.chk_delogo_watermark.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        delogo_layout.addWidget(self.chk_delogo_watermark)

        self.watermark_delogo_container = QWidget()
        wm_delogo_layout = QVBoxLayout(self.watermark_delogo_container)
        wm_delogo_layout.setContentsMargins(18, 0, 0, 4)
        wm_delogo_layout.setSpacing(6)

        wm_d_row_mode = QHBoxLayout()
        wm_d_row_mode.setSpacing(10)
        lbl_wm_mode = QLabel("Kiểu xóa:")
        lbl_wm_mode.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        wm_d_row_mode.addWidget(lbl_wm_mode)

        self.combo_wm_delogo_mode = QComboBox()
        self.combo_wm_delogo_mode.addItem("🥷 Xóa sạch tàng hình (Delogo) - Siêu tốc 0s, không để lại vết mờ", "delogo")
        self.combo_wm_delogo_mode.addItem("⚡ Tẩy nhanh phục hồi nền (OpenCV Telea) - Nhanh 2s", "telea")
        self.combo_wm_delogo_mode.addItem("🎭 Mặt nạ che mờ mịn (BoxBlur) - Siêu tốc 1s", "boxblur")
        wm_d_row_mode.addWidget(self.combo_wm_delogo_mode, 1)
        wm_delogo_layout.addLayout(wm_d_row_mode)

        wm_d_row1 = QHBoxLayout()
        self.lbl_watermark_delogo_info = QLabel("📐 Tọa độ: x=35.0%, y=49.0%, w=30.0%, h=4.5%")
        self.lbl_watermark_delogo_info.setStyleSheet("color: #38bdf8; font-size: 11px;")
        wm_d_row1.addWidget(self.lbl_watermark_delogo_info)

        self.btn_grab_watermark_rect = QPushButton("📐 Lấy tọa độ từ khung [Xóa Watermark]")
        self.btn_grab_watermark_rect.setToolTip("Lấy tọa độ vùng chọn từ khung màu xanh dương trên màn hình Preview")
        self.btn_grab_watermark_rect.setStyleSheet("""
            QPushButton {
                background-color: #082f49; color: #38bdf8;
                border: 1px solid #0284c7; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #0369a1; color: #fff; }
        """)
        self.btn_grab_watermark_rect.clicked.connect(self._grab_watermark_preview_rect)
        wm_d_row1.addWidget(self.btn_grab_watermark_rect)
        wm_d_row1.addStretch()
        wm_delogo_layout.addLayout(wm_d_row1)

        delogo_layout.addWidget(self.watermark_delogo_container)
        self.chk_delogo_watermark.toggled.connect(self.watermark_delogo_container.setEnabled)

        # 3. Custom Rect: Vùng xóa tùy chỉnh do người dùng chỉ định (Option 3 kết hợp)
        self.chk_delogo_custom = QCheckBox("📐 Xóa / Làm mờ vùng tùy chọn bổ sung (Khung tự chọn)")
        self.chk_delogo_custom.setStyleSheet("color: #34d399; font-weight: bold; font-size: 12px;")
        delogo_layout.addWidget(self.chk_delogo_custom)

        self.custom_delogo_container = QWidget()
        custom_layout = QVBoxLayout(self.custom_delogo_container)
        custom_layout.setContentsMargins(18, 0, 0, 4)
        custom_layout.setSpacing(8)

        custom_row1 = QHBoxLayout()
        custom_row1.setSpacing(10)

        lbl_cmode = QLabel("Kiểu xử lý:")
        lbl_cmode.setStyleSheet("color: #d7e3f4; font-size: 11px;")
        custom_row1.addWidget(lbl_cmode)

        self.combo_custom_delogo_mode = QComboBox()
        self.combo_custom_delogo_mode.addItem("🎭 Mặt nạ che mờ mịn (BoxBlur) - Siêu tốc 1s", "boxblur")
        self.combo_custom_delogo_mode.addItem("⚡ Tẩy nhanh vết logo (OpenCV Telea) - Nhanh 5-8s", "telea")
        self.combo_custom_delogo_mode.addItem("🥷 Xóa tàng hình (Delogo nội suy viền) - Siêu tốc 1s", "delogo")
        self.combo_custom_delogo_mode.addItem("🤖 AI Deep Learning (LaMa) - Phục hồi nền (Nặng: 4-6 phút CPU)", "inpaint")
        custom_row1.addWidget(self.combo_custom_delogo_mode)

        self.btn_grab_preview_rect = QPushButton("📐 Lấy tọa độ từ Preview")
        self.btn_grab_preview_rect.setToolTip("Lấy tọa độ vùng chọn hiện tại từ khung [Xóa Logo] trên Preview")
        self.btn_grab_preview_rect.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #34d399;
                border: 1px solid #059669; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #065f46; color: #fff; }
        """)
        self.btn_grab_preview_rect.clicked.connect(self._grab_preview_rect)
        custom_row1.addWidget(self.btn_grab_preview_rect)

        self.btn_use_brand_cover = QPushButton("👑 Đè Logo của bạn (1s)")
        self.btn_use_brand_cover.setToolTip("Tự động bật Logo thương hiệu ở mục 5 và đặt góc trên phải để đè lên logo Bilibili")
        self.btn_use_brand_cover.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #fbbf24;
                border: 1px solid #d97706; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #78350f; color: #fff; }
        """)
        self.btn_use_brand_cover.clicked.connect(self._activate_brand_cover_mode)
        custom_row1.addWidget(self.btn_use_brand_cover)

        custom_row1.addStretch()
        custom_layout.addLayout(custom_row1)

        # Hàng 2: Tọa độ tùy chỉnh (X%, Y%, W%, H%)
        coords_row = QHBoxLayout()
        coords_row.setSpacing(8)

        lbl_cx = QLabel("X:")
        lbl_cx.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        self.spin_delogo_x = QDoubleSpinBox()
        self.spin_delogo_x.setRange(0.0, 100.0)
        self.spin_delogo_x.setSingleStep(1.0)
        self.spin_delogo_x.setSuffix("%")
        self.spin_delogo_x.setValue(80.0)

        lbl_cy = QLabel("Y:")
        lbl_cy.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        self.spin_delogo_y = QDoubleSpinBox()
        self.spin_delogo_y.setRange(0.0, 100.0)
        self.spin_delogo_y.setSingleStep(1.0)
        self.spin_delogo_y.setSuffix("%")
        self.spin_delogo_y.setValue(2.0)

        lbl_cw = QLabel("Rộng:")
        lbl_cw.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        self.spin_delogo_w = QDoubleSpinBox()
        self.spin_delogo_w.setRange(1.0, 100.0)
        self.spin_delogo_w.setSingleStep(1.0)
        self.spin_delogo_w.setSuffix("%")
        self.spin_delogo_w.setValue(18.0)

        lbl_ch = QLabel("Cao:")
        lbl_ch.setStyleSheet("color: #8ea3bb; font-size: 11px;")
        self.spin_delogo_h = QDoubleSpinBox()
        self.spin_delogo_h.setRange(1.0, 100.0)
        self.spin_delogo_h.setSingleStep(1.0)
        self.spin_delogo_h.setSuffix("%")
        self.spin_delogo_h.setValue(10.0)

        for sp in (self.spin_delogo_x, self.spin_delogo_y, self.spin_delogo_w, self.spin_delogo_h):
            sp.setStyleSheet("background-color: #1a2738; color: #f8fbff; border: 1px solid #344862; border-radius: 4px; padding: 2px 4px; font-size: 11px;")
            sp.valueChanged.connect(self._update_custom_delogo_label)

        coords_row.addWidget(lbl_cx)
        coords_row.addWidget(self.spin_delogo_x)
        coords_row.addWidget(lbl_cy)
        coords_row.addWidget(self.spin_delogo_y)
        coords_row.addWidget(lbl_cw)
        coords_row.addWidget(self.spin_delogo_w)
        coords_row.addWidget(lbl_ch)
        coords_row.addWidget(self.spin_delogo_h)
        custom_layout.addLayout(coords_row)

        self.lbl_custom_delogo_info = QLabel("📐 Tọa độ vùng xóa: x=80.0%, y=2.0%, w=18.0%, h=10.0%")
        self.lbl_custom_delogo_info.setStyleSheet("color: #34d399; font-size: 11px;")
        custom_layout.addWidget(self.lbl_custom_delogo_info)

        delogo_layout.addWidget(self.custom_delogo_container)
        self.chk_delogo_custom.toggled.connect(self.custom_delogo_container.setEnabled)

        layout.addWidget(delogo_box)

        # --- 7. KHUNG QUÉT BỔ SUNG GIỌNG NÓI (ASR AUDIO) CHO ĐOẠN TIN NHẮN / HỘI THOẠI ---
        asr_box = QGroupBox("7. 🎙️ Quét Bổ Sung Giọng Nói (ASR Audio) Cho Tin Nhắn / Hội Thoại Không Có Sub")
        asr_layout = QVBoxLayout(asr_box)
        asr_layout.setSpacing(10)
        asr_layout.setContentsMargins(12, 14, 12, 12)

        asr_desc = QLabel(
            "💡 Tự động nghe âm thanh và bóc tách lời thoại cho các đoạn nhân vật đọc tin nhắn trên điện thoại, "
            "chat room hoặc lời dẫn không có chữ phụ đề dưới chân trang. Phụ đề tiếng Việt sau khi dịch sẽ được đặt "
            "tại Khung bạn chọn với hộp nền che đẹp mắt, không che mất màn hình điện thoại ở giữa!"
        )
        asr_desc.setWordWrap(True)
        asr_desc.setStyleSheet("color: #9fb3ca; font-size: 11px; line-height: 1.4;")
        asr_layout.addWidget(asr_desc)

        self.chk_enable_asr_supplement = QCheckBox("🎯 Bật quét bổ sung âm thanh (SenseVoice AI) cho phân cảnh đọc tin nhắn / thoại")
        self.chk_enable_asr_supplement.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        asr_layout.addWidget(self.chk_enable_asr_supplement)

        self.asr_supplement_container = QWidget()
        asr_c_layout = QVBoxLayout(self.asr_supplement_container)
        asr_c_layout.setContentsMargins(18, 0, 0, 4)
        asr_c_layout.setSpacing(8)

        # Hàng 1: Chế độ quét (Khoảng thời gian vs Toàn bộ video)
        asr_mode_row = QHBoxLayout()
        asr_mode_row.setSpacing(20)
        self.radio_asr_range = QRadioButton("Quét theo khoảng thời gian chỉ định (Khuyên dùng)")
        self.radio_asr_range.setChecked(True)
        self.radio_asr_all = QRadioButton("Quét toàn bộ video")

        radio_asr_style = """
            QRadioButton {
                color: #cbd5e1;
                font-size: 11px;
                font-weight: 600;
                spacing: 8px;
            }
            QRadioButton:hover {
                color: #ffffff;
            }
            QRadioButton:checked {
                color: #ffedd5;
                font-weight: bold;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border-radius: 9px;
                border: 2px solid #64748b;
                background-color: #0b131e;
            }
            QRadioButton::indicator:hover {
                border-color: #ff9800;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #ff7700;
                background-color: #ff7700;
                border-radius: 9px;
            }
        """
        self.radio_asr_range.setStyleSheet(radio_asr_style)
        self.radio_asr_all.setStyleSheet(radio_asr_style)
        asr_mode_row.addWidget(self.radio_asr_range)
        asr_mode_row.addWidget(self.radio_asr_all)
        asr_mode_row.addStretch()
        asr_c_layout.addLayout(asr_mode_row)

        # Hàng 2: Chọn danh sách các khoảng thời gian Từ -> Đến
        self.asr_time_widget = QWidget()
        asr_time_layout = QVBoxLayout(self.asr_time_widget)
        asr_time_layout.setContentsMargins(0, 2, 0, 2)
        asr_time_layout.setSpacing(6)

        # Danh sách widget chứa các dòng khoảng thời gian
        self._asr_range_rows: list[AsrRangeRowWidget] = []
        self.asr_ranges_container = QWidget()
        self.asr_ranges_layout = QVBoxLayout(self.asr_ranges_container)
        self.asr_ranges_layout.setContentsMargins(0, 0, 0, 0)
        self.asr_ranges_layout.setSpacing(6)
        asr_time_layout.addWidget(self.asr_ranges_container)

        # Nút thêm khoảng thời gian
        btn_add_row = QHBoxLayout()
        self.btn_add_asr_range = QPushButton("➕ Thêm khoảng thời gian ASR")
        self.btn_add_asr_range.setToolTip("Thêm một khoảng thời gian khác (ví dụ: đoạn 40s-60s hoặc 1p20-1p30) để quét bổ sung âm thanh")
        self.btn_add_asr_range.setStyleSheet("""
            QPushButton {
                background-color: #0c2338; color: #38bdf8;
                border: 1px dashed #0284c7; border-radius: 6px; padding: 5px 14px; font-weight: 700; font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0369a1; color: #ffffff; border-style: solid;
            }
        """)
        self.btn_add_asr_range.clicked.connect(lambda: self._add_asr_range_row())
        btn_add_row.addWidget(self.btn_add_asr_range)
        btn_add_row.addStretch()
        asr_time_layout.addLayout(btn_add_row)

        asr_c_layout.addWidget(self.asr_time_widget)

        self.radio_asr_range.toggled.connect(self._on_asr_mode_changed)
        self.radio_asr_all.toggled.connect(self._on_asr_mode_changed)

        # Hàng 3: Vị trí đặt phụ đề dịch & khung che chữ
        asr_place_row = QHBoxLayout()
        asr_place_row.setSpacing(10)
        lbl_place = QLabel("Vị trí hiển thị phụ đề dịch & hộp nền che:")
        lbl_place.setStyleSheet("color: #d7e3f4; font-size: 11px; font-weight: 600;")
        asr_place_row.addWidget(lbl_place)

        self.combo_asr_placement = QComboBox()
        self.combo_asr_placement.addItem("📌 Khung 1 (Đáy) - Hiển thị gọn ở đáy màn hình (Khuyên dùng, không che màn hình điện thoại)", "bottom")
        self.combo_asr_placement.addItem("📌 Khung 2 (Đỉnh) - Hiển thị ở đỉnh đầu", "top")
        self.combo_asr_placement.setStyleSheet("background-color: #1a2738; color: #38bdf8; font-weight: bold;")
        asr_place_row.addWidget(self.combo_asr_placement, 1)
        asr_c_layout.addLayout(asr_place_row)

        asr_layout.addWidget(self.asr_supplement_container)
        self.chk_enable_asr_supplement.toggled.connect(self.asr_supplement_container.setEnabled)
        self.asr_supplement_container.setEnabled(self.chk_enable_asr_supplement.isChecked())

        layout.addWidget(asr_box)

        # Nạp cài đặt đã lưu trước đó
        self._load_branding_settings()

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll, 1)

        # --- NÚT HÀNH ĐỘNG ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Hủy")
        self.cancel_btn.setMinimumHeight(38)
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        count = len(self.video_paths)
        save_txt = f"💾 Lưu giọng nói ({count} video)" if count > 1 else "💾 Lưu giọng nói"
        self.save_voice_btn = QPushButton(save_txt)
        self.save_voice_btn.setObjectName("saveVoiceBtn")
        self.save_voice_btn.setMinimumHeight(38)
        self.save_voice_btn.setMinimumWidth(150)
        self.save_voice_btn.setToolTip("Lưu lại cài đặt giọng đọc cho video này mà không xuất video ngay")
        self.save_voice_btn.setStyleSheet(
            "QPushButton { background: #0f766e; color: #ffffff; font-weight: 700; font-size: 13px; "
            "border: 1px solid #14b8a6; border-radius: 8px; padding: 6px 18px; } "
            "QPushButton:hover { background: #14b8a6; color: #ffffff; border-color: #2dd4bf; }"
        )
        def _on_save_voice_clicked():
            self.save_only = True
            self.start_now = False
            self._save_branding_settings()
            self.accept()
        self.save_voice_btn.clicked.connect(_on_save_voice_clicked)
        btn_layout.addWidget(self.save_voice_btn)

        btn_txt = f"🚀 Bắt đầu tạo video ({count} video)" if count > 1 else "🚀 Bắt đầu tạo video"
        self.start_btn = QPushButton(btn_txt)
        self.start_btn.setObjectName("primaryActionBtn")
        self.start_btn.setMinimumHeight(38)
        self.start_btn.setMinimumWidth(180)
        self.start_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6a00, stop:1 #e04800); "
            "color: #ffffff; font-weight: 800; font-size: 13px; border: 1px solid #ff8833; border-radius: 8px; padding: 6px 20px; } "
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff7d1a, stop:1 #f05400); border-color: #ffa352; }"
        )
        def _on_start_btn_clicked():
            self.save_only = False
            self.start_now = True
            self._save_branding_settings()
            self.accept()
        self.start_btn.clicked.connect(_on_start_btn_clicked)
        btn_layout.addWidget(self.start_btn)

        root_layout.addLayout(btn_layout)

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background-color: transparent; }
            QGroupBox {
                color: #f8fbff; font-weight: 700; font-size: 13px;
                border: 1px solid #253347; border-radius: 10px; margin-top: 10px; padding-top: 12px;
                background-color: #131c2a;
            }
            QGroupBox::title {
                subcontrol-origin: margin; subcontrol-position: top left; left: 14px;
                padding: 0 4px; color: #ff7b1a; font-weight: bold;
            }
            QComboBox {
                background-color: #1a2738; color: #f8fbff; border: 1px solid #344862;
                border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 12px;
            }
            QComboBox:hover { border-color: #ff7b1a; }
            QComboBox QAbstractItemView {
                background-color: #131c2a; color: #f8fbff; selection-background-color: #ff6a00;
                selection-color: #ffffff; border: 1px solid #344862;
            }
            QSlider::groove:horizontal {
                height: 6px; background: #223246; border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #ff6a00; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #f8fbff; border: 2px solid #ff7b1a; width: 14px; margin-top: -5px;
                margin-bottom: -5px; border-radius: 7px;
            }
            QCheckBox {
                color: #d7e3f4; spacing: 8px; font-size: 12px;
            }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px; border: 1px solid #344862; background: #1a2738;
            }
            QCheckBox::indicator:checked {
                background-color: #ff6a00; border-color: #ff8833;
            }
            QPushButton {
                background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
                border-radius: 8px; padding: 6px 14px; font-weight: 600;
            }
            QPushButton:hover { background-color: #29405d; }
        """)

    def is_no_voiceover(self) -> bool:
        """Kiểm tra xem người dùng có chọn chế độ Chỉ tạo phụ đề (Không lồng tiếng) hay không."""
        if hasattr(self, "chk_no_voiceover") and self.chk_no_voiceover.isChecked():
            return True
        data = self.combo_voice.currentData()
        if isinstance(data, dict) and data.get("id") in ("no_voiceover", "none"):
            return True
        return False

    def get_selected_voice(self) -> dict[str, str]:
        """Trả về dict chứa thông tin giọng đã chọn {id, name, provider}."""
        if self.is_no_voiceover():
            return {"id": "no_voiceover", "name": "🚫 Không lồng tiếng (Chỉ Sub)", "provider": "none"}
        data = self.combo_voice.currentData()
        if isinstance(data, dict):
            return data
        return {"id": "adam1", "name": "Adam", "provider": "piper"}

    def get_audio_mix_settings(self) -> dict[str, float]:
        """Trả về cài đặt hòa âm tỷ lệ float [0.0, 1.0]."""
        orig_vol = float(self.slider_orig.value()) / 100.0
        if self.is_no_voiceover():
            return {
                "original_volume": max(0.1, orig_vol),
                "tts_volume": 0.0,
                "duck_volume": 0.0,
            }
        tts_vol = float(self.slider_tts.value()) / 100.0
        return {
            "original_volume": orig_vol,
            "tts_volume": tts_vol,
            "duck_volume": min(orig_vol, max(0.05, orig_vol * 0.40)) if orig_vol > 0.001 else 0.0,
        }

    def get_mask_mode(self) -> str:
        """Trả về kiểu mặt nạ che: 'blur' (Ảnh 1), 'box' (Ảnh 2), 'none'."""
        if not self.chk_blur_mask.isChecked():
            return "none"
        mode = self.combo_mask_mode.currentData()
        return str(mode or "blur")

    def is_blur_mask_enabled(self) -> bool:
        return self.chk_blur_mask.isChecked() and self.get_mask_mode() != "none"

    def is_burn_subtitles_enabled(self) -> bool:
        return self.chk_burn_subtitles.isChecked()

    def get_blur_rect(self) -> tuple[float, float, float, float] | None:
        """Trả về tọa độ vùng che chữ Trung (khớp khung OCR đã quét)."""
        if not self.chk_blur_mask.isChecked():
            return None
        return getattr(self, "blur_rect", None)

    def get_subtitle_config(self) -> SubtitleRenderConfig:
        """Trả về SubtitleRenderConfig được người dùng chọn."""
        cfg = self.sub_style_group.get_subtitle_render_config()
        cfg.mask_mode = self.get_mask_mode()
        cfg.blur_rect = self.get_blur_rect()
        cfg.dynamic_mask = True
        return cfg

    def get_voice_config(self) -> dict[str, Any]:
        """Trả về cấu hình giọng đọc chuẩn hóa."""
        v = self.get_selected_voice()
        mix = self.get_audio_mix_settings()
        return {
            "mode": "single",
            "voice": v,
            "voice_id": v.get("id", ""),
            "voice_name": v.get("name", ""),
            "provider": v.get("provider", "piper"),
            "orig_volume": int(mix.get("original_volume", 0.5) * 100),
            "tts_volume": int(mix.get("tts_volume", 1.0) * 100),
            "duck_volume": mix.get("duck_volume", 0.2),
            "blur_mask": self.is_blur_mask_enabled(),
            "mask_mode": self.get_mask_mode(),
            "burn_subtitles": self.is_burn_subtitles_enabled(),
            "start_now": getattr(self, "start_now", False),
        }

    def _refresh_deepseek_status(self):
        provider = str(os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER") or "deepseek").strip().lower()
        deepseek_key = str(os.getenv("DEEPSEEK_API_KEY", "")).strip()
        if provider == "google":
            self.lbl_ds.setText("🟢 Dịch thuật: Google Dịch (Miễn phí, không cần API Key)")
            self.lbl_ds.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: 600;")
        elif deepseek_key:
            masked = deepseek_key[:7] + "..." + deepseek_key[-4:] if len(deepseek_key) > 12 else "Đã thiết lập"
            self.lbl_ds.setText(f"🟢 DeepSeek AI: Đã kết nối API ({masked})")
            self.lbl_ds.setStyleSheet("color: #3ddc97; font-size: 11px; font-weight: 600;")
        else:
            self.lbl_ds.setText("🟡 Chưa có DeepSeek API Key (Bấm nút bên cạnh để nhập)")
            self.lbl_ds.setStyleSheet("color: #f0b347; font-size: 11px; font-weight: 600;")

    def _open_ai_settings(self):
        from ui.dialogs.ai_settings_dialog import AiSettingsDialog
        dlg = AiSettingsDialog(self, workspace_root=self.workspace_root)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_deepseek_status()

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
        """Khôi phục các cài đặt Logo, Khung Viền, Watermark và Xóa Logo gốc đã lưu từ trước."""
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

        # Tự động nhận diện khung Đặt Logo nếu đã được người dùng chỉnh trên Preview
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

        # Tự động nhận diện khung Đặt Watermark nếu đã được người dùng chỉnh trên Preview
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

        # Delogo & Watermark removal settings (mặc định False để không chèn dải mờ bừa bãi)
        bili_def = settings.value("delogo/enable_bilibili", False, type=bool)
        wm_delogo_def = settings.value("delogo/enable_watermark", False, type=bool)
        wm_delogo_mode = settings.value("delogo/watermark_mode", "delogo", type=str)
        custom_def = settings.value("delogo/enable_custom", False, type=bool)
        custom_mode = settings.value("delogo/custom_mode", "boxblur", type=str)
        custom_rect_str = settings.value("delogo/custom_rect", "", type=str)

        self.chk_delogo_bilibili.setChecked(bili_def)
        self.chk_delogo_watermark.setChecked(wm_delogo_def)
        self.chk_delogo_custom.setChecked(custom_def)
        self.custom_delogo_container.setEnabled(custom_def)

        wm_mode_idx = self.combo_wm_delogo_mode.findData(wm_delogo_mode)
        if wm_mode_idx >= 0:
            self.combo_wm_delogo_mode.setCurrentIndex(wm_mode_idx)

        mode_idx = self.combo_custom_delogo_mode.findData(custom_mode)
        if mode_idx >= 0:
            self.combo_custom_delogo_mode.setCurrentIndex(mode_idx)

        p = self.parent()
        delogo_loaded = False
        if p and hasattr(p, "delogo_region_overlay"):
            d_ov = getattr(p, "delogo_region_overlay", None)
            if d_ov and (getattr(d_ov, "_user_customized", False) or getattr(d_ov, "_requested_visible", False)):
                dr = d_ov.get_norm_rect()
                if dr and len(dr) == 4 and dr[2] > 0.01 and dr[3] > 0.01:
                    self.chk_delogo_custom.setChecked(True)
                    self.custom_delogo_container.setEnabled(True)
                    self.spin_delogo_x.setValue(round(dr[0] * 100, 1))
                    self.spin_delogo_y.setValue(round(dr[1] * 100, 1))
                    self.spin_delogo_w.setValue(round(dr[2] * 100, 1))
                    self.spin_delogo_h.setValue(round(dr[3] * 100, 1))
                    delogo_loaded = True

        if not delogo_loaded and custom_rect_str:
            try:
                parts = [float(p) for p in custom_rect_str.split(",")]
                if len(parts) == 4:
                    self.spin_delogo_x.setValue(round(parts[0] * 100, 1))
                    self.spin_delogo_y.setValue(round(parts[1] * 100, 1))
                    self.spin_delogo_w.setValue(round(parts[2] * 100, 1))
                    self.spin_delogo_h.setValue(round(parts[3] * 100, 1))
            except Exception:
                pass
        elif not delogo_loaded and self.blur_rect and len(self.blur_rect) == 4:
            self.spin_delogo_x.setValue(round(self.blur_rect[0] * 100, 1))
            self.spin_delogo_y.setValue(round(self.blur_rect[1] * 100, 1))
            self.spin_delogo_w.setValue(round(self.blur_rect[2] * 100, 1))
            self.spin_delogo_h.setValue(round(self.blur_rect[3] * 100, 1))
        self._update_custom_delogo_label()

        # Watermark delogo: Tự động nhận diện khung Xóa Watermark từ Preview
        wm_loaded = False
        if p and hasattr(p, "watermark_region_overlay"):
            wm_ov = getattr(p, "watermark_region_overlay", None)
            if wm_ov and (getattr(wm_ov, "_user_customized", False) or getattr(wm_ov, "_requested_visible", False)):
                wm_r = wm_ov.get_norm_rect()
                if wm_r and len(wm_r) == 4 and wm_r[2] > 0.01 and wm_r[3] > 0.01:
                    self.chk_delogo_watermark.setChecked(True)
                    self.watermark_delogo_container.setEnabled(True)
                    self._watermark_delogo_rect = wm_r
                    self._update_watermark_delogo_label()
                    wm_loaded = True

        if not wm_loaded:
            wm_rect_str = settings.value("delogo/watermark_rect", "", type=str)
            if wm_rect_str:
                try:
                    parts = [float(x) for x in wm_rect_str.split(",")]
                    if len(parts) == 4:
                        self._watermark_delogo_rect = (parts[0], parts[1], parts[2], parts[3])
                        self._update_watermark_delogo_label()
                except Exception:
                    pass

        # ASR Supplement settings
        enable_asr = settings.value("asr_supplement/enabled", False, type=bool)
        asr_mode = settings.value("asr_supplement/mode", "range", type=str)
        asr_placement = settings.value("asr_supplement/placement", "bottom", type=str)
        ranges_str = settings.value("asr_supplement/ranges_str", "", type=str)

        self.chk_enable_asr_supplement.setChecked(enable_asr)
        self.asr_supplement_container.setEnabled(enable_asr)
        if asr_mode == "all":
            self.radio_asr_all.setChecked(True)
        else:
            self.radio_asr_range.setChecked(True)

        idx_p = self.combo_asr_placement.findData(asr_placement)
        if idx_p >= 0:
            self.combo_asr_placement.setCurrentIndex(idx_p)

        # Xóa các hàng cũ và tạo lại danh sách khoảng thời gian
        for r in list(getattr(self, "_asr_range_rows", [])):
            self.asr_ranges_layout.removeWidget(r)
            r.deleteLater()
        self._asr_range_rows = []

        parsed_ranges: list[tuple[float, float]] = []
        if ranges_str:
            for part in ranges_str.split(";"):
                try:
                    ps = [float(x.strip()) for x in part.split(",")]
                    if len(ps) == 2 and ps[1] > ps[0]:
                        parsed_ranges.append((ps[0], ps[1]))
                except Exception:
                    pass

        if not parsed_ranges:
            s_old = settings.value("asr_supplement/start", 15.0, type=float)
            e_old = settings.value("asr_supplement/end", 35.0, type=float)
            parsed_ranges = [(s_old, e_old)]

        for s, e in parsed_ranges:
            self._add_asr_range_row(s, e)

        self._on_asr_mode_changed()

    def _save_branding_settings(self):
        """Lưu lại cài đặt Logo, Watermark và Delogo để tiện dùng cho những lần sau."""
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

        # Delogo
        settings.setValue("delogo/enable_bilibili", self.chk_delogo_bilibili.isChecked())
        settings.setValue("delogo/enable_watermark", self.chk_delogo_watermark.isChecked())
        settings.setValue("delogo/watermark_mode", self.combo_wm_delogo_mode.currentData() or "delogo")
        wm_r = self._get_watermark_delogo_rect()
        if wm_r:
            settings.setValue("delogo/watermark_rect", f"{wm_r[0]:.4f},{wm_r[1]:.4f},{wm_r[2]:.4f},{wm_r[3]:.4f}")
        else:
            settings.setValue("delogo/watermark_rect", "")
        settings.setValue("delogo/enable_custom", self.chk_delogo_custom.isChecked())
        settings.setValue("delogo/custom_mode", self.combo_custom_delogo_mode.currentData() or "boxblur")
        cr = self._get_custom_delogo_rect()
        if cr:
            settings.setValue("delogo/custom_rect", f"{cr[0]:.4f},{cr[1]:.4f},{cr[2]:.4f},{cr[3]:.4f}")
        else:
            settings.setValue("delogo/custom_rect", "")

        # ASR Supplement
        settings.setValue("asr_supplement/enabled", self.chk_enable_asr_supplement.isChecked())
        settings.setValue("asr_supplement/mode", "range" if self.radio_asr_range.isChecked() else "all")
        cfg_ranges = []
        for row in getattr(self, "_asr_range_rows", []):
            s = float(row.spin_start.value())
            e = float(row.spin_end.value())
            if e > s:
                cfg_ranges.append(f"{s:.2f},{e:.2f}")
        settings.setValue("asr_supplement/ranges_str", ";".join(cfg_ranges))
        settings.setValue("asr_supplement/placement", self.combo_asr_placement.currentData() or "bottom")

    def _activate_brand_cover_mode(self):
        """Bật nhanh Logo thương hiệu ở mục 5 và đặt vị trí góc trên cùng bên phải để đè lên logo Bilibili."""
        self.chk_enable_logo.setChecked(True)
        idx = self.combo_logo_pos.findData("top_right")
        if idx >= 0:
            self.combo_logo_pos.setCurrentIndex(idx)
        if not self.txt_logo_path.text().strip():
            self._browse_logo_file()

    def _update_watermark_delogo_label(self):
        if hasattr(self, "_watermark_delogo_rect") and self._watermark_delogo_rect:
            x, y, w, h = self._watermark_delogo_rect
            self.lbl_watermark_delogo_info.setText(f"📐 Tọa độ: x={x*100:.1f}%, y={y*100:.1f}%, w={w*100:.1f}%, h={h*100:.1f}%")

    def _grab_watermark_preview_rect(self):
        """Lấy tọa độ vùng chọn từ khung Xóa Watermark trên Preview."""
        p = self.parent()
        if p and hasattr(p, "watermark_region_overlay"):
            wm_ov = getattr(p, "watermark_region_overlay", None)
            if wm_ov and hasattr(wm_ov, "get_norm_rect"):
                rect = wm_ov.get_norm_rect()
                if rect and len(rect) == 4 and rect[2] > 0.01:
                    self._watermark_delogo_rect = rect
                    self._update_watermark_delogo_label()
                    self.chk_delogo_watermark.setChecked(True)
                    self.watermark_delogo_container.setEnabled(True)

    def _get_watermark_delogo_rect(self) -> tuple[float, float, float, float] | None:
        if not self.chk_delogo_watermark.isChecked():
            return None
        return getattr(self, "_watermark_delogo_rect", (0.35, 0.49, 0.30, 0.045))

    def _update_custom_delogo_label(self):
        x = self.spin_delogo_x.value()
        y = self.spin_delogo_y.value()
        w = self.spin_delogo_w.value()
        h = self.spin_delogo_h.value()
        self.lbl_custom_delogo_info.setText(f"📐 Tọa độ vùng xóa: x={x:.1f}%, y={y:.1f}%, w={w:.1f}%, h={h:.1f}%")

    def _grab_preview_rect(self):
        """Lấy tọa độ vùng chọn từ màn hình Preview."""
        rect = None
        p = self.parent()
        # Ưu tiên số 1: Khung Xóa Logo chuyên dụng (DelogoRegionOverlay)
        if p and hasattr(p, "delogo_region_overlay"):
            delogo_ov = getattr(p, "delogo_region_overlay", None)
            if delogo_ov and hasattr(delogo_ov, "get_norm_rect"):
                rect = delogo_ov.get_norm_rect()
        # Ưu tiên số 2: Khung OCR
        if not rect and p and hasattr(p, "ocr_region_overlay"):
            overlay = getattr(p, "ocr_region_overlay", None)
            if overlay and hasattr(overlay, "get_norm_rect"):
                rect = overlay.get_norm_rect()
        if not rect and self.blur_rect:
            rect = self.blur_rect
        if not rect:
            env_rect = os.getenv("OCR_SUBTITLE_RECT", "").strip()
            if env_rect:
                try:
                    parts = [float(x) for x in env_rect.split(",")]
                    if len(parts) == 4:
                        rect = tuple(parts)
                except Exception:
                    pass

        if rect and len(rect) == 4:
            rx, ry, rw, rh = rect
            self.spin_delogo_x.setValue(round(rx * 100, 1))
            self.spin_delogo_y.setValue(round(ry * 100, 1))
            self.spin_delogo_w.setValue(round(rw * 100, 1))
            self.spin_delogo_h.setValue(round(rh * 100, 1))
            self._update_custom_delogo_label()

    def _get_custom_delogo_rect(self) -> tuple[float, float, float, float] | None:
        if not self.chk_delogo_custom.isChecked():
            return None
        x = round(self.spin_delogo_x.value() / 100.0, 4)
        y = round(self.spin_delogo_y.value() / 100.0, 4)
        w = round(self.spin_delogo_w.value() / 100.0, 4)
        h = round(self.spin_delogo_h.value() / 100.0, 4)
        if w > 0.01 and h > 0.01:
            return (x, y, w, h)
        return None

    def get_delogo_settings(self) -> dict[str, Any]:
        """Trả về cấu hình Xóa Logo & Watermark video gốc."""
        return {
            "delogo_bilibili": self.chk_delogo_bilibili.isChecked(),
            "delogo_watermark": self.chk_delogo_watermark.isChecked(),
            "delogo_watermark_rect": self._get_watermark_delogo_rect(),
            "delogo_watermark_mode": str(self.combo_wm_delogo_mode.currentData() or "delogo"),
            "delogo_custom": self.chk_delogo_custom.isChecked(),
            "delogo_custom_rect": self._get_custom_delogo_rect(),
            "delogo_custom_mode": str(self.combo_custom_delogo_mode.currentData() or "boxblur"),
        }

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

    def _add_asr_range_row(self, start_val: float = 15.0, end_val: float = 35.0):
        """Thêm một hàng khoảng thời gian ASR mới vào danh sách."""
        idx = len(self._asr_range_rows) + 1
        row = AsrRangeRowWidget(self, idx, start_val=start_val, end_val=end_val, on_delete=self._remove_asr_range_row)
        self._asr_range_rows.append(row)
        self.asr_ranges_layout.addWidget(row)
        self._update_asr_del_buttons()

    def _remove_asr_range_row(self, row: AsrRangeRowWidget):
        """Xóa một hàng khoảng thời gian ASR."""
        if len(self._asr_range_rows) <= 1:
            return
        if row in self._asr_range_rows:
            self._asr_range_rows.remove(row)
            self.asr_ranges_layout.removeWidget(row)
            row.deleteLater()
            for idx, r in enumerate(self._asr_range_rows, 1):
                r.set_index(idx)
            self._update_asr_del_buttons()

    def _update_asr_del_buttons(self):
        """Ẩn/hiện nút xóa tùy thuộc số lượng hàng (nếu chỉ có 1 hàng thì không cho xóa)."""
        can_del = len(self._asr_range_rows) > 1
        for r in self._asr_range_rows:
            r.btn_del.setEnabled(can_del)
            r.btn_del.setVisible(can_del)

    def get_video_duration_seconds(self) -> float:
        """Lấy độ dài tổng thể của video (tính bằng giây) từ player, ffprobe hoặc opencv."""
        p = self.parent()
        if p and hasattr(p, "media_player"):
            try:
                dur_ms = int(p.media_player.duration() or 0)
                if dur_ms > 500:
                    return round(dur_ms / 1000.0, 2)
            except Exception:
                pass

        if self.video_path and os.path.isfile(self.video_path):
            try:
                import subprocess
                from runtime_paths import bin_path, subprocess_hidden_kwargs
                ffprobe_bin = os.path.join(bin_path(), "ffmpeg", "ffprobe.exe")
                if not os.path.isfile(ffprobe_bin):
                    import shutil
                    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
                cmd = [
                    ffprobe_bin, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    self.video_path,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, **subprocess_hidden_kwargs())
                if res.returncode == 0:
                    dur_val = float(res.stdout.strip() or 0.0)
                    if dur_val > 0.1:
                        return round(dur_val, 2)
            except Exception:
                pass

            try:
                import cv2
                cap = cv2.VideoCapture(self.video_path)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    cap.release()
                    if frames > 0 and fps > 0:
                        return round(frames / fps, 2)
            except Exception:
                pass

        return 0.0

    def _on_asr_mode_changed(self):
        """Xử lý đồng bộ khi chuyển đổi giữa Quét theo khoảng thời gian và Quét toàn bộ video."""
        is_all = self.radio_asr_all.isChecked()
        if is_all:
            dur_s = self.get_video_duration_seconds()
            if not self._asr_range_rows:
                self._add_asr_range_row(0.0, dur_s if dur_s > 0 else 180.0)

            # Tự động bắt đến giây cuối cùng của video và điền trực tiếp vào Đoạn 1
            first_row = self._asr_range_rows[0]
            first_row.spin_start.setValue(0.0)
            if dur_s > 0:
                first_row.spin_end.setRange(0.0, max(7200.0, dur_s + 60.0))
                first_row.spin_end.setValue(dur_s)

            # Khóa nhẹ và ẩn các thao tác thêm/xóa để người dùng nhìn thấy rõ ràng toàn bộ video
            first_row.spin_start.setReadOnly(True)
            first_row.spin_end.setReadOnly(True)
            first_row.btn_grab_start.setEnabled(False)
            first_row.btn_grab_end.setEnabled(False)
            first_row.btn_del.setVisible(False)
            self.btn_add_asr_range.setVisible(False)

            # Ẩn các đoạn bổ sung 2, 3... nếu có
            for r in self._asr_range_rows[1:]:
                r.setVisible(False)
        else:
            self.btn_add_asr_range.setVisible(True)
            if self._asr_range_rows:
                first_row = self._asr_range_rows[0]
                first_row.spin_start.setReadOnly(False)
                first_row.spin_end.setReadOnly(False)
                first_row.btn_grab_start.setEnabled(True)
                first_row.btn_grab_end.setEnabled(True)
            for r in self._asr_range_rows:
                r.setVisible(True)
            self._update_asr_del_buttons()

    def get_asr_supplement_settings(self) -> dict[str, Any]:
        """Trả về cài đặt quét bổ sung âm thanh ASR với danh sách nhiều khoảng thời gian."""
        is_enabled = self.chk_enable_asr_supplement.isChecked()
        is_range = self.radio_asr_range.isChecked()
        ranges: list[tuple[float, float]] = []
        if is_range:
            for row in getattr(self, "_asr_range_rows", []):
                s = float(row.spin_start.value())
                e = float(row.spin_end.value())
                if e > s:
                    ranges.append((round(s, 2), round(e, 2)))
        else:
            # Chế độ toàn bộ video: tự động lấy từ Đoạn 1 đã được tính sẵn giây cuối
            if getattr(self, "_asr_range_rows", []):
                first_row = self._asr_range_rows[0]
                s = float(first_row.spin_start.value())
                e = float(first_row.spin_end.value())
                if e > s:
                    ranges.append((round(s, 2), round(e, 2)))
            if not ranges:
                dur_s = self.get_video_duration_seconds()
                ranges.append((0.0, round(dur_s, 2) if dur_s > 0 else 0.0))

        placement = str(self.combo_asr_placement.currentData() or "bottom")
        return {
            "enabled": is_enabled,
            "mode": "range" if is_range else "all",
            "ranges": ranges,
            "start_seconds": ranges[0][0] if ranges else 0.0,
            "end_seconds": ranges[0][1] if ranges else 0.0,
            "placement": placement,
        }

    def accept(self):
        """Lưu lại cài đặt thương hiệu trước khi chấp nhận."""
        self._save_branding_settings()
        super().accept()
