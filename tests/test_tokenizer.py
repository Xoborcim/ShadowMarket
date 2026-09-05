from shadowmarket.tokenizer import normalize_keyword, tokenize


def test_unique_tokens_per_message():
    tokens = tokenize("pineapple pineapple pineapple!!!")
    assert tokens == {"pineapple"}


def test_case_and_punctuation():
    assert "lmao" in tokenize("LMAO??")
    assert normalize_keyword("  PineApple  ") == "pineapple"


def test_custom_emoji_normalized_to_shortcode():
    assert ":blob:" in tokenize("wow <:Blob:1234567890> nice")
    assert normalize_keyword("<:Blob:1234567890>") == ":blob:"
    assert normalize_keyword(":Blob:") == ":blob:"


def test_unicode_emoji():
    assert "🍍" in tokenize("I love 🍍")
    assert normalize_keyword("🍍") == "🍍"
