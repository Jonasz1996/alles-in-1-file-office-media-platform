package org.platform.app.sync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import org.platform.app.PlatformApp
import org.platform.app.R
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Eén ronde: eerst de camera, dan de map. Elk uur, en meteen met "Nu synchroniseren". */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as PlatformApp
        if (!app.session.loggedIn) return Result.success()
        val prefs = SyncPrefs(applicationContext)
        runCatching { setForeground(info(applicationContext.getString(R.string.sync_running))) }
        return try {
            CameraSync.run(app, applicationContext, prefs) { runCatching { notify(it) } }
            FolderSync.run(app, applicationContext, prefs) { runCatching { notify(it) } }
            prefs.lastRun = System.currentTimeMillis()
            prefs.lastError = null
            Result.success()
        } catch (e: IOException) {
            prefs.lastError = e.message
            Result.retry()
        } catch (e: Exception) {
            prefs.lastError = e.message ?: e.toString()
            Result.failure()
        }
    }

    private suspend fun notify(text: String) = setForeground(info(text))

    private fun info(text: String): ForegroundInfo {
        val ctx = applicationContext
        val manager = ctx.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26 && manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, ctx.getString(R.string.sync_channel), NotificationManager.IMPORTANCE_LOW),
            )
        }
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle(ctx.getString(R.string.sync_running))
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .build()
        return if (Build.VERSION.SDK_INT >= 29) ForegroundInfo(NOTE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else ForegroundInfo(NOTE, n)
    }

    companion object {
        private const val CHANNEL = "sync"
        private const val NOTE = 7
        private const val PERIODIC = "sync.periodic"
        private const val NOW = "sync.now"

        private fun constraints(prefs: SyncPrefs) = Constraints.Builder()
            .setRequiredNetworkType(if (prefs.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
            .build()

        /** De ronde elk uur aan (of uit als er niets te synchroniseren is). */
        fun schedule(context: Context) {
            val prefs = SyncPrefs(context)
            val wm = WorkManager.getInstance(context)
            if (!prefs.camera && prefs.folderUri == null) {
                wm.cancelUniqueWork(PERIODIC)
                return
            }
            val req = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS)
                .setConstraints(constraints(prefs))
                .build()
            wm.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, req)
        }

        fun now(context: Context) {
            val req = OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(constraints(SyncPrefs(context))).build()
            WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, req)
        }
    }
}
