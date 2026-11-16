package com.spirolife.savvy

import android.app.Application
import com.spirolife.savvy.notifications.Notifications

class SavvyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Graph.init(this)
        Notifications.init(this)
    }
}
