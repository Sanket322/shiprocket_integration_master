import json

import frappe
import requests
from frappe.utils import add_to_date, get_datetime, now_datetime

SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in/v1/external"

# Status mapping from Shiprocket to internal status
SR_STATUS_MAP = {
	"PENDING": "Pickup Scheduled",
	"PICKED UP": "In Transit",
	"SHIPPED": "In Transit",
	"IN TRANSIT": "In Transit",
	"OUT FOR DELIVERY": "Out for Delivery",
	"DELIVERED": "Delivered",
	"RTO INITIATED": "RTO Initiated",
	"RTO DELIVERED": "RTO Delivered",
	"CANCELLED": "Cancelled",
}


# ──────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────


def _get_settings():
	"""Get Shiprocket Settings singleton, throw if not enabled."""
	settings = frappe.get_single("Shiprocket Settings")
	if not settings.enabled:
		frappe.throw("Shiprocket Integration is not enabled. Please enable it in Shiprocket Settings.")
	return settings


def _headers(token):
	"""Return request headers with auth token."""
	return {
		"Content-Type": "application/json",
		"Authorization": f"Bearer {token}",
	}


def _handle_response(resp, context="Shiprocket API"):
	"""Check response and return JSON, logging errors as needed."""
	try:
		resp.raise_for_status()
	except requests.exceptions.HTTPError:
		error_detail = ""
		try:
			error_detail = json.dumps(resp.json(), indent=2)
		except Exception:
			error_detail = resp.text

		frappe.log_error(
			title=f"Shiprocket API Error: {context}",
			message=f"Status: {resp.status_code}\n{error_detail}",
		)
		frappe.throw(f"Shiprocket API error ({context}): {resp.status_code} - {error_detail[:200]}")

	return resp.json()


def _clean_url(value):
	"""Return ``value`` only if it looks like a usable URL string.

	Shiprocket's endpoints sometimes return status flags (0/1) on the same
	keys that normally carry URLs (e.g. ``label_created`` next to
	``label_url``). The Shiprocket Shipment doctype declares those fields
	with ``options: "URL"`` which means Frappe's URL validator runs on
	every save — feeding it ``"0"`` or ``"1"`` throws
	``'0' is not a valid URL`` and blocks the entire save (which then
	swallows the legitimate AWB / tracking writes too).

	This helper coerces the value to a string, strips it, and returns it
	only when it starts with ``http://`` or ``https://``. Anything else
	(0, 1, empty, garbage) becomes ``""`` which is what the URL validator
	expects for "no value".
	"""
	if value is None:
		return ""
	s = str(value).strip()
	if not s:
		return ""
	if s.lower().startswith(("http://", "https://")):
		return s
	return ""


def _update_dn_tracking_fields(dn_name, shipment):
	"""Update custom tracking fields on the linked Delivery Note."""
	if not dn_name:
		return

	try:
		frappe.db.set_value(
			"Delivery Note",
			dn_name,
			{
				"shiprocket_awb": shipment.awb_number or "",
				"shiprocket_status": shipment.status or "",
				"shiprocket_courier": shipment.courier_name or "",
				"shiprocket_tracking_url": _clean_url(shipment.tracking_url),
			},
			update_modified=False,
		)
	except Exception:
		# Custom fields may not exist yet
		frappe.log_error(
			title="Shiprocket DN Update Error",
			message=frappe.get_traceback(),
		)


def _parse_shipping_address(dn):
	"""Extract structured address from DN's shipping_address_name or customer_address."""
	address_name = dn.shipping_address_name or dn.customer_address
	if not address_name:
		return {}

	address = frappe.get_doc("Address", address_name)
	return {
		"shipping_customer_name": dn.customer_name or "",
		"shipping_address_line1": address.address_line1 or "",
		"shipping_address_line2": address.address_line2 or "",
		"shipping_city": address.city or "",
		"shipping_state": address.state or "",
		"shipping_pincode": address.pincode or "",
		"shipping_country": address.country or "India",
		"shipping_phone": address.phone or "",
		"shipping_email": address.email_id or "",
	}


def _calculate_weight(dn):
	"""Calculate total weight from DN or item weights. Fallback to 0.5 kg."""
	if dn.total_net_weight and dn.total_net_weight > 0:
		return float(dn.total_net_weight)

	total_weight = 0
	for item in dn.items:
		item_doc = frappe.get_cached_doc("Item", item.item_code)
		weight = (item_doc.weight_per_unit or 0) * item.qty
		total_weight += weight

	return total_weight if total_weight > 0 else 0.5


def _get_customer_email(customer):
	"""Get email via Dynamic Link → Contact."""
	contact_names = frappe.db.sql(
		"""SELECT parent FROM `tabDynamic Link`
		WHERE link_doctype='Customer' AND link_name=%s AND parenttype='Contact'
		LIMIT 1""",
		customer,
		as_list=True,
	)

	if contact_names:
		email = frappe.db.get_value("Contact", contact_names[0][0], "email_id")
		return email or ""

	return ""


def _get_customer_phone(customer):
	"""Get phone/mobile via Dynamic Link → Contact."""
	contact_name = frappe.db.sql(
		"""SELECT parent FROM `tabDynamic Link`
		WHERE link_doctype='Customer' AND link_name=%s AND parenttype='Contact'
		LIMIT 1""",
		customer,
		as_list=True,
	)

	if contact_name:
		contact = frappe.get_doc("Contact", contact_name[0][0])
		return contact.mobile_no or contact.phone or ""

	return ""


# ──────────────────────────────────────────────
# Token Management
# ──────────────────────────────────────────────


def get_auth_token(force_refresh=False):
	"""
	Get a valid Shiprocket auth token.
	- Checks cached token and expiry
	- Refreshes if expired or within 10 hours of expiry
	- Token valid for 240 hours; refresh at 230 hours for safety
	"""
	settings = _get_settings()

	if not force_refresh and settings.auth_token and settings.token_expiry:
		expiry = get_datetime(settings.token_expiry)
		if now_datetime() < expiry:
			return settings.auth_token

	# Get fresh token
	api_email = settings.api_email
	api_password = settings.get_password("api_password")

	if not api_email or not api_password:
		frappe.throw("Shiprocket API email and password are required.")

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/auth/login",
		json={"email": api_email, "password": api_password},
		headers={"Content-Type": "application/json"},
		timeout=30,
	)

	data = _handle_response(resp, "Auth Login")

	token = data.get("token")
	if not token:
		frappe.throw("Failed to get auth token from Shiprocket.")

	# Save token with 230-hour expiry (safety margin)
	token_expiry = add_to_date(now_datetime(), hours=230)
	frappe.db.set_single_value("Shiprocket Settings", "auth_token", token)
	frappe.db.set_single_value("Shiprocket Settings", "token_expiry", token_expiry)
	frappe.db.commit()

	return token


# ──────────────────────────────────────────────
# Pickup Locations
# ──────────────────────────────────────────────


def get_pickup_locations(token):
	"""GET /settings/company/pickup → return list of pickup locations."""
	resp = requests.get(
		f"{SHIPROCKET_BASE_URL}/settings/company/pickup",
		headers=_headers(token),
		timeout=30,
	)
	data = _handle_response(resp, "Fetch Pickup Locations")
	return data.get("data", {}).get("shipping_address", [])


# ──────────────────────────────────────────────
# Create Order
# ──────────────────────────────────────────────


def create_order(shipment_name):
	"""Create a Shiprocket order from a Shiprocket Shipment doc."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if shipment.sr_order_id:
		frappe.throw("Shiprocket order already created for this shipment.")

	dn = frappe.get_doc("Delivery Note", shipment.delivery_note)

	# Build order items
	order_items = []
	for item in dn.items:
		item_doc = frappe.get_cached_doc("Item", item.item_code)
		order_items.append({
			"name": (item.item_name or "")[:200],
			"sku": (item.item_code or "")[:100],
			"units": int(item.qty),
			"selling_price": float(item.rate),
			"discount": 0,
			"tax": 0,
			"hsn": int(item_doc.gst_hsn_code) if hasattr(item_doc, "gst_hsn_code") and item_doc.gst_hsn_code and str(item_doc.gst_hsn_code).strip().isdigit() else "",
		})

	settings = _get_settings()

	payload = {
		"order_id": shipment.name,
		"order_date": str(shipment.posting_date),
		"pickup_location": shipment.pickup_location or settings.default_pickup_location or "Primary",
		"channel_id": settings.default_channel_id or "",
		"comment": f"ERPNext DN: {dn.name}",
		"billing_customer_name": shipment.shipping_customer_name or dn.customer_name or "",
		"billing_last_name": "",
		"billing_address": shipment.shipping_address_line1 or "",
		"billing_address_2": shipment.shipping_address_line2 or "",
		"billing_city": shipment.shipping_city or "",
		"billing_pincode": shipment.shipping_pincode or "",
		"billing_state": shipment.shipping_state or "",
		"billing_country": shipment.shipping_country or "India",
		"billing_email": shipment.shipping_email or _get_customer_email(dn.customer) or "",
		"billing_phone": shipment.shipping_phone or _get_customer_phone(dn.customer) or "",
		"shipping_is_billing": True,
		"shipping_customer_name": shipment.shipping_customer_name or dn.customer_name or "",
		"shipping_last_name": "",
		"shipping_address": shipment.shipping_address_line1 or "",
		"shipping_address_2": shipment.shipping_address_line2 or "",
		"shipping_city": shipment.shipping_city or "",
		"shipping_pincode": shipment.shipping_pincode or "",
		"shipping_country": shipment.shipping_country or "India",
		"shipping_state": shipment.shipping_state or "",
		"shipping_email": shipment.shipping_email or _get_customer_email(dn.customer) or "",
		"shipping_phone": shipment.shipping_phone or _get_customer_phone(dn.customer) or "",
		"order_items": order_items,
		"payment_method": shipment.payment_method or settings.default_payment_method or "Prepaid",
		"shipping_charges": 0,
		"giftwrap_charges": 0,
		"transaction_charges": 0,
		"total_discount": 0,
		"sub_total": float(dn.grand_total),
		"length": shipment.length or settings.default_length or 10,
		"breadth": shipment.breadth or settings.default_breadth or 10,
		"height": shipment.height or settings.default_height or 10,
		"weight": shipment.weight or 0.5,
	}

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/orders/create/adhoc",
		json=payload,
		headers=_headers(token),
		timeout=60,
	)

	# Handle 401 - token expired, retry once
	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/orders/create/adhoc",
			json=payload,
			headers=_headers(token),
			timeout=60,
		)

	data = _handle_response(resp, "Create Order")

	shipment.sr_order_id = str(data.get("order_id", ""))
	shipment.sr_shipment_id = str(data.get("shipment_id", ""))
	shipment.sr_channel_order_id = str(data.get("channel_order_id", ""))
	shipment.status = "Order Created"
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	_update_dn_tracking_fields(shipment.delivery_note, shipment)

	frappe.msgprint(f"Shiprocket order created. Order ID: {shipment.sr_order_id}", indicator="green", alert=True)


# ──────────────────────────────────────────────
# Assign AWB
# ──────────────────────────────────────────────


def assign_awb(shipment_name, courier_id=None):
	"""Assign AWB number to a shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_shipment_id:
		frappe.throw("Shiprocket Shipment ID is required. Create the order first.")

	payload = {"shipment_id": shipment.sr_shipment_id}
	if courier_id:
		payload["courier_id"] = courier_id

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/courier/assign/awb",
		json=payload,
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/courier/assign/awb",
			json=payload,
			headers=_headers(token),
			timeout=30,
		)

	data = _handle_response(resp, "Assign AWB")

	# Shiprocket returns either a real AWB or zeros on rejection (insufficient
	# wallet balance, courier not serviceable for that lane, etc.). When the
	# AWB is missing or "0", surface the API's own error message instead of
	# writing garbage that later trips the URL validator on save.
	awb_data = data.get("response", {}).get("data", {}) or {}
	raw_awb = awb_data.get("awb_code")
	awb_code = str(raw_awb).strip() if raw_awb not in (None, "") else ""
	assign_status = data.get("awb_assign_status")

	if not awb_code or awb_code in ("0",) or assign_status == 0:
		api_message = (
			data.get("message")
			or awb_data.get("message")
			or "Shiprocket did not assign an AWB. Check wallet balance and pickup serviceability."
		)
		frappe.throw(f"AWB assignment failed: {api_message}")

	shipment.awb_number = awb_code
	shipment.courier_name = awb_data.get("courier_name", "") or ""
	raw_courier_id = awb_data.get("courier_company_id")
	shipment.courier_id = str(raw_courier_id) if raw_courier_id not in (None, "") else ""
	shipment.status = "AWB Assigned"
	shipment.tracking_url = _clean_url(f"https://shiprocket.co/tracking/{awb_code}")
	# Defensive — clear any URL field already polluted by a prior buggy
	# generate_label run, so this save isn't blocked by stale "0" data.
	shipment.label_url = _clean_url(shipment.label_url)
	shipment.manifest_url = _clean_url(shipment.manifest_url)
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	_update_dn_tracking_fields(shipment.delivery_note, shipment)

	frappe.msgprint(
		f"AWB assigned: {shipment.awb_number} ({shipment.courier_name})",
		indicator="green",
		alert=True,
	)


# ──────────────────────────────────────────────
# Schedule Pickup
# ──────────────────────────────────────────────


def schedule_pickup(shipment_name):
	"""Schedule courier pickup for a shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_shipment_id:
		frappe.throw("Shiprocket Shipment ID is required.")

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/courier/generate/pickup",
		json={"shipment_id": [shipment.sr_shipment_id]},
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/courier/generate/pickup",
			json={"shipment_id": [shipment.sr_shipment_id]},
			headers=_headers(token),
			timeout=30,
		)

	_handle_response(resp, "Schedule Pickup")

	shipment.status = "Pickup Scheduled"
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	_update_dn_tracking_fields(shipment.delivery_note, shipment)

	frappe.msgprint("Pickup scheduled successfully.", indicator="green", alert=True)


# ──────────────────────────────────────────────
# Generate Label
# ──────────────────────────────────────────────


def generate_label(shipment_name):
	"""Generate shipping label for a shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_shipment_id:
		frappe.throw("Shiprocket Shipment ID is required.")

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/courier/generate/label",
		json={"shipment_id": [shipment.sr_shipment_id]},
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/courier/generate/label",
			json={"shipment_id": [shipment.sr_shipment_id]},
			headers=_headers(token),
			timeout=30,
		)

	data = _handle_response(resp, "Generate Label")

	# Shiprocket's response carries both ``label_url`` (the real URL) and
	# ``label_created`` (a 0/1 status flag). The old code did
	# ``label_url or label_created`` which short-circuited the empty string
	# to the integer 0 and persisted "0" into a URL-validated field —
	# which then blocked every subsequent save on the doc (e.g. assign_awb).
	# Take ONLY the real URL; if it's not there yet, surface the API
	# message so the operator knows to retry.
	label_url = _clean_url(data.get("label_url"))
	if not label_url:
		api_message = data.get("message") or "Label not yet generated by Shiprocket. Try again in a moment."
		frappe.throw(f"Label generation pending: {api_message}")

	shipment.label_url = label_url
	shipment.manifest_url = _clean_url(shipment.manifest_url)
	shipment.tracking_url = _clean_url(shipment.tracking_url)
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.msgprint("Label generated successfully.", indicator="green", alert=True)


# ──────────────────────────────────────────────
# Generate Manifest
# ──────────────────────────────────────────────


def generate_manifest(shipment_name):
	"""Generate manifest for a shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_shipment_id:
		frappe.throw("Shiprocket Shipment ID is required.")

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/manifests/generate",
		json={"shipment_id": [shipment.sr_shipment_id]},
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/manifests/generate",
			json={"shipment_id": [shipment.sr_shipment_id]},
			headers=_headers(token),
			timeout=30,
		)

	data = _handle_response(resp, "Generate Manifest")

	manifest_url = _clean_url(data.get("manifest_url"))
	if not manifest_url:
		api_message = data.get("message") or "Manifest not yet generated by Shiprocket. Try again in a moment."
		frappe.throw(f"Manifest generation pending: {api_message}")

	shipment.manifest_url = manifest_url
	shipment.label_url = _clean_url(shipment.label_url)
	shipment.tracking_url = _clean_url(shipment.tracking_url)
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.msgprint("Manifest generated successfully.", indicator="green", alert=True)


# ──────────────────────────────────────────────
# Check Serviceability
# ──────────────────────────────────────────────


def check_serviceability(shipment_name):
	"""Check courier serviceability for a shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)
	settings = _get_settings()

	pickup_pincode = _get_pickup_pincode(settings, token)
	delivery_pincode = shipment.shipping_pincode

	if not delivery_pincode:
		frappe.throw("Delivery pincode is required for serviceability check.")

	is_cod = 1 if shipment.payment_method == "COD" else 0

	resp = requests.get(
		f"{SHIPROCKET_BASE_URL}/courier/serviceability/",
		params={
			"pickup_postcode": pickup_pincode,
			"delivery_postcode": delivery_pincode,
			"weight": shipment.weight or 0.5,
			"cod": is_cod,
		},
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.get(
			f"{SHIPROCKET_BASE_URL}/courier/serviceability/",
			params={
				"pickup_postcode": pickup_pincode,
				"delivery_postcode": delivery_pincode,
				"weight": shipment.weight or 0.5,
				"cod": is_cod,
			},
			headers=_headers(token),
			timeout=30,
		)

	data = _handle_response(resp, "Check Serviceability")

	couriers = []
	available = data.get("data", {}).get("available_courier_companies", [])
	for c in available:
		couriers.append({
			"courier_company_id": c.get("courier_company_id"),
			"courier_name": c.get("courier_name", ""),
			"rate": c.get("rate", 0),
			"etd": c.get("etd", ""),
			"cod": bool(c.get("cod", 0)),
		})

	return couriers


def _get_pickup_pincode(settings, token):
	"""Get pincode from the default pickup location."""
	pickup_name = settings.default_pickup_location
	if not pickup_name:
		frappe.throw("Default Pickup Location is not configured in Shiprocket Settings.")

	locations = get_pickup_locations(token)
	for loc in locations:
		if loc.get("pickup_location") == pickup_name:
			return loc.get("pin_code", "")

	frappe.throw(f"Pickup location '{pickup_name}' not found in Shiprocket account.")


# ──────────────────────────────────────────────
# Tracking
# ──────────────────────────────────────────────


def sync_shipment_status(shipment_name):
	"""Sync tracking status for a single shipment."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_shipment_id:
		frappe.throw("Shiprocket Shipment ID is required for tracking.")

	previous_status = shipment.status

	resp = requests.get(
		f"{SHIPROCKET_BASE_URL}/courier/track/shipment/{shipment.sr_shipment_id}",
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.get(
			f"{SHIPROCKET_BASE_URL}/courier/track/shipment/{shipment.sr_shipment_id}",
			headers=_headers(token),
			timeout=30,
		)

	data = _handle_response(resp, "Track Shipment")

	tracking_data = data.get("tracking_data", {})
	shipment_track = tracking_data.get("shipment_track", [])

	if shipment_track:
		current = shipment_track[0]
		sr_status = (current.get("current_status") or "").upper()
		mapped_status = SR_STATUS_MAP.get(sr_status, shipment.status)
		shipment.current_status = current.get("current_status", "")
		shipment.status = mapped_status

	# Update tracking events
	shipment.tracking_events = []
	activities = tracking_data.get("shipment_track_activities", [])
	for activity in activities:
		shipment.append("tracking_events", {
			"event_date": activity.get("date"),
			"status": activity.get("sr-status-label", ""),
			"location": activity.get("location", ""),
			"description": activity.get("activity", ""),
			"sr_status_code": str(activity.get("sr-status", "")),
		})

	shipment.last_synced = now_datetime()
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	_update_dn_tracking_fields(shipment.delivery_note, shipment)

	# Auto-create Sales Invoice + Payment Entry on transition to Delivered
	if shipment.status == "Delivered" and previous_status != "Delivered":
		try:
			from shiprocket_integration.shiprocket_integration.invoicing import handle_delivered

			handle_delivered(shipment.delivery_note)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Shiprocket Auto-Invoice Error: {shipment.name}",
				message=frappe.get_traceback(),
			)


def sync_shipment_status_all():
	"""Scheduled hourly: sync all active shipments."""
	active_shipments = frappe.get_all(
		"Shiprocket Shipment",
		filters={
			"status": ["not in", ["Draft", "Delivered", "Cancelled", "RTO Delivered", "Error"]],
			"sr_shipment_id": ["is", "set"],
		},
		pluck="name",
	)

	for name in active_shipments:
		try:
			sync_shipment_status(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Shiprocket Tracking Sync Error: {name}",
				message=frappe.get_traceback(),
			)


# ──────────────────────────────────────────────
# Cancel Order
# ──────────────────────────────────────────────


def cancel_order(shipment_name):
	"""Cancel a Shiprocket order."""
	token = get_auth_token()
	shipment = frappe.get_doc("Shiprocket Shipment", shipment_name)

	if not shipment.sr_order_id:
		frappe.throw("No Shiprocket Order ID found to cancel.")

	resp = requests.post(
		f"{SHIPROCKET_BASE_URL}/orders/cancel",
		json={"ids": [int(shipment.sr_order_id)]},
		headers=_headers(token),
		timeout=30,
	)

	if resp.status_code == 401:
		token = get_auth_token(force_refresh=True)
		resp = requests.post(
			f"{SHIPROCKET_BASE_URL}/orders/cancel",
			json={"ids": [int(shipment.sr_order_id)]},
			headers=_headers(token),
			timeout=30,
		)

	_handle_response(resp, "Cancel Order")

	shipment.status = "Cancelled"
	shipment.save(ignore_permissions=True)
	frappe.db.commit()

	_update_dn_tracking_fields(shipment.delivery_note, shipment)

	frappe.msgprint("Shipment cancelled successfully.", indicator="green", alert=True)


# ──────────────────────────────────────────────
# Delivery Note Hook
# ──────────────────────────────────────────────


def on_delivery_note_submit(doc, method):
	"""Hook called on Delivery Note submit. Auto-creates shipment if enabled."""
	try:
		settings = frappe.get_single("Shiprocket Settings")
	except Exception:
		return

	if not settings.enabled or not settings.auto_create_order_on_submit:
		return

	if not (doc.shipping_address_name or doc.customer_address):
		frappe.msgprint(
			"No shipping address found. Shiprocket shipment was not created automatically.",
			indicator="orange",
		)
		return

	try:
		_create_shipment_from_dn(doc)
	except Exception:
		frappe.log_error(
			title=f"Shiprocket Auto-Create Error: {doc.name}",
			message=frappe.get_traceback(),
		)
		frappe.msgprint(
			"Failed to auto-create Shiprocket shipment. Check Error Log for details.",
			indicator="red",
		)


def _create_shipment_from_dn(dn):
	"""Create a Shiprocket Shipment doc from a Delivery Note."""
	settings = _get_settings()
	address_data = _parse_shipping_address(dn)
	weight = _calculate_weight(dn)

	shipment = frappe.new_doc("Shiprocket Shipment")
	shipment.delivery_note = dn.name
	shipment.customer = dn.customer
	shipment.customer_name = dn.customer_name
	shipment.company = dn.company
	shipment.posting_date = dn.posting_date

	# Address fields
	shipment.shipping_customer_name = address_data.get("shipping_customer_name", dn.customer_name)
	shipment.shipping_address_line1 = address_data.get("shipping_address_line1", "")
	shipment.shipping_address_line2 = address_data.get("shipping_address_line2", "")
	shipment.shipping_city = address_data.get("shipping_city", "")
	shipment.shipping_state = address_data.get("shipping_state", "")
	shipment.shipping_pincode = address_data.get("shipping_pincode", "")
	shipment.shipping_country = address_data.get("shipping_country", "India")
	shipment.shipping_phone = address_data.get("shipping_phone", "") or _get_customer_phone(dn.customer)
	shipment.shipping_email = address_data.get("shipping_email", "") or _get_customer_email(dn.customer)

	# Package and settings defaults
	shipment.weight = weight
	shipment.length = settings.default_length or 10
	shipment.breadth = settings.default_breadth or 10
	shipment.height = settings.default_height or 10
	shipment.payment_method = settings.default_payment_method or "Prepaid"
	shipment.pickup_location = settings.default_pickup_location or "Primary"

	shipment.insert(ignore_permissions=True)
	frappe.db.commit()

	frappe.msgprint(
		f"Shiprocket Shipment <a href='/app/shiprocket-shipment/{shipment.name}'>{shipment.name}</a> created.",
		indicator="green",
	)

	return shipment.name


@frappe.whitelist()
def create_shipment_from_delivery_note(delivery_note):
	"""Whitelisted method called from DN form button."""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	# Check if shipment already exists
	existing = frappe.db.exists("Shiprocket Shipment", {"delivery_note": delivery_note})
	if existing:
		frappe.throw(f"Shiprocket Shipment {existing} already exists for this Delivery Note.")

	return _create_shipment_from_dn(dn)


# ──────────────────────────────────────────────
# Webhook
# ──────────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
def shiprocket_webhook():
	"""
	Webhook endpoint for Shiprocket status updates.
	URL: /api/method/shiprocket_integration.shiprocket_integration.api.shiprocket_webhook
	"""
	try:
		data = frappe.request.get_json(force=True)
	except Exception:
		frappe.response["http_status_code"] = 400
		return {"status": "error", "message": "Invalid JSON payload"}

	if not data:
		frappe.response["http_status_code"] = 400
		return {"status": "error", "message": "Empty payload"}

	awb = data.get("awb", "")
	order_id = data.get("order_id", "")

	# Find matching shipment
	shipment_name = None
	if awb:
		shipment_name = frappe.db.get_value("Shiprocket Shipment", {"awb_number": awb}, "name")
	if not shipment_name and order_id:
		shipment_name = frappe.db.get_value(
			"Shiprocket Shipment", {"sr_order_id": str(order_id)}, "name"
		)

	if not shipment_name:
		frappe.response["http_status_code"] = 404
		return {"status": "error", "message": "Shipment not found"}

	try:
		sync_shipment_status(shipment_name)
		return {"status": "ok"}
	except Exception:
		frappe.log_error(
			title=f"Shiprocket Webhook Error: {shipment_name}",
			message=frappe.get_traceback(),
		)
		return {"status": "error", "message": "Failed to sync status"}


# ──────────────────────────────────────────────
# Manual Invoice + Payment Fallback
# ──────────────────────────────────────────────


@frappe.whitelist()
def create_invoice_and_payment(delivery_note):
	"""
	Manually run the auto-invoice flow for a Delivered Shiprocket shipment.
	Used by the Delivery Note "Create Invoice & Payment" button as a fallback
	for shipments that were already Delivered before the toggle was enabled.
	"""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	shipment_name = frappe.db.get_value(
		"Shiprocket Shipment", {"delivery_note": dn.name}, "name"
	)
	if not shipment_name:
		frappe.throw("No Shiprocket Shipment found for this Delivery Note.")

	shipment_status = frappe.db.get_value("Shiprocket Shipment", shipment_name, "status")
	if shipment_status != "Delivered":
		frappe.throw(
			f"Shiprocket status must be 'Delivered' (current: {shipment_status or '-'})."
		)

	from shiprocket_integration.shiprocket_integration.invoicing import (
		handle_delivered,
		_existing_invoice_for_dn,
	)

	handle_delivered(dn.name)
	return {"sales_invoice": _existing_invoice_for_dn(dn.name)}
