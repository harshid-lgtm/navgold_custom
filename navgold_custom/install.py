import click

from navgold_custom.setup import after_install as setup


def after_install():
	try:
		print("Setting up Navgold Custom...")
		setup()

		click.secho("Thank you for installing Navgold Custom!", fg="green")

	except Exception as e:
		BUG_REPORT_URL = "https://github.com/buildwithhussain/navgold_custom/issues/new"
		click.secho(
			"Installation for Navgold Custom app failed due to an error."
			" Please try re-installing the app or"
			f" report the issue on {BUG_REPORT_URL} if not resolved.",
			fg="bright_red",
		)
		raise e


def before_tests():
	import frappe

	# Frappe's before_tests seeds via the wizard only when a single app is installed, so our 3-app
	# CI site gets none. Call setup_complete directly (not complete_setup_wizard) to pass explicit
	# fiscal-year dates — without them erpnext's Fiscal Year validation fails and silently aborts
	# Company creation.
	if not frappe.is_setup_complete():
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
		from frappe.utils import getdate

		today = getdate()
		setup_complete(
			{
				"language": "English",
				"email": "test@navgold.local",
				"full_name": "Test User",
				"password": "test",
				"country": "United Kingdom",
				"timezone": "Europe/London",
				"currency": "GBP",
				"company_name": "Navgold Test",
				"company_abbr": "NT",
				"chart_of_accounts": "Standard",
				"fy_start_date": today.replace(month=1, day=1).isoformat(),
				"fy_end_date": today.replace(month=12, day=31).isoformat(),
			}
		)

	if "erpnext" in frappe.get_installed_apps():
		from erpnext.setup.utils import enable_all_roles_and_domains, set_defaults_for_tests

		enable_all_roles_and_domains()
		set_defaults_for_tests()

		# Tests enable amazon_pricing_enabled without setting a price list, which validate() now
		# requires — seed the wizard's Standard Buying list so those saves pass.
		if frappe.db.exists("Price List", "Standard Buying"):
			frappe.db.set_single_value("Navgold Settings", "buying_price_list", "Standard Buying")

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist seed data before the test run begins
