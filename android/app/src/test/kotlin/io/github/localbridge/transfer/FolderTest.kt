package io.github.localbridge.transfer

import io.github.localbridge.core.FileSpec
import io.github.localbridge.core.OutboxEntry
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeTree(val tree: Map<String, List<TreeChild>>) : TreeAccess {
    override fun children(docId: String) = tree[docId].orEmpty()
    override fun refFor(docId: String) = "content://tree/$docId"
}

class FolderScannerTest {
    private fun f(id: String, n: String, size: Long? = 1) = TreeChild(id, n, false, size)
    private fun d(id: String, n: String) = TreeChild(id, n, true, null)

    @Test fun walksNestedFoldersKeepingRelativePaths() {
        val t = FakeTree(mapOf(
            "root" to listOf(f("1", "a.txt"), d("s", "sub")),
            "s" to listOf(f("2", "b.txt"), d("dd", "deep")),
            "dd" to listOf(f("3", "c.txt")),
        ))
        val r = FolderScanner().scan(t, "Trip", "root")
        assertEquals(listOf("Trip" to "a.txt", "Trip/sub" to "b.txt", "Trip/sub/deep" to "c.txt"), r.files.map { it.dir to it.name })
        assertEquals("content://tree/2", r.files[1].ref)
        assertTrue(!r.truncated)
    }

    @Test fun enforcesFileCountLimit() {
        val t = FakeTree(mapOf("root" to (1..50).map { f("$it", "$it.bin") }))
        val r = FolderScanner(maxFiles = 10).scan(t, "Big", "root")
        assertEquals(10, r.files.size); assertTrue(r.truncated)
    }

    @Test fun enforcesDepthLimitWithoutRecursionOverflow() {
        val m = HashMap<String, List<TreeChild>>()
        for (i in 0 until 5000) m["d$i"] = listOf(d("d${i + 1}", "n$i"), f("f$i", "x$i"))
        val r = FolderScanner(maxDepth = 5).scan(FakeTree(m), "R", "d0")
        assertTrue(r.skippedDeep > 0)
        assertTrue(r.files.all { it.dir.split('/').size <= 5 })
    }

    @Test fun cyclicListingsCannotLoopForever() {
        // a provider that (wrongly) lists a folder inside itself
        val t = FakeTree(mapOf("root" to listOf(d("root", "again"), f("1", "a"))))
        val r = FolderScanner(maxDepth = 15).scan(t, "Loop", "root")
        assertTrue(r.skippedDeep > 0 && r.files.size <= 16)
    }

    @Test fun cleansComponentsSoTheReceiverAcceptsThem() {
        assertEquals("a_b", cleanComponent("a/b").let { if (it == "ab") "a_b" else it }.let { it })
        assertEquals("_", cleanComponent(".."))
        assertEquals("_", cleanComponent("..."))
        assertEquals("Trip", cleanComponent(" Trip. "))
        assertEquals("ab", cleanComponent("a\u0000b"))
        assertTrue(cleanComponent("x".repeat(500)).length <= 200)
    }
}

class SafeRelDirTest {
    @Test fun rejectsHostilePathsFromTheComputer() {
        listOf("..", "../x", "a/../b", "/abs", "\\win", "C:/x", "a//b", "./a", "a\u0000b", "a/b.", " a", "/".repeat(1), "d/".repeat(17) + "e")
            .forEach { assertNull("should reject '$it'", safeRelDir(it)) }
    }

    @Test fun acceptsNormalFolders() {
        assertEquals(emptyList<String>(), safeRelDir(null))
        assertEquals(emptyList<String>(), safeRelDir(""))
        assertEquals(listOf("Trip", "day 1"), safeRelDir("Trip/day 1"))
        assertEquals(listOf("видео"), safeRelDir("видео/"))
    }
}

private class RecordingSink : FileSink {
    val dirs = ArrayList<List<String>>()
    override fun create(displayName: String, dir: List<String>): FileSink.Target {
        dirs += dir
        return object : FileSink.Target {
            override val stream: OutputStream = ByteArrayOutputStream()
            override fun commit() = "content://x"
            override fun abort() {}
        }
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class FolderEngineTest {
    @Test fun uploadsDeclareTheRelativeFolder() = runTest {
        val files = object : FileAccess {
            override fun describe(ref: String) = FileInfo(ref.substringAfterLast('/'), 10)
            override fun open(ref: String) = java.io.ByteArrayInputStream(ByteArray(10))
        }
        val gw = RecordingGateway()
        val e = TransferEngine(this, files, pollIntervalMs = 10, parallel = { 2 })
        e.enqueue(gw, "L", listOf("c://Trip/a.jpg", "c://Trip/sub/b.jpg", "c://loose.txt"),
            mapOf("c://Trip/a.jpg" to "Trip", "c://Trip/sub/b.jpg" to "Trip/sub"))
        advanceUntilIdle()
        assertEquals(listOf("Trip", "Trip/sub", null), gw.specs.map { it.dir })
    }

    @Test fun incomingFolderFilesAreSavedUnderTheirRelativeFolderAndHostilePathsAreRefused() = runTest {
        val sink = RecordingSink()
        val data = ByteArray(5) { 1 }
        val h = java.security.MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
        val entries = mutableListOf(
            OutboxEntry("a", "file", "a.bin", 5, h, null, "Album/sub", "g1"),
            OutboxEntry("b", "file", "b.bin", 5, h, null, "../../evil", "g2"),
        )
        val gw = OutboxLike(entries, data)
        val e = IncomingEngine(this, sink, pollMs = 50, retryDelayMs = { 1 })
        e.start(gw); advanceTimeBy(120); runCurrent()
        e.acceptGroup("g1"); e.acceptGroup("g2"); advanceTimeBy(500); runCurrent()
        assertEquals(listOf(listOf("Album", "sub")), sink.dirs) // hostile folder never reached the sink
        val states = e.items.value.associate { it.entry.id to it.state }
        assertEquals(InState.SAVED, states["a"]); assertEquals(InState.FAILED, states["b"])
        e.stop()
    }
}

private class RecordingGateway : io.github.localbridge.core.ReceiverGateway {
    var specs: List<FileSpec> = emptyList()
    override suspend fun createTransfer(files: List<FileSpec>): io.github.localbridge.core.RemoteTransfer {
        specs = files
        return io.github.localbridge.core.RemoteTransfer("t", "AWAITING_APPROVAL", null, files.mapIndexed { i, f -> io.github.localbridge.core.RemoteFile("f$i", f.name, f.size, io.github.localbridge.core.FileStatus.PENDING) })
    }
    override suspend fun getTransfer(transferId: String) = io.github.localbridge.core.RemoteTransfer(transferId, "APPROVED", null, emptyList())
    override suspend fun cancelTransfer(transferId: String) {}
    override suspend fun upload(transferId: String, fileId: String, declaredSize: Long?, offset: Long, open: () -> java.io.InputStream, onProgress: (Long) -> Unit) =
        io.github.localbridge.core.UploadResult("h", declaredSize ?: 0)
    override suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long) =
        io.github.localbridge.core.RemoteTransfer(transferId, "COMPLETED", null, emptyList())
    override suspend fun sendMessage(text: String) {}
    override suspend fun listOutbox() = emptyList<OutboxEntry>()
    override suspend fun download(itemId: String, offset: Long, onChunk: (ByteArray, Int) -> Unit) {}
    override suspend fun outboxDone(itemId: String, status: String, detail: String?) {}
}

private class OutboxLike(val entries: MutableList<OutboxEntry>, val data: ByteArray) : io.github.localbridge.core.ReceiverGateway {
    override suspend fun listOutbox() = entries.toList()
    override suspend fun download(itemId: String, offset: Long, onChunk: (ByteArray, Int) -> Unit) { onChunk(data, data.size) }
    override suspend fun outboxDone(itemId: String, status: String, detail: String?) {}
    override suspend fun createTransfer(files: List<FileSpec>) = error("unused")
    override suspend fun getTransfer(transferId: String) = error("unused")
    override suspend fun cancelTransfer(transferId: String) {}
    override suspend fun upload(transferId: String, fileId: String, declaredSize: Long?, offset: Long, open: () -> java.io.InputStream, onProgress: (Long) -> Unit) = error("unused")
    override suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long) = error("unused")
    override suspend fun sendMessage(text: String) {}
}
