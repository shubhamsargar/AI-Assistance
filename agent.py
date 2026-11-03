from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import pyautogui
import psutil
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.getenv("VIDMAHI_SERVER_URL", "http://127.0.0.1:5000").rstrip("/")
AGENT_TOKEN = os.getenv("VIDMAHI_AGENT_TOKEN", "")

WINDOWS_APPS = {
    "chrome": Path(os.getenv("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")),
    "code": Path(os.getenv("VSCODE_PATH", r"C:\Program Files\Microsoft VS Code\Code.exe")),
}


def request_json(path: str, method: str = "GET", data: dict | None = None) -> dict:
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(
        f"{SERVER_URL}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {AGENT_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def execute_action(action: dict) -> str:
    kind = action.get("kind")
    if kind == "open_url":
        url = str(action.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("The requested URL is not allowed.")
        if not webbrowser.open(url):
            raise RuntimeError("No browser was able to open the requested page.")
        return f"Opened {url}."

    if kind == "battery":
        battery = psutil.sensors_battery()
        if battery is None:
            raise RuntimeError("Battery status is unavailable on this device.")
        return f"Battery is at {battery.percent} percent."

    if kind == "screenshot":
        image = pyautogui.screenshot()
        path = Path.home() / "Pictures" / "Vidmahi-screenshot.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return f"Screenshot saved locally to {path}."

    if kind == "keyboard":
        key = action.get("key")
        shortcuts = {"ctrl+w": ("ctrl", "w"), "ctrl+t": ("ctrl", "t")}
        if key not in shortcuts:
            raise ValueError("That keyboard shortcut is not allowed.")
        pyautogui.hotkey(*shortcuts[key])
        return "Keyboard shortcut completed."

    if kind == "refresh":
        pyautogui.press("f5")
        return "Refreshed the active page."

    if kind in {"open_chrome", "open_code"}:
        if platform.system() == "Windows":
            app_key = "chrome" if kind == "open_chrome" else "code"
            executable = WINDOWS_APPS[app_key]
            if not executable.is_file():
                raise FileNotFoundError(f"Application not found: {executable}")
            subprocess.Popen([str(executable)])
        elif kind == "open_code":
            subprocess.Popen(["code"])
        else:
            webbrowser.open("https://www.google.com/chrome/")
        return "Application opened."

    if kind in {"close_chrome", "close_code"}:
        process_names = {
            "close_chrome": {"chrome.exe", "chrome"},
            "close_code": {"code.exe", "code"},
        }[kind]
        for process in psutil.process_iter(["name"]):
            try:
                if (process.info["name"] or "").lower() in process_names:
                    process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return "Requested application closed."

    if kind in {"lock", "sleep", "shutdown", "restart"}:
        if platform.system() != "Windows":
            raise RuntimeError("This system action is currently supported on Windows only.")
        commands = {
            "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            "shutdown": ["shutdown", "/s", "/t", "1"],
            "restart": ["shutdown", "/r", "/t", "1"],
        }
        subprocess.Popen(commands[kind], shell=False)
        return f"{kind.capitalize()} requested."

    raise ValueError("This action is not supported.")


def main() -> int:
    if not AGENT_TOKEN:
        print("Set VIDMAHI_AGENT_TOKEN in your .env file before starting the laptop agent.")
        return 2
    print(f"Vidmahi laptop agent connected to {SERVER_URL}. Press Ctrl+C to stop.")
    while True:
        try:
            payload = request_json("/api/agent/next")
            action = payload.get("action")
            if not action:
                time.sleep(2)
                continue
            action_id = action.pop("id")
            try:
                result = execute_action(action)
                status = "completed"
                print(result)
            except Exception as error:
                result = str(error)
                status = "failed"
                print(f"Action failed: {error}", file=sys.stderr)
            request_json(
                f"/api/agent/actions/{action_id}/result",
                method="POST",
                data={"status": status, "result": result},
            )
        except urllib.error.HTTPError as error:
            print(f"Server returned HTTP {error.code}; retrying in 5 seconds.", file=sys.stderr)
            time.sleep(5)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"Connection error: {error}; retrying in 5 seconds.", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
