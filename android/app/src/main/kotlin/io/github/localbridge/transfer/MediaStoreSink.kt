package io.github.localbridge.transfer

import android.content.ContentResolver
import android.content.ContentValues
import android.os.Environment
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import java.io.BufferedOutputStream
import java.io.IOException
import java.io.OutputStream

/** One path component, no control characters, never empty. Remote names are untrusted. */
fun safeFileName(raw: String, max: Int = 200): String {
    var n = raw.substringAfterLast('/').substringAfterLast('\\')
    n = n.filter { !it.isISOControl() && it != '\u202e' }.trim().trim('.').trim()
    if (n.isEmpty()) n = "file"
    if (n.length > max) {
        val dot = n.lastIndexOf('.')
        val ext = if (dot > 0 && n.length - dot <= 16) n.substring(dot) else ""
        n = n.take(max - ext.length) + ext
    }
    return n
}

/** Saves into Downloads/LocalBridge via MediaStore (no storage permission needed, Android 10+). */
class MediaStoreSink(private val resolver: ContentResolver) : FileSink {
    override fun create(displayName: String): FileSink.Target {
        val name = safeFileName(displayName)
        val ext = name.substringAfterLast('.', "").lowercase()
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/LocalBridge")
            put(MediaStore.Downloads.IS_PENDING, 1) // invisible to other apps until complete
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IOException("Could not create file")
        val out = resolver.openOutputStream(uri) ?: run { resolver.delete(uri, null, null); throw IOException("Could not open file") }
        val buffered = BufferedOutputStream(out, 256 * 1024)
        return object : FileSink.Target {
            override val stream: OutputStream = buffered
            override fun commit(): String {
                buffered.flush(); buffered.close()
                resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
                return uri.toString()
            }
            override fun abort() {
                runCatching { buffered.close() }
                runCatching { resolver.delete(uri, null, null) }
            }
        }
    }
}
