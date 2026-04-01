import click


def after_install():
	try:
		click.secho("Setting up Alfred...", fg="blue")
		click.secho("Alfred installed successfully!", fg="green")
	except Exception as e:
		click.secho(f"Error during Alfred installation: {e}", fg="red")
		raise
