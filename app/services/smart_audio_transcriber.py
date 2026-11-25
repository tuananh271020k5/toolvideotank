"""Smart Audio Transcriber Service.

Thực thi trọn vẹn 3 giải pháp tích hợp:
1. Bóc tách lời thoại từ âm thanh bằng SenseVoice / Faster-Whisper siêu tốc.
2. Bộ lọc Blacklist chống dính Watermark và từ khóa rác (阿怖, 故事纯属虚构, 请勿模仿...).
3. Tự động nhận diện phân loại vị trí câu thoại (Trên đỉnh Top vs Dưới đáy Bottom).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Any, Callable

from app.models.subtitle_segment import SubtitleSegment
from app.runtime_paths import bin_path, models_path

logger = logging.getLogger(__name__)

DEFAULT_BLACKLIST = [
    "阿怖",
    "故事纯属虚构",
    "无不良引导",
    "请勿模仿",
    "关注",
    "点赞",
    "转发",
    "抖音",
    "快手",
    "投币",
    "收藏",
]


class SmartAudioTranscriber:
    """Service bóc tách lời thoại âm thanh và lọc sạch watermark."""

    def __init__(self, workspace_root: str = ""):
        self.workspace_root = workspace_root or os.getcwd()

    def extract_audio(self, video_path: str, output_wav_path: str) -> bool:
        """Trích xuất âm thanh từ video sang định dạng WAV 16kHz Mono PCM."""
        if not video_path or not os.path.isfile(video_path):
            logger.error("Video path not found: %s", video_path)
            return False

        os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)
        ffmpeg_bin = os.path.join(bin_path(), "ffmpeg", "ffmpeg.exe")
        if not os.path.isfile(ffmpeg_bin):
            ffmpeg_bin = "ffmpeg"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            output_wav_path,
        ]

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                startupinfo=startupinfo,
                check=False,
            )
            return res.returncode == 0 and os.path.isfile(output_wav_path)
        except Exception as exc:
            logger.error("Extract audio failed: %s", exc)
            return False

    def transcribe_audio(
        self,
        audio_path: str,
        engine: str = "sensevoice",
        source_language: str = "zh",
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> list[dict]:
        """Bóc tách lời thoại bằng SenseVoice hoặc Faster-Whisper."""
        if progress_cb:
            progress_cb(15.0, f"Đang bóc tách giọng nói bằng {engine.upper()}...")

        results: list[dict] = []
        engine_clean = str(engine or "sensevoice").strip().lower()

        if engine_clean == "sensevoice":
            try:
                from app.sensevoice_processor import transcribe_audio as sv_transcribe
                sv_dir = models_path("sensevoice")
                if not os.path.isdir(sv_dir):
                    fallback_sv = os.path.join(self.workspace_root, "models", "sensevoice")
                    if os.path.isdir(fallback_sv):
                        sv_dir = fallback_sv

                logger.info("Running SenseVoice from model dir: %s", sv_dir)
                raw_segs = sv_transcribe(audio_path, sv_dir, language=source_language)
                for s in raw_segs:
                    txt = str(s.get("text", "")).strip()
                    if txt:
                        results.append({
                            "start": float(s.get("start", 0.0)),
                            "end": float(s.get("end", 0.0)),
                            "text": txt,
                        })
            except Exception as exc:
                logger.warning("SenseVoice failed (%s), fallback to Whisper...", exc)
                engine_clean = "whisper"

        if engine_clean == "whisper" or not results:
            try:
                from app.whisper_processor import transcribe_audio as wh_transcribe
                wh_dir = models_path("whisper")
                raw_segs = wh_transcribe(
                    audio_path,
                    wh_dir,
                    language=source_language,
                    task="transcribe",
                )
                for s in raw_segs:
                    txt = str(s.get("text", "")).strip()
                    if txt:
                        results.append({
                            "start": float(s.get("start", 0.0)),
                            "end": float(s.get("end", 0.0)),
                            "text": txt,
                        })
            except Exception as exc:
                logger.error("Whisper transcription failed: %s", exc)

        return results

    def filter_blacklist_and_clean(
        self,
        raw_segments: list[dict],
        blacklist_keywords: list[str] | None = None,
    ) -> list[dict]:
        """Giải pháp 3: Lọc bỏ toàn bộ từ khóa rác & watermark (阿怖, 故事纯属虚构, 请勿模仿...)."""
        blacklist = blacklist_keywords if blacklist_keywords is not None else DEFAULT_BLACKLIST
        # Làm sạch và loại bỏ các chuỗi rỗng
        clean_bl = [kw.strip() for kw in blacklist if kw.strip()]

        cleaned_segments: list[dict] = []
        for seg in raw_segments:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue

            cleaned_text = text
            for kw in clean_bl:
                cleaned_text = cleaned_text.replace(kw, "")

            # Loại bỏ ký tự thừa hoặc ký tự đặc biệt lặp lại
            cleaned_text = re.sub(r"[\s\-_—·•,，。！？!?:：@#%&*]+", " ", cleaned_text).strip()

            # Nếu câu không chứa chữ cái hoặc chữ Hán nào (chỉ toàn ký tự rác) hoặc rỗng
            has_letters = bool(re.search(r"[\w\u4e00-\u9fff]", cleaned_text))
            if not cleaned_text or not has_letters:
                logger.info("Filtered out watermark segment: '%s' (was '%s')", cleaned_text, text)
                continue

            seg_copy = dict(seg)
            seg_copy["text"] = cleaned_text
            seg_copy["raw_text"] = text
            cleaned_segments.append(seg_copy)

        return cleaned_segments

    def detect_segment_positions(
        self,
        video_path: str,
        segments: list[dict],
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> list[dict]:
        """Tự động phát hiện vị trí câu thoại nằm ở Trên đỉnh (Top) hay Dưới đáy (Bottom)."""
        if not segments or not os.path.isfile(video_path):
            for s in segments:
                s.setdefault("position", "bottom")
            return segments

        if progress_cb:
            progress_cb(35.0, "Đang phân tích vị trí câu thoại (Trên đỉnh vs Dưới đáy)...")

        try:
            import cv2
            from app.engines.ocr_adapter import OcrAdapter
            ocr = OcrAdapter()
        except Exception as exc:
            logger.warning("OCR position detection unavailable: %s", exc)
            for s in segments:
                s.setdefault("position", "bottom")
            return segments

        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                for s in segments:
                    s.setdefault("position", "bottom")
                return segments

            vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1080
            vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1920
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0

            for idx, seg in enumerate(segments):
                mid_t = (float(seg.get("start", 0.0)) + float(seg.get("end", 0.0))) / 2.0
                frame_idx = max(0, int(mid_t * fps))
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    seg["position"] = "bottom"
                    continue

                # Cắt 2 vùng tiềm năng: Top (10% - 30%) và Bottom (65% - 90%)
                # Đồng thời bỏ mép trái 12% để tránh dính chữ dọc cảnh báo
                x_start = int(vid_w * 0.12)
                top_roi = frame[int(vid_h * 0.08) : int(vid_h * 0.32), x_start:]
                bot_roi = frame[int(vid_h * 0.65) : int(vid_h * 0.92), x_start:]

                # Quét nhanh vùng Top và Bottom
                top_boxes = ocr.detect_text_boxes(top_roi) if hasattr(ocr, "detect_text_boxes") else []
                bot_boxes = ocr.detect_text_boxes(bot_roi) if hasattr(ocr, "detect_text_boxes") else []

                # Nếu không có detect_text_boxes, dùng detect mặc định
                if not top_boxes and not bot_boxes:
                    try:
                        res_top = ocr.detect(top_roi) if hasattr(ocr, "detect") else []
                        res_bot = ocr.detect(bot_roi) if hasattr(ocr, "detect") else []
                        top_count = len(res_top) if res_top else 0
                        bot_count = len(res_bot) if res_bot else 0
                    except Exception:
                        top_count, bot_count = 0, 1
                else:
                    top_count = len(top_boxes)
                    bot_count = len(bot_boxes)

                # Quyết định vị trí
                if top_count > bot_count:
                    seg["position"] = "top"
                else:
                    seg["position"] = "bottom"

            cap.release()
        except Exception as exc:
            logger.warning("Error during segment position detection: %s", exc)
            for s in segments:
                s.setdefault("position", "bottom")

        return segments

    def classify_genders(
        self,
        audio_path: str,
        segments: list[dict],
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> list[dict]:
        """Tự động phân vai Đa Giọng (Nam / Nữ) cho từng câu thoại từ âm thanh thực tế."""
        if not segments or not audio_path or not os.path.isfile(audio_path):
            for s in segments:
                s.setdefault("speaker", "male")
            return segments

        if progress_cb:
            progress_cb(40.0, "Đang phân tích âm học giọng nói để phân vai Nam / Nữ...")

        try:
            from app.services.gender_service import GenderService
            gender_svc = GenderService()
            cues_to_detect = [
                {
                    "id": i + 1,
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", 0.0)),
                    "text": s.get("text", ""),
                }
                for i, s in enumerate(segments)
            ]
            detected = gender_svc.detect_cues(audio_path, cues_to_detect)
            if detected:
                for i, d in enumerate(detected):
                    if i < len(segments):
                        g = str(d.get("gender", "male")).lower()
                        segments[i]["speaker"] = "female" if ("female" in g or "nữ" in g or "nu" in g) else "male"
                logger.info("Classified %d segments with GenderService AI", len(segments))
                return segments
        except Exception as exc:
            logger.info("GenderService AI notice (%s), using acoustic acoustic pitch fallback...", exc)

        # Fallback bằng năng lượng tần số âm học Zero Crossing Rate (ZCR)
        try:
            import soundfile as sf
            import numpy as np

            data, sr = sf.read(audio_path)
            if data.ndim > 1:
                data = data.mean(axis=1)

            for s in segments:
                idx1 = max(0, int(float(s.get("start", 0.0)) * sr))
                idx2 = min(len(data), int(float(s.get("end", 0.0)) * sr))
                chunk = data[idx1:idx2]
                if len(chunk) > sr * 0.15:
                    zcr = float(np.mean(np.abs(np.diff(np.sign(chunk))))) / 2.0
                    # Tần số cao hơn (ZCR > 0.075) thường là giọng Nữ
                    s["speaker"] = "female" if zcr > 0.075 else "male"
                else:
                    s["speaker"] = "male"
            logger.info("Classified %d segments using acoustic pitch fallback", len(segments))
        except Exception as e:
            logger.warning("Acoustic pitch classification error: %s", e)
            for s in segments:
                s.setdefault("speaker", "male")

        return segments

    def translate_segments(
        self,
        segments: list[dict],
        target_language: str = "vi",
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> list[SubtitleSegment]:
        """Dịch lời thoại sang tiếng Việt và đóng gói thành SubtitleSegment."""
        if not segments:
            return []

        if progress_cb:
            progress_cb(50.0, "Đang dịch lời thoại sang Tiếng Việt...")

        from app.translation.orchestrator import TranslationOrchestrator
        orchestrator = TranslationOrchestrator()

        res = orchestrator.translate_segments(
            segments=segments,
            src_lang="zh-Hans",
            target_lang=target_language,
            enable_polish=True,
        )

        translated_segments = res.segments if (res and res.success and res.segments) else []

        subtitle_segments: list[SubtitleSegment] = []
        for i, s in enumerate(segments):
            vi_text = ""
            if i < len(translated_segments):
                vi_text = str(translated_segments[i].get("text", "") or "").strip()
            if not vi_text:
                vi_text = str(s.get("translated_text", "") or s.get("text", "")).strip()

            speaker_raw = str(s.get("speaker") or s.get("gender") or "male").lower().strip()
            gender_val = "female" if ("female" in speaker_raw or "nữ" in speaker_raw or "nu" in speaker_raw) else "male"
            spk_label = "Nữ" if gender_val == "female" else "Nam"

            sub = SubtitleSegment(
                id=i + 1,
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=str(s.get("text", "")).strip(),
                translated_text=vi_text,
                speaker_label=spk_label,
                gender=gender_val,
                final_gender=gender_val,
                decision_mode="raw_audio",
                status="ok",
            )
            # Gán vị trí "top" hoặc "bottom" và speaker compatibility
            setattr(sub, "speaker", gender_val)
            setattr(sub, "position", str(s.get("position", "bottom")))
            subtitle_segments.append(sub)

        return subtitle_segments
