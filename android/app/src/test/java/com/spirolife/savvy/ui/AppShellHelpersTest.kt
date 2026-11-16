package com.spirolife.savvy.ui

import com.spirolife.savvy.ui.connect.toLabel
import com.spirolife.savvy.ui.settings.displayTime
import com.spirolife.savvy.ui.settings.parseTime
import com.spirolife.savvy.ui.settings.wireTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AppShellHelpersTest {
    @Test fun labels() {
        assertEquals("school", toLabel(" School "))
        assertEquals("side_job", toLabel("Side job"))
        assertEquals("work-2", toLabel("work-2"))
        assertNull(toLabel(""))
        assertNull(toLabel("café"))
        assertNull(toLabel("x".repeat(33)))
    }

    @Test fun times() {
        assertEquals(8 to 30, parseTime("08:30"))
        assertEquals(9 to 0, parseTime("nonsense"))
        assertEquals("08:05", wireTime(8, 5))
        assertEquals("8:30", displayTime("08:30"))
        assertEquals("22:00", displayTime("22:00"))
    }
}
