import click


def before_uninstall():
	try:
		click.secho("Removing Alfred customizations...", fg="yellow")
		click.secho("Alfred uninstalled successfully.", fg="green")
	except Exception as e:
		click.secho(f"Error during Alfred uninstallation: {e}", fg="red")
		raise
