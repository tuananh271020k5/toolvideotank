"""Speech-driven subtitle fusion (ASR + VAD + OCR), CapCut-style.

Goal: every spoken passage gets a subtitle cue, cue timing follows the real
voice, and pauses between cues are preserved.

Pipeline
--------
1. ``faster-whisper`` ASR (word timestamps) runs ONCE on the extracted audio.
   Hardware aware: NVIDIA/CUDA -> ``large-v3`` (fallback medium/small),
   CPU -> a moderate model (user choice or ``small``) with int8.
2. Silero VAD (sherpa-onnx) gives speech intervals; when unavailable, speech
   intervals are derived from the ASR words.
3. Every OCR cue (text read from the burned-in subtitle) is re-timed with the
   ASR words spoken inside its on-screen window (or snapped to VAD).
4. Spoken words that no OCR cue claimed form "orphan" passages.  The subtitle
   region is re-scanned densely for that window; when OCR still finds
   nothing, the ASR text is used.
5. Final audit: VAD speech still not covered by any cue is re-scanned with
   OCR, then a targeted ASR pass; anything still empty is reported.

The module never raises on partial failures: if ASR cannot run it degrades
to VAD-only re-timing, and if everything fails the caller falls back to the
legacy fusion path.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import threading
import time
import wave
from bisect import bisect_right
from difflib import SequenceMatcher
from typing import Any

import numpy as np

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")
_END_PUNCT = set("。！？!?…")
_STRIP_PUNCT = " \t\r\n，。！？、,.!?;；:：…~～\"'“”‘’「」『』()（）[]【】-—_"
# Typical Whisper hallucinations on music / silence for Chinese audio.
_HALLUCINATION_RE = re.compile(
    r"(字幕[由制作提供志愿者]|字幕组|订阅|訂閱|点赞|點贊|转发|轉發|打赏|打賞|明镜|明鏡|"
    r"感谢观看|感謝觀看|谢谢观看|謝謝觀看|请不吝|請不吝|中文字幕|优优独播|優優獨播|"
    r"Amara|subtitles? by|小铃铛|小鈴鐺)",
    re.IGNORECASE,
)

FUSION_VERSION = "speech-fusion-v2"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default


def _cjk(text: str) -> str:
    return "".join(_CJK_RE.findall(str(text or "")))


def _normalized(text: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(text or "")).lower()


def _similarity(left: str, right: str) -> float:
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _join_tokens(tokens: list[str]) -> str:
    out = ""
    for token in tokens:
        token = str(token or "").strip()
        if not token:
            continue
        if out and re.match(r"[A-Za-z0-9]", token[0]) and re.match(r"[A-Za-z0-9]", out[-1]):
            out += " "
        out += token
    return out.strip(_STRIP_PUNCT)


_T2S = None


def _to_simplified(text: str) -> str:
    """Convert Traditional -> Simplified when OpenCC is installed (optional)."""
    global _T2S
    if _T2S is None:
        _T2S = False
        try:
            from opencc import OpenCC  # type: ignore
            for config in ("t2s", "t2s.json"):
                try:
                    _T2S = OpenCC(config)
                    break
                except Exception:
                    continue
        except Exception:
            _T2S = False
    if _T2S:
        try:
            return _T2S.convert(text)
        except Exception:
            pass
    return text


def _merge_intervals(intervals: list[tuple[float, float]], max_gap: float) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted((float(s), float(e)) for s, e in intervals if float(e) > float(s)):
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(base: tuple[float, float], cover: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [base]
    for cs, ce in cover:
        next_pieces = []
        for ps, pe in pieces:
            if ce <= ps or cs >= pe:
                next_pieces.append((ps, pe))
                continue
            if cs > ps:
                next_pieces.append((ps, cs))
            if ce < pe:
                next_pieces.append((ce, pe))
        pieces = next_pieces
    return pieces


# ---------------------------------------------------------------------------
# Audio IO
# ---------------------------------------------------------------------------
def _load_audio_16k(audio_path: str) -> np.ndarray | None:
    """Load the pipeline's 16 kHz mono PCM wav as float32 (no extra deps)."""
    try:
        with wave.open(audio_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            raw = wav_file.readframes(wav_file.getnframes())
        if sample_width != 2 or sample_rate != 16000:
            return None
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        return audio
    except Exception as exc:
        print(f"[Speech Fusion] Cannot read audio for VAD: {exc}")
        return None


def _write_clip_wav(audio: np.ndarray, start: float, end: float) -> str:
    s = max(0, int(start * 16000))
    e = min(len(audio), int(end * 16000))
    clip = np.clip(audio[s:e], -1.0, 1.0)
    fd, path = tempfile.mkstemp(prefix="capcap_fusion_", suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((clip * 32767.0).astype(np.int16).tobytes())
    return path


def detect_speech_intervals(audio: np.ndarray | None) -> list[tuple[float, float]]:
    """Silero VAD speech intervals, merged so short breaths do not split them."""
    if audio is None or len(audio) == 0:
        return []
    try:
        try:
            from vad_processor import get_speech_segments  # app/ on sys.path
        except ImportError:
            from app.vad_processor import get_speech_segments  # repo root on sys.path
        raw = get_speech_segments(audio, sr=16000)
    except Exception as exc:
        print(f"[Speech Fusion] Silero VAD unavailable ({exc}); using ASR words as speech evidence.")
        return []
    intervals = [(float(seg["start"]), float(seg["end"])) for seg in raw or []]
    merged = _merge_intervals(intervals, max_gap=0.25)
    return [(s, e) for s, e in merged if e - s >= 0.15]


# ---------------------------------------------------------------------------
# ASR (faster-whisper), hardware aware
# ---------------------------------------------------------------------------
def _normalize_model_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    base = os.path.basename(value.rstrip("/\\"))
    if base.lower().startswith("ggml-"):
        base = base[5:]
    if base.lower().endswith(".bin"):
        base = base[:-4]
    if base.startswith("models--Systran--faster-whisper-"):
        base = base[len("models--Systran--faster-whisper-"):]
    return base.lower()


def _model_available_locally(name: str) -> bool:
    import whisper_processor as wp
    if wp._cached_model_snapshot(name):
        return True
    local_dir = wp.models_path("faster_whisper", name)
    return os.path.isfile(os.path.join(local_dir, "model.bin"))


def _ensure_model(name: str) -> bool:
    """Make sure a faster-whisper model is on disk (download without timeout)."""
    import whisper_processor as wp
    if _model_available_locally(name):
        return True
    if str(os.getenv("CAPCAP_FUSION_ALLOW_DOWNLOAD", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return False
    print(f"[Speech Fusion] Whisper model '{name}' not found locally; downloading (first run only)...")
    try:
        if wp._download_whisper_from_custom_repo(name):
            return True
    except Exception as exc:
        print(f"[Speech Fusion] Custom repo download failed for {name}: {exc}")
    try:
        from faster_whisper.utils import download_model
        download_model(name, cache_dir=wp._faster_whisper_cache_dir())
        return _model_available_locally(name)
    except Exception as exc:
        print(f"[Speech Fusion] Download of '{name}' failed: {exc}")
        return False


def choose_asr_model(user_model: str = "") -> tuple[str, bool]:
    """Return (model_name, is_gpu).

    GPU (CUDA): large-v3 -> medium -> small.  CPU: user's moderate choice ->
    ``CAPCAP_FUSION_CPU_MODEL`` (default ``small``) -> base.
    ``CAPCAP_FUSION_WHISPER_MODEL`` forces a specific model.
    """
    import whisper_processor as wp
    runtime = wp._detect_faster_whisper_runtime()
    is_gpu = runtime.get("device") == "cuda"
    user = _normalize_model_name(user_model)
    forced = _normalize_model_name(os.getenv("CAPCAP_FUSION_WHISPER_MODEL", ""))
    if forced:
        candidates = [forced]
    elif is_gpu:
        vram = wp._gpu_memory_mb()
        candidates = ["large-v3", "medium", "small"] if (vram == 0 or vram >= 4500) else ["medium", "small"]
    else:
        cpu_default = _normalize_model_name(os.getenv("CAPCAP_FUSION_CPU_MODEL", "small")) or "small"
        candidates = [user] if user in {"tiny", "base", "small", "medium"} else []
        candidates += [cpu_default, "small", "base"]
    seen = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)
    for candidate in seen:
        if _ensure_model(candidate):
            return candidate, is_gpu
    return (seen[-1] if seen else "small"), is_gpu


def _segment_is_reliable(segment) -> bool:
    text = str(getattr(segment, "text", "") or "").strip()
    if not _normalized(text):
        return False
    if _HALLUCINATION_RE.search(text):
        return False
    no_speech = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
    logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
    compression = float(getattr(segment, "compression_ratio", 1.0) or 1.0)
    if no_speech > 0.6 and logprob < -0.8:
        return False
    if logprob < -1.2 or compression > 2.6:
        return False
    return True


def _asr_beam_size(is_gpu: bool) -> int:
    raw = str(os.getenv("CAPCAP_FUSION_BEAM", "") or "").strip()
    if raw:
        try:
            return max(1, min(10, int(raw)))
        except ValueError:
            pass
    # OCR supplies the text; on CPU ASR is mainly used for timing and gap
    # filling, so greedy decoding (~2x faster) is the better trade-off.
    return 5 if is_gpu else 1


def _asr_kwargs(language: str | None, is_gpu: bool, *, use_vad: bool) -> dict:
    kwargs: dict[str, Any] = {
        "language": language,
        "task": "transcribe",
        "beam_size": _asr_beam_size(is_gpu),
        "word_timestamps": True,
        "condition_on_previous_text": False,
        "vad_filter": use_vad,
    }
    if use_vad:
        kwargs["vad_parameters"] = {
            "threshold": 0.35,
            "min_silence_duration_ms": 350,
            "speech_pad_ms": 150,
        }
    if language in (None, "zh"):
        kwargs["initial_prompt"] = "以下是普通话的句子，使用简体中文。"
    return kwargs


def _asr_batch_size(is_gpu: bool) -> int:
    import whisper_processor as wp
    if is_gpu:
        return wp._whisper_gpu_batch_size()
    raw = str(os.getenv("CAPCAP_FUSION_CPU_BATCH", "4") or "4").strip()
    try:
        return max(0, min(16, int(raw)))
    except ValueError:
        return 4


def _run_batched(model, audio_path: str, kwargs: dict, is_gpu: bool):
    """BatchedInferencePipeline with OOM back-off. Returns segments or None."""
    import whisper_processor as wp
    batch_size = _asr_batch_size(is_gpu)
    if batch_size <= 1 or not kwargs.get("vad_filter"):
        return None
    try:
        pipeline = wp._get_batched_whisper_pipeline(model)
    except Exception as exc:
        print(f"[Speech Fusion] Batched pipeline unavailable: {exc}")
        return None
    batched_kwargs = {k: v for k, v in kwargs.items() if k != "condition_on_previous_text"}
    while batch_size > 1:
        try:
            segments, _info = pipeline.transcribe(audio_path, batch_size=batch_size, **batched_kwargs)
            result = list(segments)
            print(f"[Speech Fusion] Batched ASR ok (batch_size={batch_size}).")
            return result
        except Exception as exc:
            if is_gpu and wp._is_cuda_memory_error(exc):
                batch_size //= 2
                print(f"[Speech Fusion] Batched ASR out of VRAM; retrying batch_size={batch_size}.")
                continue
            print(f"[Speech Fusion] Batched ASR failed; using sequential decoding: {exc}")
            return None
    return None


def _transcribe_words(
    model, audio_path: str, *, language, is_gpu: bool, use_vad: bool,
    offset: float = 0.0, batched: bool = False,
):
    """Return (words, segments) with absolute timestamps."""
    import whisper_processor as wp
    kwargs = _asr_kwargs(language, is_gpu, use_vad=use_vad)
    with wp._WHISPER_TRANSCRIBE_LOCK:
        raw_segments = _run_batched(model, audio_path, kwargs, is_gpu) if batched else None
        if raw_segments is None:
            try:
                segments, _info = model.transcribe(audio_path, **kwargs)
                raw_segments = list(segments)
            except Exception as exc:
                if use_vad and "onnxruntime" in str(exc).lower():
                    kwargs["vad_filter"] = False
                    kwargs.pop("vad_parameters", None)
                    segments, _info = model.transcribe(audio_path, **kwargs)
                    raw_segments = list(segments)
                else:
                    raise
    words: list[dict] = []
    seg_dicts: list[dict] = []
    for segment in raw_segments:
        if not _segment_is_reliable(segment):
            continue
        seg_text = _to_simplified(str(segment.text or "").strip())
        seg_dicts.append({
            "start": round(float(segment.start) + offset, 3),
            "end": round(float(segment.end) + offset, 3),
            "text": seg_text.strip(_STRIP_PUNCT),
        })
        seg_words = [w for w in (segment.words or []) if getattr(w, "start", None) is not None]
        if not seg_words:
            words.append({
                "start": float(segment.start) + offset,
                "end": float(segment.end) + offset,
                "text": seg_text,
                "prob": 0.6,
            })
            continue
        for word in seg_words:
            token = _to_simplified(str(word.word or "").strip())
            if not token:
                continue
            start = float(word.start) + offset
            end = max(start + 0.02, float(word.end) + offset)
            words.append({
                "start": start,
                "end": end,
                "text": token,
                "prob": float(getattr(word, "probability", 0.6) or 0.6),
            })
    words.sort(key=lambda w: w["start"])
    return words, seg_dicts


# ---------------------------------------------------------------------------
# Speech analysis (VAD + ASR), runnable in the background while OCR scans
# ---------------------------------------------------------------------------
def _resolve_asr_mode(is_gpu: bool) -> str:
    """``full``: Whisper over the whole audio (word timing for every cue).
    ``fast``: VAD timing, Whisper only on speech no subtitle covers.
    ``auto`` (default): full on CUDA or strong CPUs (>= 12 threads), else fast.
    """
    raw = str(os.getenv("CAPCAP_FUSION_ASR_MODE", "auto") or "auto").strip().lower()
    if raw in {"full", "fast"}:
        return raw
    return "full" if is_gpu or (os.cpu_count() or 4) >= 12 else "fast"


class SpeechAnalysis:
    """VAD + Whisper for one audio file. ``start()`` runs it in a thread."""

    def __init__(self, *, audio_path: str, whisper_model_name: str = "", language: str | None = None):
        self.audio_path = audio_path
        self.user_model = whisper_model_name
        self.language = language
        self.audio = None
        self.vad: list[tuple[float, float]] = []
        self.words: list[dict] = []
        self.asr_segments: list[dict] = []
        self.model = None
        self.model_name = ""
        self.is_gpu = False
        self.mode = "fast"
        self.report: dict[str, Any] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> "SpeechAnalysis":
        self._thread = threading.Thread(target=self._run, name="speech-analysis", daemon=True)
        self._thread.start()
        return self

    def result(self) -> "SpeechAnalysis":
        if self._thread is None:
            self._run()
        else:
            self._thread.join()
        return self

    def _gpu_guard(self):
        """Serialize with the OCR GPU stage only on small-VRAM cards."""
        if not self.is_gpu:
            return contextlib.nullcontext()
        mode = str(os.getenv("CAPCAP_FUSION_PARALLEL_GPU", "auto") or "auto").strip().lower()
        if mode in {"1", "true", "yes", "on"}:
            return contextlib.nullcontext()
        try:
            import whisper_processor as wp
            vram = wp._gpu_memory_mb()
        except Exception:
            vram = 0
        if mode in {"0", "false", "no", "off"} or (vram and vram < 6000):
            try:
                from services.gpu_stage_scheduler import GPUStageScheduler
                return GPUStageScheduler.stage("whisper-fusion")
            except Exception:
                return contextlib.nullcontext()
        return contextlib.nullcontext()

    def _run(self) -> None:
        started = time.perf_counter()
        try:
            self.audio = _load_audio_16k(self.audio_path) if self.audio_path else None
            self.vad = detect_speech_intervals(self.audio)
        except Exception as exc:
            print(f"[Speech Fusion] VAD failed: {exc}")
        try:
            import whisper_processor as wp
            self.model_name, self.is_gpu = choose_asr_model(self.user_model)
            self.mode = _resolve_asr_mode(self.is_gpu)
            print(
                f"[Speech Fusion] ASR model: {self.model_name} "
                f"({'CUDA GPU' if self.is_gpu else 'CPU int8'}), mode={self.mode}"
            )
            self.model = wp.load_whisper_model(self.model_name)
            # load_whisper_model may fall back to CPU after a CUDA failure.
            self.is_gpu = getattr(self.model, "_capcap_runtime_device", "cpu") == "cuda"
            self.report.update({
                "asr_model": self.model_name,
                "asr_device": "cuda" if self.is_gpu else "cpu",
                "asr_mode": self.mode,
            })
        except Exception as exc:
            print(f"[Speech Fusion] ASR unavailable, continuing with VAD + OCR only: {exc}")
            self.model = None
            self.report["asr_error"] = str(exc)
        if self.model is not None and self.mode == "full" and self.audio_path:
            try:
                asr_started = time.perf_counter()
                with self._gpu_guard():
                    self.words, self.asr_segments = _transcribe_words(
                        self.model, self.audio_path, language=self.language,
                        is_gpu=self.is_gpu, use_vad=True, batched=True,
                    )
                elapsed = time.perf_counter() - asr_started
                self.report["asr_seconds"] = round(elapsed, 2)
                print(
                    f"[Speech Fusion] ASR: {len(self.asr_segments)} segment(s), "
                    f"{len(self.words)} word(s) in {elapsed:.1f}s"
                )
            except Exception as exc:
                print(f"[Speech Fusion] ASR transcription failed: {exc}")
                self.report["asr_error"] = str(exc)
        if not self.vad and self.words:
            self.vad = _merge_intervals([(w["start"], w["end"]) for w in self.words], max_gap=0.3)
        self.report["speech_analysis_seconds"] = round(time.perf_counter() - started, 2)


def start_speech_analysis(
    audio_path: str, *, whisper_model_name: str = "", language: str = "auto",
) -> SpeechAnalysis:
    """Start VAD + ASR in the background (call before the OCR scan)."""
    return SpeechAnalysis(
        audio_path=audio_path,
        whisper_model_name=whisper_model_name,
        language=_asr_language(language),
    ).start()


def _asr_language(language) -> str | None:
    lang = str(language or "auto").strip().lower()
    return None if lang in {"", "auto"} else lang.split("-")[0]


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------
class _Fusion:
    WORD_PAD = 0.35          # seconds a word may sit outside an OCR window
    ORPHAN_SPLIT_GAP = 0.5   # pause that splits an orphan passage
    MAX_CUE_CHARS = 22
    MAX_RESCANS = 160        # merged OCR re-scan windows

    def __init__(self, *, video_path, ocr_region, speech):
        self.video_path = video_path
        self.ocr_region = ocr_region or "bottom"
        self.speech = speech
        self.rescan_fps = max(4.0, _env_float("OCR_FUSION_RESCAN_FPS", 8.0))
        fast = getattr(speech, "mode", "full") == "fast"
        self.min_uncovered = 0.5 if fast else 0.7
        self.max_audit_pieces = 600 if fast else 200
        self.ocr_engine = None
        self.rescans = 0
        self._rescan_windows: list[tuple[float, float]] = []
        self._rescan_segments: list[dict] = []
        self.report: dict[str, Any] = {
            "version": FUSION_VERSION,
            "ocr_cues": 0,
            "retimed_by_asr": 0,
            "retimed_by_vad": 0,
            "kept_ocr_timing": 0,
            "added_from_ocr_rescan": 0,
            "added_from_asr": 0,
            "added_from_vad_audit": 0,
            "extended_existing": 0,
            "skipped_low_confidence": 0,
            "uncovered_speech": [],
        }
        self.report.update(dict(getattr(speech, "report", {}) or {}))

    # -- OCR re-scan with window cache -------------------------------------
    def _covered(self, start: float, end: float) -> bool:
        return any(ws <= start + 1e-6 and we >= end - 1e-6 for ws, we in self._rescan_windows)

    def _scan_window(self, start: float, end: float) -> None:
        if not self.video_path or self.rescans >= self.MAX_RESCANS:
            return
        self.rescans += 1
        try:
            from ocr_processor import _load_ocr_engine, transcribe_video_ocr
            if self.ocr_engine is None:
                self.ocr_engine = _load_ocr_engine()
            with contextlib.redirect_stdout(io.StringIO()):
                found = transcribe_video_ocr(
                    self.video_path,
                    region=self.ocr_region,
                    fps=self.rescan_fps,
                    ocr_engine=self.ocr_engine,
                    start_seconds=max(0.0, start),
                    end_seconds=end,
                )
        except Exception as exc:
            print(f"[Speech Fusion] OCR re-scan failed at {start:.2f}s: {exc}")
            return
        self._rescan_windows.append((max(0.0, start), end))
        self._rescan_segments.extend(found or [])

    def _prefetch_rescans(self, windows: list[tuple[float, float]]) -> None:
        """Scan many gap windows in a few merged passes (one seek each)."""
        pending = [
            (max(0.0, s - 0.3), e + 0.3) for s, e in windows
            if not self._covered(max(0.0, s - 0.3), e + 0.3)
        ]
        for start, end in _merge_intervals(pending, max_gap=2.0):
            self._scan_window(start, end)

    def _rescan_ocr(self, start: float, end: float) -> str:
        """Best OCR text inside [start, end] from a dense re-scan."""
        window = (max(0.0, start - 0.3), end + 0.3)
        if not self._covered(*window):
            self._scan_window(*window)
            if not self._covered(*window):
                return ""
        best_text, best_overlap = "", 0.0
        for seg in self._rescan_segments:
            overlap = min(end, float(seg["end"])) - max(start, float(seg["start"]))
            text = str(seg.get("text", "") or "").strip()
            if text and overlap > best_overlap:
                best_text, best_overlap = text, overlap
        return best_text

    # -- ASR for many short pieces in ONE pass -----------------------------
    def _targeted_asr_batch(self, pieces: list[tuple[float, float]]) -> list[tuple[str, list[dict]]]:
        """Concatenate uncovered pieces (1 s silence between) and transcribe
        once, then map words back.  Far cheaper than one call per piece."""
        empty = [("", []) for _ in pieces]
        speech = self.speech
        audio = getattr(speech, "audio", None)
        model = getattr(speech, "model", None)
        if not pieces or audio is None or model is None:
            return empty
        total = len(audio) / 16000.0
        silence = np.zeros(16000, dtype=np.float32)
        chunks, layout, cursor = [], [], 0.0
        for start, end in pieces:
            clip_start, clip_end = max(0.0, start - 0.2), min(total, end + 0.2)
            if clip_end - clip_start <= 0.05:
                continue
            chunk = audio[int(clip_start * 16000):int(clip_end * 16000)]
            layout.append((cursor, cursor + len(chunk) / 16000.0, clip_start, len(layout)))
            chunks.extend([chunk, silence])
            cursor += len(chunk) / 16000.0 + 1.0
        if not chunks:
            return empty
        joined = np.concatenate(chunks)
        fd, path = tempfile.mkstemp(prefix="capcap_fusion_gaps_", suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes((np.clip(joined, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
            words, _segs = _transcribe_words(
                model, path, language=speech.language, is_gpu=speech.is_gpu,
                use_vad=True, batched=True,
            )
        except Exception as exc:
            print(f"[Speech Fusion] Gap ASR failed: {exc}")
            return empty
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        starts = [item[0] for item in layout]
        per_piece: dict[int, list[dict]] = {}
        valid_pieces = [p for p in pieces if min(total, p[1] + 0.2) - max(0.0, p[0] - 0.2) > 0.05]
        for word in words:
            mid = (word["start"] + word["end"]) / 2.0
            pos = bisect_right(starts, mid) - 1
            if pos < 0 or mid > layout[pos][1]:
                continue
            seg_start, _seg_end, clip_start, piece_idx = layout[pos]
            shift = clip_start - seg_start
            mapped = dict(word, start=word["start"] + shift, end=word["end"] + shift)
            p_start, p_end = valid_pieces[piece_idx]
            if mapped["end"] > p_start - 0.1 and mapped["start"] < p_end + 0.1:
                per_piece.setdefault(piece_idx, []).append(mapped)
        results = []
        valid_idx = 0
        for start, end in pieces:
            if min(total, end + 0.2) - max(0.0, start - 0.2) <= 0.05:
                results.append(("", []))
                continue
            piece_words = per_piece.get(valid_idx, [])
            valid_idx += 1
            if not piece_words or sum(w["prob"] for w in piece_words) / len(piece_words) < 0.35:
                results.append(("", []))
                continue
            results.append((_join_tokens([w["text"] for w in piece_words]), piece_words))
        return results

    # -- timing helpers --------------------------------------------------
    @staticmethod
    def _snap_to_vad(start, end, vad, pad: float = 0.4):
        """Move cue edges to nearby speech on/offsets.

        An edge that already lies inside continuous speech is left alone, so
        a VAD interval spanning several sentences cannot drag a cue into its
        neighbour.
        """
        if not vad:
            return start, end, False
        hits = [v for v in vad if v[1] > start - pad and v[0] < end + pad]
        if not hits:
            return start, end, False
        new_start, new_end = start, end
        onsets = [v[0] for v in hits if start - pad <= v[0] <= start + pad]
        if onsets:
            new_start = min(onsets, key=lambda value: abs(value - start)) - 0.05
        elif not any(v[0] < start < v[1] for v in hits):
            first = next((v[0] for v in hits if v[0] >= start), None)
            if first is not None and first < end:
                new_start = first - 0.05
        offsets = [v[1] for v in hits if end - pad <= v[1] <= end + pad]
        if offsets:
            new_end = min(offsets, key=lambda value: abs(value - end)) + 0.08
        elif not any(v[0] < end < v[1] for v in hits):
            last = max((v[1] for v in hits if v[1] <= end), default=None)
            if last is not None and last > new_start:
                new_end = last + 0.08
        if new_end - new_start < 0.25:
            return start, end, False
        return max(0.0, new_start), new_end, (new_start, new_end) != (start, end)

    def _find_duplicate_neighbor(self, cues, start, end, text):
        best_idx, best_sim = -1, 0.0
        for idx, cue in enumerate(cues):
            if cue["end"] < start - 1.5 or cue["start"] > end + 1.5:
                continue
            sim = _similarity(cue.get("text", ""), text)
            if sim > best_sim:
                best_idx, best_sim = idx, sim
        return best_idx if best_sim >= 0.8 else -1

    def _add_or_extend(self, cues, start, end, text, *, source, words=None) -> str:
        text = str(text or "").strip(_STRIP_PUNCT)
        if not _normalized(text):
            return "empty"
        dup = self._find_duplicate_neighbor(cues, start, end, text)
        if dup >= 0:
            cue = cues[dup]
            if start < cue["start"] and cue["start"] - end < 1.2:
                cue["start"] = start
            if end > cue["end"] and start - cue["end"] < 1.2:
                cue["end"] = end
            self.report["extended_existing"] += 1
            return "extended"
        cues.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "words": [
                {"start": round(w["start"], 3), "end": round(w["end"], 3), "text": w["text"]}
                for w in (words or [])
            ],
            "source": source,
            "timing_source": "asr_words" if words else "vad",
            "fusion_added": True,
        })
        cues.sort(key=lambda c: c["start"])
        return "added"

    # -- main ------------------------------------------------------------
    def run(self, ocr_segments: list[dict]) -> tuple[list[dict], dict]:
        started = time.perf_counter()
        speech = self.speech
        vad = list(getattr(speech, "vad", []) or [])
        words = list(getattr(speech, "words", []) or [])
        self.report["vad_intervals"] = len(vad)

        # 1. OCR cues (accurate text) -------------------------------------
        cues = []
        for seg in sorted(ocr_segments or [], key=lambda s: float(s.get("start", 0.0))):
            text = str(seg.get("text", "") or "").strip()
            if not text:
                continue
            cue = dict(seg)
            cue["start"], cue["end"] = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
            cue["ocr_start"], cue["ocr_end"] = round(cue["start"], 3), round(cue["end"], 3)
            cue["source"] = cue.get("source") or "ocr"
            cues.append(cue)
        self.report["ocr_cues"] = len(cues)

        # 2. Assign spoken words to OCR cues -------------------------------
        owner = [None] * len(words)
        assigned: list[list[int]] = [[] for _ in cues]
        starts = [c["start"] for c in cues]
        cue_chars = [set(_cjk(c["text"])) for c in cues]
        for wi, word in enumerate(words):
            mid = (word["start"] + word["end"]) / 2.0
            word_chars = set(_cjk(word["text"]))
            best, best_key = None, None
            idx = bisect_right(starts, mid + self.WORD_PAD) - 1
            while idx >= 0 and cues[idx]["start"] > mid - 30.0:
                cue = cues[idx]
                if cue["end"] + self.WORD_PAD >= mid:
                    dist = max(0.0, cue["start"] - mid, mid - cue["end"])
                    char_match = bool(word_chars & cue_chars[idx]) if word_chars else True
                    # Words in the padding zone must share a character with the
                    # cue, so the next (missed) sentence is not swallowed.
                    if dist == 0.0 or char_match:
                        key = (dist, 0 if char_match else 1)
                        if best_key is None or key < best_key:
                            best, best_key = idx, key
                idx -= 1
            if best is not None:
                owner[wi] = best
                assigned[best].append(wi)

        # 3. Re-time OCR cues with the real voice --------------------------
        for ci, cue in enumerate(cues):
            o_start, o_end = cue["start"], cue["end"]
            cue_words = [words[i] for i in assigned[ci]]
            if cue_words:
                w_start = min(w["start"] for w in cue_words)
                w_end = max(w["end"] for w in cue_words)
                asr_text = _join_tokens([w["text"] for w in cue_words])
                cue["asr_text"] = asr_text
                coverage = (
                    len(set(_cjk(asr_text)) & cue_chars[ci]) / len(cue_chars[ci])
                    if cue_chars[ci] else 1.0
                )
                cue["asr_coverage"] = round(coverage, 3)
                if coverage >= 0.6:
                    new_start, new_end = w_start, w_end
                    cue["timing_source"] = "asr_words"
                    self.report["retimed_by_asr"] += 1
                else:
                    new_start, new_end, _ = self._snap_to_vad(min(w_start, o_start), max(w_end, o_end), vad)
                    cue["timing_source"] = "asr_vad_union"
                    self.report["retimed_by_vad"] += 1
                new_start = max(new_start, o_start - 0.6)
                new_end = min(new_end, o_end + 0.8)
            else:
                new_start, new_end, snapped = self._snap_to_vad(o_start, o_end, vad)
                cue["timing_source"] = "vad" if snapped else "ocr"
                self.report["retimed_by_vad" if snapped else "kept_ocr_timing"] += 1
            if new_end - new_start < 0.25:
                new_end = new_start + 0.25
            cue["start"], cue["end"] = round(max(0.0, new_start), 3), round(new_end, 3)

        # 4. Orphan speech (no OCR cue claimed it) --------------------------
        runs: list[list[dict]] = []
        current: list[dict] = []
        for wi, word in enumerate(words):
            if owner[wi] is not None:
                if current:
                    runs.append(current)
                current = []
                continue
            if current and (
                word["start"] - current[-1]["end"] >= self.ORPHAN_SPLIT_GAP
                or len(_cjk(_join_tokens([w["text"] for w in current]))) >= self.MAX_CUE_CHARS
                or (current[-1]["text"] and current[-1]["text"][-1] in _END_PUNCT)
            ):
                runs.append(current)
                current = []
            current.append(word)
        if current:
            runs.append(current)

        candidates = []
        for run in runs:
            text = _join_tokens([w["text"] for w in run])
            start, end = run[0]["start"], run[-1]["end"]
            meaningful = len(_cjk(text)) >= 2 or len(_LATIN_WORD_RE.findall(text)) >= 2
            avg_prob = sum(w["prob"] for w in run) / len(run)
            if not meaningful or end - start < 0.25 or avg_prob < 0.35 or _HALLUCINATION_RE.search(text):
                self.report["skipped_low_confidence"] += 1
                continue
            candidates.append((start, end, text, run))
        self._prefetch_rescans([(c[0], c[1]) for c in candidates])
        for start, end, text, run in candidates:
            ocr_text = self._rescan_ocr(start, end)
            if ocr_text:
                outcome = self._add_or_extend(cues, start, end, ocr_text, source="ocr_rescan", words=run)
                if outcome == "added":
                    self.report["added_from_ocr_rescan"] += 1
                if outcome in {"added", "extended"}:
                    continue
            outcome = self._add_or_extend(cues, start, end, text, source="asr_fill", words=run)
            if outcome == "added":
                self.report["added_from_asr"] += 1

        # 5. Audit: speech still without subtitle ---------------------------
        cue_cover = _merge_intervals([(c["start"], c["end"]) for c in cues], max_gap=0.0)
        pieces = []
        for v_start, v_end in vad:
            if v_end - v_start < self.min_uncovered:
                continue
            for p_start, p_end in _subtract_intervals((v_start, v_end), cue_cover):
                if p_end - p_start >= self.min_uncovered:
                    pieces.append((p_start, p_end))
        if len(pieces) > self.max_audit_pieces:
            pieces = sorted(pieces, key=lambda p: p[1] - p[0], reverse=True)[: self.max_audit_pieces]
            pieces.sort()
        self._prefetch_rescans(pieces)
        need_asr = []
        for p_start, p_end in pieces:
            ocr_text = self._rescan_ocr(p_start, p_end)
            if ocr_text:
                outcome = self._add_or_extend(cues, p_start, p_end, ocr_text, source="ocr_rescan")
                if outcome == "added":
                    self.report["added_from_vad_audit"] += 1
                if outcome in {"added", "extended"}:
                    continue
            need_asr.append((p_start, p_end))
        asr_results = self._targeted_asr_batch(need_asr) if need_asr else []
        for (p_start, p_end), (text, asr_words) in zip(need_asr, asr_results):
            if text and not _HALLUCINATION_RE.search(text) and (
                len(_cjk(text)) >= 2 or len(_LATIN_WORD_RE.findall(text)) >= 2
            ):
                if asr_words:
                    p_start = max(p_start - 0.2, asr_words[0]["start"])
                    p_end = min(p_end + 0.2, asr_words[-1]["end"])
                outcome = self._add_or_extend(
                    cues, p_start, p_end, text, source="asr_vad_fill", words=asr_words
                )
                if outcome == "added":
                    self.report["added_from_vad_audit"] += 1
                if outcome in {"added", "extended"}:
                    continue
            self.report["uncovered_speech"].append({
                "start": round(p_start, 3), "end": round(p_end, 3),
            })

        # 6. Monotonic timeline, real pauses kept ---------------------------
        cues = self._finalize(cues)
        self.report["final_cues"] = len(cues)
        self.report["ocr_rescans"] = self.rescans
        self.report["gap_asr_pieces"] = len(need_asr)
        self.report["fusion_seconds"] = round(time.perf_counter() - started, 2)
        print(
            "[Speech Fusion] Done: "
            f"ocr={self.report['ocr_cues']}, retimed_asr={self.report['retimed_by_asr']}, "
            f"retimed_vad={self.report['retimed_by_vad']}, +ocr_rescan={self.report['added_from_ocr_rescan']}, "
            f"+asr={self.report['added_from_asr']}, +audit={self.report['added_from_vad_audit']}, "
            f"uncovered={len(self.report['uncovered_speech'])}, final={len(cues)}, "
            f"rescans={self.rescans}, gap_asr={len(need_asr)} ({self.report['fusion_seconds']}s)"
        )
        context = {
            "speech_intervals": vad,
            "asr_segments": list(getattr(speech, "asr_segments", []) or []),
            "report": self.report,
        }
        return cues, context

    @staticmethod
    def _finalize(cues: list[dict]) -> list[dict]:
        ordered = sorted(cues, key=lambda c: (float(c["start"]), float(c["end"])))
        result: list[dict] = []
        for cue in ordered:
            start, end = float(cue["start"]), float(cue["end"])
            if result:
                prev = result[-1]
                if start < prev["end"] + 0.02:
                    # Shorten the previous cue first; only push this one if
                    # the previous would become unreasonably short.
                    prev["end"] = round(max(prev["start"] + 0.2, start - 0.02), 3)
                    if start < prev["end"] + 0.02:
                        start = prev["end"] + 0.02
            if end - start < 0.2:
                end = start + 0.2
            cue["start"], cue["end"] = round(start, 3), round(end, 3)
            result.append(cue)
        for idx, cue in enumerate(result):
            next_start = result[idx + 1]["start"] if idx + 1 < len(result) else None
            cue["pause_after"] = round(max(0.0, next_start - cue["end"]), 3) if next_start is not None else 0.0
            cue.setdefault("words", [])
        return result


def run_speech_ocr_fusion(
    *,
    video_path: str,
    audio_path: str,
    ocr_segments: list[dict],
    ocr_region: str = "bottom",
    whisper_model_name: str = "",
    language: str = "auto",
    speech: SpeechAnalysis | None = None,
) -> tuple[list[dict], dict]:
    """Fuse OCR cues with ASR/VAD. Returns ``(cues, context)``.

    Pass ``speech`` from :func:`start_speech_analysis` (started before the
    OCR scan) so VAD + Whisper overlap with OCR instead of running after it.
    ``context`` contains ``speech_intervals``, ``asr_segments`` and ``report``.
    """
    if speech is None:
        speech = SpeechAnalysis(
            audio_path=audio_path,
            whisper_model_name=whisper_model_name,
            language=_asr_language(language),
        )
    wait_started = time.perf_counter()
    speech.result()
    waited = time.perf_counter() - wait_started
    if waited > 0.5:
        print(f"[Speech Fusion] Waited {waited:.1f}s for speech analysis after OCR finished.")
    fusion = _Fusion(video_path=video_path, ocr_region=ocr_region, speech=speech)
    fusion.report["waited_for_speech_seconds"] = round(waited, 2)
    return fusion.run(ocr_segments)
