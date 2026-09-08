from pathlib import Path

from shadowmarket.playlist import list_tracks, pick_track
from shadowmarket.tokenizer import keyword_hits


def test_list_and_pick_tracks(tmp_path: Path):
    (tmp_path / "001-a.opus").write_bytes(b"x")
    (tmp_path / "002-b.mp3").write_bytes(b"y")
    (tmp_path / "notes.txt").write_text("ignore")
    (tmp_path / ".hidden.opus").write_bytes(b"z")
    tracks = list_tracks(tmp_path)
    assert [t.name for t in tracks] == ["001-a.opus", "002-b.mp3"]
    assert pick_track(tmp_path) in tracks


def test_pick_empty_dir(tmp_path: Path):
    assert pick_track(tmp_path) is None


def test_back_trigger_is_whole_word():
    assert "back" in keyword_hits("im back", {"back"})
    assert "back" in keyword_hits("Back!", {"back"})
    assert "back" not in keyword_hits("backpack", {"back"})
    assert "back" not in keyword_hits("hi backsoon", {"back"})
