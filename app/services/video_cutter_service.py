"""Video Cutter & Splitter Service - Cắt video ngắn (Shorts/TikTok/Reels) & Chia video đa phần.

Tính năng:
1. Cắt 1 đoạn video ngắn theo thời gian bắt đầu & độ dài (Start Time & Duration) hoặc End Time.
2. Tự động chia cả video thành nhiều phần nhỏ (Part 001, Part 002...) theo thời lượng (ví dụ 60s, 120s, 5 phút...).
3. Hỗ trợ 2 chế độ:
   - Stream Copy (-c copy): Cắt tức thì trong 1 giây, không nén lại, 100% giữ nguyên chất lượng gốc.
   - Re-encode (-c:v libx264): Cắt chuẩn xác từng khung hình mili-giây.
4. Trích xuất thông tin video (thời lượng, kích thước, fps, codec).
"""

import math
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple


def subprocess_hidden_flags() -> dict:
    kwargs = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def get_ffmpeg_bin() -> str:
    from app.runtime_paths import resolve_ffmpeg_path
    try:
        return resolve_ffmpeg_path()
    except Exception:
        # Fallback to local bin
        from app.runtime_paths import bin_path
        p = bin_path("ffmpeg", "ffmpeg.exe")
        if os.path.isfile(p):
            return p
        return "ffmpeg"


def get_ffprobe_bin() -> str:
    ffmpeg = get_ffmpeg_bin()
    ffprobe = ffmpeg.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
    if os.path.isfile(ffprobe):
        return ffprobe
    from app.runtime_paths import bin_path
    p = bin_path("ffmpeg", "ffprobe.exe")
    if os.path.isfile(p):
        return p
    return "ffprobe"


def probe_video_details(video_path: str) -> Dict[str, Any]:
    """Lấy thông tin chi tiết của video: duration, width, height, fps, bitrate, size."""
    info = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 30.0,
        "size_bytes": 0,
        "size_mb": 0.0,
        "aspect_ratio": "16:9",
        "is_vertical": False,
        "video_codec": "",
        "audio_codec": "",
    }
    if not video_path or not os.path.isfile(video_path):
        return info

    try:
        info["size_bytes"] = os.path.getsize(video_path)
        info["size_mb"] = round(info["size_bytes"] / (1024 * 1024), 2)
    except Exception:
        pass

    ffprobe = get_ffprobe_bin()
    try:
        cmd = [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name:format=duration",
            "-of", "json",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **subprocess_hidden_flags())
        if res.returncode == 0 and res.stdout.strip():
            import json
            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            if fmt.get("duration"):
                info["duration"] = float(fmt["duration"])
            if streams:
                v = streams[0]
                info["width"] = int(v.get("width") or 0)
                info["height"] = int(v.get("height") or 0)
                info["video_codec"] = v.get("codec_name") or ""
                # FPS
                r_fps = v.get("r_frame_rate", "30/1")
                if "/" in r_fps:
                    num, den = r_fps.split("/")
                    info["fps"] = round(float(num) / max(1.0, float(den)), 2)
                else:
                    info["fps"] = float(r_fps)

                if info["width"] > 0 and info["height"] > 0:
                    info["is_vertical"] = info["height"] > info["width"]
                    if info["is_vertical"]:
                        info["aspect_ratio"] = "9:16 (Shorts/TikTok)"
                    elif abs(info["width"] / info["height"] - 1.777) < 0.05:
                        info["aspect_ratio"] = "16:9 (Ngang chuẩn)"
                    else:
                        info["aspect_ratio"] = f"{info['width']}:{info['height']}"
    except Exception as e:
        print(f"[VideoCutter] Probe error: {e}")

    # Fallback duration if 0
    if info["duration"] <= 0.0:
        try:
            cmd = [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                video_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, **subprocess_hidden_flags())
            if res.returncode == 0 and res.stdout.strip():
                info["duration"] = float(res.stdout.strip())
        except Exception:
            pass

    return info


def format_seconds(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    s = max(0.0, float(seconds or 0.0))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def format_seconds_detailed(seconds: float) -> str:
    """Format seconds into readable Vietnamese text (vd: 5 phút 20 giây)."""
    s = max(0.0, float(seconds or 0.0))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    parts = []
    if h > 0:
        parts.append(f"{h} giờ")
    if m > 0 or h > 0:
        parts.append(f"{m} phút")
    parts.append(f"{sec} giây")
    return " ".join(parts)


def cut_single_clip(
    video_path: str,
    output_path: str,
    start_sec: float,
    duration_sec: float,
    mode: str = "copy",  # "copy" hoặc "reencode"
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> Tuple[bool, str]:
    """Cắt 1 đoạn video ngắn theo start_sec và duration_sec."""
    if not os.path.isfile(video_path):
        return False, "Tệp video nguồn không tồn tại."

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    ffmpeg = get_ffmpeg_bin()

    start_sec = max(0.0, round(float(start_sec), 3))
    duration_sec = max(0.5, round(float(duration_sec), 3))

    if progress_cb:
        progress_cb(10.0, f"Đang chuẩn bị cắt clip từ {format_seconds(start_sec)}...")

    if mode == "copy":
        # Stream copy (Siêu tốc trong 1 giây)
        cmd = [
            ffmpeg, "-y",
            "-ss", str(start_sec),
            "-i", video_path,
            "-t", str(duration_sec),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ]
    else:
        # Re-encode (Khung hình chuẩn xác tuyệt đối)
        cmd = [
            ffmpeg, "-y",
            "-ss", str(start_sec),
            "-i", video_path,
            "-t", str(duration_sec),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "19",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **subprocess_hidden_flags(),
        )
        _, stderr_bytes = proc.communicate(timeout=600)
        if proc.returncode != 0:
            # Fallback nếu mode copy thất bại do keyframe header
            if mode == "copy":
                if progress_cb:
                    progress_cb(30.0, "Thử lại chế độ mã hóa chuẩn xác...")
                cmd_fb = [
                    ffmpeg, "-y",
                    "-ss", str(start_sec),
                    "-i", video_path,
                    "-t", str(duration_sec),
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "20",
                    "-c:a", "aac",
                    output_path,
                ]
                proc_fb = subprocess.Popen(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_hidden_flags())
                _, stderr_fb = proc_fb.communicate(timeout=600)
                if proc_fb.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    if progress_cb:
                        progress_cb(100.0, "Cắt clip thành công!")
                    return True, ""

            err_msg = stderr_bytes.decode("utf-8", errors="ignore")[-400:] if stderr_bytes else "Lỗi FFmpeg không xác định."
            return False, f"Lỗi cắt video: {err_msg}"

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            return False, "File đầu ra không được tạo hoặc có dung lượng 0 byte."

        if progress_cb:
            progress_cb(100.0, "Cắt clip thành công!")
        return True, ""

    except Exception as exc:
        return False, f"Ngoại lệ khi cắt video: {exc}"


def sanitize_filename(name: str) -> str:
    """Làm sạch tên file, loại bỏ ký tự cấm trên Windows (\\ / : * ? \" < > |) và khoảng trắng thừa."""
    if not name:
        return ""
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    clean = re.sub(r'\s+', " ", clean)
    return clean


def calculate_split_ranges(
    total_duration: float,
    segment_seconds: float,
    merge_remainder: bool = True,
) -> List[Tuple[float, float]]:
    """Tính toán danh sách các khoảng thời gian [(start_sec, duration_sec), ...] khi chia video.

    Nếu merge_remainder=True và phần thừa cuối cùng < 70% segment_seconds,
    phần thừa sẽ được gộp vào part cuối cùng để tránh tạo clip cụt lủn (vd: 4:30 chia 2p -> Part 1: 2p, Part 2: 2p30s).
    """
    total_dur = max(0.0, float(total_duration or 0.0))
    segment_sec = max(5.0, float(segment_seconds or 60.0))
    if total_dur <= 0.0:
        return []

    if total_dur <= segment_sec:
        return [(0.0, total_dur)]

    base_parts = int(total_dur // segment_sec)
    remainder = total_dur - (base_parts * segment_sec)

    # Nếu có phần dư và merge_remainder bật và phần dư < 70% segment: gộp vào part cuối
    should_merge = (merge_remainder and base_parts >= 1 and 0.2 < remainder < (segment_sec * 0.75))

    num_parts = base_parts if should_merge else (base_parts + (1 if remainder > 0.2 else 0))

    ranges = []
    for i in range(num_parts):
        start_s = i * segment_sec
        if i == num_parts - 1:
            # Part cuối cùng: lấy hết đến total_dur
            dur_s = round(total_dur - start_s, 3)
        else:
            dur_s = segment_sec
        if dur_s > 0.2:
            ranges.append((start_s, dur_s))

    return ranges


def split_video_into_parts(
    video_path: str,
    output_dir: str,
    segment_seconds: float,
    mode: str = "copy",  # "copy" hoặc "reencode"
    custom_base_name: Optional[str] = None,
    merge_remainder: bool = True,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
) -> Tuple[List[str], str]:
    """Chia toàn bộ video thành các phần ngắn (Part 01, Part 02...) theo segment_seconds."""
    if not os.path.isfile(video_path):
        return [], "Tệp video nguồn không tồn tại."

    info = probe_video_details(video_path)
    total_dur = info.get("duration", 0.0)
    if total_dur <= 0.0:
        return [], "Không thể xác định thời lượng video nguồn."

    split_ranges = calculate_split_ranges(total_dur, segment_seconds, merge_remainder=merge_remainder)
    num_parts = len(split_ranges)
    if num_parts == 0:
        return [], "Không có đoạn video hợp lệ để chia."

    os.makedirs(output_dir, exist_ok=True)
    if custom_base_name and custom_base_name.strip():
        base_name = sanitize_filename(custom_base_name.strip())
    else:
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        base_name = sanitize_filename(base_name)
    if not base_name:
        base_name = "video"
    ext = os.path.splitext(video_path)[1] or ".mp4"

    output_files = []

    for i, (start_s, dur_s) in enumerate(split_ranges):
        if is_cancelled_cb and is_cancelled_cb():
            break

        part_tag = f"Part{i + 1:02d}" if num_parts < 100 else f"Part{i + 1:03d}"
        out_name = f"{base_name}_{part_tag}{ext}"
        out_file = os.path.join(output_dir, out_name)

        pct = (i / float(num_parts)) * 100.0
        msg = f"Đang cắt Part {i + 1}/{num_parts} ({format_seconds(start_s)} ➔ {format_seconds(start_s + dur_s)})..."
        if progress_cb:
            progress_cb(pct, msg)

        ok, err = cut_single_clip(
            video_path=video_path,
            output_path=out_file,
            start_sec=start_s,
            duration_sec=dur_s,
            mode=mode,
        )
        if ok and os.path.isfile(out_file):
            output_files.append(out_file)
        else:
            print(f"[VideoSplitter] Part {i + 1} error: {err}")

    if progress_cb:
        progress_cb(100.0, f"Đã chia thành công {len(output_files)}/{num_parts} đoạn!")

    return output_files, ""
