import frappe
from frappe.model.document import Document


class ShiprocketShipment(Document):

	@frappe.whitelist()
	def create_shiprocket_order(self):
		from shiprocket_integration.shiprocket_integration.api import create_order

		create_order(self.name)
		self.reload()

	@frappe.whitelist()
	def assign_awb(self, courier_id=None):
		from shiprocket_integration.shiprocket_integration.api import assign_awb

		assign_awb(self.name, courier_id=courier_id)
		self.reload()

	@frappe.whitelist()
	def schedule_pickup(self):
		from shiprocket_integration.shiprocket_integration.api import schedule_pickup

		schedule_pickup(self.name)
		self.reload()

	@frappe.whitelist()
	def generate_label(self):
		from shiprocket_integration.shiprocket_integration.api import generate_label

		generate_label(self.name)
		self.reload()

	@frappe.whitelist()
	def generate_manifest(self):
		from shiprocket_integration.shiprocket_integration.api import generate_manifest

		generate_manifest(self.name)
		self.reload()

	@frappe.whitelist()
	def sync_tracking(self):
		from shiprocket_integration.shiprocket_integration.api import sync_shipment_status

		sync_shipment_status(self.name)
		self.reload()

	@frappe.whitelist()
	def cancel_shipment(self):
		from shiprocket_integration.shiprocket_integration.api import cancel_order

		cancel_order(self.name)
		self.reload()

	@frappe.whitelist()
	def check_serviceability(self):
		from shiprocket_integration.shiprocket_integration.api import check_serviceability

		return check_serviceability(self.name)
