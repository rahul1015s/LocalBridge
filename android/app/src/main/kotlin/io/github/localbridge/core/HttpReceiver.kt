package io.github.localbridge.core

import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSink
import org.json.JSONObject

private val JSON = "application/json".toMediaType()
private val BINARY = "application/octet-stream".toMediaType()

internal suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
    cont.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (cont.isActive) cont.resumeWithException(e)
        }

        override fun onResponse(call: Call, response: Response) {
            cont.resume(response)
        }
    })
}

internal fun mapIo(e: Throwable): ReceiverException = when {
    PinnedTls.isPinFailure(e) -> ReceiverException(
        ReceiverException.Kind.CERT_MISMATCH, null,
        "The computer's identity doesn't match the code you scanned. Nothing was sent.", e,
    )
    e is SocketTimeoutException -> ReceiverException(
        ReceiverException.Kind.TIMEOUT, null,
        "The computer didn't respond. Check that both devices are on the same network.", e,
    )
    e is ConnectException || e is UnknownHostException -> ReceiverException(
        ReceiverException.Kind.UNREACHABLE, null,
        "Couldn't reach the computer. Is it still receiving, and on the same network?", e,
    )
    e is SSLException -> ReceiverException(
        ReceiverException.Kind.CERT_MISMATCH, null, "Secure connection failed.", e,
    )
    else -> ReceiverException(ReceiverException.Kind.UNREACHABLE, null, "Connection lost.", e)
}

/** Request body that streams from any InputStream with a bounded buffer and hashes as it sends. */
internal class HashingStreamBody(
    private val open: () -> InputStream,
    private val declared: Long?,
    private val onProgress: (Long) -> Unit,
) : RequestBody() {
    private val digest = MessageDigest.getInstance("SHA-256")
    var sent: Long = 0
        private set

    val sha256Hex: String get() = digest.digest().joinToString("") { "%02x".format(it) }

    override fun contentType() = BINARY
    override fun contentLength(): Long = declared ?: -1L // -1 => chunked (unknown size)
    override fun isOneShot() = true

    override fun writeTo(sink: BufferedSink) {
        open().use { input ->
            val buf = ByteArray(BUFFER) // bounded: file size never affects memory use
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                digest.update(buf, 0, n)
                sink.write(buf, 0, n)
                sent += n
                if (declared != null && sent > declared) throw IOException("File grew while sending")
                onProgress(sent)
            }
        }
        if (declared != null && sent != declared) throw IOException("File shrank while sending")
    }

    companion object {
        const val BUFFER = 256 * 1024
    }
}

class HttpReceiver(
    private val client: OkHttpClient,
    private val host: String,
    private val port: Int,
    private val token: String,
) : ReceiverGateway {
    private fun url(vararg segments: String): HttpUrl =
        HttpUrl.Builder().scheme("https").host(host).port(port)
            .apply { segments.forEach { addPathSegment(it) } }.build()

    private fun req(url: HttpUrl) = Request.Builder().url(url).header("Authorization", "Bearer $token")

    private suspend fun exec(c: OkHttpClient, r: Request): Pair<Int, String> {
        try {
            c.newCall(r).await().use { resp -> return resp.code to (resp.body.string()) }
        } catch (e: IOException) {
            throw mapIo(e)
        }
    }

    private fun check(status: Int, body: String): String {
        if (status in 200..299) return body
        val err = runCatching { JSONObject(body).getJSONObject("error") }.getOrNull()
        val code = err?.optString("code")
        val msg = err?.optString("message")?.takeIf { it.isNotBlank() } ?: "Unexpected response ($status)."
        val kind = when {
            status == 401 -> ReceiverException.Kind.UNAUTHORIZED
            status == 429 -> ReceiverException.Kind.RATE_LIMITED
            code == "not_approved" -> ReceiverException.Kind.NOT_APPROVED
            status == 507 -> ReceiverException.Kind.STORAGE
            code == "integrity_failed" -> ReceiverException.Kind.INTEGRITY
            else -> ReceiverException.Kind.OTHER
        }
        throw ReceiverException(kind, code, msg)
    }

    override suspend fun createTransfer(files: List<FileSpec>): RemoteTransfer {
        val body = JSONObject().put(
            "files",
            org.json.JSONArray().also { arr ->
                files.forEach { f ->
                    arr.put(JSONObject().put("name", f.name).apply { if (f.size != null) put("size", f.size) })
                }
            },
        ).toString().toRequestBody(JSON)
        val (s, b) = exec(client, req(url("v1", "transfers")).post(body).build())
        return parseTransfer(check(s, b))
    }

    override suspend fun getTransfer(transferId: String): RemoteTransfer {
        val (s, b) = exec(client, req(url("v1", "transfers", transferId)).get().build())
        return parseTransfer(check(s, b))
    }

    override suspend fun cancelTransfer(transferId: String) {
        exec(client, req(url("v1", "transfers", transferId, "cancel")).post("".toRequestBody(JSON)).build())
    }

    override suspend fun upload(
        transferId: String,
        fileId: String,
        declaredSize: Long?,
        open: () -> InputStream,
        onProgress: (Long) -> Unit,
    ): UploadResult {
        val body = HashingStreamBody(open, declaredSize, onProgress)
        val r = req(url("v1", "transfers", transferId, "files", fileId, "content")).put(body).build()
        val c = client.newBuilder().readTimeout(120, TimeUnit.SECONDS).build()
        val (s, b) = exec(c, r)
        check(s, b)
        val serverHash = JSONObject(b).getString("sha256")
        val localHash = body.sha256Hex
        // Receiver hashed what it wrote; we hashed what we sent. They must agree.
        if (serverHash != localHash) {
            throw ReceiverException(ReceiverException.Kind.INTEGRITY, "integrity_failed", "The file was corrupted in transit.")
        }
        return UploadResult(localHash, body.sent)
    }

    override suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long): RemoteTransfer {
        val body = JSONObject().put("sha256", sha256).put("size", size).toString().toRequestBody(JSON)
        val c = client.newBuilder().readTimeout(60, TimeUnit.SECONDS).build()
        val (s, b) = exec(c, req(url("v1", "transfers", transferId, "files", fileId, "finalize")).post(body).build())
        return parseTransfer(check(s, b))
    }

    private fun parseTransfer(json: String): RemoteTransfer {
        val o = JSONObject(json)
        val files = o.getJSONArray("files")
        return RemoteTransfer(
            o.getString("transfer_id"),
            o.getString("state"),
            o.optString("reason").takeIf { it.isNotEmpty() && it != "null" },
            (0 until files.length()).map {
                val f = files.getJSONObject(it)
                RemoteFile(
                    f.getString("file_id"),
                    f.getString("name"),
                    if (f.isNull("size")) null else f.getLong("size"),
                    runCatching { FileStatus.valueOf(f.getString("state")) }.getOrDefault(FileStatus.PENDING),
                )
            },
        )
    }

    companion object {
        /** Pair using the QR contents. Blocks until the person confirms on the computer. */
        suspend fun pair(info: PairingInfo, deviceName: String, onVerified: () -> Unit): Pair<HttpReceiver, String> {
            val client = PinnedTls.client(info.fingerprint) { onVerified() }
            val url = HttpUrl.Builder().scheme("https").host(info.host).port(info.port)
                .addPathSegments("v1/pair").build()
            val body = JSONObject()
                .put("protocol", 1).put("session_id", info.sessionId)
                .put("secret", info.secret).put("device_name", deviceName.take(64))
                .toString().toRequestBody(JSON)
            val c = client.newBuilder().readTimeout(90, TimeUnit.SECONDS).build()
            val resp = try {
                c.newCall(Request.Builder().url(url).post(body).build()).await()
            } catch (e: IOException) {
                throw mapIo(e)
            }
            resp.use {
                val text = it.body.string()
                if (it.code != 200) {
                    val err = runCatching { JSONObject(text).getJSONObject("error") }.getOrNull()
                    val code = err?.optString("code")
                    val kind = when (code) {
                        "pairing_denied" -> ReceiverException.Kind.DENIED
                        "rate_limited" -> ReceiverException.Kind.RATE_LIMITED
                        "pairing_invalid" -> ReceiverException.Kind.UNAUTHORIZED
                        else -> ReceiverException.Kind.OTHER
                    }
                    val msg = when (code) {
                        "pairing_denied" -> "Pairing was declined on the computer."
                        "pairing_invalid" -> "This code has expired or was already used. Show a new code on the computer."
                        "rate_limited" -> "Too many attempts. Wait a minute and try again."
                        else -> err?.optString("message") ?: "Pairing failed."
                    }
                    throw ReceiverException(kind, code, msg)
                }
                val o = JSONObject(text)
                val token = o.getString("token")
                return HttpReceiver(client, info.host, info.port, token) to o.optString("receiver_name", "Computer")
            }
        }
    }
}
