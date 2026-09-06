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


def test_phrase_stocks_match_the_whole_line():
    from shadowmarket.tokenizer import keyword_hits

    listed = {"hi back", "pineapple"}
    assert keyword_hits("hi back", listed) == {"hi back"}
    assert keyword_hits("Heisenberg said hi back in general", listed) == {"hi back"}
    assert "hi back" not in keyword_hits("high back", listed)
    assert keyword_hits("pineapple!!!", listed) == {"pineapple"}
    assert keyword_hits("im back", listed) == set()


def test_phrase_stocks_match_three_or_more_words():
    from shadowmarket.tokenizer import keyword_hits

    listed = {"what the dog doing", "hi back"}
    assert keyword_hits("what the dog doing", listed) == {"what the dog doing"}
    assert keyword_hits("lol what the dog doing in vc", listed) == {"what the dog doing"}
    assert keyword_hits("what the dog", listed) == set()
    assert keyword_hits("what the dog doings", listed) == set()


def test_normalize_collapses_spaces():
    assert normalize_keyword("  Hi   Back  ") == "hi back"
