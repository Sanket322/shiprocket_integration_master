frappe.ui.form.on("Shiprocket Shipment", {
	refresh(frm) {
		if (frm.is_new()) return;

		let status = frm.doc.status;
		let group = __("Shiprocket");

		// Create Shiprocket Order
		if ((status === "Draft" || !status) && !frm.doc.sr_order_id) {
			frm.add_custom_button(
				__("Create Shiprocket Order"),
				function () {
					frappe.confirm(
						__("Create order on Shiprocket?"),
						function () {
							frm.call("create_shiprocket_order").then(() => {
								frm.reload_doc();
							});
						}
					);
				},
				group
			);
		}

		// Check Serviceability + Assign AWB
		if (frm.doc.sr_shipment_id && !frm.doc.awb_number) {
			frm.add_custom_button(
				__("Check Serviceability"),
				function () {
					frm.call("check_serviceability").then((r) => {
						if (r && r.message) {
							show_serviceability_dialog(frm, r.message);
						}
					});
				},
				group
			);

			frm.add_custom_button(
				__("Assign AWB"),
				function () {
					frm.call("assign_awb").then(() => {
						frm.reload_doc();
					});
				},
				group
			);
		}

		// Schedule Pickup
		if (
			frm.doc.awb_number &&
			["AWB Assigned", "Order Created"].includes(status)
		) {
			frm.add_custom_button(
				__("Schedule Pickup"),
				function () {
					frm.call("schedule_pickup").then(() => {
						frm.reload_doc();
					});
				},
				group
			);
		}

		// Generate Label + Manifest
		if (frm.doc.sr_shipment_id && !frm.doc.label_url) {
			frm.add_custom_button(
				__("Generate Label"),
				function () {
					frm.call("generate_label").then(() => {
						frm.reload_doc();
					});
				},
				group
			);
		}
		if (frm.doc.sr_shipment_id && !frm.doc.manifest_url) {
			frm.add_custom_button(
				__("Generate Manifest"),
				function () {
					frm.call("generate_manifest").then(() => {
						frm.reload_doc();
					});
				},
				group
			);
		}

		// Sync Tracking
		if (
			frm.doc.sr_shipment_id &&
			!["Draft", "Cancelled", "Delivered", "RTO Delivered", "Error"].includes(
				status
			)
		) {
			frm.add_custom_button(
				__("Sync Tracking"),
				function () {
					frm.call("sync_tracking").then(() => {
						frm.reload_doc();
					});
				},
				group
			);
		}

		// Cancel Shipment
		if (
			frm.doc.sr_order_id &&
			!["Cancelled", "Delivered", "RTO Delivered"].includes(status)
		) {
			frm.add_custom_button(
				__("Cancel Shipment"),
				function () {
					frappe.confirm(
						__("Are you sure you want to cancel this shipment on Shiprocket?"),
						function () {
							frm.call("cancel_shipment").then(() => {
								frm.reload_doc();
							});
						}
					);
				},
				group
			);
		}

		// Download Label
		if (frm.doc.label_url) {
			frm.add_custom_button(
				__("Download Label"),
				function () {
					window.open(frm.doc.label_url, "_blank");
				},
				group
			);
		}

		// Track Shipment
		if (frm.doc.tracking_url) {
			frm.add_custom_button(
				__("Track Shipment"),
				function () {
					window.open(frm.doc.tracking_url, "_blank");
				},
				group
			);
		}
	},
});

function show_serviceability_dialog(frm, couriers) {
	let fields = [
		{
			fieldtype: "HTML",
			fieldname: "courier_table",
		},
	];

	let dialog = new frappe.ui.Dialog({
		title: __("Available Couriers"),
		fields: fields,
		size: "large",
	});

	let html = `<table class="table table-bordered table-hover">
		<thead><tr>
			<th>${__("Courier")}</th>
			<th>${__("Rate (₹)")}</th>
			<th>${__("ETD (days)")}</th>
			<th>${__("COD")}</th>
			<th>${__("Action")}</th>
		</tr></thead><tbody>`;

	(couriers || []).forEach(function (c) {
		html += `<tr>
			<td>${c.courier_name || ""}</td>
			<td>${c.rate || ""}</td>
			<td>${c.etd || ""}</td>
			<td>${c.cod ? __("Yes") : __("No")}</td>
			<td><button class="btn btn-xs btn-primary select-courier"
				data-courier-id="${c.courier_company_id || ""}"
				data-courier-name="${c.courier_name || ""}">
				${__("Select")}</button></td>
		</tr>`;
	});

	html += "</tbody></table>";

	dialog.fields_dict.courier_table.$wrapper.html(html);

	dialog.$wrapper.on("click", ".select-courier", function () {
		let courier_id = $(this).data("courier-id");
		dialog.hide();
		frm.call("assign_awb", { courier_id: courier_id }).then(() => {
			frm.reload_doc();
		});
	});

	dialog.show();
}
