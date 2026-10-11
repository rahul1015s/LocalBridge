package io.github.localbridge.data

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject

data class HistoryEntry(
    val name: String,
    val size: Long?,
    val ok: Boolean,
    val detail: String?,
    val receiver: String,
    val atMs: Long,
)

/** Local-only transfer history (names, sizes, outcome). Never contains paths, tokens or content. */
class HistoryStore(context: Context, private val limit: Int = 200) {
    private val file = File(context.filesDir, "history.json")
    private val _entries = MutableStateFlow(read())
    val entries: StateFlow<List<HistoryEntry>> = _entries.asStateFlow()

    @Synchronized
    fun add(e: HistoryEntry) {
        _entries.value = (listOf(e) + _entries.value).take(limit).also(::write)
    }

    @Synchronized
    fun clear() {
        _entries.value = emptyList()
        file.delete()
    }

    private fun read(): List<HistoryEntry> = runCatching {
        val arr = JSONArray(file.readText())
        (0 until arr.length()).map {
            val o = arr.getJSONObject(it)
            HistoryEntry(
                o.getString("name"), if (o.isNull("size")) null else o.getLong("size"), o.getBoolean("ok"),
                o.optString("detail").takeIf { d -> d.isNotEmpty() }, o.optString("receiver"), o.getLong("at"),
            )
        }
    }.getOrDefault(emptyList())

    private fun write(list: List<HistoryEntry>) {
        runCatching {
            val arr = JSONArray()
            list.forEach {
                arr.put(
                    JSONObject().put("name", it.name).put("size", it.size ?: JSONObject.NULL).put("ok", it.ok)
                        .put("detail", it.detail ?: "").put("receiver", it.receiver).put("at", it.atMs),
                )
            }
            val tmp = File(file.path + ".tmp")
            tmp.writeText(arr.toString())
            tmp.renameTo(file)
        }
    }
}
