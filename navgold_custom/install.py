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

	# CI runs the suite on a fresh bare site (frappe + erpnext + navgold_custom). Frappe's own
	# before_tests seeds the site by completing the setup wizard only when a single app is
	# installed, so with erpnext + navgold_custom present it bails and leaves the site with no
	# Company / Warehouse / UOM "Nos" / Item Group tree / Price List. Complete the wizard here
	# to reuse erpnext's canonical seed suite instead of hand-rolling fixtures.
	#
	# We call setup_complete directly rather than frappe.utils.install.complete_setup_wizard
	# because that helper omits the fiscal-year dates; erpnext then fails Fiscal Year validation
	# (end must be exactly one year after start) and aborts Company creation, leaving the site
	# with no Company or Warehouses.
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

		# validate() requires buying_price_list once amazon_pricing_enabled is on, but tests flip
		# that flag via change_settings without supplying a price list. Seed the singleton with the
		# Standard Buying list the wizard already created so those saves pass the guard.
		if frappe.db.exists("Price List", "Standard Buying"):
			frappe.db.set_single_value("Navgold Settings", "buying_price_list", "Standard Buying")

	frappe.db.commit()  # nosemgrep: frappe-manual-commit -- persist seed data before the test run begins
