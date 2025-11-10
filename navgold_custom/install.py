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
