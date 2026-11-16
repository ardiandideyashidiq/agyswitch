
import rich_click as click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agyswitch.auth import login_flow
from agyswitch.keyring import get_system_token
from agyswitch.quota import fetch_all_quotas
from agyswitch.runner import run_best, run_profile, switch_profile
from agyswitch.vault import (
    Profile,
    delete_profile,
    get_active_profile,
    list_profiles,
    rename_profile,
    save_profile,
)

console = Console()

# Configure rich-click styling
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_OPTIONS_PANEL_BORDER = "cyan"
click.rich_click.STYLE_COMMANDS_PANEL_BORDER = "bright_blue"


@click.group()
def cli() -> None:
    """[bold cyan]agyswitch[/bold cyan] - Modern profile & quota manager for Antigravity CLI."""


@cli.command("login")
@click.argument("name")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Automatically open browser tab.")
@click.option("--port", type=int, default=None, help="Custom local port for OAuth redirect callback.")
def cmd_login(name: str, open_browser: bool, port: int | None) -> None:
    """Log in to a Google account and save it as a profile (reuses your existing browser)."""
    try:
        login_flow(profile_name=name, open_browser=open_browser, port=port)
    except Exception as e:
        console.print(f"[bold red]Login failed:[/bold red] {e}")
        raise click.Abort()


@cli.command("import")
@click.argument("name")
def cmd_import(name: str) -> None:
    """Import active machine credentials into a named profile."""
    system_tok = get_system_token()
    if not system_tok:
        console.print("[bold red]No active Antigravity credentials found on this machine.[/bold red]")
        raise click.Abort()

    profile = Profile.from_token_payload(name, system_tok)
    save_profile(profile)
    console.print(
        f"[bold green]Imported active credentials into profile [bold cyan]{name}[/bold cyan] "
        f"([white]{profile.email}[/white]).[/bold green]"
    )


@cli.command("list")
def cmd_list() -> None:
    """List all configured profiles."""
    profiles = list_profiles()
    if not profiles:
        console.print("[yellow]No profiles configured yet. Run [bold]agyswitch login <name>[/bold] to add one.[/yellow]")
        return

    active_name = get_active_profile()

    table = Table(title="Antigravity Profiles", border_style="cyan")
    table.add_column("Active", justify="center", style="bold green", width=8)
    table.add_column("Profile", style="bold cyan")
    table.add_column("Email", style="white")

    for p in profiles:
        is_active = "●" if p.name == active_name else ""
        table.add_row(is_active, p.name, p.email)

    console.print(table)


@cli.command("switch")
@click.argument("name")
def cmd_switch(name: str) -> None:
    """Switch active profile for normal 'agy' CLI usage."""
    try:
        switch_profile(name)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise click.Abort()


@cli.command("run", context_settings=dict(ignore_unknown_options=True))
@click.argument("name")
@click.argument("agy_args", nargs=-1, type=click.UNPROCESSED)
def cmd_run(name: str, agy_args: list[str]) -> None:
    """Run agy session with the specified profile."""
    code = run_profile(name, list(agy_args))
    raise SystemExit(code)


@cli.command("best", context_settings=dict(ignore_unknown_options=True))
@click.argument("agy_args", nargs=-1, type=click.UNPROCESSED)
def cmd_best(agy_args: list[str]) -> None:
    """Run agy with the profile having highest available quota."""
    code = run_best(list(agy_args))
    raise SystemExit(code)


@cli.command("b", context_settings=dict(ignore_unknown_options=True))
@click.argument("agy_args", nargs=-1, type=click.UNPROCESSED)
def cmd_b(agy_args: list[str]) -> None:
    """Short alias for 'best' (run agy with highest quota)."""
    cmd_best(agy_args)


@cli.command("quotas")
@click.option("--details", "-d", is_flag=True, help="List quota for every individual model.")
def cmd_quotas(details: bool = False) -> None:
    """Check quota across all profiles."""
    profiles = list_profiles()
    if not profiles:
        console.print("[yellow]No profiles configured. Run 'agyswitch login <name>' to add one.[/yellow]")
        return

    with console.status("[bold cyan]Fetching real-time quotas via curl-cffi...[/bold cyan]"):
        results = fetch_all_quotas(profiles)

    active_name = get_active_profile()

    for res in results:
        prof_name = res["profile"]
        is_active = " [bold green](Active)[/bold green]" if prof_name == active_name else ""
        tier = res.get("tier", "STANDARD")
        email = res.get("email", "")

        if res.get("error"):
            console.print(Panel(f"[red]Error: {res['error']}[/red]", title=f"{prof_name} ({email})", border_style="red"))
            continue

        models = res.get("models", [])
        gemini_models = [m for m in models if m.get("is_gemini")]
        claude_gpt_models = [m for m in models if not m.get("is_gemini")]

        col1_header = "Model" if details else "Category"
        table = Table(
            title=f"Profile: [bold cyan]{prof_name}[/bold cyan] ({email}) - Tier: [bold yellow]{tier}[/bold yellow]{is_active}",
            border_style="blue",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column(col1_header, style="white", min_width=24)
        table.add_column("Quota Available", justify="right", width=18)
        table.add_column("Reset In", justify="center", width=10)
        table.add_column("Weekly Reset", justify="center", width=14)

        def _format_bar(pct: float) -> str:
            color = "green" if pct > 40 else ("yellow" if pct > 15 else "red")
            bar_len = int(pct / 10)
            return f"[{color}]{'█' * bar_len}{'░' * (10 - bar_len)}[/] {pct:5.1f}%"

        def _format_weekly(weekly_str: str, wpct: float | None) -> str:
            if weekly_str and weekly_str != "-":
                if wpct is not None and wpct <= 0:
                    return f"[bold red]{weekly_str}[/bold red]"
                elif wpct is not None and wpct < 20:
                    return f"[yellow]{weekly_str}[/yellow]"
                else:
                    return f"[cyan]{weekly_str}[/cyan]"
            return "[dim]-[/dim]"

        if not models:
            table.add_row("[dim]No model quotas returned[/dim]", "[yellow]N/A[/yellow]", "-", "-")
        elif not details:
            # Summary view: GEMINI MODELS & CLAUDE AND GPT MODELS
            if gemini_models:
                g_pct = min(m["percentage"] for m in gemini_models)
                g_reset = gemini_models[0]["reset_str"]
                g_weekly = gemini_models[0].get("weekly_reset_str", "-")
                g_wpct = gemini_models[0].get("weekly_percentage")
                table.add_row("GEMINI MODELS", _format_bar(g_pct), g_reset, _format_weekly(g_weekly, g_wpct))

            if claude_gpt_models:
                c_pct = min(m["percentage"] for m in claude_gpt_models)
                c_reset = claude_gpt_models[0]["reset_str"]
                c_weekly = claude_gpt_models[0].get("weekly_reset_str", "-")
                c_wpct = claude_gpt_models[0].get("weekly_percentage")
                table.add_row("CLAUDE AND GPT MODELS", _format_bar(c_pct), c_reset, _format_weekly(c_weekly, c_wpct))
        else:
            # Detailed view: list every model under its group
            if gemini_models:
                table.add_row("[bold cyan]GEMINI MODELS[/bold cyan]", "", "", "")
                for m in gemini_models:
                    table.add_row(
                        f"  {m['name']}",
                        _format_bar(m["percentage"]),
                        m["reset_str"],
                        _format_weekly(m.get("weekly_reset_str", "-"), m.get("weekly_percentage")),
                    )

            if claude_gpt_models:
                if gemini_models:
                    table.add_section()
                table.add_row("[bold magenta]CLAUDE AND GPT MODELS[/bold magenta]", "", "", "")
                for m in claude_gpt_models:
                    table.add_row(
                        f"  {m['name']}",
                        _format_bar(m["percentage"]),
                        m["reset_str"],
                        _format_weekly(m.get("weekly_reset_str", "-"), m.get("weekly_percentage")),
                    )

        console.print(table)
        console.print()

    if not details and results:
        console.print("[dim]💡 Tip: Run 'agyswitch quotas --details' (or 'ags quotas -d') to list every individual model.[/dim]\n")


@cli.command("rename")
@click.argument("old_name")
@click.argument("new_name")
def cmd_rename(old_name: str, new_name: str) -> None:
    """Rename an existing profile."""
    if rename_profile(old_name, new_name):
        console.print(
            f"[bold green]Renamed profile '[bold cyan]{old_name}[/bold cyan]' "
            f"to '[bold cyan]{new_name}[/bold cyan]'.[/bold green]"
        )
    else:
        console.print(f"[bold red]Profile '{old_name}' not found.[/bold red]")


@cli.command("delete")
@click.argument("name")
def cmd_delete(name: str) -> None:
    """Delete a profile."""
    if delete_profile(name):
        console.print(f"[bold green]Deleted profile '[bold cyan]{name}[/bold cyan]'.[/bold green]")
    else:
        console.print(f"[bold red]Profile '{name}' does not exist.[/bold red]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
