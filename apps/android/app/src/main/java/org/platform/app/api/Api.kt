package org.platform.app.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/** Een fout van het platform: de HTTP-status en de code uit {"detail": {"code", "message"}}. */
class ApiException(val status: Int, val code: String?, message: String) : IOException(message)

/**
 * De REST-API van het platform met OkHttp. Elk verzoek krijgt het access-token; na een 401 één
 * keer verversen (het refresh-token werkt één keer, dus nooit twee tegelijk: een Mutex).
 */
class Api(private val session: Session) {
    val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.MINUTES)
        .build()

    private val refreshing = Mutex()
    private val jsonType = "application/json".toMediaType()

    fun url(path: String): String {
        if (path.startsWith("http://") || path.startsWith("https://")) return path
        val base = session.server ?: throw ApiException(0, "no_server", "Geen platform gekozen")
        return base.trimEnd('/') + path
    }

    /** Een verzoek zonder login (adres nakijken, inloggen). */
    suspend fun info(base: String): Info = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(base.trimEnd('/') + "/api/v1/info").build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) throw ApiException(res.code, null, "HTTP ${res.code}")
            json.decodeFromString(Info.serializer(), res.body!!.string())
        }
    }

    suspend fun login(email: String, password: String, code: String?): Tokens = withContext(Dispatchers.IO) {
        val body = json.encodeToString(LoginIn.serializer(), LoginIn(email, password, code, device()))
        val req = Request.Builder().url(url("/api/v1/auth/login")).post(body.toRequestBody(jsonType)).build()
        http.newCall(req).execute().use { res ->
            check(res)
            json.decodeFromString(Tokens.serializer(), res.body!!.string()).also { session.store(it) }
        }
    }

    suspend fun logout() = withContext(Dispatchers.IO) {
        val refresh = session.refreshToken
        if (refresh != null) {
            runCatching {
                val body = json.encodeToString(RefreshIn.serializer(), RefreshIn(refresh))
                val req = Request.Builder().url(url("/api/v1/auth/logout")).post(body.toRequestBody(jsonType))
                    .header("Authorization", "Bearer ${session.accessToken}").build()
                http.newCall(req).execute().close()
            }
        }
        session.clear()
    }

    private fun device(): String = "Android-app (${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL})"

    private suspend fun refresh(old: String?): Boolean = refreshing.withLock {
        // Een ander verzoek ververste al terwijl dit wachtte
        if (session.accessToken != old && session.accessToken != null) return@withLock true
        val token = session.refreshToken ?: return@withLock false
        val body = json.encodeToString(RefreshIn.serializer(), RefreshIn(token))
        val req = Request.Builder().url(url("/api/v1/auth/refresh")).post(body.toRequestBody(jsonType)).build()
        http.newCall(req).execute().use { res ->
            if (res.code == 401 || res.code == 403) {
                session.clear()
                return@withLock false
            }
            check(res)
            session.store(json.decodeFromString(Tokens.serializer(), res.body!!.string()))
            true
        }
    }

    /** Een verzoek met het token; geeft het Response (de aanroeper sluit het). */
    suspend fun call(method: String, path: String, body: RequestBody? = null, headers: Map<String, String> = emptyMap()): Response =
        withContext(Dispatchers.IO) {
            fun build(): Request {
                val b = Request.Builder().url(url(path)).method(method, body)
                session.accessToken?.let { b.header("Authorization", "Bearer $it") }
                if (path.startsWith("/api/v1/media")) session.mediaProfile?.let { b.header("X-Media-Profile", it) }
                headers.forEach { (k, v) -> b.header(k, v) }
                return b.build()
            }
            val used = session.accessToken
            var res = http.newCall(build()).execute()
            if (res.code == 401 && refresh(used)) {
                res.close()
                res = http.newCall(build()).execute()
            }
            if (!res.isSuccessful) res.use { check(it) }
            res
        }

    suspend fun <T> get(path: String, decode: kotlinx.serialization.DeserializationStrategy<T>): T =
        call("GET", path).use { json.decodeFromString(decode, it.body!!.string()) }

    suspend fun <I, T> send(
        method: String,
        path: String,
        encode: kotlinx.serialization.SerializationStrategy<I>,
        value: I,
        decode: kotlinx.serialization.DeserializationStrategy<T>?,
    ): T? = call(method, path, json.encodeToString(encode, value).toRequestBody(jsonType)).use { res ->
        if (decode == null || res.code == 204) null else json.decodeFromString(decode, res.body!!.string())
    }

    private fun check(res: Response) {
        if (res.isSuccessful) return
        val text = runCatching { res.body?.string() }.getOrNull().orEmpty()
        var code: String? = null
        var message = "HTTP ${res.code}"
        runCatching {
            val detail = json.parseToJsonElement(text).jsonObject["detail"]
            if (detail is JsonObject) {
                code = detail["code"]?.jsonPrimitive?.contentOrNull
                detail["message"]?.jsonPrimitive?.contentOrNull?.let { message = it }
            } else if (detail != null) {
                detail.jsonPrimitive.contentOrNull?.let { message = it }
            }
        }
        throw ApiException(res.code, code, message)
    }

    companion object {
        fun q(value: String): String = URLEncoder.encode(value, "UTF-8")
    }
}
