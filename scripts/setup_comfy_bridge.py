"""Install the local blog bridge, register its token and start it at Windows login."""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/repository")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", type=Path, default=Path(__file__).with_name("comfy_workflow.json"))
    parser.add_argument("--no-startup", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This installer configures Windows login; run comfy_bridge.py manually on other systems")
    gh, cloudflared = shutil.which("gh"), shutil.which("cloudflared")
    if not gh or not cloudflared:
        parser.error("GitHub CLI and cloudflared must be installed and gh authenticated")
    install = Path(os.environ["LOCALAPPDATA"]) / "HornsbyBlog" / "ComfyBridge"
    install.mkdir(parents=True, exist_ok=True)
    account = subprocess.check_output(["whoami"], text=True).strip()
    # The private token and tunnel address never go in the repository or public assets.
    subprocess.run(["icacls", str(install), "/inheritance:r", "/grant:r",
                    f"{account}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"], check=True, capture_output=True)
    config_path = install / "config.json"
    previous = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = {
        "token": previous.get("token") or secrets.token_urlsafe(48),
        "comfy_url": args.comfy_url, "repo": args.repo, "port": 8796,
        "workflow": str(install / "workflow.json"),
        "state_file": str(install / "connection.json"), "gh": gh, "cloudflared": cloudflared,
    }
    shutil.copyfile(Path(__file__).with_name("comfy_bridge.py"), install / "comfy_bridge.py")
    shutil.copyfile(args.workflow, install / "workflow.json")
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    # Validate config and authentication before registering a persistent process.
    from comfy_bridge import ComfyBridge, set_github_secret
    ComfyBridge(config)
    set_github_secret(gh, args.repo, "COMFYUI_BRIDGE_TOKEN", config["token"])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        parser.error("pythonw.exe is required to start the bridge without opening a window")
    command = [str(pythonw), str(install / "comfy_bridge.py"), "--config", str(config_path), "--tunnel"]
    if not args.no_startup:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, "HornsbyBlogComfyBridge", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
    # A second installation updates files/config but does not launch a duplicate bridge.
    import requests
    try:
        response = requests.get("http://127.0.0.1:8796/health",
                                headers={"Authorization": "Bearer " + config["token"]}, timeout=3)
        running = response.status_code in {200, 409, 503} and response.headers.get("Content-Type") == "application/json"
    except requests.RequestException:
        running = False
    if not running:
        subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("ComfyUI blog bridge installed. It starts at Windows login and renews its GitHub connection automatically.")
    print("Local ComfyUI offline/busy/errors/timeouts will use the existing OpenAI fallback.")
    print(f"Configuration and private logs: {install}")


if __name__ == "__main__":
    main()
