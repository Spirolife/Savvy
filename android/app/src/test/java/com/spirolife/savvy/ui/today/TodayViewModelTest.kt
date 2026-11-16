package com.spirolife.savvy.ui.today

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.data.api.RestSuggestion
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.data.api.Today
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

class TodayViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val sample = Today(
        date = "2026-11-16",
        tasks = listOf(Task("1", "Essay"), Task("2", "Errand", completed = true)),
        rest = RestSuggestion(true, "2026-11-19", "Thursday has 7 open hours"),
    )

    private class Api(val today: Today) : FakeApi() {
        var failTasks: Exception? = null
        var failToday: Exception? = null
        val calls = mutableListOf<String>()
        override suspend fun today(date: String?): Today { failToday?.let { throw it }; return today }
        override suspend fun completeTask(id: String): Ok { calls += "complete $id"; failTasks?.let { throw it }; return Ok() }
        override suspend fun reopenTask(id: String): Ok { calls += "reopen $id"; failTasks?.let { throw it }; return Ok() }
        override suspend fun planRestDay(day: String): Ok { calls += "rest $day"; return Ok(message = "Booked") }
    }

    @Test fun loads() {
        val vm = TodayViewModel(Api(sample))
        vm.load()
        val s = vm.state.value
        assertFalse(s.loading)
        assertEquals("2026-11-16", s.today?.date)
        assertNull(s.error)
    }

    @Test fun completeIsOptimisticAndSticks() {
        val api = Api(sample)
        val vm = TodayViewModel(api).apply { load() }
        vm.setTaskDone(sample.tasks[0], true)
        assertTrue(vm.state.value.today!!.tasks[0].completed)
        assertEquals(listOf("complete 1"), api.calls)
        assertNull(vm.state.value.message)
    }

    @Test fun completeRollsBackOnError() {
        val api = Api(sample).apply { failTasks = IOException("down") }
        val vm = TodayViewModel(api).apply { load() }
        vm.setTaskDone(sample.tasks[0], true)
        assertFalse(vm.state.value.today!!.tasks[0].completed)
        assertEquals("Can't reach your PC", vm.state.value.message)
    }

    @Test fun reopenRollsBackWithServerMessage() {
        val api = Api(sample).apply { failTasks = ApiException(502, "todoist_error", "Todoist said no") }
        val vm = TodayViewModel(api).apply { load() }
        vm.setTaskDone(sample.tasks[1], false)
        assertEquals(listOf("reopen 2"), api.calls)
        assertTrue(vm.state.value.today!!.tasks[1].completed)
        assertEquals("Todoist said no", vm.state.value.message)
    }

    @Test fun loadErrorWithoutDataShowsError() {
        val vm = TodayViewModel(Api(sample).apply { failToday = IOException() }).apply { load() }
        assertEquals("Can't reach your PC", vm.state.value.error)
        assertNull(vm.state.value.today)
    }

    @Test fun refreshErrorKeepsData() {
        val api = Api(sample)
        val vm = TodayViewModel(api).apply { load() }
        api.failToday = IOException()
        vm.refresh()
        assertEquals("2026-11-16", vm.state.value.today?.date)
        assertNull(vm.state.value.error)
        assertEquals("Can't reach your PC", vm.state.value.message)
        assertFalse(vm.state.value.refreshing)
    }

    @Test fun planRestDayDismissesCard() {
        val api = Api(sample)
        val vm = TodayViewModel(api).apply { load() }
        vm.planRestDay()
        assertEquals(listOf("rest 2026-11-19"), api.calls)
        assertTrue(vm.state.value.restDismissed)
        assertEquals("Booked", vm.state.value.message)
    }

    @Test fun notNowDismisses() {
        val vm = TodayViewModel(Api(sample)).apply { load() }
        vm.dismissRest()
        assertTrue(vm.state.value.restDismissed)
        vm.load()
        assertTrue(vm.state.value.restDismissed)
    }
}
