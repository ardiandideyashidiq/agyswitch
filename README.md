# agyswitch (`ags`)

Fast, zero-overhead profile and quota manager for the **Google Antigravity CLI (`agy`)**.

## Features

- **Browser Session Reuse**: Fast OAuth login via local loopback URL; pick existing signed-in accounts without re-entering credentials.
- **Native `$HOME`**: Runs `agy` directly with real user environment (git, SSH, dotfiles, compilers intact).
- **Secure Keyring**: Seamless Linux Secret Service, macOS Keychain, and token fallback.
- **Live Quota Engine**: Real-time 5-hour rolling and weekly tier quota monitoring via `curl-cffi`.
- **Auto-Best Switcher**: Runs tasks using the profile with the highest available capacity.

## Quick Reference

| Command | Description |
| :--- | :--- |
| `ags login <name> [--open]` | Authenticate a new profile via browser |
| `ags import <name>` | Import currently active machine credentials |
| `ags list` | List all saved profiles and active status |
| `ags switch <name>` | Switch system-wide active profile |
| `ags quotas` | View summary quotas (Gemini / Claude & GPT) |
| `ags quotas -d` | View detailed per-model quota breakdown |
| `ags run <name> -- <args>` | Run `agy` with a specific profile |
| `ags best -- <args>` (`ags b`) | Automatically run `agy` with highest quota profile |
| `ags rename <old> <new>` | Rename an existing profile |
| `ags delete <name>` | Delete a profile |

## Installation

```bash
uv tool install git+https://github.com/ardiandideyashidiq/agyswitch.git

# or from local source:
uv tool install .

# or run directly without installing:
uv run ags <command>
```
