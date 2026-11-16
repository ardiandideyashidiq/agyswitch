import base64
import json
import secrets
import socket
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from curl_cffi import requests
from loguru import logger
from rich.console import Console
from rich.panel import Panel

from agyswitch.config import (
    AUTH_ENDPOINT,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    OAUTH_SCOPES,
    TOKEN_ENDPOINT,
    TOKENINFO_ENDPOINT,
)
from agyswitch.keyring import set_system_token
from agyswitch.vault import Profile, save_profile, set_active_profile

console = Console()


def find_free_port(preferred_port: int = 51123) -> int:
    """Try preferred port first, otherwise bind to an ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred_port))
            return preferred_port
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    auth_error: str | None = None
    auth_state: str | None = None
    auth_url: str | None = None
    done_event = threading.Event()

    def log_message(self, format: str, *args) -> None:
        # Suppress standard HTTP server request logging
        pass

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return

        params = urllib.parse.parse_qs(parsed_url.query)

        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        state = params.get("state", [None])[0]

        if error:
            OAuthCallbackHandler.auth_error = error
            self._send_response(
                400,
                "Authentication Failed",
                f"Error from Google: {error}. You may close this tab.",
                is_error=True,
            )
            OAuthCallbackHandler.done_event.set()
        elif code:
            OAuthCallbackHandler.auth_code = code
            OAuthCallbackHandler.auth_state = state
            self._send_response(
                200,
                "✓ Login Successful",
                "Your Antigravity profile is now authorized!<br>You can safely close this tab and return to your terminal.",
                is_error=False,
            )
            OAuthCallbackHandler.done_event.set()
        elif OAuthCallbackHandler.auth_url:
            # If user visits http://127.0.0.1:<port>/ directly, redirect to Google OAuth
            self.send_response(302)
            self.send_header("Location", OAuthCallbackHandler.auth_url)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        else:
            self._send_response(
                400,
                "Invalid Request",
                "No authorization code received. You may close this tab.",
                is_error=True,
            )

    def _send_response(self, status: int, title: str, message: str, is_error: bool = False) -> None:
        color = "#f43f5e" if is_error else "#10b981"
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #1e293b;
      padding: 2.5rem 3rem;
      border-radius: 1rem;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
      text-align: center;
      max-width: 440px;
      border: 1px solid #334155;
    }}
    h1 {{
      color: {color};
      font-size: 1.5rem;
      margin: 0 0 1rem 0;
    }}
    p {{
      color: #94a3b8;
      line-height: 1.6;
      margin: 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True


def build_auth_url(
    port: int,
    state: str,
    client_id: str = DEFAULT_CLIENT_ID,
) -> tuple[str, str]:
    """Build the authorization URL prompting account selection."""
    redirect_uri = f"http://127.0.0.1:{port}"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"
    return url, redirect_uri


def exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str = DEFAULT_CLIENT_ID,
    client_secret: str = DEFAULT_CLIENT_SECRET,
) -> dict:
    """Exchange authorization code for access and refresh tokens using curl_cffi."""
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = requests.post(
        TOKEN_ENDPOINT,
        data=data,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed (HTTP {resp.status_code}): {resp.text}")

    return resp.json()


def extract_email_from_token(token_data: dict) -> str:
    """Extract email from id_token or fallback to tokeninfo endpoint."""
    id_token = token_data.get("id_token")
    if id_token and "." in id_token:
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload_segment = parts[1]
                # Pad base64
                padded = payload_segment + "=" * (-len(payload_segment) % 4)
                decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8")
                id_payload = json.loads(decoded)
                email = id_payload.get("email")
                if email:
                    return str(email)
        except Exception as e:
            logger.debug(f"Failed decoding id_token: {e}")

    access_token = token_data.get("access_token")
    if access_token:
        try:
            resp = requests.get(
                f"{TOKENINFO_ENDPOINT}?access_token={access_token}",
                impersonate="chrome",
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "email" in data:
                    return str(data["email"])
        except Exception as e:
            logger.debug(f"Failed querying tokeninfo: {e}")

    return "unknown@gmail.com"


def login_flow(
    profile_name: str,
    open_browser: bool = False,
    port: int | None = None,
    timeout_seconds: int = 180,
) -> Profile:
    """Execute OAuth login flow with browser session reuse."""
    listen_port = port or find_free_port()
    state = secrets.token_urlsafe(16)
    auth_url, redirect_uri = build_auth_url(listen_port, state)

    OAuthCallbackHandler.auth_code = None
    OAuthCallbackHandler.auth_error = None
    OAuthCallbackHandler.auth_state = None
    OAuthCallbackHandler.auth_url = auth_url
    OAuthCallbackHandler.done_event.clear()

    server = HTTPServer(("127.0.0.1", listen_port), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    console.print(
        Panel(
            f"[bold cyan]Log in to Google Antigravity[/bold cyan]\n\n"
            f"Buka browser Anda dan klik/akses link singkat berikut:\n\n"
            f"👉 [bold green underline]http://127.0.0.1:{listen_port}[/bold green underline]\n\n"
            f"[dim](Link di atas otomatis mengarahkan ke halaman login & persetujuan Google)[/dim]\n\n"
            f"Atau buka URL Google langsung:\n"
            f"[dim underline]{auth_url}[/dim underline]\n\n"
            f"Pilih akun Google yang ingin digunakan untuk profil [bold cyan]{profile_name}[/bold cyan].",
            title=f"Profile: {profile_name}",
            border_style="cyan",
        )
    )

    if open_browser:
        try:
            import webbrowser
            webbrowser.open(auth_url)
        except Exception:
            pass

    try:
        # Wait cleanly on event rather than polling
        finished = OAuthCallbackHandler.done_event.wait(timeout=timeout_seconds)

        if not finished:
            raise TimeoutError("Login timed out waiting for browser callback.")

        if OAuthCallbackHandler.auth_error:
            raise RuntimeError(f"Authentication was rejected: {OAuthCallbackHandler.auth_error}")

        code = OAuthCallbackHandler.auth_code
        if not code:
            raise RuntimeError("No authorization code received.")
    finally:
        # Shutdown server asynchronously to avoid socket keep-alive deadlocks
        def _cleanup_server():
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        threading.Thread(target=_cleanup_server, daemon=True).start()

    console.print("[dim]Exchanging authorization code for credentials...[/dim]")
    token_data = exchange_code(code, redirect_uri)

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3599)
    expiry_dt = datetime.now(UTC) + timedelta(seconds=expires_in)
    expiry_iso = expiry_dt.isoformat()
    email = extract_email_from_token(token_data)

    profile = Profile(
        name=profile_name,
        email=email,
        access_token=access_token,
        refresh_token=refresh_token,
        expiry=expiry_iso,
    )

    save_profile(profile)
    set_active_profile(profile.name)
    set_system_token(profile.to_token_payload())

    console.print(
        f"[bold green]✓ Successfully logged in as [bold white]{email}[/bold white] "
        f"for profile [bold cyan]{profile_name}[/bold cyan]![/bold green]"
    )
    return profile
