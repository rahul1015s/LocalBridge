"""mDNS advertisement on the machine's real LAN interface (skipped if none / multicast blocked)."""

import threading
import time

import pytest

from localbridge.discovery import SERVICE_TYPE, Advertiser
from localbridge.network import local_ipv4_addresses
from localbridge.server import ReceiverConfig, ReceiverService

zeroconf = pytest.importorskip("zeroconf")


def browse(rid: str, host: str, secs: float = 6.0):
    found: dict = {}
    done = threading.Event()

    class L:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if info and info.properties.get(b"rid") == rid.encode():
                found["info"] = info
                done.set()

        def update_service(self, *a): ...
        def remove_service(self, *a): ...

    zc = zeroconf.Zeroconf(interfaces=[host])
    try:
        zeroconf.ServiceBrowser(zc, SERVICE_TYPE, L())
        done.wait(secs)
    finally:
        zc.close()
    return found.get("info")


@pytest.fixture
def lan_ip():
    addrs = local_ipv4_addresses()
    if not addrs:
        pytest.skip("no LAN interface")
    return addrs[0][1]


def test_advertised_service_is_discoverable_and_carries_only_the_rid(lan_ip):
    adv = Advertiser(lan_ip, 45678, "ab12cd34ef56ab78")
    adv.start_async()
    try:
        info = browse("ab12cd34ef56ab78", lan_ip)
        if info is None:
            pytest.skip("multicast not delivered on this network")
        assert info.port == 45678
        assert lan_ip in info.parsed_addresses()
        assert set(info.properties) == {b"v", b"rid"}  # no name, fingerprint or secret
    finally:
        adv.stop()


def test_stop_withdraws_the_service(lan_ip):
    adv = Advertiser(lan_ip, 45679, "0011223344556677")
    adv.start_async()
    if browse("0011223344556677", lan_ip, 5) is None:
        adv.stop()
        pytest.skip("multicast not delivered on this network")
    adv.stop()
    assert not adv.active
    time.sleep(1)
    assert browse("0011223344556677", lan_ip, 3) is None


def test_receiver_advertises_only_while_running_and_puts_rid_in_the_qr(tmp_path, lan_ip):
    t0 = time.time()
    svc = ReceiverService(ReceiverConfig(lan_ip, 0, tmp_path, advertise=True))
    svc.start()
    assert time.time() - t0 < 1.5  # advertising must not delay Start
    try:
        assert svc.rid and f"rid={svc.rid}" in svc.pairing_uri()
        assert "secret=" in svc.pairing_uri()
    finally:
        svc.stop()
    assert svc._adv is None


def test_advertising_can_be_disabled(tmp_path):
    svc = ReceiverService(ReceiverConfig("127.0.0.1", 0, tmp_path, advertise=False))
    svc.start()
    try:
        assert svc._adv is None and "rid=" not in svc.pairing_uri()
    finally:
        svc.stop()


def test_stop_during_registration_leaves_nothing_behind(lan_ip):
    adv = Advertiser(lan_ip, 45680, "8899aabbccddeeff")
    adv.start_async()
    adv.stop()  # immediately, while registration is still in flight
    time.sleep(3)
    assert not adv.active
    assert browse("8899aabbccddeeff", lan_ip, 3) is None


def test_failure_to_advertise_never_breaks_the_receiver(tmp_path):
    adv = Advertiser("203.0.113.9", 1, "ffffffffffffffff")  # not a local address
    assert adv.start() in (True, False)  # must not raise
    adv.stop()
