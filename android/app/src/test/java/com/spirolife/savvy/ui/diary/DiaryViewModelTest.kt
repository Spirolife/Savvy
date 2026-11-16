package com.spirolife.savvy.ui.diary

import com.spirolife.savvy.data.api.DiaryEntry
import com.spirolife.savvy.data.api.Note
import com.spirolife.savvy.data.api.NoteSummary
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.ui.today.FakeApi
import com.spirolife.savvy.ui.today.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import java.io.IOException
import java.time.LocalTime

class DiaryViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private class Api : FakeApi() {
        val saved = mutableListOf<Pair<String, String>>()
        var failSave = false
        var notesCalls = 0
        override suspend fun diary(days: Int) = saved.mapIndexed { i, (t, x) -> DiaryEntry("$i", t, x, "2026-11-16T08:00:00-05:00") }.reversed()
        override suspend fun addDiary(type: String, text: String): Ok {
            if (failSave) throw IOException()
            saved += type to text
            return Ok()
        }
        override suspend fun notes(): List<NoteSummary> { notesCalls++; return listOf(NoteSummary("groceries.md")) }
        override suspend fun note(name: String) = Note(name, "eggs")
    }

    @Test fun promptByTimeOfDay() {
        assertEquals(DiaryPrompt.Morning, promptFor(LocalTime.of(6, 0)))
        assertEquals(DiaryPrompt.Morning, promptFor(LocalTime.of(11, 59)))
        assertEquals(DiaryPrompt.Evening, promptFor(LocalTime.NOON))
        assertEquals(DiaryPrompt.Evening, promptFor(LocalTime.of(22, 0)))
    }

    @Test fun morningSaveIsBod() {
        val api = Api()
        val vm = DiaryViewModel(api) { LocalTime.of(8, 30) }.apply { load() }
        vm.setDraft("  Lab, then gym ")
        vm.save()
        assertEquals(listOf("bod" to "Lab, then gym"), api.saved)
        assertEquals("", vm.state.value.draft)
        assertEquals(listOf("bod"), vm.state.value.entries?.map { it.type })
    }

    @Test fun eveningSaveIsEod() {
        val api = Api()
        val vm = DiaryViewModel(api) { LocalTime.of(21, 0) }.apply { load() }
        assertEquals(DiaryPrompt.Evening, vm.state.value.prompt)
        vm.setDraft("Fine")
        vm.save()
        assertEquals(listOf("eod" to "Fine"), api.saved)
    }

    @Test fun promptFollowsClockOnResume() {
        var now = LocalTime.of(9, 0)
        val vm = DiaryViewModel(Api()) { now }
        assertEquals(DiaryPrompt.Morning, vm.state.value.prompt)
        now = LocalTime.of(18, 0)
        vm.load()
        assertEquals(DiaryPrompt.Evening, vm.state.value.prompt)
    }

    @Test fun badDayWithEmptyDraft() {
        val api = Api()
        val vm = DiaryViewModel(api) { LocalTime.of(21, 0) }.apply { load() }
        vm.badDay()
        assertEquals(listOf("bad_day" to "It was a bad day."), api.saved)
    }

    @Test fun failedSaveKeepsDraft() {
        val api = Api().apply { failSave = true }
        val vm = DiaryViewModel(api) { LocalTime.of(21, 0) }.apply { load() }
        vm.setDraft("Keep me")
        vm.save()
        assertEquals("Keep me", vm.state.value.draft)
        assertEquals("Can't reach your PC", vm.state.value.message)
    }

    @Test fun notesLoadLazilyAndOpen() {
        val api = Api()
        val vm = DiaryViewModel(api) { LocalTime.of(9, 0) }.apply { load() }
        assertEquals(0, api.notesCalls)
        vm.selectTab(DiaryTab.Notes)
        assertEquals(listOf("groceries.md"), vm.state.value.notes?.map { it.name })
        vm.openNote("groceries.md")
        assertEquals("eggs", vm.state.value.sheet?.note?.text)
        vm.closeNote()
        assertNull(vm.state.value.sheet)
    }
}
