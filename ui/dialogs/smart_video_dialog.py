"""Smart Video Dialog (Redesigned).

Giao diện Tab chuyên nghiệp, thẩm mỹ cao cấp chuẩn TankVideo Studio:
- Tab 1: 🎯 Bóc tách & Mặt nạ thông minh (SenseVoice siêu tốc, Dynamic Mask, Lọc Watermark).
- Tab 2: 🎙️ Phân vai Đa Giọng & Hòa âm (Tự động nhận diện Nam/Nữ, lồng tiếng đa nhân vật).
- Tab 3: 🏷️ Logo Thương hiệu & Watermark (Tự động đồng bộ cài đặt Logo từ hệ thống).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.subtitle_segment import SubtitleSegment
from app.services.smart_audio_transcriber import DEFAULT_BLACKLIST, SmartAudioTranscriber
from app.services.video_creator_service import (
    VideoCreatorConfig,
    VideoCreatorRequest,
    VideoCreatorService,
)
from app.services.video_export_service import SubtitleRenderConfig
from app.services.voice_mapping_service import VoiceAssignment, VoiceMappingConfig

logger = logging.getLogger(__name__)


def _load_available_voices() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Nạp danh sách giọng đọc Piper / VieNeu hiện có trên máy."""
    females: list[dict[str, str]] = [
        {"id": "vieneu_cogaihoatbat", "name": "Cô gái hoạt ngôn (VieNeu AI)", "provider": "vieneu"},
        {"id": "vieneu_cogai_moi", "name": "Cô Gái Mới (VieNeu AI)", "provider": "vieneu"},
        {"id": "ngochuyennew", "name": "Ngọc Huyền New (Piper)", "provider": "piper"},
    ]
    males: list[dict[str, str]] = [
        {"id": "vieneu_thanhnientutin", "name": "Thanh niên tự tin (VieNeu AI)", "provider": "vieneu"},
        {"id": "yannew", "name": "Yan New (Piper)", "provider": "piper"},
    ]
    seen_ids = {v["id"] for v in females + males}

    from app.runtime_paths import load_piper_voice_manifest
    manifest_items = load_piper_voice_manifest("piper")
    if manifest_items and isinstance(manifest_items, list):
        for item in manifest_items:
            raw_name = str(item.get("name", "") or "").strip()
            if not raw_name:
                continue
            audio_path = str(item.get("audio_path", "") or "").replace("\\", "/").strip()
            v_id = os.path.splitext(os.path.basename(audio_path))[0]
            if not v_id or v_id in seen_ids or "capcut" in v_id.lower():
                continue
            gender = str(item.get("gender", "male")).lower()
            entry = {"id": v_id, "name": raw_name, "provider": "piper"}
            if "female" in gender or "nữ" in gender or "nu" in gender:
                females.append(entry)
            else:
                males.append(entry)
            seen_ids.add(v_id)

    return females, males


class SmartVideoWorker(QThread):
    """Worker chạy toàn bộ quy trình: Quét âm thanh -> Lọc watermark -> Phân vai Nam/Nữ -> Dịch -> TTS -> Xuất MP4 Dynamic Mask & Logo."""

    progress_signal = Signal(float, str)
    log_signal = Signal(str)
    finished_signal = Signal(bool, str, str)  # success, output_mp4, error_message

    def __init__(
        self,
        video_path: str,
        output_mp4: str,
        source_mode: str,          # "audio_sensevoice", "audio_whisper", "master_data"
        mask_solution: str,        # "dynamic_blur", "subtitle_badge", "none"
        blacklist_keywords: list[str],
        voice_mode: str,           # "multi_gender", "single"
        voice_female_id: str,
        voice_male_id: str,
        voice_single_id: str,
        orig_volume: float,
        tts_volume: float,
        brand_logo_settings: dict[str, Any],
        watermark_settings: dict[str, Any],
        existing_segments: list[SubtitleSegment] | None = None,
        workspace_root: str = "",
    ):
        super().__init__()
        self.video_path = video_path
        self.output_mp4 = output_mp4
        self.source_mode = source_mode
        self.mask_solution = mask_solution
        self.blacklist_keywords = blacklist_keywords
        self.voice_mode = voice_mode
        self.voice_female_id = voice_female_id
        self.voice_male_id = voice_male_id
        self.voice_single_id = voice_single_id
        self.orig_volume = orig_volume
        self.tts_volume = tts_volume
        self.brand_logo_settings = brand_logo_settings
        self.watermark_settings = watermark_settings
        self.existing_segments = existing_segments or []
        self.workspace_root = workspace_root or os.getcwd()
        self._is_cancelled = False

    def request_cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self._execute_pipeline()
        except Exception as exc:
            logger.exception("SmartVideoWorker exception: %s", exc)
            self.finished_signal.emit(False, "", str(exc))

    def _execute_pipeline(self):
        transcriber = SmartAudioTranscriber(self.workspace_root)
        segments: list[SubtitleSegment] = []
        temp_wav = ""

        # -------------------------------------------------------------
        # BƯỚC 1: LẤY HOẶC BÓC TÁCH LỜI THOẠI (GIẢI PHÁP 1)
        # -------------------------------------------------------------
        if self.source_mode == "smart_ocr":
            self.log_signal.emit("[Bước 1/4] Quét chữ Thông Minh Đa Vùng (Smart OCR)...")
            self.progress_signal.emit(10.0, "Đang quét phụ đề trên màn hình (Đỉnh & Đáy)...")

            from app.ocr_processor import transcribe_video_ocr
            def _on_ocr_progress(cur, total, ocr_cnt, skip_cnt):
                if self._is_cancelled:
                    return
                if total > 0:
                    pct = 10.0 + (cur / total) * 30.0
                    p_int = cur * 100 // total
                    msg = f"Đang quét OCR: Frame {cur}/{total} ({p_int}%) - Đã tìm thấy {ocr_cnt} câu..."
                    self.progress_signal.emit(pct, msg)
                    self.log_signal.emit(f"[OCR] Frame {cur}/{total} ({p_int}%, OCR: {ocr_cnt}, skip: {skip_cnt})")

            raw_cues = transcribe_video_ocr(
                self.video_path,
                region="smart",
                progress_callback=_on_ocr_progress,
                is_cancelled_func=lambda: self._is_cancelled,
            )

            if self._is_cancelled:
                return

            if not raw_cues:
                raise RuntimeError("Không đọc được phụ đề tiếng Trung nào từ video bằng OCR.")

            self.log_signal.emit(f"  => Quét hoàn tất: Phát hiện {len(raw_cues)} đoạn văn bản.")

            cleaned_segs = []
            for cue in raw_cues:
                txt = str(cue.get("text", "")).strip()
                if not txt:
                    continue
                if self.blacklist_keywords:
                    is_blacklisted = any(kw.lower() in txt.lower() for kw in self.blacklist_keywords)
                    if is_blacklisted:
                        continue
                cleaned_segs.append({
                    "start": float(cue.get("start", 0.0)),
                    "end": float(cue.get("end", 0.0)),
                    "text": txt,
                    "speaker": "single",
                    "position": cue.get("position", "bottom"),
                    "bbox": cue.get("bbox", [0.1, 0.7, 0.9, 0.95]),
                })

            if not cleaned_segs:
                raise RuntimeError("Sau khi lọc bỏ watermark, không còn câu thoại nào.")

            self.log_signal.emit(f"  => Đã lọc sạch: Còn lại {len(cleaned_segs)} câu thoại chuẩn xác.")

            # Dịch sang Tiếng Việt
            self.log_signal.emit("[Bước 2/4] Dịch phụ đề sang Tiếng Việt qua AI...")
            self.progress_signal.emit(45.0, "Đang dịch câu thoại sang Tiếng Việt...")
            segments = transcriber.translate_segments(
                cleaned_segs,
                target_language="vi",
                progress_cb=lambda pct, msg: (self.progress_signal.emit(pct, msg), self.log_signal.emit(f"  -> {msg}")),
            )

        elif self.source_mode in ("audio_sensevoice", "audio_whisper"):
            self.log_signal.emit("[Bước 1/5] Trích xuất âm thanh từ video gốc...")
            self.progress_signal.emit(5.0, "Đang trích xuất luồng âm thanh...")
            
            temp_wav = os.path.join(
                os.path.dirname(self.output_mp4),
                f"smart_audio_temp_{int(time.time())}.wav",
            )
            success = transcriber.extract_audio(self.video_path, temp_wav)
            if not success or not os.path.isfile(temp_wav):
                raise RuntimeError("Không thể trích xuất âm thanh từ video gốc.")

            if self._is_cancelled:
                return

            engine = "sensevoice" if self.source_mode == "audio_sensevoice" else "whisper"
            self.log_signal.emit(f"[Bước 1/4] Bóc tách lời thoại bằng {engine.upper()} AI...")
            self.progress_signal.emit(15.0, f"Đang nhận diện giọng nói ({engine.upper()})...")

            def _on_transcribe_progress(pct: float, msg: str):
                self.progress_signal.emit(pct, msg)
                self.log_signal.emit(f"  -> {msg}")

            raw_segs = transcriber.transcribe_audio(
                temp_wav,
                engine=engine,
                source_language="zh",
                progress_cb=_on_transcribe_progress,
            )

            if not raw_segs:
                raise RuntimeError("Không nhận diện được câu thoại nào từ âm thanh video.")

            self.log_signal.emit(f"  => Nhận diện thành công {len(raw_segs)} câu thoại.")

            if self._is_cancelled:
                return

            # GIẢI PHÁP 3: Lọc bỏ từ khóa rác & Watermark
            self.log_signal.emit("[Bước 2/4] Bộ lọc Watermark & Từ khóa rác...")
            self.progress_signal.emit(30.0, "Đang lọc sạch watermark và từ rác...")
            cleaned_segs = transcriber.filter_blacklist_and_clean(raw_segs, self.blacklist_keywords)
            self.log_signal.emit(f"  => Đã làm sạch: Còn lại {len(cleaned_segs)} câu thoại chuẩn.")

            if not cleaned_segs:
                raise RuntimeError("Toàn bộ câu thoại đều là watermark hoặc rác.")

            for s in cleaned_segs:
                s["speaker"] = "single"

            # GIẢI PHÁP 2: Tự động phát hiện vị trí câu thoại (Trên đỉnh vs Dưới đáy)
            self.log_signal.emit("[Bước 3/4] Nhận diện vị trí hiển thị (Trên đỉnh vs Dưới đáy)...")
            positioned_segs = transcriber.detect_segment_positions(
                self.video_path,
                cleaned_segs,
                progress_cb=_on_transcribe_progress,
            )

            if self._is_cancelled:
                return

            # Dọn file wav tạm
            try:
                if temp_wav and os.path.isfile(temp_wav):
                    os.remove(temp_wav)
            except Exception:
                pass

            # Dịch sang tiếng Việt
            self.log_signal.emit("[Bước 4/4] Dịch phụ đề sang Tiếng Việt...")
            self.progress_signal.emit(45.0, "Đang dịch câu thoại sang Tiếng Việt...")
            segments = transcriber.translate_segments(
                positioned_segs,
                target_language="vi",
                progress_cb=_on_transcribe_progress,
            )

        elif self.existing_segments:
            self.log_signal.emit(f"[Bước 1/4] Sử dụng {len(self.existing_segments)} câu thoại có sẵn...")
            segments = list(self.existing_segments)
            for s in segments:
                s.gender = "male"
                s.final_gender = "male"
                setattr(s, "speaker", "single")
        else:
            raise RuntimeError("Chưa có dữ liệu câu thoại nào để xử lý.")

        if self._is_cancelled:
            return

        self.log_signal.emit(f"[Render] Bắt đầu render Video MP4 (Lồng tiếng AI + Dynamic Mask + Logo)...")
        self.progress_signal.emit(55.0, "Đang tạo giọng nói và dựng hiệu ứng...")

        # -------------------------------------------------------------
        # CẤU HÌNH CREATOR ENGINE & BRANDING
        # -------------------------------------------------------------
        creator_config = VideoCreatorConfig()
        creator_config.original_volume = self.orig_volume
        creator_config.tts_volume = self.tts_volume
        creator_config.duck_volume = min(0.20, self.orig_volume * 0.4)

        # Cấu hình Mặt nạ
        if self.mask_solution == "dynamic_blur":
            creator_config.dynamic_mask = True
            creator_config.enable_blur_mask = True
            creator_config.mask_mode = "blur"
            self.log_signal.emit("  -> Mặt nạ: Mờ ĐỘNG theo thời gian (Chỉ mờ khi có chữ).")
        elif self.mask_solution == "subtitle_badge":
            creator_config.dynamic_mask = True
            creator_config.enable_blur_mask = True
            creator_config.mask_mode = "box"
            sub_cfg = SubtitleRenderConfig()
            sub_cfg.mask_mode = "box"
            sub_cfg.background_box = True
            sub_cfg.badge_bg_color = "&H26000000&"  # Đen mờ 85% sang trọng
            sub_cfg.badge_text_color = "&H00FFFFFF&"
            sub_cfg.font_size = 32
            sub_cfg.bold = True
            creator_config.subtitle_config = sub_cfg
            self.log_signal.emit("  -> Mặt nạ: Hộp phụ đề Tiếng Việt bo góc (CapCut/TikTok Badge).")
        else:
            creator_config.dynamic_mask = False
            creator_config.enable_blur_mask = False
            creator_config.mask_mode = "none"
            self.log_signal.emit("  -> Mặt nạ: Không che.")

        # Lồng tiếng 1 giọng duy nhất (Không phân vai Nam / Nữ)
        eff_v = self.voice_single_id or self.voice_male_id or self.voice_female_id or "vieneu_thanhnientutin"
        for s in segments:
            s.gender = "male"
            s.final_gender = "male"
            s.voice_id = eff_v
            setattr(s, "speaker", "single")

        voice_cfg = VoiceMappingConfig(
            default_female_voice=VoiceAssignment(
                voice_id=eff_v,
                name="Giọng Đọc AI",
                gender="female",
            ),
            default_male_voice=VoiceAssignment(
                voice_id=eff_v,
                name="Giọng Đọc AI",
                gender="male",
            ),
        )
        creator_config.voice_config = voice_cfg
        self.log_signal.emit(f"  -> Lồng tiếng AI (1 giọng đồng nhất): {eff_v}")

        # Cấu hình Logo Thương Hiệu
        if self.brand_logo_settings and self.brand_logo_settings.get("enable"):
            creator_config.enable_brand_logo = True
            creator_config.brand_logo_path = str(self.brand_logo_settings.get("path", ""))
            creator_config.brand_logo_position = str(self.brand_logo_settings.get("position", "top_left"))
            creator_config.brand_logo_size_percent = int(self.brand_logo_settings.get("size_percent", 15))
            creator_config.brand_logo_opacity = float(self.brand_logo_settings.get("opacity", 1.0))
            creator_config.brand_logo_custom_rect = self.brand_logo_settings.get("custom_rect")
            self.log_signal.emit(f"  -> Đóng Logo: {os.path.basename(creator_config.brand_logo_path)} ({creator_config.brand_logo_position})")

        # Cấu hình Watermark Chữ
        if self.watermark_settings and self.watermark_settings.get("enable"):
            creator_config.enable_watermark = True
            creator_config.watermark_text = str(self.watermark_settings.get("text", ""))
            creator_config.watermark_position = str(self.watermark_settings.get("position", "center"))
            creator_config.watermark_font_size = int(self.watermark_settings.get("font_size", 36))
            creator_config.watermark_opacity = float(self.watermark_settings.get("opacity", 0.35))
            creator_config.watermark_custom_rect = self.watermark_settings.get("custom_rect")
            self.log_signal.emit(f"  -> Đóng Watermark: '{creator_config.watermark_text}'")

        # Khởi chạy VideoCreatorService
        creator_svc = VideoCreatorService()
        req = VideoCreatorRequest(
            original_video_path=self.video_path,
            segments=segments,
            output_video_path=self.output_mp4,
            config=creator_config,
            is_cancelled_func=lambda: self._is_cancelled,
        )

        def _on_creator_progress(pct: float, msg: str):
            scaled_pct = 55.0 + (pct * 0.43)
            self.progress_signal.emit(scaled_pct, msg)
            if "%" in msg or "hoàn tất" in msg.lower() or "xuất bản" in msg.lower():
                self.log_signal.emit(f"  [Render] {msg}")

        req.progress_callback = _on_creator_progress

        result = creator_svc.run(req)

        if self._is_cancelled or (result and result.status == "cancelled"):
            self.log_signal.emit("[Hủy] Bạn đã dừng tiến trình.")
            self.finished_signal.emit(False, "", "Tiến trình đã bị dừng bởi người dùng.")
            return

        if not result or result.status not in ("success", "completed") or not os.path.isfile(self.output_mp4):
            err_msg = result.error if result else "Không rõ lỗi khi tạo video."
            self.log_signal.emit(f"[Lỗi] {err_msg}")
            self.finished_signal.emit(False, "", err_msg)
            return

        self.progress_signal.emit(100.0, "Tạo video hoàn tất!")
        self.log_signal.emit(f"🎉 XUẤT VIDEO THÀNH CÔNG: {self.output_mp4}")
        self.finished_signal.emit(True, self.output_mp4, "")


class SmartVideoDialog(QDialog):
    """Giao diện tạo video thông minh thiết kế dạng Tab hiện đại, sang trọng."""

    def __init__(
        self,
        parent: QWidget | None = None,
        video_path: str = "",
        workspace_root: str = "",
        existing_segments: list[SubtitleSegment] | None = None,
    ):
        super().__init__(parent)
        self.video_path = video_path or ""
        self.workspace_root = workspace_root or os.getcwd()
        self.existing_segments = existing_segments or []
        self._worker: SmartVideoWorker | None = None
        self.output_video_path = ""

        self.setWindowTitle("🎯 TẠO VIDEO THÔNG MINH - ÂM THANH & MẶT NẠ ĐỘNG")
        self.resize(820, 640)
        self.setMinimumSize(780, 580)

        self._setup_ui()
        self._apply_styling()
        self._load_all_settings()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(10)

        # -------------------------------------------------------------
        # HEADER BAR
        # -------------------------------------------------------------
        header = QFrame()
        header.setObjectName("headerCard")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 10, 14, 10)

        title_col = QVBoxLayout()
        t1 = QLabel("🎙️ TẠO VIDEO THÔNG MINH (QUÉT ÂM THANH & MẶT NẠ ĐỘNG)")
        t1.setObjectName("mainTitle")
        t2 = QLabel("Tự động bóc tách âm thanh siêu tốc • Mặt nạ mờ biến mất theo thời gian • Phân vai đa giọng AI • Đóng Logo")
        t2.setObjectName("subTitle")
        title_col.addWidget(t1)
        title_col.addWidget(t2)
        h_layout.addLayout(title_col, 1)

        root_layout.addWidget(header)

        # -------------------------------------------------------------
        # KHUNG ĐƯỜNG DẪN VIDEO GỌN GÀNG
        # -------------------------------------------------------------
        path_card = QFrame()
        path_card.setObjectName("cardFrame")
        p_layout = QVBoxLayout(path_card)
        p_layout.setContentsMargins(12, 8, 12, 8)
        p_layout.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Video gốc:"))
        self.video_in_edit = QLineEdit(self.video_path)
        self.video_in_edit.setPlaceholderText("Chọn video gốc cần xử lý...")
        btn_in = QPushButton("Chọn file")
        btn_in.clicked.connect(self._browse_input_video)
        r1.addWidget(self.video_in_edit, 1)
        r1.addWidget(btn_in)
        p_layout.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Video xuất:"))
        default_out_dir = os.path.join(self.workspace_root, "output")
        v_name = os.path.splitext(os.path.basename(self.video_path))[0] if self.video_path else "video"
        default_out_path = os.path.join(default_out_dir, f"{v_name}_smart_mask_vi.mp4")
        self.video_out_edit = QLineEdit(default_out_path)
        btn_out = QPushButton("Nơi lưu")
        btn_out.clicked.connect(self._browse_output_video)
        r2.addWidget(self.video_out_edit, 1)
        r2.addWidget(btn_out)
        p_layout.addLayout(r2)

        root_layout.addWidget(path_card)

        # -------------------------------------------------------------
        # TABS CHÍNH
        # -------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        # TAB 1: BÓC TÁCH & MẶT NẠ
        tab1 = QWidget()
        t1_layout = QVBoxLayout(tab1)
        t1_layout.setContentsMargins(12, 12, 12, 12)
        t1_layout.setSpacing(12)

        # Khối Nguồn Bóc Tách
        b1_frame = QFrame()
        b1_frame.setObjectName("subCard")
        b1_layout = QVBoxLayout(b1_frame)
        b1_layout.setContentsMargins(12, 10, 12, 10)
        b1_layout.setSpacing(6)

        lbl_s = QLabel("1. NGUỒN BÓC TÁCH LỜI THOẠI:")
        lbl_s.setObjectName("sectionHeader")
        b1_layout.addWidget(lbl_s)

        self.src_group = QButtonGroup(self)
        self.src_smart_ocr_rb = QRadioButton("👁️ Quét chữ Thông Minh Đa Vùng (Smart OCR) - Tự động bắt Đỉnh & Đáy, lọc Watermark [Khuyên dùng cho hoạt hình/short drama]")
        self.src_smart_ocr_rb.setChecked(True)
        self.src_group.addButton(self.src_smart_ocr_rb)
        b1_layout.addWidget(self.src_smart_ocr_rb)

        self.src_sensevoice_rb = QRadioButton("🎙️ Bóc tách bằng Âm Thanh (SenseVoice AI) - Cho vlog / phỏng vấn có ngắt nghỉ rõ ràng")
        self.src_group.addButton(self.src_sensevoice_rb)
        b1_layout.addWidget(self.src_sensevoice_rb)

        self.src_whisper_rb = QRadioButton("🎙️ Bóc tách bằng Âm Thanh (Faster-Whisper)")
        self.src_group.addButton(self.src_whisper_rb)
        b1_layout.addWidget(self.src_whisper_rb)

        self.src_existing_rb = QRadioButton(f"📜 Sử dụng phụ đề đã có trong Dự án ({len(self.existing_segments)} câu)")
        self.src_group.addButton(self.src_existing_rb)
        if not self.existing_segments:
            self.src_existing_rb.setEnabled(False)
        b1_layout.addWidget(self.src_existing_rb)
        t1_layout.addWidget(b1_frame)

        # Khối Mặt Nạ Động
        b2_frame = QFrame()
        b2_frame.setObjectName("subCard")
        b2_layout = QVBoxLayout(b2_frame)
        b2_layout.setContentsMargins(12, 10, 12, 10)
        b2_layout.setSpacing(6)

        lbl_m = QLabel("2. CƠ CHẾ CHE CHỮ TIẾNG TRUNG (DYNAMIC MASK):")
        lbl_m.setObjectName("sectionHeader")
        b2_layout.addWidget(lbl_m)

        self.mask_group = QButtonGroup(self)
        self.mask_dyn_blur_rb = QRadioButton("🌫️ Mặt nạ mờ ĐỘNG theo thời gian (Chữ xuất hiện lúc nào mờ lúc đó, hết chữ biến mất 100%)")
        self.mask_dyn_blur_rb.setChecked(True)
        self.mask_group.addButton(self.mask_dyn_blur_rb)
        b2_layout.addWidget(self.mask_dyn_blur_rb)

        self.mask_badge_rb = QRadioButton("🏷️ Hộp Phụ Đề Bo Góc (Subtitle Badge CapCut/TikTok - Đè lên chữ gốc, không nhòe video)")
        self.mask_group.addButton(self.mask_badge_rb)
        b2_layout.addWidget(self.mask_badge_rb)

        self.mask_none_rb = QRadioButton("🚫 Không dùng mặt nạ (Chỉ hiện phụ đề viền nổi)")
        self.mask_group.addButton(self.mask_none_rb)
        b2_layout.addWidget(self.mask_none_rb)
        t1_layout.addWidget(b2_frame)

        # Khối Bộ Lọc Watermark
        b3_frame = QFrame()
        b3_frame.setObjectName("subCard")
        b3_layout = QVBoxLayout(b3_frame)
        b3_layout.setContentsMargins(12, 10, 12, 10)
        b3_layout.setSpacing(6)

        lbl_w = QLabel("3. BỘ LỌC CHỐNG WATERMARK & TỪ KHÓA RÁC:")
        lbl_w.setObjectName("sectionHeader")
        b3_layout.addWidget(lbl_w)

        self.enable_blacklist_cb = QCheckBox("Tự động xóa các từ khóa rác & watermark Douyin sau:")
        self.enable_blacklist_cb.setChecked(True)
        b3_layout.addWidget(self.enable_blacklist_cb)

        self.blacklist_edit = QLineEdit(", ".join(DEFAULT_BLACKLIST))
        b3_layout.addWidget(self.blacklist_edit)
        t1_layout.addWidget(b3_frame)
        t1_layout.addStretch()

        self.tabs.addTab(tab1, "🎯 1. Bóc Tách && Mặt Nạ")

        # TAB 2: PHÂN VAI ĐA GIỌNG & ÂM LƯỢNG
        tab2 = QWidget()
        t2_layout = QVBoxLayout(tab2)
        t2_layout.setContentsMargins(12, 12, 12, 12)
        t2_layout.setSpacing(12)

        # Compatibility radio buttons
        self.voice_mode_group = QButtonGroup(self)
        self.vmode_single_rb = QRadioButton()
        self.vmode_single_rb.setChecked(True)
        self.vmode_multi_rb = QRadioButton()
        self.voice_mode_group.addButton(self.vmode_single_rb)
        self.voice_mode_group.addButton(self.vmode_multi_rb)

        # Chọn giọng đọc AI duy nhất cho toàn bộ video
        v_pick_frame = QFrame()
        v_pick_frame.setObjectName("subCard")
        vp_layout = QVBoxLayout(v_pick_frame)
        vp_layout.setContentsMargins(12, 12, 12, 12)
        vp_layout.setSpacing(10)

        lbl_vp = QLabel("CHỌN GIỌNG ĐỌC AI (TOÀN BỘ VIDEO):")
        lbl_vp.setObjectName("sectionHeader")
        vp_layout.addWidget(lbl_vp)

        females, males = _load_available_voices()

        r_s = QHBoxLayout()
        lbl_vname = QLabel("Giọng đọc AI:")
        lbl_vname.setStyleSheet("font-weight: bold; font-size: 12px;")
        r_s.addWidget(lbl_vname)

        self.single_combo = QComboBox()
        self.single_combo.setMinimumHeight(34)
        for f in females:
            self.single_combo.addItem(f"♀ {f['name']}", f["id"])
        for m in males:
            self.single_combo.addItem(f"♂ {m['name']}", m["id"])
        r_s.addWidget(self.single_combo, 1)
        vp_layout.addLayout(r_s)

        # Placeholder references
        self.female_combo = self.single_combo
        self.male_combo = self.single_combo

        note_lbl = QLabel("💡 Lưu ý: Toàn bộ video sẽ được lồng tiếng đồng nhất bằng 1 giọng đọc đã chọn ở trên.")
        note_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-style: italic; margin-top: 2px;")
        vp_layout.addWidget(note_lbl)

        t2_layout.addWidget(v_pick_frame)

        # Âm lượng
        vol_frame = QFrame()
        vol_frame.setObjectName("subCard")
        vl_layout = QVBoxLayout(vol_frame)
        vl_layout.setContentsMargins(12, 10, 12, 10)
        vl_layout.setSpacing(8)

        lbl_vl = QLabel("HÒA ÂM (VOLUME & DUCKING):")
        lbl_vl.setObjectName("sectionHeader")
        vl_layout.addWidget(lbl_vl)

        row_orig = QHBoxLayout()
        row_orig.addWidget(QLabel("Âm lượng video gốc:"))
        self.orig_slider = QSlider(Qt.Horizontal)
        self.orig_slider.setRange(0, 100)
        self.orig_slider.setValue(40)
        self.orig_val_lbl = QLabel("40%")
        self.orig_slider.valueChanged.connect(lambda v: self.orig_val_lbl.setText(f"{v}%"))
        row_orig.addWidget(self.orig_slider, 1)
        row_orig.addWidget(self.orig_val_lbl)
        vl_layout.addLayout(row_orig)

        row_tts = QHBoxLayout()
        row_tts.addWidget(QLabel("Âm lượng lồng tiếng AI:"))
        self.tts_slider = QSlider(Qt.Horizontal)
        self.tts_slider.setRange(0, 150)
        self.tts_slider.setValue(100)
        self.tts_val_lbl = QLabel("100%")
        self.tts_slider.valueChanged.connect(lambda v: self.tts_val_lbl.setText(f"{v}%"))
        row_tts.addWidget(self.tts_slider, 1)
        row_tts.addWidget(self.tts_val_lbl)
        vl_layout.addLayout(row_tts)
        t2_layout.addWidget(vol_frame)
        t2_layout.addStretch()

        self.tabs.addTab(tab2, "🎙️ 2. Giọng Đọc AI && Âm Lượng")

        # TAB 3: LOGO THƯƠNG HIỆU & WATERMARK
        tab3 = QWidget()
        t3_layout = QVBoxLayout(tab3)
        t3_layout.setContentsMargins(12, 12, 12, 12)
        t3_layout.setSpacing(12)

        # Logo
        logo_frame = QFrame()
        logo_frame.setObjectName("subCard")
        lf_layout = QVBoxLayout(logo_frame)
        lf_layout.setContentsMargins(12, 10, 12, 10)
        lf_layout.setSpacing(8)

        lbl_lg = QLabel("LOGO THƯƠNG HIỆU:")
        lbl_lg.setObjectName("sectionHeader")
        lf_layout.addWidget(lbl_lg)

        self.enable_logo_cb = QCheckBox("Đóng Logo thương hiệu lên video")
        lf_layout.addWidget(self.enable_logo_cb)

        l_path_row = QHBoxLayout()
        l_path_row.addWidget(QLabel("File ảnh Logo:"))
        self.logo_path_edit = QLineEdit()
        self.logo_path_edit.setPlaceholderText("Đường dẫn file ảnh logo (.png, .jpg)...")
        btn_logo = QPushButton("Chọn Logo")
        btn_logo.clicked.connect(self._browse_logo_file)
        l_path_row.addWidget(self.logo_path_edit, 1)
        l_path_row.addWidget(btn_logo)
        lf_layout.addLayout(l_path_row)

        l_opts_row = QHBoxLayout()
        l_opts_row.addWidget(QLabel("Vị trí:"))
        self.logo_pos_combo = QComboBox()
        self.logo_pos_combo.addItem("Góc trên bên trái", "top_left")
        self.logo_pos_combo.addItem("Góc trên bên phải", "top_right")
        self.logo_pos_combo.addItem("Góc dưới bên trái", "bottom_left")
        self.logo_pos_combo.addItem("Góc dưới bên phải", "bottom_right")
        self.logo_pos_combo.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        l_opts_row.addWidget(self.logo_pos_combo, 1)

        l_opts_row.addWidget(QLabel("Kích thước:"))
        self.logo_size_slider = QSlider(Qt.Horizontal)
        self.logo_size_slider.setRange(5, 35)
        self.logo_size_slider.setValue(15)
        self.logo_size_lbl = QLabel("15%")
        self.logo_size_slider.valueChanged.connect(lambda v: self.logo_size_lbl.setText(f"{v}%"))
        l_opts_row.addWidget(self.logo_size_slider, 1)
        l_opts_row.addWidget(self.logo_size_lbl)

        l_opts_row.addWidget(QLabel("Độ mờ:"))
        self.logo_op_slider = QSlider(Qt.Horizontal)
        self.logo_op_slider.setRange(10, 100)
        self.logo_op_slider.setValue(100)
        self.logo_op_lbl = QLabel("100%")
        self.logo_op_slider.valueChanged.connect(lambda v: self.logo_op_lbl.setText(f"{v}%"))
        l_opts_row.addWidget(self.logo_op_slider, 1)
        l_opts_row.addWidget(self.logo_op_lbl)
        lf_layout.addLayout(l_opts_row)
        t3_layout.addWidget(logo_frame)

        # Watermark
        wm_frame = QFrame()
        wm_frame.setObjectName("subCard")
        wm_layout = QVBoxLayout(wm_frame)
        wm_layout.setContentsMargins(12, 10, 12, 10)
        wm_layout.setSpacing(8)

        lbl_wmt = QLabel("WATERMARK CHỮ BẢN QUYỀN:")
        lbl_wmt.setObjectName("sectionHeader")
        wm_layout.addWidget(lbl_wmt)

        self.enable_wm_cb = QCheckBox("Chèn chữ Watermark mờ bản quyền")
        wm_layout.addWidget(self.enable_wm_cb)

        wm_text_row = QHBoxLayout()
        wm_text_row.addWidget(QLabel("Nội dung chữ:"))
        self.wm_text_edit = QLineEdit()
        self.wm_text_edit.setPlaceholderText("Ví dụ: @TenKenhCuaBan")
        wm_text_row.addWidget(self.wm_text_edit, 1)
        wm_layout.addLayout(wm_text_row)

        wm_opts_row = QHBoxLayout()
        wm_opts_row.addWidget(QLabel("Vị trí:"))
        self.wm_pos_combo = QComboBox()
        self.wm_pos_combo.addItem("Chính giữa màn hình", "center")
        self.wm_pos_combo.addItem("Góc dưới giữa", "center_bottom")
        self.wm_pos_combo.addItem("Góc trên bên phải", "top_right")
        self.wm_pos_combo.addItem("Góc trên bên trái", "top_left")
        self.wm_pos_combo.addItem("🎯 Theo khung vẽ trên Video (Khung tùy chọn)", "custom_rect")
        wm_opts_row.addWidget(self.wm_pos_combo, 1)

        wm_opts_row.addWidget(QLabel("Độ mờ:"))
        self.wm_op_slider = QSlider(Qt.Horizontal)
        self.wm_op_slider.setRange(10, 100)
        self.wm_op_slider.setValue(35)
        self.wm_op_lbl = QLabel("35%")
        self.wm_op_slider.valueChanged.connect(lambda v: self.wm_op_lbl.setText(f"{v}%"))
        wm_opts_row.addWidget(self.wm_op_slider, 1)
        wm_opts_row.addWidget(self.wm_op_lbl)
        wm_layout.addLayout(wm_opts_row)
        t3_layout.addWidget(wm_frame)
        t3_layout.addStretch()

        self.tabs.addTab(tab3, "🏷️ 3. Logo && Watermark")

        root_layout.addWidget(self.tabs, 1)

        # -------------------------------------------------------------
        # KHUNG TIẾN TRÌNH & LOG
        # -------------------------------------------------------------
        self.progress_frame = QFrame()
        self.progress_frame.setVisible(False)
        pr_layout = QVBoxLayout(self.progress_frame)
        pr_layout.setContentsMargins(0, 4, 0, 0)
        pr_layout.setSpacing(6)

        self.progress_lbl = QLabel("Đang chuẩn bị...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(90)
        self.log_text.setReadOnly(True)

        pr_layout.addWidget(self.progress_lbl)
        pr_layout.addWidget(self.progress_bar)
        pr_layout.addWidget(self.log_text)
        root_layout.addWidget(self.progress_frame)

        # -------------------------------------------------------------
        # FOOTER ACTION BUTTONS
        # -------------------------------------------------------------
        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.open_folder_btn = QPushButton("📂 Mở thư mục xuất")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._open_output_folder)

        self.play_video_btn = QPushButton("▶ Xem video")
        self.play_video_btn.setVisible(False)
        self.play_video_btn.clicked.connect(self._play_output_video)

        self.cancel_btn = QPushButton("Đóng")
        self.cancel_btn.clicked.connect(self._handle_cancel)

        self.start_btn = QPushButton("🚀 BẮT ĐẦU TẠO VIDEO NGAY")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self._start_processing)

        footer.addWidget(self.open_folder_btn)
        footer.addWidget(self.play_video_btn)
        footer.addStretch()
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.start_btn)
        root_layout.addLayout(footer)

    def _apply_styling(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #111215;
                color: #e5e7eb;
            }
            #headerCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2e1065, stop:1 #1e1b4b);
                border: 1px solid #4338ca;
                border-radius: 8px;
            }
            #mainTitle {
                color: #c7d2fe;
                font-size: 15px;
                font-weight: bold;
            }
            #subTitle {
                color: #94a3b8;
                font-size: 11px;
            }
            #cardFrame {
                background-color: #17181d;
                border: 1px solid #282a33;
                border-radius: 6px;
            }
            #subCard {
                background-color: #16181f;
                border: 1px solid #282a35;
                border-radius: 6px;
            }
            #sectionHeader {
                color: #f59e0b;
                font-size: 11px;
                font-weight: bold;
            }
            QTabWidget::pane {
                border: 1px solid #282a35;
                background-color: #14151b;
                border-radius: 6px;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #1b1c24;
                color: #9ca3af;
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #26273b;
                color: #818cf8;
                border-bottom: 2px solid #6366f1;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #0b0c0f;
                color: #ffffff;
                border: 1px solid #2f3342;
                border-radius: 4px;
                padding: 5px 8px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #6366f1;
            }
            QRadioButton, QCheckBox {
                color: #f3f4f6;
                font-size: 12px;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid #4b5563;
                background-color: #1f2937;
            }
            QRadioButton::indicator:checked {
                border-color: #818cf8;
                background-color: #6366f1;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 2px solid #4b5563;
                background-color: #1f2937;
            }
            QCheckBox::indicator:checked {
                border-color: #818cf8;
                background-color: #6366f1;
            }
            #startBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #3b82f6);
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
            }
            #startBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c3aed, stop:1 #2563eb);
            }
            QPushButton {
                background-color: #242630;
                color: #e5e7eb;
                border: 1px solid #3b3f4f;
                border-radius: 4px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #313442;
                color: #ffffff;
            }
            QProgressBar {
                background-color: #1b1c24;
                border: 1px solid #323545;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #10b981);
                border-radius: 3px;
            }
        """)

    def _browse_input_video(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Chọn video gốc", self.workspace_root, "Video Files (*.mp4 *.mkv *.mov *.avi)"
        )
        if f:
            self.video_in_edit.setText(f)
            v_name = os.path.splitext(os.path.basename(f))[0]
            out_dir = os.path.join(self.workspace_root, "output")
            self.video_out_edit.setText(os.path.join(out_dir, f"{v_name}_smart_mask_vi.mp4"))

    def _browse_output_video(self):
        f, _ = QFileDialog.getSaveFileName(
            self, "Chọn nơi lưu video", self.video_out_edit.text(), "MP4 Video (*.mp4)"
        )
        if f:
            self.video_out_edit.setText(f)

    def _browse_logo_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Chọn file ảnh Logo", self.workspace_root, "Image Files (*.png *.jpg *.jpeg *.webp)"
        )
        if f:
            self.logo_path_edit.setText(f)

    def _load_all_settings(self):
        """Đồng bộ cài đặt từ QSettings (kế thừa Logo/Watermark từ SelectMasterDialog)."""
        branding_st = QSettings("TankVideo", "TankVideoStudio")
        # Logo
        enable_l = branding_st.value("branding/enable_logo", False, type=bool)
        l_path = branding_st.value("branding/logo_path", "", type=str)
        l_pos = branding_st.value("branding/logo_pos", "top_left", type=str)
        l_sz = branding_st.value("branding/logo_size", 15, type=int)
        l_op = branding_st.value("branding/logo_opacity", 100, type=int)

        self.enable_logo_cb.setChecked(enable_l)
        if l_path:
            self.logo_path_edit.setText(l_path)
        pos_idx = self.logo_pos_combo.findData(l_pos)
        if pos_idx >= 0:
            self.logo_pos_combo.setCurrentIndex(pos_idx)
        self.logo_size_slider.setValue(l_sz)
        self.logo_op_slider.setValue(l_op)

        # Tự động nhận diện khung Đặt Logo nếu đã được người dùng chỉnh trên Preview
        p = self.parent()
        self._logo_custom_rect = None
        if p and hasattr(p, "logo_placement_overlay"):
            lp_ov = getattr(p, "logo_placement_overlay", None)
            if lp_ov and (getattr(lp_ov, "_user_customized", False) or getattr(lp_ov, "_requested_visible", False)):
                lpr = lp_ov.get_norm_rect()
                if lpr and len(lpr) == 4 and lpr[2] > 0.01:
                    self._logo_custom_rect = lpr
                    idx_custom = self.logo_pos_combo.findData("custom_rect")
                    if idx_custom >= 0:
                        self.logo_pos_combo.setCurrentIndex(idx_custom)
        if self._logo_custom_rect is None:
            saved_lp = branding_st.value("branding/logo_custom_rect", "", type=str)
            if saved_lp:
                try:
                    parts = [float(x) for x in saved_lp.split(",")]
                    if len(parts) == 4:
                        self._logo_custom_rect = tuple(parts)
                except Exception:
                    pass

        # Watermark
        enable_w = branding_st.value("branding/enable_watermark", False, type=bool)
        w_text = branding_st.value("branding/watermark_text", "", type=str)
        w_pos = branding_st.value("branding/watermark_pos", "center", type=str)
        w_op = branding_st.value("branding/watermark_opacity", 35, type=int)

        self.enable_wm_cb.setChecked(enable_w)
        if w_text:
            self.wm_text_edit.setText(w_text)
        w_idx = self.wm_pos_combo.findData(w_pos)
        if w_idx >= 0:
            self.wm_pos_combo.setCurrentIndex(w_idx)
        self.wm_op_slider.setValue(w_op)

        # Tự động nhận diện khung Đặt Watermark
        self._watermark_custom_rect = None
        if p and hasattr(p, "watermark_placement_overlay"):
            wmp_ov = getattr(p, "watermark_placement_overlay", None)
            if wmp_ov and (getattr(wmp_ov, "_user_customized", False) or getattr(wmp_ov, "_requested_visible", False)):
                wmpr = wmp_ov.get_norm_rect()
                if wmpr and len(wmpr) == 4 and wmpr[2] > 0.01:
                    self._watermark_custom_rect = wmpr
                    idx_custom_wm = self.wm_pos_combo.findData("custom_rect")
                    if idx_custom_wm >= 0:
                        self.wm_pos_combo.setCurrentIndex(idx_custom_wm)
        if self._watermark_custom_rect is None:
            saved_wmp = branding_st.value("branding/watermark_custom_rect", "", type=str)
            if saved_wmp:
                try:
                    parts = [float(x) for x in saved_wmp.split(",")]
                    if len(parts) == 4:
                        self._watermark_custom_rect = tuple(parts)
                except Exception:
                    pass

        # Voice settings: Nạp giọng đã lưu
        last_s = branding_st.value("smart_voice_single", "", type=str)
        if not last_s:
            last_s = branding_st.value("smart_voice_male", "", type=str) or branding_st.value("smart_voice_female", "", type=str)

        if last_s:
            idx = self.single_combo.findData(last_s)
            if idx >= 0:
                self.single_combo.setCurrentIndex(idx)

    def _save_all_settings(self):
        """Lưu lại cài đặt để đồng bộ toàn hệ thống."""
        branding_st = QSettings("TankVideo", "TankVideoStudio")
        branding_st.setValue("branding/enable_logo", self.enable_logo_cb.isChecked())
        branding_st.setValue("branding/logo_path", self.logo_path_edit.text().strip())
        branding_st.setValue("branding/logo_pos", self.logo_pos_combo.currentData() or "top_left")
        branding_st.setValue("branding/logo_size", self.logo_size_slider.value())
        branding_st.setValue("branding/logo_opacity", self.logo_op_slider.value())
        if getattr(self, "_logo_custom_rect", None):
            lr = self._logo_custom_rect
            branding_st.setValue("branding/logo_custom_rect", f"{lr[0]:.4f},{lr[1]:.4f},{lr[2]:.4f},{lr[3]:.4f}")

        branding_st.setValue("branding/enable_watermark", self.enable_wm_cb.isChecked())
        branding_st.setValue("branding/watermark_text", self.wm_text_edit.text().strip())
        branding_st.setValue("branding/watermark_pos", self.wm_pos_combo.currentData() or "center")
        branding_st.setValue("branding/watermark_opacity", self.wm_op_slider.value())
        if getattr(self, "_watermark_custom_rect", None):
            wmr = self._watermark_custom_rect
            branding_st.setValue("branding/watermark_custom_rect", f"{wmr[0]:.4f},{wmr[1]:.4f},{wmr[2]:.4f},{wmr[3]:.4f}")

        # Save voice settings (1 giọng duy nhất)
        cur_v = self.single_combo.currentData()
        branding_st.setValue("smart_voice_single", cur_v)
        branding_st.setValue("smart_voice_female", cur_v)
        branding_st.setValue("smart_voice_male", cur_v)
        branding_st.setValue("smart_voice_mode", "single")

    def _start_processing(self):
        video_in = self.video_in_edit.text().strip()
        if not video_in or not os.path.isfile(video_in):
            QMessageBox.warning(self, "Thiếu video", "Vui lòng chọn video gốc hợp lệ.")
            return

        video_out = self.video_out_edit.text().strip()
        if not video_out:
            QMessageBox.warning(self, "Thiếu nơi lưu", "Vui lòng chỉ định đường dẫn lưu video xuất ra.")
            return

        os.makedirs(os.path.dirname(os.path.abspath(video_out)), exist_ok=True)
        self._save_all_settings()

        # Nguồn bóc tách
        if self.src_smart_ocr_rb.isChecked():
            source_mode = "smart_ocr"
        elif self.src_sensevoice_rb.isChecked():
            source_mode = "audio_sensevoice"
        elif self.src_whisper_rb.isChecked():
            source_mode = "audio_whisper"
        else:
            source_mode = "master_data"

        # Mặt nạ
        if self.mask_dyn_blur_rb.isChecked():
            mask_solution = "dynamic_blur"
        elif self.mask_badge_rb.isChecked():
            mask_solution = "subtitle_badge"
        else:
            mask_solution = "none"

        # Blacklist
        blacklist: list[str] = []
        if self.enable_blacklist_cb.isChecked():
            raw_bl = self.blacklist_edit.text().strip()
            blacklist = [w.strip() for w in raw_bl.split(",") if w.strip()]

        # Giọng đọc AI được chọn
        selected_voice = str(self.single_combo.currentData() or "vieneu_thanhnientutin")

        # Logo Settings
        brand_logo = {
            "enable": self.enable_logo_cb.isChecked() and bool(self.logo_path_edit.text().strip()),
            "path": self.logo_path_edit.text().strip(),
            "position": self.logo_pos_combo.currentData() or "top_left",
            "size_percent": self.logo_size_slider.value(),
            "opacity": float(self.logo_op_slider.value()) / 100.0,
            "custom_rect": getattr(self, "_logo_custom_rect", None) if self.logo_pos_combo.currentData() == "custom_rect" else None,
        }

        # Watermark Settings
        watermark = {
            "enable": self.enable_wm_cb.isChecked() and bool(self.wm_text_edit.text().strip()),
            "text": self.wm_text_edit.text().strip(),
            "position": self.wm_pos_combo.currentData() or "center",
            "font_size": 36,
            "opacity": float(self.wm_op_slider.value()) / 100.0,
            "custom_rect": getattr(self, "_watermark_custom_rect", None) if self.wm_pos_combo.currentData() == "custom_rect" else None,
        }

        # UI state
        self.progress_frame.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_text.clear()
        self.start_btn.setEnabled(False)
        self.cancel_btn.setText("Dừng lại")
        self.open_folder_btn.setVisible(False)
        self.play_video_btn.setVisible(False)

        # Worker
        self._worker = SmartVideoWorker(
            video_path=video_in,
            output_mp4=video_out,
            source_mode=source_mode,
            mask_solution=mask_solution,
            blacklist_keywords=blacklist,
            voice_mode="single",
            voice_female_id=selected_voice,
            voice_male_id=selected_voice,
            voice_single_id=selected_voice,
            orig_volume=float(self.orig_slider.value()) / 100.0,
            tts_volume=float(self.tts_slider.value()) / 100.0,
            brand_logo_settings=brand_logo,
            watermark_settings=watermark,
            existing_segments=self.existing_segments if source_mode == "master_data" else None,
            workspace_root=self.workspace_root,
        )

        self._worker.progress_signal.connect(self._on_worker_progress)
        self._worker.log_signal.connect(self._on_worker_log)
        self._worker.finished_signal.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_progress(self, pct: float, msg: str):
        self.progress_bar.setValue(int(pct))
        self.progress_lbl.setText(f"{int(pct)}% - {msg}")

    def _on_worker_log(self, text: str):
        self.log_text.append(text)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_worker_finished(self, success: bool, output_mp4: str, error_msg: str):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setText("Đóng")

        if success and output_mp4 and os.path.isfile(output_mp4):
            self.output_video_path = output_mp4
            self.open_folder_btn.setVisible(True)
            self.play_video_btn.setVisible(True)
            QMessageBox.information(
                self,
                "Thành công!",
                f"Tạo video thông minh hoàn tất rực rỡ!\n\nFile xuất ra:\n{output_mp4}",
            )
        elif not success and error_msg != "Tiến trình đã bị dừng bởi người dùng.":
            QMessageBox.critical(
                self,
                "Lỗi xử lý",
                f"Đã xảy ra lỗi trong quá trình tạo video:\n\n{error_msg}",
            )

    def _handle_cancel(self):
        if self._worker and self._worker.isRunning():
            ret = QMessageBox.question(
                self,
                "Xác nhận dừng",
                "Tiến trình đang chạy. Bạn có chắc muốn dừng lại không?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self._worker.request_cancel()
        else:
            self.close()

    def _open_output_folder(self):
        if self.output_video_path and os.path.isfile(self.output_video_path):
            folder = os.path.dirname(os.path.abspath(self.output_video_path))
            if os.name == "nt":
                subprocess.run(["explorer", f"/select,{os.path.normpath(self.output_video_path)}"])
            else:
                os.system(f'open "{folder}"')

    def _play_output_video(self):
        if self.output_video_path and os.path.isfile(self.output_video_path):
            if os.name == "nt":
                os.startfile(os.path.normpath(self.output_video_path))
            else:
                os.system(f'open "{self.output_video_path}"')

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(1000)
        super().closeEvent(event)
