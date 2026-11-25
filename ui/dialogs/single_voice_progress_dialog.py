"""Single Voice Pipeline Progress Dialog and Background Worker.

Executes the full 1-voice automated workflow:
1. Video OCR Subtitle Extraction.
2. DeepSeek AI Contextual Translation.
3. 1-Voice Neural TTS Synthesis with Duration Fitting.
4. Video Rendering with Chinese Blur Mask + Vietnamese Subtitles + Audio Ducking.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import traceback
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
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

from app.models.subtitle_segment import SubtitleSegment
from app.services.video_creator_service import (
    VideoCreatorConfig,
    VideoCreatorRequest,
    VideoCreatorResult,
    VideoCreatorService,
)
from app.services.video_export_service import SubtitleRenderConfig


class SingleVoicePipelineWorker(QThread):
    """QThread worker chạy toàn bộ quy trình 1 giọng không làm đơ giao diện."""

    progress_updated = Signal(float, str)
    stage_changed = Signal(str, str)
    log_emitted = Signal(str)
    pipeline_finished = Signal(object, list)  # (VideoCreatorResult, translated_cues)
    pipeline_error = Signal(str)
    pipeline_cancelled = Signal()

    def __init__(
        self,
        *,
        workspace_root: str,
        video_path: str,
        selected_voice: dict[str, str],
        audio_mix_settings: dict[str, float],
        enable_blur_mask: bool,
        mask_mode: str = "box",
        blur_rect: tuple[float, float, float, float] | None,
        burn_subtitles: bool,
        existing_cues: list[dict[str, Any]] | None,
        output_video_path: str,
        subtitle_config: SubtitleRenderConfig | None = None,
        brand_logo_settings: dict[str, Any] | None = None,
        frame_border_settings: dict[str, Any] | None = None,
        watermark_settings: dict[str, Any] | None = None,
        delogo_settings: dict[str, Any] | None = None,
        dual_voice_config: dict[str, Any] | None = None,
        approved_cues: list[dict[str, Any]] | None = None,
        blur_rect_2: tuple[float, float, float, float] | None = None,
        is_dual_ocr: bool = False,
        dynamic_mask: bool = False,
        asr_supplement_settings: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.selected_voice = selected_voice
        self.audio_mix_settings = audio_mix_settings
        self.enable_blur_mask = enable_blur_mask
        self.subtitle_config = subtitle_config or SubtitleRenderConfig()
        self.mask_mode = str(mask_mode or getattr(self.subtitle_config, "mask_mode", "box") or "box").strip().lower()
        self.blur_rect = blur_rect
        self.blur_rect_2 = blur_rect_2
        self.is_dual_ocr = is_dual_ocr
        self.dynamic_mask = dynamic_mask
        self.burn_subtitles = burn_subtitles
        self.existing_cues = existing_cues
        self.output_video_path = output_video_path
        self.brand_logo_settings = brand_logo_settings or {}
        self.frame_border_settings = frame_border_settings or {}
        self.watermark_settings = watermark_settings or {}
        self.delogo_settings = delogo_settings or {}
        self.dual_voice_config = dual_voice_config
        self.approved_cues = approved_cues
        self.asr_supplement_settings = asr_supplement_settings or {}

        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self):
        try:
            from runtime_paths import optimize_windows_performance
            optimize_windows_performance()
        except Exception:
            pass
        try:
            self._execute_pipeline()
        except InterruptedError:
            self.pipeline_cancelled.emit()
        except Exception as exc:
            if self._cancel_event.is_set():
                self.pipeline_cancelled.emit()
                return
            trace_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"[SingleVoicePipelineWorker] Error:\n{trace_str}")
            self.pipeline_error.emit(str(exc))
        else:
            if self._cancel_event.is_set():
                self.pipeline_cancelled.emit()

    def _execute_pipeline(self):
        translated_cues: list[dict[str, Any]] = []

        # NẾU ĐÃ CÓ KỊCH BẢN ĐÃ DUYỆT TỪ BẢNG LỊCH SỬ PHÂN VAI AI:
        if self.approved_cues and len(self.approved_cues) > 0:
            self.stage_changed.emit("render", "Nạp kịch bản phân vai đã duyệt và tạo video...")
            self.progress_updated.emit(35.0, f"Đã nạp {len(self.approved_cues)} câu thoại đã kiểm duyệt.")
            self.log_emitted.emit(f"[Phân vai AI] Đã nạp thành công {len(self.approved_cues)} câu thoại đã kiểm duyệt từ Lịch sử.")
            translated_cues = list(self.approved_cues)
        else:
            # --- BƯỚC 1: QUÉT OCR PHỤ ĐỀ (0% -> 25%) ---
            self.stage_changed.emit("ocr", "Đang quét phụ đề video (OCR)...")
            self.progress_updated.emit(5.0, "Đang quét phụ đề video...")

            # Trích xuất các khoảng thời gian phân cảnh tin nhắn / ASR nếu có
            asr_enabled = bool(self.asr_supplement_settings.get("enabled"))
            asr_mode = str(self.asr_supplement_settings.get("mode", "range") or "range").lower()
            asr_ranges: list[tuple[float, float]] = []
            if asr_enabled and asr_mode == "range":
                for r in self.asr_supplement_settings.get("ranges") or []:
                    if isinstance(r, (list, tuple)) and len(r) >= 2 and float(r[1]) > float(r[0]):
                        asr_ranges.append((round(float(r[0]), 2), round(float(r[1]), 2)))
                if not asr_ranges:
                    s_s = max(0.0, float(self.asr_supplement_settings.get("start_seconds", 0.0) or 0.0))
                    e_s = float(self.asr_supplement_settings.get("end_seconds", 0.0) or 0.0)
                    if e_s > s_s:
                        asr_ranges.append((round(s_s, 2), round(e_s, 2)))

            def _is_in_asr_range(c_start: float, c_end: float) -> bool:
                for r_s, r_e in asr_ranges:
                    overlap = max(0.0, min(c_end, r_e) - max(c_start, r_s))
                    c_dur = max(0.1, c_end - c_start)
                    if (r_s <= c_start < r_e) or (r_s < c_end <= r_e) or (overlap / c_dur >= 0.35) or (overlap > 0.8):
                        return True
                return False

            cues: list[dict[str, Any]] = []
            if self.existing_cues and len(self.existing_cues) > 0 and any(str(c.get("text", "")).strip() for c in self.existing_cues):
                raw_existing = [dict(c) for c in self.existing_cues if str(c.get("text", "")).strip()]
                if asr_ranges:
                    cues = [c for c in raw_existing if not _is_in_asr_range(float(c.get("start", 0.0)), float(c.get("end", 0.0)))]
                    rem_cnt = len(raw_existing) - len(cues)
                    range_desc = ", ".join(f"{s}s-{e}s" for s, e in asr_ranges)
                    self.log_emitted.emit(f"[OCR] Tái sử dụng {len(cues)} câu phụ đề (đã loại bỏ {rem_cnt} câu OCR dính trong khoảng thời gian tin nhắn ASR: {range_desc}).")
                else:
                    cues = raw_existing
                    self.log_emitted.emit(f"[OCR] Tái sử dụng {len(cues)} câu phụ đề đã nhận diện trên giao diện.")
            elif self.is_dual_ocr or self.blur_rect_2:
                self.log_emitted.emit(f"[OCR 2 Khung] Bắt đầu quét đồng thời 2 khung OCR trên video: {os.path.basename(self.video_path)}")
                from app.ocr_processor import transcribe_video_dual_ocr
                from services.subtitle_merge_service import SubtitleMergeService

                r1 = self.blur_rect or (0.08, 0.70, 0.84, 0.09)
                r2 = self.blur_rect_2 or (0.08, 0.09, 0.84, 0.085)
                os.environ["OCR_SUBTITLE_RECT"] = f"{r1[0]},{r1[1]},{r1[2]},{r1[3]}"
                os.environ["OCR_SUBTITLE_RECT_2"] = f"{r2[0]},{r2[1]},{r2[2]},{r2[3]}"

                def _on_ocr_progress(cur, total, ocr_cnt, skip_cnt):
                    if self._cancel_event.is_set():
                        return
                    if total > 0:
                        pct = 5.0 + (cur / total) * 20.0
                        p_int = cur * 100 // total
                        msg = f"Đang quét 2 Khung OCR: Frame {cur}/{total} ({p_int}%) - Đã tìm thấy {ocr_cnt} câu..."
                        self.progress_updated.emit(pct, msg)
                        self.log_emitted.emit(f"[Dual OCR] Frame {cur}/{total} ({p_int}%, OCR: {ocr_cnt}, skip: {skip_cnt})")

                raw_cues = list(
                    transcribe_video_dual_ocr(
                        self.video_path,
                        rect1=r1,
                        rect2=r2,
                        skip_ranges=asr_ranges,
                        progress_callback=_on_ocr_progress,
                        is_cancelled_func=lambda: self._cancel_event.is_set(),
                    ) or []
                )
                self.log_emitted.emit(f"[Dual OCR] Quét 2 khung hoàn tất, tìm thấy {len(raw_cues)} câu thoại.")
                cues = SubtitleMergeService.sanitize_cue_timings([dict(c) for c in raw_cues if str(c.get("text", "")).strip()])
            else:
                self.log_emitted.emit(f"[OCR] Bắt đầu quét OCR từ video: {os.path.basename(self.video_path)}")
                from services.engine_runtime import EngineRuntime
                from services.subtitle_merge_service import SubtitleMergeService

                if self.blur_rect and len(self.blur_rect) == 4 and self.blur_rect[2] > 0 and self.blur_rect[3] > 0:
                    os.environ["OCR_SUBTITLE_RECT"] = f"{self.blur_rect[0]},{self.blur_rect[1]},{self.blur_rect[2]},{self.blur_rect[3]}"
                engine = EngineRuntime()
                region = str(os.getenv("OCR_SUBTITLE_REGION", "bottom") or "bottom")

                def _on_ocr_progress(cur, total, ocr_cnt, skip_cnt):
                    if self._cancel_event.is_set():
                        return
                    if total > 0:
                        pct = 5.0 + (cur / total) * 20.0
                        p_int = cur * 100 // total
                        msg = f"Đang quét OCR: Frame {cur}/{total} ({p_int}%) - Đã tìm thấy {ocr_cnt} câu..."
                        self.progress_updated.emit(pct, msg)
                        self.log_emitted.emit(f"[OCR] Frame {cur}/{total} ({p_int}%, OCR: {ocr_cnt}, skip: {skip_cnt})")

                raw_cues = list(
                    engine.transcribe_video_ocr(
                        self.video_path,
                        region=region,
                        skip_ranges=asr_ranges,
                        progress_callback=_on_ocr_progress,
                        is_cancelled_func=lambda: self._cancel_event.is_set(),
                    ) or []
                )
                self.log_emitted.emit(f"[OCR] Quét thô hoàn tất, tìm thấy {len(raw_cues)} đoạn text.")
                cues = SubtitleMergeService.merge_cues([dict(c) for c in raw_cues if str(c.get("text", "")).strip()])
                cues = SubtitleMergeService.sanitize_cue_timings(cues)

            # Lọc sạch các câu OCR rơi vào khoảng thời gian phân cảnh tin nhắn ASR
            if asr_ranges and cues:
                before_cnt = len(cues)
                cues = [c for c in cues if not _is_in_asr_range(float(c.get("start", 0.0)), float(c.get("end", 0.0)))]
                rem_cnt = before_cnt - len(cues)
                if rem_cnt > 0:
                    self.log_emitted.emit(f"[OCR] Đã loại bỏ triệt để {rem_cnt} đoạn text OCR rác trong khoảng thời gian tin nhắn ASR.")

            if self._cancel_event.is_set():
                self.pipeline_cancelled.emit()
                return

            if not cues and not asr_enabled:
                raise RuntimeError(
                    "Không đọc được phụ đề từ video. Vui lòng kiểm tra lại khung OCR trên màn hình xem video."
                )

            self.log_emitted.emit(f"[OCR] Đã chuẩn hóa {len(cues)} câu thoại hợp lệ.")
            self.progress_updated.emit(25.0, f"Đã quét xong {len(cues)} câu phụ đề.")

            # --- BƯỚC 1.5: QUÉT BỔ SUNG GIỌNG NÓI ASR CHO ĐOẠN TIN NHẮN / HỘI THOẠI KHÔNG CÓ SUB ---
            if self.asr_supplement_settings.get("enabled"):
                self.log_emitted.emit("[ASR Bổ sung] Đang quét giọng nói bổ sung cho các đoạn tin nhắn / hội thoại không có phụ đề chữ...")
                supp_cues = self._run_asr_supplement(cues)
                if supp_cues:
                    cues.extend(supp_cues)
                    cues = sorted(cues, key=lambda c: float(c.get("start", 0.0)))
                    from services.subtitle_merge_service import SubtitleMergeService
                    cues = SubtitleMergeService.sanitize_cue_timings(cues)
                    self.log_emitted.emit(f"[ASR Bổ sung] Tổng số câu thoại sau khi hợp nhất: {len(cues)} câu.")

            if self.dual_voice_config:
                # --- BƯỚC 2 (DUAL VOICE): NHẬN DIỆN GIỌNG NÓI & PHÂN VAI NAM/NỮ AI (25% -> 45%) ---
                self.stage_changed.emit("gender", "Đang nhận diện giọng nói & phân vai Nam/Nữ AI...")
                self.progress_updated.emit(26.0, "Đang nhận diện âm học và phân vai nhân vật...")
                self.log_emitted.emit("[Phân vai AI] Bắt đầu phân tích âm học và kịch bản phân vai Nam/Nữ chuẩn (ASR + VAD + OCR)...")
                from services.voice_gender_check_service import VoiceGenderCheckService
                gender_svc = VoiceGenderCheckService()

                def _on_gender_prog(pct_or_msg, maybe_msg=""):
                    if isinstance(pct_or_msg, (int, float)):
                        scaled = 25.0 + (float(pct_or_msg) * 0.20)
                        msg_str = str(maybe_msg) if maybe_msg else "Đang phân vai AI..."
                        self.progress_updated.emit(scaled, msg_str)
                        self.log_emitted.emit(f"[Phân vai AI] {msg_str}")
                    else:
                        self.log_emitted.emit(f"[Phân vai AI] {pct_or_msg}")

                gender_res = gender_svc.run(
                    self.video_path,
                    existing_cues=None,
                    progress=_on_gender_prog,
                    is_cancelled_func=lambda: self._cancel_event.is_set(),
                )
                if self._cancel_event.is_set() or (isinstance(gender_res, dict) and gender_res.get("cancelled")):
                    self.pipeline_cancelled.emit()
                    return
                translated_cues = gender_res.get("rows", [])
                self.progress_updated.emit(45.0, f"Phân vai hoàn tất: {len(translated_cues)} câu thoại.")
            else:
                # --- BƯỚC 2 (SINGLE VOICE): DỊCH PHỤ ĐỀ BẰNG DEEPSEEK AI (25% -> 45%) ---
                self.stage_changed.emit("translate", "Đang dịch kịch bản chuẩn ngữ cảnh (DeepSeek AI)...")
                deepseek_key = str(os.getenv("DEEPSEEK_API_KEY", "")).strip()
                from services.subtitle_translation_service import SubtitleTranslationService

                cues = SubtitleMergeService.merge_incomplete_split_dialogues(cues)
                cues = SubtitleMergeService.sanitize_cue_timings(cues)
                # Đánh số ID cho các câu
                indexed_cues = [
                    {**cue, "id": cue.get("id", idx)}
                    for idx, cue in enumerate(cues, start=1)
                ]

                if deepseek_key:
                    try:
                        from services.deepseek_service import DeepSeekService
                        ds_svc = DeepSeekService(api_key=deepseek_key)
                        sub_trans_svc = SubtitleTranslationService(ds_svc)
                        self.log_emitted.emit("[Dịch thuật] Đang kết nối DeepSeek AI API để dịch theo văn cảnh...")

                        def _on_ds_progress(msg, cur, tot):
                            if self._cancel_event.is_set():
                                return
                            if tot > 0:
                                pct = 25.0 + (cur / tot) * 20.0
                                self.progress_updated.emit(pct, f"DeepSeek AI đang dịch ({cur}/{tot})...")
                            self.log_emitted.emit(f"[DeepSeek] {msg}")

                        translated_cues, _ = sub_trans_svc.translate_with_deepseek(
                            indexed_cues,
                            overwrite_manual=True,
                            progress=_on_ds_progress,
                        )
                        self.log_emitted.emit(f"[Dịch thuật] DeepSeek AI đã dịch thành công {len(translated_cues)} câu.")
                    except Exception as ds_err:
                        self.log_emitted.emit(f"[Dịch thuật] DeepSeek AI thông báo: {ds_err}. Chuyển sang bộ dịch dự phòng...")
                        translated_cues = []

                if self._cancel_event.is_set():
                    self.pipeline_cancelled.emit()
                    return

                if not translated_cues:
                    self.log_emitted.emit("[Dịch thuật] Đang dịch bằng bộ dịch dự phòng...")
                    translated_cues = SubtitleTranslationService().translate_cues(
                        indexed_cues,
                        target_lang="vi",
                        progress=lambda msg: self.log_emitted.emit(f"[Dịch thuật] {msg}"),
                    )

                self.progress_updated.emit(45.0, "Dịch thuật kịch bản hoàn tất.")

        # --- BƯỚC 3 & 4: TẠO GIỌNG ĐỌC AI & XUẤT VIDEO (45% -> 100%) ---
        self.stage_changed.emit("render", "Đang lồng tiếng AI, che chữ Trung và xuất video MP4...")

        CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
        AD_NOISE_KEYWORDS = ("闲置", "二手", "转卖", "私信", "关注", "点击", "下载", "优惠", "进群", "看简介", "主页")

        segments: list[SubtitleSegment] = []

        if self.dual_voice_config:
            m_cfg = self.dual_voice_config.get("male_voice") or {}
            f_cfg = self.dual_voice_config.get("female_voice") or {}
            male_id = str(m_cfg.get("id", "vi-VN-NamMinhNeural")).strip()
            male_name = str(m_cfg.get("name", "Nam")).strip()
            male_provider = str(m_cfg.get("provider", "edge_tts")).strip()
            female_id = str(f_cfg.get("id", "vi-VN-HoaiMyNeural")).strip()
            female_name = str(f_cfg.get("name", "Nữ")).strip()
            female_provider = str(f_cfg.get("provider", "edge_tts")).strip()
            self.log_emitted.emit(f"[Lồng tiếng Phân vai] Giọng Nam: {male_name} | Giọng Nữ: {female_name}")

            for idx, cue in enumerate(translated_cues, start=1):
                start_s = float(cue.get("start", 0.0) or 0.0)
                end_s = float(cue.get("end", 0.0) or 0.0)
                orig_txt = str(cue.get("original_text", "") or cue.get("text", "")).strip()
                trans_txt = str(cue.get("translated_text", "") or cue.get("text", "")).strip()
                final_g = str(cue.get("final_gender", "") or cue.get("gender", "unknown")).lower()
                is_noise = bool(cue.get("is_noise", False))
                if any(kw in orig_txt for kw in AD_NOISE_KEYWORDS) and (not trans_txt or bool(CHINESE_CHAR_RE.search(trans_txt))):
                    is_noise = True
                has_chinese = bool(CHINESE_CHAR_RE.search(trans_txt))
                skip_tts = bool(cue.get("skip_tts", False)) or is_noise or (not trans_txt) or has_chinese or (final_g in ("none", "unknown"))
                effective_trans = "" if (is_noise or not trans_txt) else trans_txt

                if final_g == "female":
                    v_id, v_nm = female_id, female_name
                elif final_g == "male":
                    v_id, v_nm = male_id, male_name
                else:
                    v_id, v_nm = (female_id, female_name) if "nữ" in female_name.lower() else (male_id, male_name)

                c_meta = cue.get("metadata") if isinstance(cue.get("metadata"), dict) else {}
                raw_box = cue.get("ocr_box_index") if cue.get("ocr_box_index") is not None else c_meta.get("ocr_box_index")
                raw_pos = cue.get("position") or c_meta.get("position")
                calc_box = int(raw_box if raw_box is not None else (2 if str(raw_pos or "").lower() == "top" else 1))
                calc_pos = str(raw_pos or ("top" if calc_box == 2 else "bottom")).lower()

                seg = SubtitleSegment(
                    id=idx,
                    start=start_s,
                    end=end_s,
                    text=orig_txt,
                    translated_text=effective_trans,
                    voice_id=v_id,
                    voice_name=v_nm,
                    gender=final_g if final_g in ("male", "female") else "male",
                    final_gender=final_g if final_g in ("male", "female") else "male",
                    is_noise=is_noise,
                    skip_tts=skip_tts,
                    bbox=cue.get("bbox", []) or [],
                    ocr_box_index=calc_box,
                    position=calc_pos,
                )
                segments.append(seg)
        else:
            voice_id = str(self.selected_voice.get("id", "adam1")).strip()
            voice_name = str(self.selected_voice.get("name", voice_id)).strip()
            is_no_voice = (voice_id in ("no_voiceover", "none", "no_voice") or str(self.selected_voice.get("provider", "")).lower() == "none")

            if is_no_voice:
                self.log_emitted.emit("[Chế độ Phụ đề] Người dùng chọn: KHÔNG LỒNG TIẾNG. Bỏ qua hoàn toàn bước sinh giọng đọc AI, giữ 100% âm thanh gốc của video.")
            else:
                self.log_emitted.emit(f"[Lồng tiếng] Áp dụng giọng thuyết minh: {voice_name} (ID: {voice_id})")

            for idx, cue in enumerate(translated_cues, start=1):
                start_s = float(cue.get("start", 0.0) or 0.0)
                end_s = float(cue.get("end", 0.0) or 0.0)
                orig_txt = str(cue.get("text", "") or cue.get("original_text", "") or cue.get("source_text", "")).strip()
                trans_txt = str(cue.get("translated_text", "") or cue.get("translated", "")).strip()

                is_noise = bool(cue.get("is_noise", False))
                if any(kw in orig_txt for kw in AD_NOISE_KEYWORDS) and (not trans_txt or bool(CHINESE_CHAR_RE.search(trans_txt))):
                    is_noise = True

                # Xác định có đọc giọng AI (TTS) hay không:
                has_chinese_in_trans = bool(CHINESE_CHAR_RE.search(trans_txt))
                skip_tts = is_no_voice or is_noise or (not trans_txt) or has_chinese_in_trans

                # Phụ đề Tiếng Việt in lên video (Hardsub):
                # Tuyệt đối không xóa trắng bản dịch khi người dùng chọn 'Không lồng tiếng'!
                # Chỉ bỏ qua phụ đề nếu câu thoại là rác/quảng cáo hoặc không có bản dịch.
                effective_trans = "" if (is_noise or not trans_txt) else trans_txt

                c_meta = cue.get("metadata") if isinstance(cue.get("metadata"), dict) else {}
                raw_box = cue.get("ocr_box_index") if cue.get("ocr_box_index") is not None else c_meta.get("ocr_box_index")
                raw_pos = cue.get("position") or c_meta.get("position")
                calc_box = int(raw_box if raw_box is not None else (2 if str(raw_pos or "").lower() == "top" else 1))
                calc_pos = str(raw_pos or ("top" if calc_box == 2 else "bottom")).lower()

                seg = SubtitleSegment(
                    id=idx,
                    start=start_s,
                    end=end_s,
                    text=orig_txt,
                    translated_text=effective_trans,
                    voice_id="none" if is_no_voice else voice_id,
                    voice_name="None" if is_no_voice else voice_name,
                    gender="none" if is_no_voice else "male",
                    final_gender="none" if is_no_voice else "male",
                    is_noise=is_noise,
                    skip_tts=skip_tts,
                    bbox=cue.get("bbox", []) or [],
                    ocr_box_index=calc_box,
                    position=calc_pos,
                )
                segments.append(seg)

        creator_config = VideoCreatorConfig()
        creator_config.enable_blur_mask = self.enable_blur_mask
        creator_config.mask_mode = self.mask_mode
        creator_config.blur_rect = self.blur_rect
        creator_config.dynamic_mask = self.dynamic_mask if self.dynamic_mask is not None else True
        creator_config.burn_subtitles = self.burn_subtitles
        if self.subtitle_config:
            self.subtitle_config.blur_rect_2 = self.blur_rect_2
            self.subtitle_config.dynamic_mask = creator_config.dynamic_mask
        creator_config.subtitle_config = self.subtitle_config

        is_no_voice_mode = (not self.dual_voice_config and (self.selected_voice.get("id") in ("no_voiceover", "none", "no_voice") or str(self.selected_voice.get("provider", "")).lower() == "none"))
        if is_no_voice_mode:
            orig_vol = float(self.audio_mix_settings.get("original_volume", 1.00))
            creator_config.original_volume = max(0.1, orig_vol) if orig_vol > 0.0 else 1.00
            creator_config.tts_volume = 0.00
            creator_config.duck_volume = 0.00
        else:
            creator_config.original_volume = float(self.audio_mix_settings.get("original_volume", 0.50))
            creator_config.tts_volume = float(self.audio_mix_settings.get("tts_volume", 1.00))
            creator_config.duck_volume = float(self.audio_mix_settings.get("duck_volume", 0.20))

        # Cấu hình Logo Thương hiệu
        if self.brand_logo_settings and isinstance(self.brand_logo_settings, dict):
            creator_config.enable_brand_logo = bool(self.brand_logo_settings.get("enable", False))
            creator_config.brand_logo_path = str(self.brand_logo_settings.get("path", "")).strip()
            creator_config.brand_logo_position = str(self.brand_logo_settings.get("position", "top_left"))
            creator_config.brand_logo_size_percent = int(self.brand_logo_settings.get("size_percent", 15))
            creator_config.brand_logo_opacity = float(self.brand_logo_settings.get("opacity", 1.0))
            creator_config.brand_logo_custom_rect = self.brand_logo_settings.get("custom_rect")

        # Cấu hình Khung viền video trang trí
        if self.frame_border_settings and isinstance(self.frame_border_settings, dict):
            creator_config.enable_frame_border = bool(self.frame_border_settings.get("enable", False))
            creator_config.frame_border_top_path = str(self.frame_border_settings.get("top_path", "") or self.frame_border_settings.get("path", "")).strip()
            creator_config.frame_border_bottom_path = str(self.frame_border_settings.get("bottom_path", "")).strip()
            creator_config.frame_border_path = creator_config.frame_border_top_path or creator_config.frame_border_bottom_path
            creator_config.frame_border_opacity = float(self.frame_border_settings.get("opacity", 1.0))
            creator_config.frame_border_top_height = int(self.frame_border_settings.get("top_height", 0) or 0)
            creator_config.frame_border_bottom_height = int(self.frame_border_settings.get("bottom_height", 0) or 0)

        # Cấu hình Thu phóng video kiểu CapCut (100% - 150%)
        zoom_val = 100
        if hasattr(self, "video_scale_percent") and self.video_scale_percent:
            zoom_val = int(self.video_scale_percent)
        elif self.frame_border_settings and "video_scale_percent" in self.frame_border_settings:
            zoom_val = int(self.frame_border_settings["video_scale_percent"])
        else:
            from PySide6.QtCore import QSettings
            zoom_val = QSettings("TankVideo", "TankVideoStudio").value("branding/video_scale_percent", 100, type=int)
        creator_config.video_scale_percent = zoom_val

        # Cấu hình Hình mờ chữ Watermark
        if self.watermark_settings and isinstance(self.watermark_settings, dict):
            creator_config.enable_watermark = bool(self.watermark_settings.get("enable", False))
            creator_config.watermark_text = str(self.watermark_settings.get("text", "")).strip()
            creator_config.watermark_position = str(self.watermark_settings.get("position", "center"))
            creator_config.watermark_font_size = int(self.watermark_settings.get("font_size", 36))
            creator_config.watermark_opacity = float(self.watermark_settings.get("opacity", 0.35))
            creator_config.watermark_color = str(self.watermark_settings.get("color", "#FFFFFF"))
            creator_config.watermark_custom_rect = self.watermark_settings.get("custom_rect")

        # Cấu hình Xóa Logo & Watermark video gốc (Delogo AI)
        if self.delogo_settings and isinstance(self.delogo_settings, dict):
            creator_config.delogo_bilibili = bool(self.delogo_settings.get("delogo_bilibili", False))
            creator_config.delogo_watermark = bool(self.delogo_settings.get("delogo_watermark", False))
            creator_config.delogo_watermark_rect = self.delogo_settings.get("delogo_watermark_rect")
            creator_config.delogo_watermark_mode = str(self.delogo_settings.get("delogo_watermark_mode", "delogo"))
            if self.delogo_settings.get("delogo_custom"):
                creator_config.delogo_custom_rect = self.delogo_settings.get("delogo_custom_rect")
                creator_config.delogo_custom_mode = str(self.delogo_settings.get("delogo_custom_mode", "boxblur"))

        # Khởi tạo voice mapping config tương ứng với chế độ 2 giọng hoặc 1 giọng
        from app.services.voice_mapping_service import VoiceAssignment, VoiceMappingConfig
        if self.dual_voice_config:
            m_va = VoiceAssignment(
                voice_id=male_id,
                name=male_name,
                provider=male_provider,
                gender="male",
                speed=1.0,
            )
            f_va = VoiceAssignment(
                voice_id=female_id,
                name=female_name,
                provider=female_provider,
                gender="female",
                speed=1.0,
            )
            vm_config = VoiceMappingConfig(
                default_male_voice=m_va,
                default_female_voice=f_va,
                fallback_voice=f_va,
            )
            creator_config.voice_config = vm_config
        else:
            if is_no_voice_mode:
                single_va = VoiceAssignment(
                    voice_id="none",
                    name="None",
                    provider="none",
                    gender="none",
                    speed=1.0,
                )
            else:
                single_va = VoiceAssignment(
                    voice_id=voice_id,
                    name=voice_name,
                    provider=str(self.selected_voice.get("provider", "piper")),
                    gender="male",
                    speed=1.0,
                )
            vm_config = VoiceMappingConfig(
                default_male_voice=single_va,
                default_female_voice=single_va,
                fallback_voice=single_va,
            )
            creator_config.voice_config = vm_config


        def _on_creator_progress(raw_pct: float, message: str):
            scaled = 45.0 + (raw_pct * 0.55)
            self.progress_updated.emit(scaled, message)
            self.log_emitted.emit(f"[Tạo video] {message}")

        request = VideoCreatorRequest(
            original_video_path=self.video_path,
            segments=segments,
            output_video_path=self.output_video_path,
            config=creator_config,
            progress_callback=_on_creator_progress,
            is_cancelled_func=lambda: self._cancel_event.is_set(),
        )

        service = VideoCreatorService()
        result = service.run(request)

        if getattr(result, "status", "") == "cancelled" or self._cancel_event.is_set():
            self.pipeline_cancelled.emit()
            return

        if getattr(result, "status", "") == "failed":
            raise RuntimeError(getattr(result, "error", "") or "Quá trình xuất video thất bại.")

        self.progress_updated.emit(100.0, "Hoàn tất tạo video thành công!")
        final_cues = [s.to_dict() for s in segments]
        self.pipeline_finished.emit(result, final_cues)

    def _run_asr_supplement(self, existing_ocr_cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Quét nhận diện âm thanh bổ sung cho nhiều đoạn tin nhắn hoặc phân cảnh không có chữ phụ đề."""
        if not self.video_path or not os.path.isfile(self.video_path):
            return []

        mode = str(self.asr_supplement_settings.get("mode", "range") or "range").lower()
        raw_ranges = list(self.asr_supplement_settings.get("ranges") or [])
        if not raw_ranges:
            s_s = max(0.0, float(self.asr_supplement_settings.get("start_seconds", 0.0) or 0.0))
            e_s = float(self.asr_supplement_settings.get("end_seconds", 0.0) or 0.0)
            if e_s > s_s:
                raw_ranges = [(s_s, e_s)]
            elif mode == "all":
                raw_ranges = [(0.0, 0.0)]

        if not raw_ranges and mode != "all":
            self.log_emitted.emit("[ASR Bổ sung] Chưa thiết lập khoảng thời gian quét. Bỏ qua.")
            return []

        placement = str(self.asr_supplement_settings.get("placement", "bottom") or "bottom").lower()
        target_box_idx = 2 if placement == "top" else 1
        target_pos = "top" if target_box_idx == 2 else "bottom"
        fallback_rect = list(self.blur_rect_2 or [0.08, 0.09, 0.84, 0.085]) if target_box_idx == 2 else list(self.blur_rect or [0.08, 0.70, 0.84, 0.09])

        scratch_dir = os.path.join(self.workspace_root, "scratch")
        os.makedirs(scratch_dir, exist_ok=True)
        import uuid
        import subprocess
        from app.sensevoice_processor import transcribe_audio
        from runtime_paths import models_path
        from services.subtitle_merge_service import SubtitleMergeService

        sv_dir = str(models_path("sensevoice"))
        if not os.path.isdir(sv_dir):
            self.log_emitted.emit(f"[ASR Bổ sung] Thư mục mô hình SenseVoice không tìm thấy ({sv_dir}). Bỏ qua ASR.")
            return []

        all_collected_cues = []

        for r_idx, (r_start, r_end) in enumerate(raw_ranges, 1):
            if self._cancel_event.is_set():
                break

            is_range_part = (r_end > r_start)
            r_desc = f"Đoạn {r_idx} ({r_start:.1f}s -> {r_end:.1f}s)" if is_range_part else "Toàn bộ video"
            self.log_emitted.emit(f"[ASR Bổ sung] Đang xử lý {r_desc}...")

            temp_wav = os.path.join(scratch_dir, f"asr_supp_{uuid.uuid4().hex[:8]}.wav")
            try:
                ffmpeg_cmd = ["ffmpeg", "-y"]
                if is_range_part:
                    ffmpeg_cmd.extend(["-ss", f"{r_start:.2f}", "-to", f"{r_end:.2f}"])
                ffmpeg_cmd.extend(["-i", self.video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", temp_wav])

                proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if proc.returncode != 0 or not os.path.isfile(temp_wav) or os.path.getsize(temp_wav) < 1000:
                    self.log_emitted.emit(f"[ASR Bổ sung] Không thể trích xuất audio cho {r_desc}.")
                    continue

                raw_asr = transcribe_audio(temp_wav, sv_dir, language="zh") or []
                if not raw_asr:
                    self.log_emitted.emit(f"[ASR Bổ sung] {r_desc}: Không phát hiện thấy giọng nói.")
                    continue

                base_offset = r_start if is_range_part else 0.0
                split_items = []
                for item in raw_asr:
                    a_start = base_offset + float(item.get("start", 0.0))
                    a_end = base_offset + float(item.get("end", 0.0))
                    raw_txt = str(item.get("text", "")).strip()
                    if not raw_txt:
                        continue

                    parts = [p.strip() for p in re.split(r'([。？！?!，,]+)', raw_txt) if p.strip()]
                    sentences = []
                    for idx in range(0, len(parts), 2):
                        sent = parts[idx]
                        if idx + 1 < len(parts):
                            sent += parts[idx + 1]
                        sentences.append(sent)

                    if len(sentences) <= 1 or (a_end - a_start) <= 3.0:
                        split_items.append({"start": round(a_start, 3), "end": round(a_end, 3), "text": raw_txt})
                    else:
                        tot_len = sum(max(1, len(s)) for s in sentences)
                        tot_dur = max(0.5, a_end - a_start)
                        cur_t = a_start
                        for sent in sentences:
                            s_dur = round(tot_dur * (len(sent) / tot_len), 3)
                            s_end = round(cur_t + s_dur, 3)
                            if sent.strip():
                                split_items.append({"start": cur_t, "end": s_end, "text": sent.strip()})
                            cur_t = s_end

                # Lọc trùng với OCR và các câu ASR đã thêm
                for item in split_items:
                    i_txt = item["text"]
                    i_start = item["start"]
                    i_end = item["end"]

                    is_dup = False
                    for ocr_c in existing_ocr_cues + all_collected_cues:
                        o_txt = str(ocr_c.get("text", "") or ocr_c.get("original_text", "")).strip()
                        o_start = float(ocr_c.get("start", 0.0))
                        o_end = float(ocr_c.get("end", 0.0))

                        time_overlap = (max(0.0, min(i_end, o_end) - max(i_start, o_start)) > 0.3) or abs(i_start - o_start) < 2.0
                        if time_overlap and SubtitleMergeService.are_cues_near_duplicate(i_txt, o_txt, min_similarity=0.75):
                            is_dup = True
                            break
                        if time_overlap and ((len(i_txt) >= 4 and i_txt in o_txt) or (len(o_txt) >= 4 and o_txt in i_txt)):
                            is_dup = True
                            break

                    if not is_dup:
                        all_collected_cues.append({
                            "start": i_start,
                            "end": i_end,
                            "text": i_txt,
                            "original_text": i_txt,
                            "ocr_box_index": target_box_idx,
                            "position": target_pos,
                            "bbox": fallback_rect,
                            "source": "asr_supplement",
                        })

            except Exception as e:
                self.log_emitted.emit(f"[ASR Bổ sung] Lỗi xử lý {r_desc}: {e}")
            finally:
                if os.path.isfile(temp_wav):
                    try:
                        os.remove(temp_wav)
                    except Exception:
                        pass

        all_collected_cues = sorted(all_collected_cues, key=lambda c: float(c.get("start", 0.0)))
        box_title = "Khung 2 (Đỉnh)" if target_box_idx == 2 else "Khung 1 (Đáy)"
        self.log_emitted.emit(f"[ASR Bổ sung] Đã lọc ra tổng cộng {len(all_collected_cues)} câu thoại bổ sung độc lập từ {len(raw_ranges)} đoạn, gán vị trí: {box_title}.")
        return all_collected_cues


class SingleVoiceProgressDialog(QDialog):
    """Hộp thoại trực quan hiển thị toàn bộ 4 bước tạo video 1 giọng."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        workspace_root: str = "",
        video_path: str = "",
        selected_voice: dict[str, str],
        audio_mix_settings: dict[str, float],
        enable_blur_mask: bool = True,
        mask_mode: str = "box",
        blur_rect: tuple[float, float, float, float] | None = None,
        burn_subtitles: bool = True,
        existing_cues: list[dict[str, Any]] | None = None,
        output_video_path: str = "",
        subtitle_config: SubtitleRenderConfig | None = None,
        brand_logo_settings: dict[str, Any] | None = None,
        frame_border_settings: dict[str, Any] | None = None,
        watermark_settings: dict[str, Any] | None = None,
        delogo_settings: dict[str, Any] | None = None,
        dual_voice_config: dict[str, Any] | None = None,
        approved_cues: list[dict[str, Any]] | None = None,
        blur_rect_2: tuple[float, float, float, float] | None = None,
        is_dual_ocr: bool = False,
        dynamic_mask: bool = True,
        asr_supplement_settings: dict[str, Any] | None = None,
    ):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.selected_voice = selected_voice
        self.audio_mix_settings = audio_mix_settings
        self.enable_blur_mask = enable_blur_mask
        self.subtitle_config = subtitle_config or SubtitleRenderConfig()
        self.mask_mode = str(mask_mode or getattr(self.subtitle_config, "mask_mode", "box") or "box").strip().lower()
        self.blur_rect = blur_rect
        self.blur_rect_2 = blur_rect_2
        self.is_dual_ocr = is_dual_ocr
        self.dynamic_mask = dynamic_mask
        self.burn_subtitles = burn_subtitles
        self.existing_cues = existing_cues
        self.asr_supplement_settings = asr_supplement_settings or {}
        self.output_video_path = output_video_path
        self.brand_logo_settings = brand_logo_settings or {}
        self.frame_border_settings = frame_border_settings or {}
        self.watermark_settings = watermark_settings or {}
        self.delogo_settings = delogo_settings or {}
        self.dual_voice_config = dual_voice_config
        self.approved_cues = approved_cues
        self._close_requested = False

        self._worker: SingleVoicePipelineWorker | None = None
        self._last_result: VideoCreatorResult | None = None
        self._translated_cues: list[dict[str, Any]] = []

        if self.is_dual_ocr:
            self.setWindowTitle("🎬 Tạo video 2 Khung OCR (1 Giọng) - Mặt nạ che mờ động")
        else:
            self.setWindowTitle("Tiến trình tạo video (1 giọng) - Toàn bộ quy trình")
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
        header_title = QLabel("⚡ TIẾN TRÌNH TẠO VIDEO (1 GIỌNG) - TANKVIDEO")
        h_font = QFont()
        h_font.setPointSize(12)
        h_font.setBold(True)
        header_title.setFont(h_font)
        header_title.setStyleSheet("color: #ff7b1a;")
        root_layout.addWidget(header_title)

        video_name = os.path.basename(self.video_path) if self.video_path else "Video"
        v_name = self.selected_voice.get("name", "Mặc định")
        lbl_info = QLabel(f"🎥 Video: {video_name} | 🎙️ Giọng thuyết minh: {v_name}")
        lbl_info.setStyleSheet("color: #8b949e; font-size: 11px;")
        root_layout.addWidget(lbl_info)

        # 3 Step Visual Indicators
        steps_frame = QFrame()
        steps_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 6px;")
        steps_layout = QHBoxLayout(steps_frame)
        steps_layout.setContentsMargins(10, 8, 10, 8)

        self.lbl_step1 = QLabel("1. 📄 Quét OCR & DeepSeek\n[Đang chạy...]")
        self.lbl_step1.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")
        self.lbl_step1.setAlignment(Qt.AlignCenter)

        self.lbl_step2 = QLabel("2. 🎙️ Lồng tiếng AI (1 giọng)\n[Chờ xử lý]")
        self.lbl_step2.setStyleSheet("color: #8b949e; font-weight: bold; font-size: 11px;")
        self.lbl_step2.setAlignment(Qt.AlignCenter)

        self.lbl_step3 = QLabel("3. 🎞️ Che chữ & Xuất MP4\n[Chờ xử lý]")
        self.lbl_step3.setStyleSheet("color: #8b949e; font-weight: bold; font-size: 11px;")
        self.lbl_step3.setAlignment(Qt.AlignCenter)

        steps_layout.addWidget(self.lbl_step1, 1)
        steps_layout.addWidget(self.lbl_step2, 1)
        steps_layout.addWidget(self.lbl_step3, 1)
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
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f6feb, stop:1 #238636);
                border-radius: 5px;
            }
        """)
        root_layout.addWidget(self.progress_bar)

        # Status Label
        self.lbl_status = QLabel("Khởi tạo tiến trình...")
        self.lbl_status.setStyleSheet("color: #e6edf3; font-weight: bold; font-size: 12px;")
        root_layout.addWidget(self.lbl_status)

        # Realtime Log Box
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

        self.btn_open_folder = QPushButton("📂 Mở thư mục video")
        self.btn_open_folder.setVisible(False)
        self.btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        btn_layout.addWidget(self.btn_open_folder)

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
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6a00, stop:1 #e04800);
                color: #ffffff; border: 1px solid #ff8833;
            }
        """)

    def _start_worker(self):
        self._worker = SingleVoicePipelineWorker(
            workspace_root=self.workspace_root,
            video_path=self.video_path,
            selected_voice=self.selected_voice,
            audio_mix_settings=self.audio_mix_settings,
            enable_blur_mask=self.enable_blur_mask,
            mask_mode=self.mask_mode,
            blur_rect=self.blur_rect,
            burn_subtitles=self.burn_subtitles,
            existing_cues=self.existing_cues,
            output_video_path=self.output_video_path,
            subtitle_config=self.subtitle_config,
            brand_logo_settings=self.brand_logo_settings,
            frame_border_settings=self.frame_border_settings,
            watermark_settings=self.watermark_settings,
            delogo_settings=self.delogo_settings,
            dual_voice_config=getattr(self, "dual_voice_config", None),
            approved_cues=getattr(self, "approved_cues", None),
            blur_rect_2=self.blur_rect_2,
            is_dual_ocr=self.is_dual_ocr,
            dynamic_mask=self.dynamic_mask,
            asr_supplement_settings=self.asr_supplement_settings,
            parent=None,
        )

        self._worker.progress_updated.connect(self._on_progress_updated)
        self._worker.stage_changed.connect(self._on_stage_changed)
        self._worker.log_emitted.connect(self._on_log_emitted)
        self._worker.pipeline_finished.connect(self._on_pipeline_finished)
        self._worker.pipeline_error.connect(self._on_pipeline_error)
        self._worker.pipeline_cancelled.connect(self._on_pipeline_cancelled)
        self._worker.finished.connect(self._on_worker_finished)

        self._start_time = time.time()
        self._worker.start(QThread.Priority.HighestPriority)

    def _on_progress_updated(self, pct: float, message: str):
        val = max(0, min(100, int(round(pct))))
        self.progress_bar.setValue(val)
        self.lbl_status.setText(f"{message} ({val}%)")

    def _on_stage_changed(self, stage: str, message: str):
        if stage == "ocr":
            self.lbl_step1.setText("1. 📄 Quét phụ đề OCR\n[🔄 Đang chạy]")
            self.lbl_step1.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")
        elif stage == "translate":
            self.lbl_step1.setText("1. 📄 Dịch DeepSeek AI\n[🔄 Đang dịch]")
            self.lbl_step1.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")
        elif stage == "gender":
            self.lbl_step1.setText("1. 📄 OCR & Phân vai AI\n[🔄 Đang phân vai]")
            self.lbl_step1.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")
        elif stage == "render":
            step1_lbl = "1. 📄 OCR & Phân vai AI" if getattr(self, "dual_voice_config", None) else "1. 📄 OCR & DeepSeek"
            self.lbl_step1.setText(f"{step1_lbl}\n[✅ Đã xong]")
            self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 11px;")
            step2_lbl = "2. 🎙️ Lồng tiếng Phân vai" if getattr(self, "dual_voice_config", None) else "2. 🎙️ Lồng tiếng AI (1 giọng)"
            self.lbl_step2.setText(f"{step2_lbl}\n[🔄 Đang tạo]")
            self.lbl_step2.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")
            self.lbl_step3.setText("3. 🎞️ Che chữ & Xuất MP4\n[🔄 Đang xử lý]")
            self.lbl_step3.setStyleSheet("color: #ff7b1a; font-weight: bold; font-size: 11px;")

    def _on_log_emitted(self, msg: str):
        self.log_box.append(msg)

    def _on_pipeline_finished(self, result: VideoCreatorResult, translated_cues: list):
        self._last_result = result
        self._translated_cues = translated_cues

        elapsed = max(0.1, time.time() - getattr(self, "_start_time", time.time()))
        s_int = int(round(elapsed))
        if s_int < 60:
            dur_str = f"{s_int}s"
            full_str = f"{s_int} giây"
        else:
            m = s_int // 60
            rem = s_int % 60
            dur_str = f"{m}m {rem:02d}s"
            full_str = f"{m} phút {rem} giây"

        if self.video_path:
            norm_v = os.path.normpath(self.video_path)
            parent_gui = self.parent() if hasattr(self, "parent") else None
            if parent_gui:
                if hasattr(parent_gui, "_video_durations"):
                    parent_gui._video_durations[norm_v] = elapsed
                if hasattr(parent_gui, "_video_output_paths"):
                    parent_gui._video_output_paths[norm_v] = self.output_video_path
                if hasattr(parent_gui, "_video_translated_cues"):
                    parent_gui._video_translated_cues[norm_v] = translated_cues
                self._sync_to_parent_gui(parent_gui, translated_cues)

        step1_lbl = "1. 📄 OCR & Phân vai AI" if getattr(self, "dual_voice_config", None) else "1. 📄 OCR & DeepSeek"
        self.lbl_step1.setText(f"{step1_lbl}\n[✅ Xong]")
        self.lbl_step1.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 11px;")
        step2_lbl = "2. 🎙️ Lồng tiếng Phân vai" if getattr(self, "dual_voice_config", None) else "2. 🎙️ Lồng tiếng AI (1 giọng)"
        self.lbl_step2.setText(f"{step2_lbl}\n[✅ Xong]")
        self.lbl_step2.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 11px;")
        self.lbl_step3.setText("3. 🎞️ Che chữ & Xuất MP4\n[✅ Hoàn thành]")
        self.lbl_step3.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 11px;")

        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"🎉 Đã tạo video thành công trong {dur_str}!")
        self.lbl_status.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 13px;")

        self.log_box.append("\n" + "=" * 50)
        self.log_box.append(f"⏱️ TỔNG THỜI GIAN HOÀN THÀNH: {full_str} ({elapsed:.1f}s)")
        self.log_box.append(f"⚡ HIỆU SUẤT XỬ LÝ: Hoàn tất xuất video MP4 {os.path.basename(self.video_path)}")
        self.log_box.append("=" * 50)

        self.btn_cancel.setVisible(False)
        self.btn_open_folder.setVisible(True)
        self.btn_close.setEnabled(True)

    def _on_pipeline_error(self, err_msg: str):
        self.lbl_status.setText(f"❌ Thất bại: {err_msg}")
        self.lbl_status.setStyleSheet("color: #f85149; font-weight: bold; font-size: 12px;")
        self.btn_cancel.setVisible(False)
        self.btn_close.setEnabled(True)
        QMessageBox.critical(self, "Lỗi tạo video", f"Không thể hoàn thành quy trình tạo video:\n\n{err_msg}")

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            ans = QMessageBox.question(
                self,
                "Hủy tiến trình",
                "Bạn có chắc muốn hủy tiến trình tạo video này không?",
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

    def _on_pipeline_cancelled(self):
        self.lbl_status.setText("⏹️ Đã dừng và hủy tiến trình tạo video.")
        self.lbl_status.setStyleSheet("color: #d29922; font-weight: bold; font-size: 12px;")
        self.log_box.append("\n" + "=" * 50)
        self.log_box.append("⏹️ TIẾN TRÌNH TẠO VIDEO ĐÃ ĐƯỢC DỪNG VÀ HỦY AN TOÀN.")
        self.log_box.append("=" * 50)
        self.btn_cancel.setVisible(False)
        self.btn_close.setEnabled(True)
        if getattr(self, "_close_requested", False):
            super().reject()

    def _on_worker_finished(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if getattr(self, "_close_requested", False):
            super().reject()

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

    def _on_open_folder_clicked(self):
        out_path = self.output_video_path
        if self._last_result and getattr(self._last_result, "output_video_path", ""):
            out_path = self._last_result.output_video_path

        target_dir = os.path.dirname(os.path.abspath(out_path)) if out_path else os.getcwd()
        try:
            if os.name == "nt":
                if os.path.isfile(out_path):
                    subprocess.run(["explorer", "/select,", os.path.abspath(out_path)])
                else:
                    os.startfile(target_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Lỗi mở thư mục", str(exc))

    def _sync_to_parent_gui(self, parent_gui, translated_cues: list):
        """Đồng bộ danh sách phụ đề tiếng Việt và tiếng Trung lên giao diện chính."""
        try:
            trans_segs = []
            base_segs = []
            for c in translated_cues:
                if not isinstance(c, dict):
                    continue
                s_s = float(c.get("start", 0.0))
                e_s = float(c.get("end", 0.0))
                t_txt = str(c.get("translated_text", "") or c.get("translated", "")).strip()
                o_txt = str(c.get("source_text", "") or c.get("original", "") or c.get("text", "")).strip()

                trans_segs.append({
                    "start": s_s,
                    "end": e_s,
                    "text": t_txt,
                    "translated": t_txt,
                    "translated_text": t_txt,
                    "original": o_txt,
                    "source_text": o_txt,
                })
                base_segs.append({
                    "start": s_s,
                    "end": e_s,
                    "text": o_txt,
                    "original": o_txt,
                    "source_text": o_txt,
                })

            parent_gui.current_translated_segments = trans_segs
            if base_segs:
                parent_gui.current_segments = base_segs
            parent_gui._pipeline_synced_segment_lists = (trans_segs, base_segs)

            if hasattr(parent_gui, "timeline") and hasattr(parent_gui.timeline, "set_segments"):
                parent_gui.timeline.set_segments(trans_segs)

            if hasattr(parent_gui, "subtitle_list_view") and hasattr(parent_gui.subtitle_list_view, "refresh_segments"):
                parent_gui.subtitle_list_view.refresh_segments()

            if hasattr(parent_gui, "toggle_ocr_overlay_visibility"):
                parent_gui.toggle_ocr_overlay_visibility(False)

            if getattr(self, "burn_subtitles", False):
                parent_gui._preview_video_has_burned_subtitles = True
                if hasattr(parent_gui, "video_view") and hasattr(parent_gui.video_view, "subtitle_item"):
                    parent_gui.video_view.subtitle_item.set_text("")
                    parent_gui.video_view.subtitle_item.hide()
        except Exception:
            pass
