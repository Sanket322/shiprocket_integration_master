frappe.ui.form.on("Shiprocket Settings", {
	refresh(frm) {
		if (frm.doc.enabled && frm.doc.api_email && frm.doc.api_password) {
			frm.add_custom_button(__("Test Connection"), function () {
				frm.call("test_connection");
			});

			frm.add_custom_button(__("Fetch Pickup Locations"), function () {
				frm.call("fetch_pickup_locations");
			});
		}
	},
});
