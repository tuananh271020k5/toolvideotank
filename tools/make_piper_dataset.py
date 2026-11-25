"""Biến 1 file ghi âm thô thành bộ dữ liệu train Piper (định dạng LJSpeech).

Cách dùng:
    .venv\\Scripts\\python.exe tools\\make_piper_dataset.py "D:\\path\\giongbe.aac" --name giongbe

Kết quả (mặc định trong ..\\piper_datasets\\<name>):
    wavs/<name>_0001.wav ...   22050 Hz, mono, 16-bit, mỗi đoạn 2-12 giây
    metadata.csv               <id>|<lời thoại>      (đúng định dạng piper_train.preprocess ljspeech)
    review.csv                 id, thời lượng, độ tin cậy, cờ cần kiểm tra
    <name>_dataset.zip         nén sẵn wavs + metadata.csv để upload lên Drive/Colab

Các bước: ffmpeg chuẩn hoá âm thanh -> faster-whisper (tiếng Việt, mốc thời gian
từng từ) -> cắt tại chỗ ngắt câu / khoảng lặng -> ghi file + lời thoại.
Nên nghe lại các dòng bị gắn cờ trong review.csv và sửa lời trong metadata.csv.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import wave
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

try:
    from vietnormalizer.normalizer import VietnameseNormalizer
    _normalizer = VietnameseNormalizer()
except Exception:
    _normalizer = None

SAMPLE_RATE = 22050
MIN_CLIP = 2.0
MAX_CLIP = 12.0
TARGET_CLIP = 8.0
PAUSE_SPLIT = 0.35      # khoảng lặng đủ dài để cắt câu
EDGE_PAD = 0.12         # đệm lặng ở 2 đầu mỗi đoạn
SENTENCE_END = (".", "?", "!", "…")
CLAUSE_END = (",", ";", ":")


def _ffmpeg() -> str:
    for cand in (
        os.path.join(ROOT, "bin", "ffmpeg", "ffmpeg.exe"),
        os.path.join(ROOT, "bin", "ffmpeg.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return "ffmpeg"


def convert_to_wav(src: str, dst: str) -> None:
    # highpass bỏ ù tần thấp; loudnorm cân âm lượng đều cho cả bộ dữ liệu.
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-af", "highpass=f=60,loudnorm=I=-20:TP=-2:LRA=11",
        "-sample_fmt", "s16", dst,
    ]
    subprocess.run(cmd, check=True)


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE and wf.getnchannels() == 1
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def write_wav(path: str, samples: np.ndarray) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples.astype(np.int16).tobytes())


def transcribe_words(wav_path: str, model_name: str, device: str, model_dir: str = ""):
    from faster_whisper import WhisperModel

    download_root = model_dir or os.path.join(ROOT, "models", "faster_whisper")
    os.makedirs(download_root, exist_ok=True)

    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    print(f"[ASR] model={model_name} device={device} compute={compute_type}")
    model = WhisperModel(model_name, device=device, compute_type=compute_type, download_root=download_root)
    # Feed a 16 kHz float32 array instead of a path: faster-whisper's own
    # decoder uses PyAV, and PyAV >= 15 removed an argument it passes.
    pcm = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", wav_path,
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    audio16k = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(
        audio16k,
        language="vi",
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = (w.word or "").strip()
            if text:
                words.append({"start": float(w.start), "end": float(w.end), "text": text, "prob": float(w.probability)})
        percent = (seg.end / info.duration * 100.0) if info.duration > 0 else 0.0
        print(f"\r[ASR] {seg.end:7.1f}s / {info.duration:.0f}s ({percent:.1f}%)", end="", flush=True)
    print()
    return words


def group_clips(words: list[dict]) -> list[list[dict]]:
    clips, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        dur = cur[-1]["end"] - cur[0]["start"]
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99.0
        txt = w["text"]
        cut = False
        if nxt is None:
            cut = True
        elif dur >= MAX_CLIP - 1.0:
            cut = True
        elif txt.endswith(SENTENCE_END) and dur >= MIN_CLIP:
            cut = True
        elif gap >= PAUSE_SPLIT and dur >= MIN_CLIP:
            cut = True
        elif txt.endswith(CLAUSE_END) and dur >= TARGET_CLIP:
            cut = True
        if cut:
            clips.append(cur)
            cur = []
    # gộp đoạn quá ngắn vào đoạn trước nếu còn chỗ
    merged = []
    for c in clips:
        if merged and (c[-1]["end"] - c[0]["start"]) < MIN_CLIP and (c[-1]["end"] - merged[-1][0]["start"]) <= MAX_CLIP:
            merged[-1].extend(c)
        else:
            merged.append(c)
    return merged


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("“", "").replace("”", "").replace('"', "")
    text = text.replace("|", " ")
    if _normalizer:
        try:
            text = _normalizer.normalize(text)
        except Exception:
            pass
    # Loại bỏ các ký tự đặc biệt còn sót lại nhưng giữ dấu câu tiếng Việt
    text = re.sub(r"[^\w\s\.,\?!…áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệóòỏõọôốồổỗộơớờởỡợíìỉĩịúùủũụưứừửữựýỳỷỹỵđÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÉÈẺẼẸÊẾỀỂỄỆÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÍÌỈĨỊÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴĐ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" -")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="file âm thanh/video thô (aac, mp3, m4a, wav, mp4...)")
    ap.add_argument("--name", default="voice", help="tên giọng, dùng làm tiền tố file")
    ap.add_argument("--out", default="", help="thư mục xuất (mặc định ..\\piper_datasets\\<name>)")
    ap.add_argument("--model", default="small", help="model whisper: small / medium / large-v3")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--model-dir", default="", help="nơi lưu model whisper (Colab: /content/whisper_models)")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(os.path.dirname(ROOT), "piper_datasets", args.name)
    wav_dir = os.path.join(out_dir, "wavs")
    os.makedirs(wav_dir, exist_ok=True)
    full_wav = os.path.join(out_dir, "_full_22050.wav")

    print("[1/4] Chuẩn hoá âm thanh (mono 22050 Hz, cân âm lượng)...")
    convert_to_wav(args.input, full_wav)
    audio = read_wav(full_wav)
    total = len(audio) / SAMPLE_RATE

    print("[2/4] Nhận dạng lời nói tiếng Việt (có thể mất 5-20 phút trên CPU)...")
    words = transcribe_words(full_wav, args.model, args.device, args.model_dir)
    if not words:
        sys.exit("Không nhận dạng được lời nói nào.")

    print("[3/4] Cắt câu...")
    clips = group_clips(words)

    print("[4/4] Ghi file...")
    meta_rows, review_rows = [], []
    kept_seconds = 0.0
    for idx, clip in enumerate(clips, start=1):
        start = max(0.0, clip[0]["start"] - EDGE_PAD)
        end = min(total, clip[-1]["end"] + EDGE_PAD)
        dur = end - start
        text = clean_text(" ".join(w["text"] for w in clip))
        if dur < 1.0 or dur > MAX_CLIP + 1.0 or len(text) < 2:
            continue
        clip_id = f"{args.name}_{idx:04d}"
        write_wav(os.path.join(wav_dir, f"{clip_id}.wav"), audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
        avg_prob = float(np.mean([w["prob"] for w in clip]))
        min_prob = float(np.min([w["prob"] for w in clip]))
        flags = []
        if avg_prob < 0.75 or min_prob < 0.35:
            flags.append("do_tin_cay_thap")
        if re.search(r"\d", text):
            flags.append("co_chu_so")
        if dur < MIN_CLIP:
            flags.append("qua_ngan")
        meta_rows.append((clip_id, text))
        review_rows.append((clip_id, f"{dur:.2f}", f"{avg_prob:.2f}", f"{min_prob:.2f}", ";".join(flags), text))
        kept_seconds += dur

    with open(os.path.join(out_dir, "metadata.csv"), "w", encoding="utf-8", newline="") as f:
        for clip_id, text in meta_rows:
            f.write(f"{clip_id}|{text}\n")
    with open(os.path.join(out_dir, "review.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "giay", "tin_cay_tb", "tin_cay_min", "can_kiem_tra", "loi_thoai"])
        writer.writerows(review_rows)

    zip_path = os.path.join(out_dir, f"{args.name}_dataset.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(out_dir, "metadata.csv"), "metadata.csv")
        for clip_id, _ in meta_rows:
            z.write(os.path.join(wav_dir, f"{clip_id}.wav"), f"wavs/{clip_id}.wav")
    try:
        os.remove(full_wav)
    except OSError:
        pass

    flagged = sum(1 for r in review_rows if r[4])
    print()
    print(f"Xong: {len(meta_rows)} đoạn, tổng {kept_seconds / 60:.1f} phút / {total / 60:.1f} phút gốc")
    print(f"Cần nghe lại: {flagged} đoạn (xem review.csv)")
    print(f"Thư mục: {out_dir}")
    print(f"File nén để upload: {zip_path}")


if __name__ == "__main__":
    main()
