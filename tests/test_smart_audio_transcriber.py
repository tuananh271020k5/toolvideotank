import pytest
from app.services.smart_audio_transcriber import SmartAudioTranscriber, DEFAULT_BLACKLIST
from app.services.video_export_service import merge_time_intervals, build_ffmpeg_between_expression


def test_blacklist_filter():
    transcriber = SmartAudioTranscriber()
    raw = [
        {"start": 0.5, "end": 2.0, "text": "故事纯属虚构 请勿模仿 这么丧良心的事儿"},
        {"start": 2.5, "end": 3.5, "text": "@阿怖 关注点赞"},  # Chỉ toàn từ rác -> cần bị loại bỏ
        {"start": 4.0, "end": 6.0, "text": "我拿出早就准备好的板砖"},
    ]
    cleaned = transcriber.filter_blacklist_and_clean(raw, DEFAULT_BLACKLIST)
    assert len(cleaned) == 2
    assert "故事纯属虚构" not in cleaned[0]["text"]
    assert "请勿模仿" not in cleaned[0]["text"]
    assert "丧良心的事儿" in cleaned[0]["text"]
    assert cleaned[1]["text"] == "我拿出早就准备好的板砖"


def test_merge_time_intervals():
    intervals = [(1.0, 2.5), (2.6, 4.0), (7.0, 9.0)]
    merged = merge_time_intervals(intervals, gap=0.25)
    # (1.0, 2.5) và (2.6, 4.0) cách nhau 0.1s -> gộp thành (1.0, 4.0)
    assert len(merged) == 2
    assert merged[0] == (1.0, 4.0)
    assert merged[1] == (7.0, 9.0)

    expr = build_ffmpeg_between_expression(merged)
    assert "between(t,1.00,4.00)" in expr
    assert "between(t,7.00,9.00)" in expr


def test_translate_segments_creates_valid_subtitle_segments(monkeypatch):
    transcriber = SmartAudioTranscriber()
    fake_segments = [
        {"start": 1.0, "end": 2.5, "text": "你好", "speaker": "female", "position": "top"},
        {"start": 3.0, "end": 4.5, "text": "再见", "speaker": "male", "position": "bottom"},
    ]

    class FakeTranslationResult:
        success = True
        segments = [{"text": "Xin chào"}, {"text": "Tạm biệt"}]

    class FakeOrchestrator:
        def translate_segments(self, **kwargs):
            return FakeTranslationResult()

    monkeypatch.setattr(
        "app.translation.orchestrator.TranslationOrchestrator",
        lambda: FakeOrchestrator(),
    )

    results = transcriber.translate_segments(fake_segments, target_language="vi")
    assert len(results) == 2
    assert results[0].translated_text == "Xin chào"
    assert results[0].gender == "female"
    assert results[0].final_gender == "female"
    assert results[0].speaker_label == "Nữ"
    assert getattr(results[0], "position") == "top"

    assert results[1].translated_text == "Tạm biệt"
    assert results[1].gender == "male"
    assert results[1].final_gender == "male"
    assert results[1].speaker_label == "Nam"
    assert getattr(results[1], "position") == "bottom"

