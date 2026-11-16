import shutil
import subprocess

from loguru import logger
from rich.console import Console

from agyswitch.keyring import get_system_token, set_system_token
from agyswitch.quota import fetch_all_quotas, refresh_profile_token
from agyswitch.vault import (
    Profile,
    list_profiles,
    load_profile,
    save_profile,
    set_active_profile,
)

console = Console()


def switch_profile(profile_name: str, verbose: bool = True) -> Profile:
    """Switch the active system credentials to the specified profile."""
    profile = load_profile(profile_name)
    if not profile:
        raise ValueError(f"Profile '{profile_name}' not found. Use 'agyswitch list' to view profiles.")

    # Refresh token if needed
    try:
        refresh_profile_token(profile)
    except Exception as e:
        logger.warning(f"Could not refresh token for '{profile_name}': {e}")

    # Set as active profile in vault & sync to system credential store
    set_active_profile(profile.name)
    set_system_token(profile.to_token_payload())

    if verbose:
        console.print(
            f"[bold green]Switched active Antigravity profile to [bold cyan]{profile.name}[/bold cyan] "
            f"([white]{profile.email}[/white])[/bold green]"
        )
    return profile


def run_profile(profile_name: str, agy_args: list[str]) -> int:
    """Stage profile token, run agy natively with real $HOME, and sync back updated tokens."""
    agy_bin = shutil.which("agy")
    if not agy_bin:
        console.print("[bold red]Error:[/bold red] 'agy' executable not found in PATH.")
        return 1

    profile = switch_profile(profile_name, verbose=False)
    console.print(
        f"[dim]Launching agy with profile [bold cyan]{profile.name}[/bold cyan] ({profile.email})...[/dim]"
    )

    cmd = [agy_bin, *agy_args]
    try:
        proc = subprocess.run(cmd)
        exit_code = proc.returncode
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        console.print(f"[bold red]Failed to execute agy:[/bold red] {e}")
        return 1

    # Sync back any refreshed token produced by agy
    try:
        current_token = get_system_token()
        if current_token:
            updated_profile = Profile.from_token_payload(profile.name, current_token)
            if updated_profile.access_token != profile.access_token:
                logger.debug(f"Syncing updated token from agy session back to profile '{profile.name}'")
                save_profile(updated_profile)
    except Exception as e:
        logger.debug(f"Could not sync post-session token: {e}")

    return exit_code


def run_best(agy_args: list[str]) -> int:
    """Find the profile with the highest available quota and run agy."""
    profiles = list_profiles()
    if not profiles:
        console.print("[bold red]No profiles configured.[/bold red] Run 'agyswitch login <name>' first.")
        return 1

    console.print("[dim]Evaluating quotas across all accounts...[/dim]")
    quotas = fetch_all_quotas(profiles)

    # Sort candidates by minimum gemini quota, then average gemini quota descending
    candidates = [q for q in quotas if not q.get("is_exhausted") and q.get("min_gemini_quota", 0) > 0]

    if not candidates:
        console.print("[bold red]All configured accounts are currently exhausted or unavailable![/bold red]")
        for q in quotas:
            console.print(f"  • {q['profile']} ({q['email']}): {q.get('error') or '0% quota'}")
        return 1

    candidates.sort(key=lambda x: (x["min_gemini_quota"], x["avg_gemini_quota"]), reverse=True)
    best_candidate = candidates[0]
    best_name = best_candidate["profile"]

    console.print(
        f"[bold green]Selected best profile:[/bold green] [bold cyan]{best_name}[/bold cyan] "
        f"({best_candidate['email']}) with [bold yellow]{best_candidate['min_gemini_quota']:.0f}%[/bold yellow] quota available."
    )

    return run_profile(best_name, agy_args)
