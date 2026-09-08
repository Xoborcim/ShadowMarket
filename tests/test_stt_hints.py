from shadowmarket.stt import whisper_hint_text


def test_hint_text_dedupes_and_keeps_phrases():
    text = whisper_hint_text(
        ["Rizz", "tung tung tung sahur", "skibidi toilet"],
        extras=["rizz", "skibidi toilet"],
    )
    assert text == "rizz, skibidi toilet, tung tung tung sahur"


def test_hint_text_skips_oversized_terms_and_keeps_later_short_ones():
    text = whisper_hint_text(
        ["rizz", "skibidi toilet", "ohio"],
        extras=[],
        max_chars=18,
    )
    assert text == "rizz, ohio"
