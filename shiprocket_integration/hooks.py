app_name = "shiprocket_integration"
app_title = "Shiprocket Integration"
app_publisher = "Reformiqo"
app_description = "Shiprocket Shipping Integration for ERPNext v16"
app_email = "hello@reformiqo.com"
app_license = "MIT"

required_apps = ["frappe", "erpnext"]

# Inject JS on Delivery Note form
doctype_js = {
	"Delivery Note": "shiprocket_integration/overrides/delivery_note.js",
}

# Export custom fields
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [["module", "=", "Shiprocket Integration"]],
	},
]

# Server-side hook on DN submit
doc_events = {
	"Delivery Note": {
		"on_submit": "shiprocket_integration.shiprocket_integration.api.on_delivery_note_submit",
	},
}

# Hourly tracking sync
scheduler_events = {
	"hourly": [
		"shiprocket_integration.shiprocket_integration.api.sync_shipment_status_all",
	],
}
