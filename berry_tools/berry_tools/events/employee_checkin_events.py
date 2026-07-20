# Copyright (c) 2026, Berry Printing and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import today, now_datetime, get_datetime

# Flow:
# Checkin
# Check Active work item today


def assign_work_on_checkin(doc, method):
	"""
	Hooked to Employee Checkin's after_insert.
	Assigns one Work Item to the employee via ToDo, using a
	capacity-weighted rotation that restarts once exhausted.
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

	assigned_so_far = count_assignments_today()
	index = assigned_so_far % len(rotation)
	work_item = rotation[index]

	create_work_todo(work_item, doc.employee, doc.name)


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
	Build a flat list of Work Items due today, each repeated
	`capacity` times, ordered by priority. This is the pool that
	gets walked through (and naturally restarts via modulo).
	"""
	weekday_name = now_datetime().strftime("%A")  # e.g. "Monday"
	day_of_month = now_datetime().day

	due_items = frappe.get_all(
		"Work Item",
		filters={"active": 1},
		fields=["name", "frequency", "weekday", "day_of_month", "capacity", "priority"],
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
		import calendar
		last_day = calendar.monthrange(now_datetime().year, now_datetime().month)[1]
		target_day = min(item.day_of_month or 1, last_day)
		return day_of_month == target_day
	return False


def count_assignments_today():
	"""Total ToDos created today by this rotation logic (across all Work Items)."""
	return frappe.db.count(
		"ToDo",
		{
			"reference_type": "Work Item",
			"creation": [">=", today()],
		},
	)


def create_work_todo(work_item_name, employee, checkin_name):
	work_item = frappe.get_doc("Work Item", work_item_name)

	employee_user = frappe.db.get_value("Employee", employee, "user_id")
	if not employee_user:
		frappe.log_error(
			f"Employee {employee} has no linked User; cannot assign ToDo for Work Item {work_item_name}",
			"assign_work_on_checkin",
		)
		return

	todo = frappe.new_doc("ToDo")
	todo.allocated_to = employee_user
	todo.reference_type = "Work Item"
	todo.reference_name = work_item_name
	todo.description = work_item.description or work_item.title
	todo.date = today()
	todo.priority = "Medium"
	todo.insert(ignore_permissions=True)

	frappe.db.commit()