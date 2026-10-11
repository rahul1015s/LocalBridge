package io.github.localbridge.transfer

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import java.io.FileNotFoundException
import java.io.InputStream

data class FileInfo(val name: String, val size: Long?)

/** Abstraction over "a file I was handed". Never assumes a filesystem path exists. */
interface FileAccess {
    /** @throws java.io.IOException when the file is missing or access was revoked. */
    fun describe(ref: String): FileInfo
    fun open(ref: String): InputStream
}

class ContentResolverFileAccess(
    private val resolver: ContentResolver,
    /** Ask Android for the un-redacted original (keeps photo GPS data) when permitted. */
    private val requireOriginal: () -> Boolean = { false },
) : FileAccess {
    override fun describe(ref: String): FileInfo {
        val uri = Uri.parse(ref)
        var name: String? = null
        var size: Long? = null
        try {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                ?.use { c ->
                    if (c.moveToFirst()) {
                        val ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        val si = c.getColumnIndex(OpenableColumns.SIZE)
                        if (ni >= 0 && !c.isNull(ni)) name = c.getString(ni)
                        // Some providers report 0/-1/null for virtual or streamed files: treat as unknown.
                        if (si >= 0 && !c.isNull(si)) size = c.getLong(si).takeIf { it > 0 }
                    }
                }
        } catch (e: SecurityException) {
            throw FileNotFoundException("Access to this file was revoked")
        }
        val display = name?.takeIf { it.isNotBlank() } ?: uri.lastPathSegment?.substringAfterLast('/') ?: "file"
        return FileInfo(display, size)
    }

    override fun open(ref: String): InputStream =
        try {
            val uri = Uri.parse(ref)
            // Bytes are always sent exactly as stored: no resizing, no recompression.
            val original = if (requireOriginal() && uri.authority?.contains("media") == true) {
                runCatching { resolver.openInputStream(android.provider.MediaStore.setRequireOriginal(uri)) }.getOrNull()
            } else null
            original ?: resolver.openInputStream(uri) ?: throw FileNotFoundException("Cannot open file")
        } catch (e: SecurityException) {
            throw FileNotFoundException("Access to this file was revoked")
        }
}
