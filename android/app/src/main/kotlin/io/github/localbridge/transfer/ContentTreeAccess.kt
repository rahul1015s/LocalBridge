package io.github.localbridge.transfer

import android.content.ContentResolver
import android.net.Uri
import android.provider.DocumentsContract

/** SAF-backed [TreeAccess] for a folder the person picked with the system folder chooser. */
class ContentTreeAccess(private val resolver: ContentResolver, private val tree: Uri) : TreeAccess {
    override fun children(docId: String): List<TreeChild> {
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId)
        val cols = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_SIZE,
        )
        val out = ArrayList<TreeChild>()
        runCatching {
            resolver.query(uri, cols, null, null, null)?.use { c ->
                while (c.moveToNext()) {
                    val dir = c.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR
                    out += TreeChild(c.getString(0), c.getString(1) ?: "_", dir, if (c.isNull(3)) null else c.getLong(3).takeIf { it > 0 })
                }
            }
        }
        return out
    }

    override fun refFor(docId: String): String = DocumentsContract.buildDocumentUriUsingTree(tree, docId).toString()

    companion object {
        fun rootName(resolver: ContentResolver, tree: Uri): String {
            val root = DocumentsContract.getTreeDocumentId(tree)
            val uri = DocumentsContract.buildDocumentUriUsingTree(tree, root)
            return runCatching {
                resolver.query(uri, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)?.use { c ->
                    if (c.moveToFirst()) c.getString(0) else null
                }
            }.getOrNull() ?: "Folder"
        }
    }
}
