package io.github.localbridge.transfer

data class TreeChild(val docId: String, val name: String, val isDir: Boolean, val size: Long?)

/** Directory listing abstraction (Storage Access Framework on Android, fake in tests). */
interface TreeAccess {
    fun children(docId: String): List<TreeChild>
    /** A reference string the transfer engine can open (a document URI on Android). */
    fun refFor(docId: String): String
}

data class ScannedFile(val ref: String, val name: String, val size: Long?, val dir: String)
data class ScanResult(val files: List<ScannedFile>, val truncated: Boolean, val skippedDeep: Int)

/** Make one folder-name component acceptable to the receiver's strict validation. */
fun cleanComponent(raw: String): String {
    var n = raw.filter { !it.isISOControl() && it != '/' && it != '\\' && it !in '\u202a'..'\u202e' && it !in '\u2066'..'\u2069' }
    n = n.trim().trimEnd('.', ' ')
    if (n.isEmpty() || n == "." || n == "..") n = "_"
    return n.take(200)
}

/**
 * Walks a folder tree iteratively (no recursion, so deep trees can't overflow the stack) with hard
 * limits matching the receiver: depth and file count. Only regular files are returned; nothing is
 * read here, and empty folders are not represented.
 */
class FolderScanner(private val maxDepth: Int = 15, private val maxFiles: Int = 10_000) {
    fun scan(access: TreeAccess, rootName: String, rootDocId: String): ScanResult {
        val out = ArrayList<ScannedFile>()
        var truncated = false
        var skippedDeep = 0
        val stack = ArrayDeque<Triple<String, List<String>, Int>>()
        stack.addLast(Triple(rootDocId, listOf(cleanComponent(rootName)), 1))
        while (stack.isNotEmpty() && !truncated) {
            val (docId, path, depth) = stack.removeLast()
            for (c in access.children(docId)) {
                if (c.isDir) {
                    if (depth + 1 > maxDepth) skippedDeep++ else stack.addLast(Triple(c.docId, path + cleanComponent(c.name), depth + 1))
                } else {
                    if (out.size >= maxFiles) { truncated = true; break }
                    out += ScannedFile(access.refFor(c.docId), c.name, c.size, path.joinToString("/"))
                }
            }
        }
        out.sortWith(compareBy({ it.dir }, { it.name }))
        return ScanResult(out, truncated, skippedDeep)
    }
}
