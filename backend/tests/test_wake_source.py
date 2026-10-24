"""What woke the box: the wake Windows logged, compared with the record at the sleep."""
from backend.services import wake_source


def _event(record, source="", kind=None):
    return {"record": record, "source": source, "type": kind}


def test_a_newer_wake_record_is_named(monkeypatch, caplog):
    records = iter([_event(10, "Old"), _event(11, "Intel(R) Wireless Bluetooth(R)", 1)])
    monkeypatch.setattr(wake_source.sleep, "last_wake", lambda: next(records))
    caplog.set_level("INFO")
    wake_source.remember()
    assert wake_source.report() == "Intel(R) Wireless Bluetooth(R)"
    assert "woken by Intel(R) Wireless Bluetooth(R)" in caplog.text


def test_the_record_from_before_the_sleep_is_not_this_wake(monkeypatch, caplog):
    """Modern Standby logs no wake: the newest record is still the old one."""
    monkeypatch.setattr(wake_source.sleep, "last_wake", lambda: _event(10, "Power Button"))
    caplog.set_level("INFO")
    wake_source.remember()
    assert wake_source.report() is None
    assert "no wake for this sleep" in caplog.text


def test_a_first_ever_record_counts_when_none_was_kept(monkeypatch):
    records = iter([None, _event(1, "", 0)])
    monkeypatch.setattr(wake_source.sleep, "last_wake", lambda: next(records))
    wake_source.remember()
    assert wake_source.report() == "an unnamed source (type 0)"


def test_a_wake_without_a_record_from_before_says_so(monkeypatch, caplog):
    monkeypatch.setattr(wake_source, "_remembered", False)
    caplog.set_level("INFO")
    assert wake_source.report() is None
    assert "no record from before the sleep" in caplog.text


def test_a_record_is_used_once(monkeypatch):
    monkeypatch.setattr(wake_source.sleep, "last_wake", lambda: _event(5, "Power Button"))
    monkeypatch.setattr(wake_source, "_before", 4)
    monkeypatch.setattr(wake_source, "_remembered", True)
    assert wake_source.report() == "Power Button"
    assert wake_source.report() is None
