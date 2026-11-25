from services.gender_service import GenderService


def test_music_is_normalized_to_none():
    result = GenderService.normalize_result({
        "gender": "unknown",
        "confidence": 0.0,
        "status": "music_or_non_speech",
    })
    assert result["gender"] == "none"
    assert result["skip_tts"] is True


# The former ``GenderService._resolve`` window-consensus tests were removed:
# that logic no longer exists in GenderService (detection now runs in the
# external gender worker), so the tests could only ever fail.
