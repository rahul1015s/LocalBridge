package io.github.localbridge.core

import java.io.InputStream

enum class FileStatus { PENDING, RECEIVING, VERIFYING, INTERRUPTED, DONE, FAILED, CANCELLED }

data class RemoteFile(val fileId: String, val name: String, val size: Long?, val state: FileStatus, val received: Long = 0)

/** Something the computer wants to send to this phone. The person must accept it first. */
data class OutboxEntry(val id: String, val kind: String, val name: String, val size: Long, val sha256: String?, val text: String?, val dir: String? = null, val group: String? = null)

data class RemoteTransfer(
    val transferId: String,
    val state: String,
    val reason: String?,
    val files: List<RemoteFile>,
)

data class FileSpec(val name: String, val size: Long?, val dir: String? = null)

data class UploadResult(val sha256: String, val bytes: Long)

/** Typed failures with messages that are safe to show to people. */
class ReceiverException(
    val kind: Kind,
    val code: String?,
    override val message: String,
    cause: Throwable? = null,
) : Exception(message, cause) {
    enum class Kind {
        UNREACHABLE, TIMEOUT, CERT_MISMATCH, UNAUTHORIZED, DENIED, RATE_LIMITED,
        NOT_APPROVED, STORAGE, INTEGRITY, CANCELLED, OTHER,
    }
}

/** Everything the app needs from a receiver. Implemented over HTTPS; faked in unit tests. */
interface ReceiverGateway {
    suspend fun createTransfer(files: List<FileSpec>): RemoteTransfer
    suspend fun getTransfer(transferId: String): RemoteTransfer
    suspend fun cancelTransfer(transferId: String)
    suspend fun upload(
        transferId: String,
        fileId: String,
        declaredSize: Long?,
        offset: Long,
        open: () -> InputStream,
        onProgress: (Long) -> Unit,
    ): UploadResult
    suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long): RemoteTransfer

    suspend fun sendMessage(text: String)
    suspend fun listOutbox(): List<OutboxEntry>

    /** Streams the item from [offset] (HTTP Range); [onChunk] gets each piece as it arrives. */
    suspend fun download(itemId: String, offset: Long, onChunk: (ByteArray, Int) -> Unit)
    suspend fun outboxDone(itemId: String, status: String, detail: String?)
}
