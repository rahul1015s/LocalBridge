package io.github.localbridge.ui

import android.annotation.SuppressLint
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import java.util.concurrent.Executors

/** Decodes QR codes from the camera's luminance plane with ZXing (no Google Play services needed). */
internal class QrAnalyzer(private val onFound: (String) -> Unit) : ImageAnalysis.Analyzer {
    private val reader = MultiFormatReader().apply {
        setHints(mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE), DecodeHintType.TRY_HARDER to true))
    }

    override fun analyze(image: ImageProxy) {
        image.use {
            val plane = it.planes[0]
            val w = it.width
            val h = it.height
            val buf = plane.buffer
            val stride = plane.rowStride
            val data = ByteArray(w * h)
            for (row in 0 until h) {
                buf.position(row * stride)
                buf.get(data, row * w, w)
            }
            try {
                val src = PlanarYUVLuminanceSource(data, w, h, 0, 0, w, h, false)
                val result = reader.decodeWithState(BinaryBitmap(HybridBinarizer(src)))
                onFound(result.text)
            } catch (_: Exception) {
                // No code in this frame.
            } finally {
                reader.reset()
            }
        }
    }
}

@SuppressLint("UnsafeOptInUsageError")
@Composable
fun QrScanner(onCode: (String) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val latest = rememberUpdatedState(onCode)
    val executor = Executors.newSingleThreadExecutor()
    var provider: ProcessCameraProvider? = null

    DisposableEffect(owner) {
        onDispose {
            provider?.unbindAll()
            executor.shutdown()
        }
    }
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            val future = ProcessCameraProvider.getInstance(ctx)
            future.addListener({
                val p = future.get()
                provider = p
                val preview = Preview.Builder().build().also { it.surfaceProvider = view.surfaceProvider }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { it.setAnalyzer(executor, QrAnalyzer { text -> latest.value(text) }) }
                runCatching {
                    p.unbindAll()
                    p.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                }
            }, ContextCompat.getMainExecutor(context))
            view
        },
    )
}
