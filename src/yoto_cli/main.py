import click


@click.group()
def cli():
    """Manage Yoto CYO playlists as folders on disk."""
    pass


@cli.command()
def auth():
    """Authenticate with Yoto (OAuth device code flow)."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
def sync(path, dry_run):
    """Push local playlist state to Yoto."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("path_or_card_id", default=".")
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
def pull(path_or_card_id, dry_run):
    """Pull remote playlist state to local."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def status(path):
    """Show diff between local and remote state."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("playlist", type=click.Path(exists=True))
def reorder(playlist):
    """Open playlist.jsonl in $EDITOR to reorder tracks."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Scaffold a new playlist folder."""
    click.echo("Not implemented yet")


@cli.command(name="import")
@click.argument("source", type=click.Path(exists=True))
def import_cmd(source):
    """Bulk import: convert a folder of audio files into a playlist."""
    click.echo("Not implemented yet")


@cli.command(name="list")
def list_cmd():
    """Show all MYO cards on your Yoto account."""
    click.echo("Not implemented yet")
