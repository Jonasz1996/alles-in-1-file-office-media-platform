package org.platform.app

import android.app.Application
import org.platform.app.api.Api
import org.platform.app.api.Session

/** De app: één sessie (adres en tokens) en één API-client voor alles. */
class PlatformApp : Application() {
    lateinit var session: Session
        private set
    lateinit var api: Api
        private set

    override fun onCreate() {
        super.onCreate()
        session = Session(this)
        api = Api(session)
        // Synchroniseren op de achtergrond (als er iets aan staat)
        org.platform.app.sync.SyncWorker.schedule(this)
    }
}
