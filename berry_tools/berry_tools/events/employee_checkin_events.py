# Copyright (c) 2026, Berry Printing and contributors
# For license information, please see license.txt

import calendar
import frappe
from frappe.utils import today, now_datetime, get_datetime, getdate


def assign_work_on_checkin(doc, method):
	"""
	Hooked to Employee Checkin's after_insert.
	Assigns one Work Item to the employee via ToDo, using a
	capacity-weighted rotation that restarts once exhausted.

	Now also respects:
	- N-Days frequency (fires every N days since last assignment)
	- Preferred hour window (only fires within a time range)
	- Preferred grade (soft filter: skips to next item if employee's
	  grade doesn't match)
	"""

	# Only trigger on the first "IN" checkin of the day for this employee.
	if doc.log_type != "IN":
		return

	if not is_first_in_today(doc.employee, doc.name):
		return

	rotation = build_todays_rotation()
	if not rotation:
		# nothing due today
		return

	employee_grade = frappe.db.get_value("Employee", doc.employee, "grade")
	checkin_time = get_datetime(doc.time).time() if doc.time else now_datetime().time()

	assigned_so_far = count_assignments_today()
	n = len(rotation)

	# Walk the rotation starting from the current position; if an item's
	# preferred-hour or preferred-grade constraints don't match, skip to
	# the next item instead of blocking the whole cycle.
	for offset in range(n):
		index = (assigned_so_far + offset) % n
		work_item_name = rotation[index]
		work_item = frappe.get_doc("Work Item", work_item_name)

		if not passes_preferred_hour(work_item, checkin_time):
			continue
		if not passes_preferred_grade(work_item, employee_grade):
			continue

		create_work_todo(work_item, doc.employee, doc.name)
		mark_assigned_if_n_day(work_item)
		return

	# No item in today's rotation matched this employee's grade/hour —
	# nothing assigned this checkin.


def is_first_in_today(employee, current_checkin_name):
	"""Check whether this is the employee's first IN checkin today."""
	earlier_checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"log_type": "IN",
			"time": [">=", today()],
			"name": ["!=", current_checkin_name],
		},
		limit=1,
	)
	return len(earlier_checkins) == 0


def build_todays_rotation():
	"""
	Build a flat list of Work Item names due today, each repeated
	`capacity` times, ordered by priority. This is the pool that
	gets walked through (and naturally restarts via modulo).
	"""
	weekday_name = now_datetime().strftime("%A")  # e.g. "Monday"
	day_of_month = now_datetime().day

	due_items = frappe.get_all(
		"Work Item",
		filters={"active": 1},
		fields=[
			"name", "frequency", "weekday", "day_of_month",
			"n_day_interval", "last_assigned_date",
			"capacity", "priority",
		],
		order_by="priority asc, name asc",
	)

	rotation = []
	for item in due_items:
		if is_due_today(item, weekday_name, day_of_month):
			rotation.extend([item.name] * max(item.capacity, 1))

	return rotation


def is_due_today(item, weekday_name, day_of_month):
	if item.frequency == "Daily":
		return True

	if item.frequency == "Weekly":
		return item.weekday == weekday_name

	if item.frequency == "Monthly":
		# handle months shorter than the configured day (e.g. 31st in a 30-day month)
		last_day = calendar.monthrange(now_datetime().year, now_datetime().month)[1]
		target_day = min(item.day_of_month or 1, last_day)
		return day_of_month == target_day

	if item.frequency == "N-Days":
		if not item.last_assigned_date:
			# never assigned before -> due immediately
			return True
		days_since = (getdate(today()) - getdate(item.last_assigned_date)).days
		interval = item.n_day_interval or 1
		return days_since >= interval

	return False


def passes_preferred_hour(work_item, checkin_time):
	"""If both preferred hour fields are set, only pass if checkin_time is inside the window."""
	if not work_item.preferred_hour_start or not work_item.preferred_hour_end:
		return True  # no restriction configured

	start = get_datetime(f"2000-01-01 {work_item.preferred_hour_start}").time()
	end = get_datetime(f"2000-01-01 {work_item.preferred_hour_end}").time()

	return start <= checkin_time <= end


def passes_preferred_grade(work_item, employee_grade):
	"""If a preferred grade is set, only pass if the employee matches it."""
	if not work_item.preferred_grade:
		return True  # no restriction configured
	return employee_grade == work_item.preferred_grade


def count_assignments_today():
	"""Total ToDos created today by this rotation logic (across all Work Items)."""
	return frappe.db.count(
		"ToDo",
		{
			"reference_type": "Work Item",
			"creation": [">=", today()],
		},
	)


def mark_assigned_if_n_day(work_item):
	"""Update last_assigned_date so N-Days frequency counts from this assignment."""
	if work_item.frequency == "N-Days":
		frappe.db.set_value("Work Item", work_item.name, "last_assigned_date", today())


def create_work_todo(work_item, employee, checkin_name):
	employee_user = frappe.db.get_value("Employee", employee, "user_id")
	if not employee_user:
		frappe.log_error(
			f"Employee {employee} has no linked User; cannot assign ToDo for Work Item {work_item.name}",
			"assign_work_on_checkin",
		)
		return

	todo = frappe.new_doc("ToDo")
	todo.allocated_to = employee_user
	todo.reference_type = "Work Item"
	todo.reference_name = work_item.name
	todo.description = work_item.description or work_item.title
	todo.date = today()
	todo.priority = "Medium"
	todo.insert(ignore_permissions=True)

	frappe.db.commit()