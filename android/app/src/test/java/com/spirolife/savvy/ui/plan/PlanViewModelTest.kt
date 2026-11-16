package com.spirolife.savvy.ui.plan

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.Goal
import com.spirolife.savvy.data.api.Meal
import com.spirolife.savvy.data.api.MealWeek
import com.spirolife.savvy.data.api.NewMeal
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.data.api.Plan
import com.spirolife.savvy.data.api.Project
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.ui.today.FakeApi
import com.spirolife.savvy.ui.today.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

class PlanViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val plan = Plan(
        goals = listOf(Goal(1, "Pull-ups", target = "5 pull-ups")),
        meals = MealWeek("2026-11-16", null, listOf(Meal(3, "Salmon"), Meal(4, "Tea", kind = "drink"))),
        projects = listOf(Project("p1", "School", 2)),
    )

    private class Api(val plan: Plan) : FakeApi() {
        var failDelete = false
        var mealRefusal: String? = null
        var added: NewMeal? = null
        override suspend fun plan(): Plan = plan
        override suspend fun deleteMeal(id: Long): Ok = if (failDelete) throw IOException() else Ok()
        override suspend fun addMeal(meal: NewMeal): Ok {
            mealRefusal?.let { throw ApiException(400, "meal_rule", it) }
            added = meal
            return Ok(id = 9)
        }
        override suspend fun projectTasks(projectId: String) = listOf(Task("t1", "Read"))
    }

    @Test fun deleteMealRollsBack() {
        val vm = PlanViewModel(Api(plan).apply { failDelete = true }).apply { load() }
        vm.deleteMeal(3)
        assertEquals(listOf(3L, 4L), vm.state.value.plan!!.meals.items.map { it.id })
        assertEquals("Can't reach your PC", vm.state.value.message)
    }

    @Test fun deleteMealSticks() {
        val vm = PlanViewModel(Api(plan)).apply { load() }
        vm.deleteMeal(3)
        assertEquals(listOf(4L), vm.state.value.plan!!.meals.items.map { it.id })
    }

    @Test fun mealRefusalStaysInDialog() {
        val api = Api(plan).apply { mealRefusal = "Not added: that would be 4 meals" }
        val vm = PlanViewModel(api).apply { load() }
        var closed = false
        vm.addMeal("Stew", "meal", listOf("Greens", " ")) { closed = true }
        assertFalse(closed)
        assertEquals("Not added: that would be 4 meals", vm.state.value.dialogError)
    }

    @Test fun addMealTrimsSides() {
        val api = Api(plan)
        val vm = PlanViewModel(api).apply { load() }
        var closed = false
        vm.addMeal(" Stew ", "meal", listOf("Greens", " ")) { closed = true }
        assertTrue(closed)
        assertEquals(NewMeal("Stew", "meal", listOf("Greens")), api.added)
        assertNull(vm.state.value.dialogError)
    }

    @Test fun projectSheet() {
        val vm = PlanViewModel(Api(plan)).apply { load() }
        vm.openProject(plan.projects[0])
        assertEquals(listOf("t1"), vm.state.value.sheet?.tasks?.map { it.id })
        vm.closeProject()
        assertNull(vm.state.value.sheet)
    }

    @Test fun goalFractions() {
        fun g(target: String?, note: String?) = Goal(1, "g", target = target, progressNote = note)
        assertEquals(0.6f, goalFraction(g("5 pull-ups", "Did some (3 pull-ups)"))!!, 0.001f)
        assertEquals(2f / 3f, goalFraction(g("Work out 3x/week", "Gym (2/3 workouts this week)"))!!, 0.001f)
        assertEquals(1f, goalFraction(g("5", "7 done"))!!, 0.001f)
        assertNull(goalFraction(g("Sleep by midnight", "Did well")))
        assertNull(goalFraction(g("5 pull-ups", null)))
        assertNull(goalFraction(g(null, "Ran")))
        assertNotNull(goalFraction(g(null, "2 of 4 units")))
    }
}
