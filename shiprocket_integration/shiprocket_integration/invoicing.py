import frappe
from frappe import _


def _existing_invoice_for_dn(dn_name):
	"""Return name of any non-cancelled Sales Invoice that already references this DN via items."""
	row = frappe.db.sql(
		"""
		SELECT si.name
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE sii.delivery_note = %s AND si.docstatus < 2
		LIMIT 1
		""",
		dn_name,
	)
	return row[0][0] if row else None


def _existing_payment_for_si(si_name):
	"""Return name of any non-cancelled Payment Entry referencing this Sales Invoice."""
	row = frappe.db.sql(
		"""
		SELECT pe.name
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		WHERE per.reference_doctype = 'Sales Invoice'
			AND per.reference_name = %s
			AND pe.docstatus < 2
		LIMIT 1
		""",
		si_name,
	)
	return row[0][0] if row else None


def _resolve_paid_to_account(settings, company, mode_of_payment):
	"""Pick the bank/cash Account to credit on the auto Payment Entry."""
	if settings.default_payment_account:
		return settings.default_payment_account

	if mode_of_payment:
		acc = frappe.db.get_value(
			"Mode of Payment Account",
			{"parent": mode_of_payment, "company": company},
			"default_account",
		)
		if acc:
			return acc

	return frappe.db.get_value("Company", company, "default_bank_account") or frappe.db.get_value(
		"Company", company, "default_cash_account"
	)


def _create_invoice_from_dn(dn_name, settings):
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

	si = make_sales_invoice(dn_name)

	if settings.default_cost_center:
		si.cost_center = settings.default_cost_center
		for row in si.items:
			if not row.cost_center:
				row.cost_center = settings.default_cost_center

	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)

	if settings.auto_submit_invoice:
		si.submit()

	frappe.db.commit()
	return si


def _create_payment_for_si(si, settings):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", si.name)

	if settings.default_mode_of_payment:
		pe.mode_of_payment = settings.default_mode_of_payment

	paid_to = _resolve_paid_to_account(settings, si.company, pe.mode_of_payment)
	if paid_to:
		pe.paid_to = paid_to
		pe.paid_to_account_currency = frappe.db.get_value("Account", paid_to, "account_currency")
		pe.paid_to_account_type = frappe.db.get_value("Account", paid_to, "account_type")

	if settings.default_cost_center and not pe.cost_center:
		pe.cost_center = settings.default_cost_center

	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)

	if settings.auto_submit_payment:
		pe.submit()

	frappe.db.commit()
	return pe


def handle_delivered(dn_name):
	"""
	Auto-create Sales Invoice (and optionally Payment Entry) for a Delivery Note
	when its Shiprocket shipment transitions to Delivered.

	Idempotent: skips when a non-cancelled SI/PE already exists, or when the DN
	is already fully billed.
	"""
	if not dn_name:
		return

	settings = frappe.get_single("Shiprocket Settings")
	if not settings.auto_create_invoice_on_delivered:
		return

	dn = frappe.get_doc("Delivery Note", dn_name)
	if dn.docstatus != 1:
		return

	existing_si_name = _existing_invoice_for_dn(dn.name)
	si = None

	try:
		if existing_si_name:
			si = frappe.get_doc("Sales Invoice", existing_si_name)
			if si.docstatus == 0 and settings.auto_submit_invoice:
				si.submit()
		elif (dn.per_billed or 0) >= 100:
			# Fully billed but no live SI was found (all cancelled?) — nothing to do
			return
		else:
			si = _create_invoice_from_dn(dn.name, settings)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title=f"Shiprocket Auto-Invoice Error: {dn.name}",
			message=frappe.get_traceback(),
		)
		return

	if not settings.auto_create_payment_on_delivered:
		return

	if not si or si.docstatus != 1:
		return

	if (si.outstanding_amount or 0) <= 0:
		return

	if _existing_payment_for_si(si.name):
		return

	try:
		_create_payment_for_si(si, settings)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title=f"Shiprocket Auto-Payment Error: {dn.name}",
			message=frappe.get_traceback(),
		)
