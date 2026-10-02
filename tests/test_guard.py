from hosts.guard import Guard, Status


def test_states(tmp_path):
    guard = Guard(tmp_path / "state.json")
    target = tmp_path / "out.conf"
    assert guard.status(target, b"a") is Status.NEW

    target.write_bytes(b"hand made")
    assert guard.status(target, b"a") is Status.UNKNOWN
    assert guard.status(target, b"hand made") is Status.SAME

    guard.record(target, b"hand made")
    assert guard.status(target, b"a") is Status.OURS

    target.write_bytes(b"edited")
    assert guard.status(target, b"a") is Status.CHANGED


def test_persists(tmp_path):
    guard = Guard(tmp_path / "state.json")
    guard.record(tmp_path / "x", b"1")
    guard.save()
    assert Guard(tmp_path / "state.json").hashes == guard.hashes
