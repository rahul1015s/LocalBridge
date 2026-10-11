package io.github.localbridge.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class LocatorTest {
    private val fp = "a".repeat(64)
    private fun info(rid: String? = "0123456789abcdef", host: String = "192.168.43.2") =
        PairingInfo(host, 4000, "sid12345678", fp, "s".repeat(43), rid)

    private fun unreachable() = ReceiverException(ReceiverException.Kind.UNREACHABLE, null, "no route")

    @Test fun usesTheQrAddressFirstAndNeverTouchesDiscoveryWhenItWorks() = runBlocking {
        var located = false
        val r = pairWithLocator(info(), { located = true; null }) { it.host }
        assertEquals("192.168.43.2", r); assertTrue(!located)
    }

    @Test fun fallsBackToTheAnnouncedAddressWhenUnreachable() = runBlocking {
        val tried = ArrayList<String>()
        val r = pairWithLocator(info(), { Endpoint("192.168.43.77", 5555) }) { t ->
            tried += "${t.host}:${t.port}"
            if (t.host == "192.168.43.2") throw unreachable() else "ok"
        }
        assertEquals("ok", r)
        assertEquals(listOf("192.168.43.2:4000", "192.168.43.77:5555"), tried)
    }

    @Test fun certificateMismatchIsNeverRetried() = runBlocking {
        var located = false
        try {
            pairWithLocator(info(), { located = true; Endpoint("192.168.43.77", 5555) }) {
                throw ReceiverException(ReceiverException.Kind.CERT_MISMATCH, null, "bad cert")
            }
            fail()
        } catch (e: ReceiverException) { assertEquals(ReceiverException.Kind.CERT_MISMATCH, e.kind) }
        assertTrue(!located)
    }

    @Test fun nonLocalAnnouncedAddressesAreIgnored() = runBlocking {
        for (bad in listOf("8.8.8.8", "example.com")) {
            try {
                pairWithLocator(info(), { Endpoint(bad, 5555) }) { throw unreachable() }
                fail()
            } catch (e: ReceiverException) { assertEquals(ReceiverException.Kind.UNREACHABLE, e.kind) }
        }
    }

    @Test fun noRidOrNoLocatorMeansNoFallback() = runBlocking {
        var calls = 0
        for ((i, l) in listOf(info(rid = null) to Locator { Endpoint("10.0.0.5", 1) }, info() to null)) {
            try { pairWithLocator(i, l) { calls++; throw unreachable() }; fail() } catch (_: ReceiverException) {}
        }
        assertEquals(2, calls)
    }

    @Test fun sameAddressIsNotRetried() = runBlocking {
        var calls = 0
        try { pairWithLocator(info(), { Endpoint("192.168.43.2", 4000) }) { calls++; throw unreachable() }; fail() } catch (_: ReceiverException) {}
        assertEquals(1, calls)
    }

    @Test fun pairingUriAcceptsAndValidatesRid() {
        val base = "localbridge://pair?v=1&host=192.168.1.5&port=4000&sid=abcdEFGH1234&fp=$fp&secret=${"s".repeat(43)}"
        assertEquals("0123456789abcdef", PairingInfo.parse("$base&rid=0123456789abcdef").getOrThrow().rid)
        assertEquals(null, PairingInfo.parse(base).getOrThrow().rid)
        assertTrue(PairingInfo.parse("$base&rid=ZZ").isFailure)
    }
}
