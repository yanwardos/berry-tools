import frappe

@frappe.whitelist(allow_guest=True, methods=['POST'])
def receive_checkin_device():
    data = frappe.request.json or {}
    attendance_device_id = data.get("attendance_device_id")
    attendance_log_type = data.get("attendance_log_type")

    if not attendance_device_id:
        frappe.throw("attendance_device_id parameter is required.")

    if not attendance_log_type:
        frappe.throw("attendance_log_type parameter is required.")

    employee = frappe.db.get_all("Employee", {"attendance_device_id": attendance_device_id}, ["*"])

    if not employee:
        frappe.response.http_status_code = 404
        frappe.response['message'] = "Employee not found"

    employee = employee[0]

    newAttendance = frappe.get_doc({
        'doctype': 'Employee Checkin',
        'employee': employee.name,
        'log_type': "IN" if attendance_log_type=="0" else "OUT" if attendance_log_type == "1" else null
    })

    newAttendance.save()
    # newAttendance.insert()
    frappe.db.commit()

    frappe.response.http_status_code = 200
    frappe.response["checkin"] = newAttendance
    frappe.response["employee"] = employee