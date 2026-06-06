#!/usr/bin/env python3
"""
╭──────────────────────────────────────────────────╮
│              ✦  RaiSaver CLI  ✦                 │
│        All-in-One Media Downloader               │
│                                                  │
│  Requires:                                       │
│    pip install rich prompt_toolkit pyfiglet      │
│               requests tqdm                      │
╰──────────────────────────────────────────────────╯
"""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import re
import sys
import time
import threading
import shutil
from datetime import datetime
from urllib.parse import unquote

# ── Third-party ───────────────────────────────────────────────────────────────
def _ensure(pkg: str, import_as: str | None = None):
    """Auto-install a missing package, then import it."""
    import importlib, subprocess
    name = import_as or pkg
    try:
        return importlib.import_module(name)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            stdout=subprocess.DEVNULL,
        )
        return importlib.import_module(name)

_ensure("rich")
_ensure("prompt_toolkit", "prompt_toolkit")
_ensure("pyfiglet")
_ensure("tqdm")
_ensure("requests")

import requests
from tqdm import tqdm
import pyfiglet

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich.progress import (
    Progress, SpinnerColumn, BarColumn,
    TextColumn, TimeRemainingColumn, TransferSpeedColumn,
    TaskProgressColumn,
)
from rich.live import Live
from rich.padding import Padding
from rich.columns import Columns
from rich.style import Style
from rich.markup import escape

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.formatted_text import HTML

# ── Console singleton ─────────────────────────────────────────────────────────
console = Console(highlight=False, soft_wrap=True)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli_config.json")

def load_config():
    """Load configuration from JSON file."""
    defaults = {
        "download_dir": os.path.join(os.path.expanduser("~"), "Music"),
        "backend_url": "http://127.0.0.1:8000"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            import json
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception:
            pass
    return defaults

def save_config(download_dir: str):
    """Save configuration to JSON file."""
    try:
        import json
        config = {"download_dir": download_dir}
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception:
        pass

_cfg = load_config()
BACKEND_URL  = os.environ.get("RAISAVER_BACKEND", _cfg["backend_url"])
DOWNLOAD_DIR = os.environ.get("RAISAVER_DIR", _cfg["download_dir"])
CHUNK_SIZE = 1024 * 64  # 64 KB
APP_VERSION = "2.0.0"

# ── Palette (Rich markup colours) ─────────────────────────────────────────────
C = {
    "cyan":    "cyan",
    "green":   "green",
    "yellow":  "yellow",
    "red":     "bright_red",
    "gray":    "bright_black",
    "white":   "white",
    "bold":    "bold white",
    "dim":     "dim white",
    "accent":  "bold cyan",
    "success": "bold green",
    "warn":    "bold yellow",
    "error":   "bold bright_red",
}

# ── prompt_toolkit style ──────────────────────────────────────────────────────
PT_STYLE = PTStyle.from_dict({
    "prompt":      "bold ansicyan",
    "placeholder": "ansibrightblack",
    "": "ansiwhite",
})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  UI COMPONENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ui_clear():
    os.system("cls" if os.name == "nt" else "clear")


def ui_banner():
    """Prints the static ASCII banner and version info."""
    fig = pyfiglet.figlet_format("RaiSaver", font="slant")
    logo_lines = fig.strip().splitlines()

    console.print()
    for line in logo_lines:
        console.print(f"  [bold cyan]{line}[/]")
    console.print()

    tagline = Text("  ✦  All-in-One Media Downloader  ✦  ", style="dim white")
    version = Text(f"  v{APP_VERSION}  CLI Edition  ", style="bright_black")
    console.print(tagline)
    console.print(version)
    console.print()


def ui_splash():
    """Animated splash screen with pyfiglet logo."""
    ui_clear()
    ui_banner()

    # Loading animation
    steps = [
        ("⠋", "Initialising engine"),
        ("⠙", "Checking backend"),
        ("⠹", "Loading config"),
        ("⠸", "Ready"),
    ]
    for spinner_char, label in steps:
        console.print(
            f"  [bright_black]{spinner_char}[/]  [dim]{label}...[/]",
            end="\r",
        )
        time.sleep(0.25)

    console.print(" " * 40, end="\r")  # clear line


def ui_rule(label: str = ""):
    if label:
        console.print(Rule(f"[bright_black]{label}[/]", style="bright_black"))
    else:
        console.print(Rule(style="bright_black"))


def ui_spacer(n: int = 1):
    console.print("\n" * (n - 1))


def ui_header(backend_ok: bool, save_dir: str, fmt: str = "MP3, MP4, JPG", status: str = "Ready"):
    """Main dashboard header."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bright_black", min_width=12)
    grid.add_column()

    status_color = "green" if backend_ok else "red"
    status_text  = "Online" if backend_ok else "Offline"

    grid.add_row("Backend", f"[{status_color}]●[/] {status_text}")
    grid.add_row("Save Dir", f"[dodger_blue1]{save_dir}[/]")
    grid.add_row("Format", fmt)
    grid.add_row("Status", f"[dim]{status}[/]")

    title = Text.from_markup("  [bold cyan]✦[/]  [bold white]RaiSaver CLI[/]  [bold cyan]✦[/]  ")
    panel = Panel(
        Padding(grid, (0, 1)),
        title=title,
        subtitle=Text.from_markup("[bright_black]All-in-One Media Downloader[/]"),
        border_style="bright_black",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)


def ui_tips():
    """Subtle tips below the input area."""
    tips = [
        "[bright_black]•[/] [dim]type[/] [cyan]dir[/] [dim]to change folder[/]",
        "[bright_black]•[/] [dim]type[/] [cyan]q[/]   [dim]to exit[/]",
        "[bright_black]•[/] [dim]Ctrl+C cancels download[/]",
    ]
    console.print()
    for tip in tips:
        console.print(f"    {tip}")
    console.print()


def ui_prompt_url() -> str:
    """Modern input with prompt_toolkit."""
    try:
        raw = pt_prompt(
            HTML('<ansibrightblack>  ❯ </ansibrightblack><ansicyan>URL  </ansicyan>'),
            style=PT_STYLE,
            placeholder="  paste YouTube · TikTok · Instagram link",
        )
        return raw.strip()
    except (KeyboardInterrupt, EOFError):
        return "q"


def ui_prompt_choice(question: str, options: list[str]) -> str:
    """Numbered choice prompt with a header."""
    console.print()
    console.print("  [bold white]Download Options[/]")
    for i, opt in enumerate(options, 1):
        console.print(f"    [bright_black]{i}.[/]  [white]{opt}[/]")
    console.print()
    try:
        raw = pt_prompt(
            HTML(f'<ansibrightblack>  ❯ </ansibrightblack><ansicyan>{escape(question)}  </ansicyan>'),
            style=PT_STYLE,
        )
        return raw.strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def ui_prompt_folder(current: str) -> str:
    """Folder input section."""
    console.print()
    console.print(Panel(
        f"[bright_black]Current[/]  [cyan]{escape(current)}[/]\n\n"
        "[dim]Enter a new path, or press [/][cyan]Enter[/][dim] to open a folder picker.[/]",
        title="[bold white]📁  Change Directory[/]",
        border_style="bright_black",
        padding=(1, 2),
    ))
    console.print()
    try:
        raw = pt_prompt(
            HTML('<ansibrightblack>  ❯ </ansibrightblack><ansicyan>Path  </ansicyan>'),
            style=PT_STYLE,
            placeholder="  leave blank to open picker",
        )
        return raw.strip().strip('"').strip("'")
    except (KeyboardInterrupt, EOFError):
        return current


def ui_info_card(title: str, channel: str, extra: str = "", custom_fields: dict[str, str] | None = None):
    """Track / video info card with grid layout."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bright_black", min_width=10)
    grid.add_column(style="white")

    if custom_fields:
        for label, value in custom_fields.items():
            if value:
                grid.add_row(label, value[:100] + "..." if len(value) > 100 else value)
    else:
        # Clean title and channel for display
        disp_title = title[:60] + "..." if len(title) > 60 else title
        grid.add_row("Title", disp_title)
        grid.add_row("Channel", channel)

        if extra:
            # If extra contains duration info, label it properly
            label = "Info"
            value = extra
            if "duration" in extra.lower():
                label = "Duration"
                value = extra.lower().replace("duration", "").strip()
            
            grid.add_row(label, value)

    console.print()
    console.print(Panel(
        Padding(grid, (0, 1)),
        title="[bold white]Video Information[/]",
        border_style="bright_black",
        padding=(1, 2),
    ))


def ui_success(filename: str, saved_to: str):
    """Success card shown after a completed download."""
    console.print()
    console.print(Panel(
        f"[bold green]✓  Download Complete[/]\n\n"
        f"[bright_black]File   [/] [white]{escape(filename)}[/]\n"
        f"[bright_black]Saved  [/] [cyan]{escape(saved_to)}[/]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()


def ui_error(message: str):
    """Clean error card — no ugly tracebacks."""
    console.print()
    console.print(Panel(
        f"[bold bright_red]✗  Error[/]\n\n[white]{escape(str(message))}[/]",
        border_style="bright_red",
        padding=(1, 2),
    ))
    console.print()


def ui_warn(message: str):
    console.print(f"\n  [bold yellow]⚠[/]  [yellow]{escape(message)}[/]\n")


def ui_platform_badge(platform: str):
    colours = {"youtube": "red", "tiktok": "bright_magenta", "instagram": "yellow"}
    col = colours.get(platform.lower(), "cyan")
    icons = {"youtube": "▶", "tiktok": "♪", "instagram": "◈"}
    icon = icons.get(platform.lower(), "⚡")
    console.print(
        f"\n  [{col}]{icon}[/]  [bold {col}]{platform.upper()}[/]  [bright_black]detected[/]"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DOWNLOAD ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download_file(url: str, save_path: str, label: str = "Downloading") -> str:
    """
    Stream-download with a Rich progress bar showing file size and speed.
    Returns the final saved path.
    """
    # Force absolute path and ensure initial directory exists
    save_path = os.path.abspath(save_path)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with requests.get(url, stream=True, timeout=(30, 600)) as resp:
        resp.raise_for_status()

        # Resolve filename from Content-Disposition
        cd = resp.headers.get("content-disposition", "")
        final_name = None
        m = re.search(r"filename\*\s*=\s*UTF-8''([^;\s]+)", cd, re.IGNORECASE)
        if m:
            final_name = unquote(m.group(1))
        if not final_name:
            m = re.search(r'filename\s*=\s*"?([^";\s]+)"?', cd, re.IGNORECASE)
            if m:
                final_name = m.group(1)
        
        if final_name:
            final_name = os.path.basename(final_name)
            save_path = os.path.join(os.path.dirname(save_path), final_name)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

        total = int(resp.headers.get("content-length", 0)) or None

        # Format total size for display in label
        size_str = ""
        if total:
            size_mb = total / (1024 * 1024)
            if size_mb >= 1024:
                size_str = f"  [bright_black]({size_mb/1024:.2f} GiB)[/]"
            else:
                size_str = f"  [bright_black]({size_mb:.2f} MiB)[/]"

        with Progress(
            TextColumn("  "),
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(f"[bold cyan]✦[/]  [white]{escape(label[:40])}[/]{size_str}"),
            BarColumn(
                bar_width=28,
                style="bright_black",
                complete_style="green",
                finished_style="green",
            ),
            TaskProgressColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=total)
            with open(save_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)
                        progress.advance(task, len(chunk))

    if not os.path.isfile(save_path):
        raise Exception(f"File failed to save at {save_path}")

    # Show final size after download
    final_size = os.path.getsize(save_path)
    size_mb = final_size / (1024 * 1024)
    if size_mb >= 1024:
        size_display = f"{size_mb/1024:.2f} GiB"
    else:
        size_display = f"{size_mb:.2f} MiB"
    console.print(f"  [green]✓[/]  [dim]Saved[/] [white]{escape(os.path.basename(save_path))}[/]  [bright_black]{size_display}[/]")

    return save_path


def poll_progress(task_id: str, label: str = "Processing", stop_event: threading.Event | None = None):
    """Poll /progress endpoint while backend processes the task."""
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    while True:
        if stop_event and stop_event.is_set():
            console.print(" " * 90, end="\r")
            break
        try:
            r = requests.get(
                f"{BACKEND_URL}/progress",
                params={"task_id": task_id},
                timeout=5,
            )
            data      = r.json()
            status    = data.get("status", "")
            prog      = data.get("progress", 0.0)
            speed     = data.get("speed", "")      # e.g. "2.66MiB/s"
            total_str = data.get("total", "")      # e.g. "900.99MiB"

            filled = int(prog * 28)
            bar    = f"[cyan]{'█' * filled}[/][bright_black]{'░' * (28 - filled)}[/]"
            pct    = f"[cyan]{prog * 100:.0f}%[/]"

            # Build extra info string
            extras = []
            if total_str:
                extras.append(f"[white]{total_str}[/]")
            if speed:
                extras.append(f"[cyan]{speed}[/]")
            extra_str = "  " + "  ".join(extras) if extras else ""

            # Only show label if no extra info, to avoid duplication
            label_str = f"  [dim]{label}[/]" if not extras else ""

            console.print(
                f"  [cyan]{spinner[i % len(spinner)]}[/]  {bar}  {pct}{extra_str}{label_str}",
                end="\r",
            )
            i += 1
            if status in ("completed", "error"):
                console.print(" " * 90, end="\r")
                break
        except Exception:
            pass
        time.sleep(0.6)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "tiktok.com" in url or "vt.tiktok.com" in url:
        return "tiktok"
    if "instagram.com" in url:
        return "instagram"
    return "unknown"


def safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[#@]\S+", "", text)
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("_.")
    return text[:max_len] or "download"


def pick_folder(current: str) -> str:
    """Native folder picker with clean fallback."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="Select save folder", initialdir=current)
        root.destroy()
        return chosen if chosen else current
    except Exception:
        return ui_prompt_folder(current)


def check_backend() -> bool:
    try:
        requests.get(f"{BACKEND_URL}/progress", params={"task_id": "ping"}, timeout=3)
        return True
    except Exception:
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PLATFORM HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def handle_youtube(url: str, save_dir: str):
    with Progress(
        TextColumn("  "),
        SpinnerColumn("dots", style="cyan"),
        TextColumn("[dim]Fetching info...[/]"),
        console=console,
        transient=True,
    ) as pg:
        pg.add_task("")
        r = requests.get(f"{BACKEND_URL}/info", params={"url": url}, timeout=30)

    if r.status_code != 200:
        ui_error(r.json().get("detail", r.text))
        return

    info     = r.json()
    title    = info.get("title", "Unknown")
    channel  = info.get("channel", "")
    duration = info.get("duration", 0)
    formats  = info.get("video_formats", [])
    mins, secs = divmod(int(duration or 0), 60)

    ui_info_card(title, channel, f"{mins}:{secs:02d} duration")

    while True:
        choice = ui_prompt_choice("Select format", ["MP3  (audio only)", "MP4  (video)"])
        task_id = str(int(time.time() * 1000))

        if choice == "1":
            save_path = os.path.join(save_dir, f"{safe_filename(title)}.mp3")
            endpoint  = (
                f"{BACKEND_URL}/download/audio"
                f"?url={requests.utils.quote(url)}&task_id={task_id}"
            )
            stop_event = threading.Event()
            poll = threading.Thread(
                target=poll_progress, args=(task_id, "Converting to MP3", stop_event), daemon=True
            )
            poll.start()
            final = download_file(endpoint, save_path, f"{title[:35]}…" if len(title) > 35 else title)
            stop_event.set()
            poll.join(timeout=2)
            ui_success(os.path.basename(final), os.path.dirname(final))
            break

        elif choice == "2":
            options = [f["resolution"] for f in formats]
            options.append("[yellow]Back[/]")
            idx_str = ui_prompt_choice("Select resolution", options)
            
            try:
                idx = int(idx_str) - 1
                if idx == len(options) - 1: # User chose "Back"
                    ui_clear()
                    ui_banner()
                    ui_info_card(title, channel, f"{mins}:{secs:02d} duration")
                    continue
                fmt = formats[idx]
            except (ValueError, IndexError):
                ui_warn("Invalid selection.")
                continue

            res       = fmt["resolution"]
            save_path = os.path.join(save_dir, f"{safe_filename(title)} [{res}].mp4")
            endpoint  = (
                f"{BACKEND_URL}/download/video"
                f"?url={requests.utils.quote(url)}"
                f"&format_id={fmt['format_id']}&task_id={task_id}"
            )
            stop_event = threading.Event()
            poll = threading.Thread(
                target=poll_progress, args=(task_id, f"Processing {res}", stop_event), daemon=True
            )
            poll.start()
            final = download_file(endpoint, save_path, f"{title[:30]} · {res}")
            stop_event.set()
            poll.join(timeout=2)
            ui_success(os.path.basename(final), os.path.dirname(final))
            break

        else:
            ui_warn("Invalid selection.")
            break


def handle_tiktok(url: str, save_dir: str):
    # Clean short URLs
    clean = url.split("?")[0]

    with Progress(
        TextColumn("  "),
        SpinnerColumn("dots", style="cyan"),
        TextColumn("[dim]Fetching TikTok info...[/]"),
        console=console,
        transient=True,
    ) as pg:
        pg.add_task("")
        r = requests.get(f"{BACKEND_URL}/tiktok/info", params={"url": clean}, timeout=30)

    if r.status_code != 200:
        ui_error(r.json().get("detail", r.text))
        return

    info    = r.json()
    title   = info.get("title", "TikTok")
    channel = info.get("channel", "")
    formats = info.get("video_formats", [])
    is_photo = info.get("is_photo", False)

    ui_info_card(title, channel)

    if is_photo and len(formats) > 1:
        while True:
            choice = ui_prompt_choice(
                "Select action",
                ["Download one photo", "Download all as ZIP"],
            )
            if choice == "1":
                options = [f["resolution"] for f in formats]
                options.append("[yellow]Back[/]")
                idx_str = ui_prompt_choice("Select photo", options)
                try:
                    idx = int(idx_str) - 1
                    if idx == len(options) - 1: # Back
                        ui_clear()
                        ui_banner()
                        ui_info_card(title, channel)
                        continue
                    fmt = formats[idx]
                except (ValueError, IndexError):
                    ui_warn("Invalid selection.")
                    continue
                
                task_id   = str(int(time.time() * 1000))
                save_path = os.path.join(save_dir, f"{safe_filename(title)}_foto{idx + 1}.jpg")
                endpoint  = (
                    f"{BACKEND_URL}/tiktok/download"
                    f"?url={requests.utils.quote(clean)}"
                    f"&format_id={fmt['format_id']}&task_id={task_id}"
                )
                final = download_file(endpoint, save_path, f"Photo {idx + 1}")
                ui_success(os.path.basename(final), os.path.dirname(final))
                break

            elif choice == "2":
                task_id   = str(int(time.time() * 1000))
                save_path = os.path.join(save_dir, f"{safe_filename(title)}.zip")
                endpoint  = (
                    f"{BACKEND_URL}/tiktok/download/all"
                    f"?url={requests.utils.quote(clean)}&task_id={task_id}"
                )
                stop_event = threading.Event()
                poll = threading.Thread(
                    target=poll_progress, args=(task_id, "Packing TikTok ZIP", stop_event), daemon=True
                )
                poll.start()
                final = download_file(endpoint, save_path, "Packing slideshow ZIP")
                stop_event.set()
                poll.join(timeout=2)
                ui_success(os.path.basename(final), os.path.dirname(final))
                break
            else:
                ui_warn("Invalid selection.")
                break
    else:
        while True:
            options = [f["resolution"] for f in formats]
            options.append("[yellow]Back[/]")
            idx_str = ui_prompt_choice("Select quality", options)
            try:
                idx = int(idx_str) - 1
                if idx == len(options) - 1: # Back
                    return
                fmt = formats[idx]
            except (ValueError, IndexError):
                ui_warn("Invalid selection.")
                continue

            task_id   = str(int(time.time() * 1000))
            save_path = os.path.join(save_dir, f"{safe_filename(title)}.mp4")
            endpoint  = (
                f"{BACKEND_URL}/tiktok/download"
                f"?url={requests.utils.quote(clean)}"
                f"&format_id={fmt['format_id']}&task_id={task_id}"
            )
            
            stop_event = threading.Event()
            poll = threading.Thread(
                target=poll_progress, args=(task_id, "Fetching from TikTok", stop_event), daemon=True
            )
            poll.start()
            
            final = download_file(endpoint, save_path, f"{title[:35]} · {fmt['resolution']}")
            
            stop_event.set()
            poll.join(timeout=2)
            
            ui_success(os.path.basename(final), os.path.dirname(final))
            break


def handle_instagram(url: str, save_dir: str):
    with Progress(
        TextColumn("  "),
        SpinnerColumn("dots", style="cyan"),
        TextColumn("[dim]Fetching Instagram info...[/]"),
        console=console,
        transient=True,
    ) as pg:
        pg.add_task("")
        r = requests.get(f"{BACKEND_URL}/instagram/info", params={"url": url}, timeout=30)

    if r.status_code != 200:
        ui_error(r.json().get("detail", r.text))
        return

    info    = r.json()
    title   = info.get("title", "Instagram")
    channel = info.get("channel", "")
    desc    = info.get("description", "")
    formats = info.get("video_formats", [])
    is_photo = info.get("is_photo", False)
    is_carousel = info.get("is_carousel", False)

    # Extract info for Instagram specific fields
    import re
    hastags = " ".join(re.findall(r"#\w+", desc))
    clean_desc = re.sub(r"#\w+", "", desc).strip()
    # If description is too long, take the first part
    display_desc = clean_desc.split("\n")[0][:80]

    ig_fields = {
        "Deskripsi": display_desc or title,
        "Akun": channel,
        "Hastag": hastags[:100] + "..." if len(hastags) > 100 else hastags
    }
    ui_info_card(title, channel, custom_fields=ig_fields)

    task_id   = str(int(time.time() * 1000))
    api_fmts  = [f for f in formats if f.get("format_id", "").startswith("api_")]

    if is_photo and is_carousel:
        console.print(
            f"\n  [bright_black]Carousel detected[/]  "
            f"[cyan]{len(api_fmts)}[/] [dim]photos[/]"
        )
        while True:
            choice = ui_prompt_choice(
                "Select action",
                ["Download one photo", "Download all as ZIP"],
            )
            if choice == "1":
                options = [f["resolution"] for f in api_fmts]
                options.append("[yellow]Back[/]")
                idx_str = ui_prompt_choice("Select photo", options)
                try:
                    idx = int(idx_str) - 1
                    if idx == len(options) - 1: # Back
                        ui_clear()
                        ui_banner()
                        ui_info_card(title, channel, custom_fields=ig_fields)
                        continue
                    fmt = api_fmts[idx]
                except (ValueError, IndexError):
                    ui_warn("Invalid selection.")
                    continue
                ext       = fmt.get("ext", "jpg")
                save_path = os.path.join(save_dir, f"{safe_filename(title)}_foto{idx + 1}.{ext}")
                endpoint  = (
                    f"{BACKEND_URL}/instagram/download"
                    f"?url={requests.utils.quote(url)}"
                    f"&format_id={fmt['format_id']}&task_id={task_id}"
                )
                final = download_file(endpoint, save_path, f"Photo {idx + 1}")
                ui_success(os.path.basename(final), os.path.dirname(final))
                break

            elif choice == "2":
                save_path = os.path.join(save_dir, f"{safe_filename(title)}.zip")
                endpoint  = (
                    f"{BACKEND_URL}/instagram/download/all"
                    f"?url={requests.utils.quote(url)}&task_id={task_id}"
                )
                stop_event = threading.Event()
                poll = threading.Thread(
                    target=poll_progress, args=(task_id, "Packing ZIP", stop_event), daemon=True
                )
                poll.start()
                final = download_file(endpoint, save_path, "Packing carousel ZIP")
                stop_event.set()
                poll.join(timeout=2)
                ui_success(os.path.basename(final), os.path.dirname(final))
                break
            else:
                ui_warn("Invalid selection.")
                break
    else:
        # Visual simplification for Instagram resolutions
        display_formats = []
        if not is_photo:
            # If backend sent multiple numeric resolutions, pick best as HD and worst as SD
            # This is a safety measure in case backend simplification isn't reflected
            numeric_fmts = [f for f in formats if "p" in f["resolution"].lower() and f["resolution"][:-1].isdigit()]
            if numeric_fmts:
                numeric_fmts.sort(key=lambda x: int(x["resolution"][:-1]), reverse=True)
                best = numeric_fmts[0].copy()
                best["resolution"] = "HD (High Quality)"
                display_formats.append(best)
                if len(numeric_fmts) > 1:
                    worst = numeric_fmts[-1].copy()
                    worst["resolution"] = "SD (Standard Quality)"
                    display_formats.append(worst)
            else:
                display_formats = formats
        else:
            display_formats = formats

        while True:
            # Determine prompt based on content type
            prompt = "Select photo" if is_photo else "Select resolution"
            options = [f["resolution"] for f in display_formats]
            options.append("[yellow]Back[/]")
            idx_str = ui_prompt_choice(prompt, options)
            try:
                idx = int(idx_str) - 1
                if idx == len(options) - 1: # Back
                    return
                fmt = display_formats[idx]
            except (ValueError, IndexError):
                ui_warn("Invalid selection.")
                continue
            ext       = fmt.get("ext", "mp4")
            save_path = os.path.join(save_dir, f"{safe_filename(title)}.{ext}")
            endpoint  = (
                f"{BACKEND_URL}/instagram/download"
                f"?url={requests.utils.quote(url)}&task_id={task_id}"
                f"&format_id={fmt['format_id']}"
            )
            
            stop_event = threading.Event()
            poll = threading.Thread(
                target=poll_progress, args=(task_id, "Fetching from Instagram", stop_event), daemon=True
            )
            poll.start()
            
            final = download_file(endpoint, save_path, f"{title[:35]} · {fmt['resolution']}")
            
            stop_event.set()
            poll.join(timeout=2)
            
            ui_success(os.path.basename(final), os.path.dirname(final))
            break


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHANGE FOLDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cmd_change_dir(current: str) -> str:
    ui_clear()
    ui_banner()
    raw = ui_prompt_folder(current)

    if not raw:
        chosen = pick_folder(current)
    else:
        chosen = raw

    if not chosen or chosen == current:
        console.print(f"\n  [bright_black]Unchanged —[/] [cyan]{escape(current)}[/]\n")
        return current

    try:
        os.makedirs(chosen, exist_ok=True)
        console.print()
        console.print(Panel(
            f"[bold green]✓  Directory Updated[/]\n\n"
            f"[bright_black]New path[/]  [cyan]{escape(chosen)}[/]",
            border_style="green",
            padding=(1, 2),
        ))
        console.print()
        try:
            console.print("  [bright_black]Press Enter to continue...[/]", end="")
            input()
        except (KeyboardInterrupt, EOFError):
            pass
        save_config(chosen)
        return chosen
    except Exception as exc:
        ui_warn(f"Could not set directory: {exc}")
        return current


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    global DOWNLOAD_DIR

    # ── Splash ──────────────────────────────────────────────────────────────
    ui_splash()

    # ── Backend check ────────────────────────────────────────────────────────
    backend_ok = check_backend()
    if not backend_ok:
        console.print()
        console.print(Panel(
            f"[bold bright_red]✗  Backend Unreachable[/]\n\n"
            f"[white]Could not connect to[/]  [cyan]{BACKEND_URL}[/]\n\n"
            f"[dim]Start the backend first:[/]\n"
            f"[bright_black]  uvicorn main:app --reload[/]",
            border_style="bright_red",
            padding=(1, 2),
        ))
        console.print()
        sys.exit(1)

    # ── Initial folder setup ─────────────────────────────────────────────────
    initial_url = None
    console.print()
    console.print(Panel(
        f"[bright_black]Save path[/]  [cyan]{escape(DOWNLOAD_DIR)}[/]\n\n"
        "[dim]Press [/][cyan]Enter[/][dim] to keep, or type a new path / [/][cyan]dir[/][dim] to browse.[/]",
        title="[bold white]📁  Save Directory[/]",
        border_style="bright_black",
        padding=(1, 2),
    ))
    console.print()
    try:
        raw = pt_prompt(
            HTML('<ansibrightblack>  ❯ </ansibrightblack><ansicyan>Path  </ansicyan>'),
            style=PT_STYLE,
            placeholder="  press Enter to confirm",
        )
    except (KeyboardInterrupt, EOFError):
        raw = ""

    raw = raw.strip().strip('"').strip("'")
    if raw.lower() == "dir":
        DOWNLOAD_DIR = cmd_change_dir(DOWNLOAD_DIR)
    elif raw.startswith("http"):
        # Smart detection: user pasted a URL instead of a path
        initial_url = raw
        save_config(DOWNLOAD_DIR)
    elif raw:
        try:
            os.makedirs(raw, exist_ok=True)
            DOWNLOAD_DIR = raw
            save_config(DOWNLOAD_DIR)
        except Exception as exc:
            ui_warn(f"Could not set path: {exc}")
    else:
        # User pressed Enter on the existing path, ensure it's saved to config
        save_config(DOWNLOAD_DIR)

    # ── Main loop ────────────────────────────────────────────────────────────
    while True:
        # Clear & redraw header on each iteration
        ui_clear()
        ui_banner()
        ui_header(backend_ok, DOWNLOAD_DIR)
        ui_tips()

        if initial_url:
            url = initial_url
            initial_url = None # Clear after first use
            console.print(f"  [cyan]❯[/] [ansicyan]URL   [/] [white]{url}[/]")
        else:
            url = ui_prompt_url()

        # ── Exit ──────────────────────────────────────────────────────────
        if url.lower() in ("q", "quit", "exit", "bye"):
            console.print()
            console.print(Rule(style="bright_black"))
            console.print(
                Align.center(
                    Text("  ✦  Thanks for using RaiSaver  ✦  ", style="dim white")
                )
            )
            console.print(Rule(style="bright_black"))
            console.print()
            break

        # ── Change dir ────────────────────────────────────────────────────
        if url.lower() in ("dir", "cd", "folder", "path"):
            DOWNLOAD_DIR = cmd_change_dir(DOWNLOAD_DIR)
            continue

        # ── Validate URL ──────────────────────────────────────────────────
        if not url.startswith("http"):
            ui_warn("That doesn't look like a URL. Paste a YouTube, TikTok, or Instagram link.")
            time.sleep(1)
            continue

        platform = detect_platform(url)
        ui_platform_badge(platform)

        if platform == "unknown":
            ui_error("Platform not supported. Only YouTube · TikTok · Instagram.")
            time.sleep(1)
            continue

        # ── Dispatch ──────────────────────────────────────────────────────
        try:
            if platform == "youtube":
                handle_youtube(url, DOWNLOAD_DIR)
            elif platform == "tiktok":
                handle_tiktok(url, DOWNLOAD_DIR)
            elif platform == "instagram":
                handle_instagram(url, DOWNLOAD_DIR)
        except KeyboardInterrupt:
            console.print()
            console.print(f"\n  [bold yellow]⚠[/]  [yellow]Download cancelled.[/]\n")
            time.sleep(0.5)
        except Exception as exc:
            ui_error(str(exc))
            time.sleep(0.5)

        # Brief pause before looping back so the success/error card is readable
        try:
            console.print(
                "  [bright_black]Press Enter to continue...[/]", end=""
            )
            input()
        except (KeyboardInterrupt, EOFError):
            break


if __name__ == "__main__":
    main()