"""Folder transfer: hostile relative paths, symlink planting, aliasing, nesting."""

import hashlib
import os
import sys

import pytest

from localbridge.models import ApiError
from localbridge.storage import Destination, sanitize_relpath

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- path validation (pure) -----------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "..",
        "../x",
        "a/../b",
        "a/..",
        "/abs/path",
        "C:/Windows",
        "c:\\Windows",
        "..\\..\\x",
        "a//b",
        "./a",
        "a/./b",
        "a\x00b",
        "a\nb/c",
        "a/ b",
        "a/b ",
        "a/b.",
        "evil\u202egnp/x",
        "/".join(["d"] * 17),
    ],
)
def test_hostile_relpaths_are_rejected_not_repaired(raw):
    with pytest.raises(ApiError) as e:
        sanitize_relpath(raw)
    assert e.value.code == "invalid_path"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("", []),
        ("Trip", ["Trip"]),
        ("Trip/day 1/raw", ["Trip", "day 1", "raw"]),
        ("Trip/", ["Trip"]),
        ("видео/Альбом", ["видео", "Альбом"]),
        ("a\\b", ["a", "b"]),  # Windows separator from some senders
        ("e\u0301/x", ["\u00e9", "x"]),
    ],
)
def test_valid_relpaths(raw, expected):
    assert sanitize_relpath(raw) == expected


def test_max_depth_boundary():
    assert len(sanitize_relpath("/".join(["d"] * 16))) == 16


# ---- storage -----------------------------------------------------------------------------------
def put(d: Destination, tmp_id: str, data=b"x"):
    pf = d.open_partial("t" * 8, tmp_id)
    pf.write(data)
    pf.close()
    return pf.path


def test_nested_dirs_created_and_duplicates_renamed(tmp_path):
    d = Destination(tmp_path)
    tm: dict[str, str] = {}
    a = d.finalize(put(d, "f1"), "pic.jpg", ["Trip", "day1"], tm)
    b = d.finalize(put(d, "f2"), "pic.jpg", ["Trip", "day1"], tm)
    assert a == tmp_path / "Trip" / "day1" / "pic.jpg"
    assert b.name == "pic (1).jpg" and b.parent == a.parent
    assert tm == {"Trip": "Trip"}


def test_preexisting_folder_is_never_merged_into(tmp_path):
    (tmp_path / "Trip").mkdir()
    (tmp_path / "Trip" / "keep.txt").write_text("mine")
    d = Destination(tmp_path)
    tm: dict[str, str] = {}
    out = d.finalize(put(d, "f1"), "keep.txt", ["Trip"], tm)
    assert out == tmp_path / "Trip (1)" / "keep.txt"
    assert (tmp_path / "Trip" / "keep.txt").read_text() == "mine"
    # a second file of the same transfer goes to the same alias
    assert d.finalize(put(d, "f2"), "b.txt", ["Trip"], tm).parent.name == "Trip (1)"
    # a new transfer (new map) gets a new alias
    assert d.finalize(put(d, "f3"), "c.txt", ["Trip"], {}).parent.name == "Trip (2)"


@posix_only
def test_symlinked_top_folder_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "inbox"
    dest.mkdir()
    (dest / "Trip").symlink_to(outside)
    d = Destination(dest)
    out = d.finalize(put(d, "f1"), "x.txt", ["Trip"], {})
    assert out.parent.name == "Trip (1)" and not out.parent.is_symlink()
    assert list(outside.iterdir()) == []


@posix_only
def test_symlink_planted_mid_transfer_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "inbox"
    dest.mkdir()
    d = Destination(dest)
    tm: dict[str, str] = {}
    d.finalize(put(d, "f1"), "a.txt", ["Trip", "sub"], tm)
    # attacker swaps the sub-folder for a link to somewhere sensitive
    (dest / "Trip" / "sub" / "a.txt").unlink()
    (dest / "Trip" / "sub").rmdir()
    (dest / "Trip" / "sub").symlink_to(outside)
    with pytest.raises(ApiError) as e:
        d.finalize(put(d, "f2"), "b.txt", ["Trip", "sub"], tm)
    assert e.value.code == "unsafe_path"
    assert list(outside.iterdir()) == []


@posix_only
def test_symlinked_leaf_name_is_not_overwritten(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("secret")
    dest = tmp_path / "inbox"
    dest.mkdir()
    (dest / "a.txt").symlink_to(victim)
    d = Destination(dest)
    out = d.finalize(put(d, "f1", b"new"), "a.txt", [], {})
    assert out.name == "a (1).txt" and victim.read_text() == "secret"


def test_file_in_the_way_of_a_folder_is_refused(tmp_path):
    d = Destination(tmp_path)
    tm: dict[str, str] = {}
    d.finalize(put(d, "f1"), "x", ["Trip"], tm)
    (tmp_path / "Trip" / "sub").write_text("i am a file")
    with pytest.raises(ApiError):
        d.finalize(put(d, "f2"), "y", ["Trip", "sub"], tm)


def test_partial_is_consumed_after_finalize(tmp_path):
    d = Destination(tmp_path)
    p = put(d, "f1")
    d.finalize(p, "a", ["T"], {})
    assert not p.exists()


# ---- over the real API ----
def upload_all(h, c, t, payloads):
    for i, data in enumerate(payloads):
        fid = t["files"][i]["file_id"]
        assert (
            c.request("PUT", f"/v1/transfers/{t['transfer_id']}/files/{fid}/content", body=data)[0]
            == 200
        )
        st, res = c.request(
            "POST",
            f"/v1/transfers/{t['transfer_id']}/files/{fid}/finalize",
            json_body={"sha256": sha(data), "size": len(data)},
        )
        assert st == 200, res
    return res


def test_folder_transfer_end_to_end(h):
    c = h.pair()
    files = [
        {"name": "a.jpg", "size": 3, "dir": "Trip/day1"},
        {"name": "b.jpg", "size": 3, "dir": "Trip/day1"},
        {"name": "notes.txt", "size": 5, "dir": "Trip"},
        {"name": "root.txt", "size": 4},
    ]
    t = h.new_transfer(c, files)
    assert [f["dir"] for f in t["files"]] == ["Trip/day1", "Trip/day1", "Trip", None]
    h.svc.core.transfers.approve(t["transfer_id"])
    res = upload_all(h, c, t, [b"AAA", b"BBB", b"notes", b"root"])
    assert res["state"] == "COMPLETED"
    assert (h.dest / "Trip" / "day1" / "a.jpg").read_bytes() == b"AAA"
    assert (h.dest / "Trip" / "day1" / "b.jpg").read_bytes() == b"BBB"
    assert (h.dest / "Trip" / "notes.txt").read_bytes() == b"notes"
    assert (h.dest / "root.txt").read_bytes() == b"root"


@pytest.mark.parametrize("bad", ["../../etc", "/etc", "a/../../b", "..\\x", "a//b", "C:/x"])
def test_api_rejects_hostile_dirs_before_anything_is_written(h, bad):
    c = h.pair()
    st, data = c.request(
        "POST", "/v1/transfers", json_body={"files": [{"name": "x", "size": 1, "dir": bad}]}
    )
    assert st == 422 and data["error"]["code"] == "invalid_path"
    assert not [p for p in h.dest.iterdir() if p.name != ".localbridge-partial"]


def test_one_bad_path_rejects_the_whole_transfer(h):
    c = h.pair()
    st, _ = c.request(
        "POST",
        "/v1/transfers",
        json_body={"files": [{"name": "ok", "size": 1, "dir": "Fine"}, {"name": "x", "dir": ".."}]},
    )
    assert st == 422
    assert c.request("GET", "/v1/transfers/none")[0] == 404
    assert not (h.dest / "Fine").exists()


def test_bad_file_name_with_separators_is_reduced_to_leaf(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "../../x.txt", "size": 1, "dir": "Safe"}])
    h.svc.core.transfers.approve(t["transfer_id"])
    upload_all(h, c, t, [b"1"])
    assert (h.dest / "Safe" / "x.txt").exists()
    assert not (h.dest.parent / "x.txt").exists()


def test_transfer_view_never_leaks_server_paths(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a", "size": 1, "dir": "T"}])
    assert str(h.dest) not in str(t)


def test_many_files_in_one_transfer(h):
    c = h.pair()
    n = 300
    t = h.new_transfer(c, [{"name": f"{i}.bin", "size": 1, "dir": f"F/{i % 5}"} for i in range(n)])
    h.svc.core.transfers.approve(t["transfer_id"])
    res = upload_all(h, c, t, [bytes([i % 256]) for i in range(n)])
    assert res["state"] == "COMPLETED"
    assert sum(1 for _ in (h.dest / "F").rglob("*.bin")) == n


def test_resume_still_works_inside_folders(h):
    # the folder path does not interfere with partial/resume handling
    c = h.pair()
    t = h.new_transfer(c, [{"name": "big.bin", "size": 10, "dir": "D"}])
    h.svc.core.transfers.approve(t["transfer_id"])
    upload_all(h, c, t, [b"0123456789"])
    assert (h.dest / "D" / "big.bin").read_bytes() == b"0123456789"
    assert os.listdir(h.dest / "D") == ["big.bin"]


def test_path_fallback_finalizer_matches_fd_finalizer(tmp_path, monkeypatch):
    """The Windows code path (no dir_fd) must behave the same: nesting, aliasing, no overwrite."""
    import localbridge.storage as st

    monkeypatch.setattr(st, "_HAS_DIR_FD", False)
    d = Destination(tmp_path)
    (tmp_path / "Trip").mkdir()
    tm: dict[str, str] = {}
    a = d.finalize(put(d, "f1"), "pic.jpg", ["Trip", "day1"], tm)
    b = d.finalize(put(d, "f2"), "pic.jpg", ["Trip", "day1"], tm)
    assert a == tmp_path / "Trip (1)" / "day1" / "pic.jpg" and b.name == "pic (1).jpg"
    (tmp_path / "x.txt").write_text("keep")
    c = d.finalize(put(d, "f3", b"new"), "x.txt", [], {})
    assert c.name == "x (1).txt" and (tmp_path / "x.txt").read_text() == "keep"
    assert not list((tmp_path / ".localbridge-partial").iterdir())


@posix_only
def test_path_fallback_refuses_symlinked_folders(tmp_path, monkeypatch):
    import localbridge.storage as st

    monkeypatch.setattr(st, "_HAS_DIR_FD", False)
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "inbox"
    dest.mkdir()
    d = Destination(dest)
    tm: dict[str, str] = {}
    d.finalize(put(d, "f1"), "a", ["T", "sub"], tm)
    (dest / "T" / "sub" / "a").unlink()
    (dest / "T" / "sub").rmdir()
    (dest / "T" / "sub").symlink_to(outside)
    with pytest.raises(ApiError):
        d.finalize(put(d, "f2"), "b", ["T", "sub"], tm)
    assert list(outside.iterdir()) == []


def test_windows_name_rules():
    from localbridge.storage import sanitize_filename

    assert sanitize_filename("CON", windows=True) == "_CON"
    assert sanitize_filename("a<b>:c.txt", windows=True) == "a_b__c.txt"
    assert sanitize_filename("name. ", windows=True) == "name"
    assert sanitize_filename("CON", windows=False) == "CON"
