import sys
from types import SimpleNamespace

from shadowmarket.voice_sink import dave_decrypt_opus


class _FakeDavey:
    class MediaType:
        audio = "audio"


class _Session:
    def __init__(self, *, ready: bool = True, result: bytes = b"opus", error: bool = False) -> None:
        self.ready = ready
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    def decrypt(self, user_id, media_type, payload):
        self.calls.append((user_id, media_type, payload))
        if self.error:
            raise RuntimeError("passthrough")
        return self.result


def test_dave_decrypt_skips_when_session_missing():
    assert dave_decrypt_opus(SimpleNamespace(), 1, b"raw") == b"raw"


def test_dave_decrypt_skips_unready_session(monkeypatch):
    monkeypatch.setitem(sys.modules, "davey", _FakeDavey)
    client = SimpleNamespace(
        _connection=SimpleNamespace(dave_session=_Session(ready=False), dave_protocol_version=1)
    )
    assert dave_decrypt_opus(client, 1, b"raw") == b"raw"


def test_dave_decrypt_passthrough_on_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "davey", _FakeDavey)
    session = _Session(error=True)
    client = SimpleNamespace(
        _connection=SimpleNamespace(dave_session=session, dave_protocol_version=1)
    )
    assert dave_decrypt_opus(client, 7, b"cipher") == b"cipher"


def test_dave_decrypt_uses_session(monkeypatch):
    monkeypatch.setitem(sys.modules, "davey", _FakeDavey)
    session = _Session(result=b"plain-opus")
    client = SimpleNamespace(
        _connection=SimpleNamespace(dave_session=session, dave_protocol_version=1)
    )
    assert dave_decrypt_opus(client, 42, b"cipher") == b"plain-opus"
    assert session.calls[0][0] == 42
    assert session.calls[0][2] == b"cipher"
