import pytest
from app.models.subtitle_segment import SubtitleSegment
from app.services.video_export_service import SubtitleRenderConfig, generate_ass_script


def test_no_voice_preserves_translated_text_and_burns_subtitles():
    """Kiểm tra chế độ 'Không lồng tiếng' (is_no_voice):
    - SubtitleSegment.skip_tts phải là True (để không sinh giọng đọc AI).
    - SubtitleSegment.translated_text phải bảo toàn câu tiếng Việt (không bị xóa rỗng).
    - generate_ass_script phải render thành công phụ đề vào file ASS.
    """
    translated_cues = [
        {
            "id": 1,
            "start": 0.5,
            "end": 3.0,
            "text": "你好世界",
            "translated_text": "Xin chào thế giới",
            "source": "asr_supplement",
        },
        {
            "id": 2,
            "start": 3.5,
            "end": 6.0,
            "text": "这是一个测试",
            "translated_text": "Đây là một bài kiểm tra",
            "source": "asr_supplement",
        },
    ]

    selected_voice = {"id": "no_voiceover", "name": "Không lồng tiếng", "provider": "none"}
    voice_id = str(selected_voice.get("id", "adam1")).strip()
    voice_name = str(selected_voice.get("name", voice_id)).strip()
    is_no_voice = (voice_id in ("no_voiceover", "none", "no_voice") or str(selected_voice.get("provider", "")).lower() == "none")
    assert is_no_voice is True

    segments: list[SubtitleSegment] = []
    for idx, cue in enumerate(translated_cues, start=1):
        start_s = float(cue.get("start", 0.0) or 0.0)
        end_s = float(cue.get("end", 0.0) or 0.0)
        orig_txt = str(cue.get("text", "") or cue.get("original_text", "")).strip()
        trans_txt = str(cue.get("translated_text", "") or cue.get("translated", "")).strip()

        is_noise = bool(cue.get("is_noise", False))
        skip_tts = is_no_voice or is_noise or (not trans_txt)
        effective_trans = "" if (is_noise or not trans_txt) else trans_txt

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
            bbox=[0.08, 0.70, 0.84, 0.09],
            ocr_box_index=1,
            position="bottom",
        )
        segments.append(seg)

    # 1. Kiểm tra các trường của SubtitleSegment
    assert len(segments) == 2
    assert segments[0].skip_tts is True
    assert segments[0].translated_text == "Xin chào thế giới"
    assert segments[1].skip_tts is True
    assert segments[1].translated_text == "Đây là một bài kiểm tra"

    # 2. Kiểm tra generate_ass_script
    cfg = SubtitleRenderConfig(font_size=34, mask_mode="blur", background_box=False)
    ass_content, rendered_cnt = generate_ass_script(
        segments=segments,
        vid_w=1920,
        vid_h=1080,
        cfg=cfg,
        target_cy=888,
        enable_mask=True,
    )

    assert rendered_cnt == 2, f"Phải render 2 câu phụ đề, thực tế: {rendered_cnt}"
    assert "Xin chào thế giới" in ass_content
    assert "Đây là một bài kiểm tra" in ass_content
