import frappe
from frappe.model.document import Document


class ShiprocketSettings(Document):

	@frappe.whitelist()
	def test_connection(self):
		from shiprocket_integration.shiprocket_integration.api import get_auth_token

		try:
			get_auth_token(force_refresh=True)
			frappe.msgprint("Connection successful! Token refreshed.", indicator="green", alert=True)
		except Exception as e:
			frappe.throw(f"Connection failed: {str(e)}")

	@frappe.whitelist()
	def fetch_pickup_locations(self):
		from shiprocket_integration.shiprocket_integration.api import (
			get_auth_token,
			get_pickup_locations,
		)

		try:
			token = get_auth_token()
			locations = get_pickup_locations(token)
			if not locations:
				frappe.msgprint("No pickup locations found in your Shiprocket account.", indicator="orange")
				return

			def is_active(loc):
				return int(loc.get("status") or 0) == 1

			def is_verified(loc):
				return int(loc.get("phone_verified") or 0) == 1 and int(loc.get("lat_long_status") or 0) == 1

			def is_primary(loc):
				return int(loc.get("is_primary_location") or 0) == 1

			chosen = None
			reason = ""
			for loc in locations:
				if is_primary(loc) and is_active(loc) and is_verified(loc):
					chosen, reason = loc, "primary, active, verified"
					break
			if not chosen:
				for loc in locations:
					if is_active(loc) and is_verified(loc):
						chosen, reason = loc, "active and verified (primary unavailable)"
						break
			if not chosen:
				for loc in locations:
					if is_primary(loc):
						chosen, reason = loc, "primary (WARNING: inactive or unverified)"
						break

			rows = []
			for loc in locations:
				flags = []
				if is_primary(loc):
					flags.append("PRIMARY")
				flags.append("ACTIVE" if is_active(loc) else "INACTIVE")
				flags.append("VERIFIED" if is_verified(loc) else "UNVERIFIED")
				rows.append(f"- {loc.get('pickup_location', 'Unknown')} [{', '.join(flags)}]")

			message = "Available Pickup Locations:<br>" + "<br>".join(rows)

			if chosen:
				name = chosen.get("pickup_location")
				if self.default_pickup_location != name:
					frappe.db.set_single_value("Shiprocket Settings", "default_pickup_location", name)
					frappe.db.commit()
					self.default_pickup_location = name
				message += f"<br><br><b>Default Pickup Location set to:</b> {name}<br><i>({reason})</i>"
				indicator = "green" if "WARNING" not in reason else "orange"
			else:
				message += "<br><br><b>Could not auto-select a pickup location.</b> Set one manually."
				indicator = "orange"

			frappe.msgprint(message, title="Pickup Locations", indicator=indicator)
		except Exception as e:
			frappe.throw(f"Failed to fetch pickup locations: {str(e)}")
