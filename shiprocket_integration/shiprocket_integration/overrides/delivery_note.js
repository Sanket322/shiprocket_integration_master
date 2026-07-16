frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;

		frappe.db.get_single_value("Shiprocket Settings", "enabled").then((enabled) => {
			if (!enabled) return;

			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Shiprocket Shipment",
					filters: { delivery_note: frm.doc.name },
					fields: ["name", "awb_number", "courier_name", "status", "tracking_url"],
					limit_page_length: 1,
				},
				callback: function (r) {
					if (r.message && r.message.length > 0) {
						let shipment = r.message[0];
						let info_html = `<div class="flex flex-col gap-1">
							<span><strong>${__("Shipment")}:</strong>
								<a href="/app/shiprocket-shipment/${shipment.name}">${shipment.name}</a>
							</span>`;

						if (shipment.awb_number) {
							info_html += `<span><strong>${__("AWB")}:</strong> ${shipment.awb_number}</span>`;
						}
						if (shipment.courier_name) {
							info_html += `<span><strong>${__("Courier")}:</strong> ${shipment.courier_name}</span>`;
						}
						if (shipment.status) {
							info_html += `<span><strong>${__("Status")}:</strong> ${shipment.status}</span>`;
						}
						if (shipment.tracking_url) {
							info_html += `<span><a href="${shipment.tracking_url}" target="_blank" class="btn btn-xs btn-default">${__("Track Shipment")}</a></span>`;
						}

						info_html += "</div>";

						frm.dashboard.add_comment(info_html, "blue", true);

						// Manual fallback button: create Sales Invoice + Payment Entry for already-Delivered shipments
						if (shipment.status === "Delivered" && (frm.doc.per_billed || 0) < 100) {
							frm.add_custom_button(
								__("Create Invoice & Payment"),
								function () {
									frappe.call({
										method: "shiprocket_integration.shiprocket_integration.api.create_invoice_and_payment",
										args: { delivery_note: frm.doc.name },
										freeze: true,
										freeze_message: __("Creating Sales Invoice and Payment Entry..."),
										callback: function (r) {
											if (r.message && r.message.sales_invoice) {
												frappe.show_alert({
													message: __("Sales Invoice {0} created.", [r.message.sales_invoice]),
													indicator: "green",
												});
											}
											frm.reload_doc();
										},
									});
								},
								__("Shiprocket")
							);
						}
					} else {
						frm.add_custom_button(
							__("Ship via Shiprocket"),
							function () {
								frappe.call({
									method: "shiprocket_integration.shiprocket_integration.api.create_shipment_from_delivery_note",
									args: { delivery_note: frm.doc.name },
									freeze: true,
									freeze_message: __("Creating Shiprocket Shipment..."),
									callback: function (r) {
										if (r.message) {
											frappe.set_route("Form", "Shiprocket Shipment", r.message);
										} else {
											frm.reload_doc();
										}
									},
								});
							},
							__("Shiprocket")
						);
					}
				},
			});
		});
	},
});
