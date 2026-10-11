import os

import pytest

from localbridge.models import ApiError, FileSpec, Limits, TransferState
from localbridge.pairing import PairingManager
from localbridge.security import FailureLimiter, constant_time_equal, new_token
from localbridge.storage import Destination, sanitize_filename
from localbridge.transfers import TransferManager


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---- security ---------------------------------------------------------------------
def test_tokens_are_unique_and_long():
    toks = {new_token() for _ in range(200)}
    assert len(toks) == 200
    assert all(len(t) >= 43 for t in toks)  # 32 bytes b64url


def test_constant_time_equal():
    assert constant_time_equal("abc", "abc")
    assert not constant_time_equal("abc", "abd")
    assert not constant_time_equal("abc", "abcd")


def test_failure_limiter_blocks_and_expires():
    clk = FakeClock()
    lim = FailureLimiter(3, 60, clock=clk)
    for _ in range(3):
        assert not lim.blocked("ip")
        lim.record_failure("ip")
    assert lim.blocked("ip")
    clk.t += 61
    assert not lim.blocked("ip")


def test_failure_limiter_is_bounded():
    lim = FailureLimiter(3, 60, max_keys=5)
    for i in range(50):
        lim.record_failure(f"ip{i}")
    assert len(lim._fails) <= 5


# ---- pairing ------------------------------------------------------------------------
def make_pm(clk=None):
    return PairingManager(Limits(), clock=clk or FakeClock())


def redeem(pm, offer, name="Phone"):
    p = pm.begin(offer.session_id, offer.secret, name, "1.2.3.4")
    pm.decide(p.pairing_id, True)
    return pm.finish(p)


def test_pairing_happy_path_and_auth():
    pm = make_pm()
    s, tok = redeem(pm, pm.new_offer())
    assert pm.authenticate(tok).session_id == s.session_id


def test_pairing_secret_is_single_use():
    pm = make_pm()
    o = pm.new_offer()
    redeem(pm, o)
    with pytest.raises(ApiError) as e:
        pm.begin(o.session_id, o.secret, "Replay", "1.2.3.4")
    assert e.value.status == 401


def test_pairing_expires():
    clk = FakeClock()
    pm = make_pm(clk)
    o = pm.new_offer()
    clk.t += Limits().pairing_ttl_seconds + 1
    with pytest.raises(ApiError):
        pm.begin(o.session_id, o.secret, "Late", "1.2.3.4")
    assert pm.current_offer() is None


def test_pairing_wrong_secret_and_wrong_sid_same_error():
    pm = make_pm()
    o = pm.new_offer()
    errs = []
    for sid, sec in [(o.session_id, "x" * 43), ("y" * 16, o.secret)]:
        with pytest.raises(ApiError) as e:
            pm.begin(sid, sec, "Evil", "1.2.3.4")
        errs.append((e.value.status, e.value.code))
    assert errs[0] == errs[1]


def test_new_offer_invalidates_old():
    pm = make_pm()
    old = pm.new_offer()
    pm.new_offer()
    with pytest.raises(ApiError):
        pm.begin(old.session_id, old.secret, "Old", "1.2.3.4")


def test_denied_pairing_creates_no_session():
    pm = make_pm()
    o = pm.new_offer()
    p = pm.begin(o.session_id, o.secret, "Nope", "1.2.3.4")
    pm.decide(p.pairing_id, False)
    assert pm.finish(p) is None
    assert pm.sessions() == []


def test_session_expiry_and_revocation():
    clk = FakeClock()
    pm = make_pm(clk)
    s, tok = redeem(pm, pm.new_offer())
    pm.revoke_session(s.session_id)
    with pytest.raises(ApiError):
        pm.authenticate(tok)
    s2, tok2 = redeem(pm, pm.new_offer())
    clk.t += Limits().session_ttl_seconds + 1
    with pytest.raises(ApiError):
        pm.authenticate(tok2)


def test_bounded_pending_pairings():
    pm = make_pm()
    for _ in range(Limits().max_pending_pairings):
        o = pm.new_offer()
        pm.begin(o.session_id, o.secret, "P", "1.1.1.1")
    o = pm.new_offer()
    with pytest.raises(ApiError) as e:
        pm.begin(o.session_id, o.secret, "P", "1.1.1.1")
    assert e.value.status == 429


# ---- filenames ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\x.dll", "x.dll"),
        ("/abs/path/file.txt", "file.txt"),
        ("..", "file"),
        (".", "file"),
        ("", "file"),
        ("   ", "file"),
        ("a\x00b.txt", "ab.txt"),
        ("evil‮gnp.exe", "evilgnp.exe"),
        ("photo.jpg", "photo.jpg"),
        ("видео.mp4", "видео.mp4"),
        ("é.txt", "é.txt"),  # NFC
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_long_name_keeps_extension_and_limit():
    n = sanitize_filename("é" * 400 + ".mp4")
    assert n.endswith(".mp4") and len(n.encode()) <= 255


# ---- storage --------------------------------------------------------------------------------
def test_finalize_never_overwrites(tmp_path):
    d = Destination(tmp_path)
    (tmp_path / "a.txt").write_text("keep")
    names = []
    for i in range(3):
        pf = d.open_partial("t" * 8, f"f{i}")
        pf.write(b"x")
        pf.close()
        names.append(d.finalize(pf.path, "a.txt").name)
    assert names == ["a (1).txt", "a (2).txt", "a (3).txt"]
    assert (tmp_path / "a.txt").read_text() == "keep"


def test_partial_perms_and_symlink_not_followed(tmp_path):
    d = Destination(tmp_path)
    pf = d.open_partial("t" * 8, "f1")
    assert (os.stat(pf.path).st_mode & 0o777) == 0o600
    pf.discard()
    victim = tmp_path / "victim"
    victim.write_text("secret")
    link = tmp_path / ".localbridge-partial" / ("t" * 8 + ".f2.part")
    link.symlink_to(victim)
    with pytest.raises(OSError):  # O_EXCL|O_NOFOLLOW refuses the planted symlink
        d.open_partial("t" * 8, "f2")
    assert victim.read_text() == "secret"


def test_bad_ids_rejected(tmp_path):
    d = Destination(tmp_path)
    with pytest.raises(ApiError):
        d.open_partial("../../x", "f")
    with pytest.raises(ApiError):
        d.open_partial("t" * 8, "a/b")


def test_disk_full_maps_to_507(tmp_path, monkeypatch):
    d = Destination(tmp_path)
    pf = d.open_partial("t" * 8, "f1")

    def boom(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", boom)
    with pytest.raises(ApiError) as e:
        pf.write(b"data")
    assert e.value.status == 507


def test_space_check_and_missing_destination(tmp_path):
    d = Destination(tmp_path)
    with pytest.raises(ApiError) as e:
        d.check_space(10**18)
    assert e.value.status == 507
    with pytest.raises(ApiError):
        Destination(tmp_path / "nope").validate()


# ---- transfer state machine ----------------------------------------------------------------------
def make_tm(**kw):
    clk = FakeClock()
    return TransferManager(Limits(**kw), clock=clk), clk


def specs(*sizes):
    return [FileSpec(name=f"f{i}.bin", size=s) for i, s in enumerate(sizes)]


def test_content_refused_before_approval():
    tm, _ = make_tm()
    t = tm.create("s1", "Phone", specs(10))
    assert t.state == TransferState.AWAITING_APPROVAL
    with pytest.raises(ApiError) as e:
        tm.begin_upload(t.transfer_id, "s1", t.files[0].file_id)
    assert e.value.code == "not_approved"


def test_invalid_transitions_rejected():
    tm, _ = make_tm()
    t = tm.create("s1", "P", specs(1))
    tm.reject(t.transfer_id)
    with pytest.raises(ApiError):
        tm.approve(t.transfer_id)  # REJECTED -> APPROVED is illegal


def test_cross_session_access_is_not_found():
    tm, _ = make_tm()
    t = tm.create("s1", "P", specs(1))
    with pytest.raises(ApiError) as e:
        tm.get(t.transfer_id, "s2")
    assert e.value.status == 404


def test_approval_expires():
    tm, clk = make_tm()
    t = tm.create("s1", "P", specs(1))
    clk.t += Limits().approval_timeout_seconds + 1
    assert tm.get(t.transfer_id, "s1").state == TransferState.EXPIRED


def test_cancel_is_idempotent_and_final():
    tm, _ = make_tm()
    t = tm.create("s1", "P", specs(1))
    tm.cancel(t.transfer_id, "s1")
    assert tm.cancel(t.transfer_id, "s1").state == TransferState.CANCELLED
    with pytest.raises(ApiError):
        tm.approve(t.transfer_id)


def test_limits_on_create():
    tm, _ = make_tm(max_file_size=100, max_transfer_size=150, max_files_per_transfer=3)
    with pytest.raises(ApiError) as e:
        tm.create("s", "P", specs(101))
    assert e.value.code == "file_too_large"
    with pytest.raises(ApiError) as e:
        tm.create("s", "P", specs(100, 100))
    assert e.value.code == "transfer_too_large"
    with pytest.raises(ApiError) as e:
        tm.create("s", "P", specs(1, 1, 1, 1))
    assert e.value.code == "too_many_files"


def test_pending_transfer_cap():
    tm, _ = make_tm(max_pending_transfers=2)
    tm.create("s", "P", specs(1))
    tm.create("s", "P", specs(1))
    with pytest.raises(ApiError) as e:
        tm.create("s", "P", specs(1))
    assert e.value.status == 429


def test_unknown_size_files_allowed():
    tm, _ = make_tm()
    t = tm.create("s", "P", [FileSpec(name="stream.bin", size=None)])
    assert not t.size_known


def test_concurrency_limit_on_uploads():
    tm, _ = make_tm(max_active_transfers=1)
    a = tm.create("s", "P", specs(1))
    b = tm.create("s", "P", specs(1))
    tm.approve(a.transfer_id)
    tm.approve(b.transfer_id)
    tm.begin_upload(a.transfer_id, "s", a.files[0].file_id)
    with pytest.raises(ApiError) as e:
        tm.begin_upload(b.transfer_id, "s", b.files[0].file_id)
    assert e.value.status == 429


def test_duplicate_upload_of_same_file_refused():
    tm, _ = make_tm()
    t = tm.create("s", "P", specs(1))
    tm.approve(t.transfer_id)
    tm.begin_upload(t.transfer_id, "s", t.files[0].file_id)
    with pytest.raises(ApiError):
        tm.begin_upload(t.transfer_id, "s", t.files[0].file_id)


def test_ids_are_unguessable_unique():
    tm, _ = make_tm(max_pending_transfers=1000)
    ids = {tm.create("s", "P", specs(1)).transfer_id for _ in range(300)}
    assert len(ids) == 300
    assert all(len(i) >= 16 for i in ids)
