#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from PIL import Image, ImageChops
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageChops = None


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent


def bundled_adb_path() -> Path | None:
    if sys.platform.startswith("darwin"):
        candidate = SKILL_ROOT / "assets" / "platform-tools" / "darwin" / "platform-tools" / "adb"
    elif sys.platform.startswith("linux"):
        candidate = SKILL_ROOT / "assets" / "platform-tools" / "linux" / "platform-tools" / "adb"
    elif sys.platform.startswith("win"):
        candidate = SKILL_ROOT / "assets" / "platform-tools" / "windows" / "platform-tools" / "adb.exe"
    else:
        return None
    if candidate.exists():
        if not sys.platform.startswith("win"):
            candidate.chmod(candidate.stat().st_mode | 0o111)
        return candidate
    return None


def resolve_adb_binary() -> tuple[str, str]:
    path_adb = shutil.which("adb")
    if path_adb:
        return path_adb, "path"
    bundled = bundled_adb_path()
    if bundled:
        return str(bundled), "bundled"
    raise RuntimeError(
        "adb was not found on PATH and no bundled platform-tools adb binary was found for this OS."
    )


ADB, ADB_SOURCE = resolve_adb_binary()


class AdbOpsError(RuntimeError):
    pass


@dataclass
class LoggedCommand:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    def shell(self) -> str:
        return " ".join(shlex_quote(part) for part in self.argv)


@dataclass
class ActionResult:
    group: str
    action: str
    serial: str | None
    out_dir: Path
    command_name: str = field(init=False)
    artifact_dir: Path = field(init=False)
    commands: list[LoggedCommand] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ok: bool = True

    def __post_init__(self) -> None:
        self.command_name = f"{self.group}-{self.action}"
        self.artifact_dir = self.out_dir / "artifacts" / self.command_name
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def record(self, completed: subprocess.CompletedProcess[Any], text_stdout: str, text_stderr: str) -> None:
        self.commands.append(
            LoggedCommand(
                argv=[str(part) for part in completed.args],
                returncode=completed.returncode,
                stdout=text_stdout,
                stderr=text_stderr,
            )
        )

    def save_text_file(self, name: str, content: str) -> str:
        path = self.artifact_dir / name
        path.write_text(content, encoding="utf-8")
        self.artifacts[name] = str(path)
        return str(path)

    def save_bytes_file(self, name: str, content: bytes) -> str:
        path = self.artifact_dir / name
        path.write_bytes(content)
        self.artifacts[name] = str(path)
        return str(path)

    def finalize(self) -> dict[str, Any]:
        stdout_log = []
        stderr_log = []
        for item in self.commands:
            stdout_log.append(f"$ {item.shell()}\n{item.stdout}".rstrip() + "\n")
            stderr_log.append(f"$ {item.shell()}\n{item.stderr}".rstrip() + "\n")
        self.save_text_file("stdout.txt", "\n".join(stdout_log).rstrip() + ("\n" if stdout_log else ""))
        self.save_text_file("stderr.txt", "\n".join(stderr_log).rstrip() + ("\n" if stderr_log else ""))
        result = {
            "ok": self.ok,
            "serial": self.serial,
            "command": [item.shell() for item in self.commands],
            "artifacts": self.artifacts,
            "summary": self.summary,
            "metrics": self.metrics,
            "notes": self.notes,
        }
        self.save_text_file("result.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return result


def shlex_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def read_text(proc: subprocess.CompletedProcess[Any]) -> tuple[str, str]:
    stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else str(proc.stdout or "")
    stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
    return stdout, stderr


def run_command(
    argv: list[str],
    *,
    check: bool = True,
    timeout: float | None = None,
    text: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[Any]:
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=text,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        stdout, stderr = read_text(completed)
        raise AdbOpsError(
            f"Command failed ({completed.returncode}): {' '.join(argv)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    return completed


def adb_command(serial: str | None, *parts: str) -> list[str]:
    cmd = [ADB]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(parts)
    return cmd


def record_run(result: ActionResult, argv: list[str], *, check: bool = True, timeout: float | None = None) -> tuple[str, str]:
    completed = run_command(argv, check=check, timeout=timeout, text=True)
    stdout, stderr = read_text(completed)
    result.record(completed, stdout, stderr)
    return stdout, stderr


def record_run_bytes(result: ActionResult, argv: list[str], *, check: bool = True, timeout: float | None = None) -> tuple[bytes, bytes]:
    completed = run_command(argv, check=check, timeout=timeout, text=False)
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else (completed.stdout or "").encode()
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else (completed.stderr or "").encode()
    result.record(completed, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"))
    return stdout, stderr


def list_online_devices(result: ActionResult | None = None) -> list[dict[str, str]]:
    proc = run_command([ADB, "devices", "-l"], check=True, text=True)
    if result:
        stdout, stderr = read_text(proc)
        result.record(proc, stdout, stderr)
    devices = []
    for line in proc.stdout.splitlines():
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        extra = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                extra[key] = value
        devices.append({"serial": serial, "state": state, **extra})
    return devices


def resolve_serial(requested: str | None, required: bool) -> str | None:
    if requested and requested != "auto":
        return requested
    if not required and requested in (None, "auto"):
        return None
    devices = [item for item in list_online_devices() if item.get("state") == "device"]
    if requested == "auto":
        if len(devices) == 1:
            return devices[0]["serial"]
        if not devices:
            raise AdbOpsError("No online devices detected for --serial auto.")
        raise AdbOpsError("More than one online device detected; pass --serial explicitly.")
    if required:
        if len(devices) == 1:
            return devices[0]["serial"]
        if not devices:
            raise AdbOpsError("No online devices detected.")
        raise AdbOpsError("More than one online device detected; pass --serial explicitly.")
    return None


def parse_device_state(value: str) -> str:
    lower = value.lower()
    if re.search(r"^\s*wifi is enabled\b", lower, re.MULTILINE):
        return "on"
    if re.search(r"^\s*wifi is disabled\b", lower, re.MULTILINE):
        return "off"
    if re.search(r"^\s*enabled:\s*true\b", lower, re.MULTILINE):
        return "on"
    if re.search(r"^\s*enabled:\s*false\b", lower, re.MULTILINE):
        return "off"
    if "wait-for-state:state_on" in lower or re.search(r"^\s*state:\s*on\b", lower, re.MULTILINE):
        return "on"
    if "wait-for-state:state_off" in lower or re.search(r"^\s*state:\s*off\b", lower, re.MULTILINE):
        return "off"
    return "unknown"


def get_prop(serial: str, key: str, result: ActionResult | None = None) -> str:
    argv = adb_command(serial, "shell", "getprop", key)
    if result:
        stdout, _ = record_run(result, argv)
    else:
        stdout = run_command(argv, check=True, text=True).stdout
    return stdout.strip()


def shell_out(serial: str, command: str, result: ActionResult | None = None, *, check: bool = True) -> str:
    argv = adb_command(serial, "shell", command)
    if result:
        stdout, _ = record_run(result, argv, check=check)
    else:
        stdout = run_command(argv, check=check, text=True).stdout
    return stdout


def shell_out_su(serial: str, script: str, result: ActionResult | None = None, *, check: bool = True) -> str:
    argv = adb_command(serial, "shell", f"su 0 sh -c {shlex_quote(script)}")
    if result:
        stdout, _ = record_run(result, argv, check=check)
    else:
        stdout = run_command(argv, check=check, text=True).stdout
    return stdout


def maybe_shell(serial: str, command: str) -> str:
    proc = run_command(adb_command(serial, "shell", command), check=False, text=True)
    return proc.stdout


def parse_simple_kv_blob(blob: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    pattern = re.compile(r"([A-Za-z0-9_.-]+):(.+?)(?=\s+[A-Za-z0-9_.-]+:|$)")
    for match in pattern.finditer(blob.strip()):
        pairs[match.group(1)] = match.group(2).strip()
    return pairs


def bytes_to_human(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size_bytes}B"


def get_foreground_activity(serial: str, result: ActionResult | None = None) -> dict[str, str]:
    output = shell_out(
        serial,
        "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp' || true",
        result,
        check=False,
    ) if result else maybe_shell(serial, "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp' || true")
    if not output.strip():
        fallback = shell_out(
            serial,
            "dumpsys activity top | grep -m 1 'ACTIVITY ' || true",
            result,
            check=False,
        ) if result else maybe_shell(serial, "dumpsys activity top | grep -m 1 'ACTIVITY ' || true")
        output = fallback
    activity = ""
    package = ""
    match = re.search(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.$/]+)", output)
    if match:
        package = match.group(1)
        activity = f"{match.group(1)}/{match.group(2)}"
    return {"package": package, "activity": activity, "raw": output.strip()}


def wait_for_boot(serial: str, result: ActionResult, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    record_run(result, adb_command(serial, "wait-for-device"), timeout=timeout)
    while time.time() < deadline:
        boot_completed = get_prop(serial, "sys.boot_completed", result).strip()
        if boot_completed == "1":
            return
        time.sleep(2)
    raise AdbOpsError(f"Timed out waiting for sys.boot_completed=1 on {serial}.")


def dumpsys_bluetooth(serial: str, result: ActionResult | None = None) -> str:
    argv = adb_command(serial, "shell", "dumpsys", "bluetooth_manager")
    if result:
        stdout, _ = record_run(result, argv)
    else:
        stdout = run_command(argv, check=True, text=True).stdout
    return stdout


def perform_root(serial: str, result: ActionResult) -> str:
    stdout, _ = record_run(result, adb_command(serial, "root"), check=False)
    record_run(result, adb_command(serial, "wait-for-device"))
    return stdout.strip()


def run_remount_strategy(serial: str, result: ActionResult) -> dict[str, Any]:
    strategies: list[tuple[str, list[str] | str]] = [
        ("direct-remount", adb_command(serial, "remount")),
        ("root-then-remount", adb_command(serial, "root")),
        ("su-mount", "mount -o rw,remount /system || mount -o rw,remount /"),
    ]
    chosen = None
    failures: list[dict[str, Any]] = []
    for name, command in strategies:
        try:
            if isinstance(command, list):
                record_run(result, command, check=False)
                if name == "root-then-remount":
                    record_run(result, adb_command(serial, "wait-for-device"))
                    stdout, _ = record_run(result, adb_command(serial, "remount"), check=False)
                else:
                    stdout = result.commands[-1].stdout
            else:
                stdout = shell_out_su(serial, command, result, check=False)
            normalized = stdout.lower()
            if "remount succeeded" in normalized or "rw" in normalized or "already running as root" in normalized:
                chosen = name
                break
            if name == "su-mount":
                chosen = name
                break
            failures.append({"strategy": name, "stdout": stdout.strip()})
        except Exception as exc:  # pragma: no cover - device-dependent failures
            failures.append({"strategy": name, "error": str(exc)})
    if not chosen:
        raise AdbOpsError("All remount strategies failed.")
    return {"strategy": chosen, "failures": failures, "strategy_count": len(strategies)}


def resolve_build_prop_path(serial: str, result: ActionResult, requested: str | None = None) -> str:
    if requested:
        exists = shell_out(serial, f"if [ -f {shlex_quote(requested)} ]; then echo FOUND; fi", result, check=False).strip()
        if exists == "FOUND":
            return requested
        raise AdbOpsError(f"Requested build.prop path does not exist: {requested}")
    candidates = ["/system/build.prop", "/system/system/build.prop", "/product/build.prop", "/vendor/build.prop"]
    for path in candidates:
        exists = shell_out(serial, f"if [ -f {shlex_quote(path)} ]; then echo FOUND; fi", result, check=False).strip()
        if exists == "FOUND":
            return path
    raise AdbOpsError("Unable to locate a readable build.prop path on the device.")


def parse_build_prop(content: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        props[key.strip()] = value.strip()
    return props


def capture_ui(serial: str, result: ActionResult) -> dict[str, str]:
    screenshot_bytes, _ = record_run_bytes(result, adb_command(serial, "exec-out", "screencap", "-p"))
    screenshot_path = result.save_bytes_file("device.png", screenshot_bytes)
    remote_xml = "/sdcard/adb_android_ops_layout.xml"
    dump_attempts = [
        adb_command(serial, "shell", "uiautomator", "dump", remote_xml),
        adb_command(serial, "shell", "uiautomator", "dump", "--compressed", remote_xml),
    ]
    last_error: Exception | None = None
    for attempt in dump_attempts:
        try:
            record_run(result, attempt)
            last_error = None
            break
        except Exception as exc:  # pragma: no cover - transient device failures
            last_error = exc
            time.sleep(1)
    if last_error:
        raise last_error
    record_run(result, adb_command(serial, "pull", remote_xml, str(result.artifact_dir / "layout.xml")))
    result.artifacts["layout.xml"] = str(result.artifact_dir / "layout.xml")
    window_summary = shell_out(serial, "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp|Window #' || true", result)
    result.save_text_file("window-summary.txt", window_summary)
    return {
        "device.png": screenshot_path,
        "layout.xml": result.artifacts["layout.xml"],
        "window-summary.txt": result.artifacts["window-summary.txt"],
    }


def ensure_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - dependency error path
        raise AdbOpsError(
            "HTML or URL design comparison requires the playwright Python package. "
            "Install it with `python3 -m pip install --user playwright` and "
            "`python3 -m playwright install chromium`."
        ) from exc
    return sync_playwright


def ensure_pillow() -> None:
    if Image is None or ImageChops is None:
        raise AdbOpsError(
            "UI image processing requires Pillow. Install it with `python3 -m pip install --user Pillow`."
        )


def design_source_kind(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return "html"
    suffix = Path(value).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "image"
    if suffix in {".html", ".htm"}:
        return "html"
    raise AdbOpsError(f"Unsupported design input: {value}")


def render_design_to_png(design: str, output_path: Path, width: int, height: int) -> None:
    ensure_pillow()
    kind = design_source_kind(design)
    if kind == "image":
        with Image.open(design) as image:
            image.convert("RGBA").resize((width, height)).save(output_path)
        return
    sync_playwright = ensure_playwright()
    target = design
    if Path(design).exists():
        target = Path(design).resolve().as_uri()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.goto(target, wait_until="networkidle")
        page.screenshot(path=str(output_path), full_page=False)
        browser.close()


def compute_image_metrics(design_path: Path, device_path: Path, diff_path: Path) -> dict[str, Any]:
    ensure_pillow()
    with Image.open(design_path) as design_img, Image.open(device_path) as device_img:
        design = design_img.convert("RGBA")
        device = device_img.convert("RGBA")
        if design.size != device.size:
            design = design.resize(device.size)
            design.save(design_path)
        diff = ImageChops.difference(design, device)
        gray = diff.convert("L")
        threshold = 24
        mask = gray.point(lambda v: 255 if v > threshold else 0)
        changed_pixels = sum(mask.histogram()[1:])
        total_pixels = mask.size[0] * mask.size[1]
        pixel_diff_percent = round((changed_pixels / total_pixels) * 100, 4) if total_pixels else 0.0

        overlay = Image.new("RGBA", device.size, (255, 64, 64, 140))
        transparent = Image.new("RGBA", device.size, (0, 0, 0, 0))
        highlighted = Image.alpha_composite(device, Image.composite(overlay, transparent, mask))
        highlighted.save(diff_path)

        sample_design = design.convert("L").resize((256, 256))
        sample_device = device.convert("L").resize((256, 256))
        a = list(sample_design.tobytes())
        b = list(sample_device.tobytes())
        if not a:
            ssim = 1.0
        else:
            mean_a = sum(a) / len(a)
            mean_b = sum(b) / len(b)
            var_a = sum((value - mean_a) ** 2 for value in a) / len(a)
            var_b = sum((value - mean_b) ** 2 for value in b) / len(b)
            cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / len(a)
            c1 = (0.01 * 255) ** 2
            c2 = (0.03 * 255) ** 2
            ssim = ((2 * mean_a * mean_b + c1) * (2 * cov + c2)) / (
                (mean_a**2 + mean_b**2 + c1) * (var_a + var_b + c2)
            )
        return {
            "device_width": device.size[0],
            "device_height": device.size[1],
            "pixel_diff_percent": pixel_diff_percent,
            "ssim": round(float(ssim), 6),
        }


def get_wifi_status(serial: str, result: ActionResult | None = None) -> str:
    output = shell_out(serial, "cmd wifi status", result) if result else maybe_shell(serial, "cmd wifi status")
    return parse_device_state(output)


def get_bluetooth_status(serial: str, result: ActionResult | None = None) -> str:
    output = dumpsys_bluetooth(serial, result) if result else dumpsys_bluetooth(serial)
    return parse_device_state(output)


def action_device_list(args: argparse.Namespace, result: ActionResult) -> None:
    devices = list_online_devices(result)
    result.summary = {"device_count": len(devices), "devices": devices}
    result.metrics = {"device_count": len(devices)}
    result.notes.append("Use --serial explicitly when more than one device is online.")


def action_device_info(args: argparse.Namespace, result: ActionResult) -> None:
    serial = result.serial
    if not serial:
        raise AdbOpsError("device info requires a resolved serial.")
    state, _ = record_run(result, adb_command(serial, "get-state"))
    battery = shell_out(serial, "dumpsys battery | head -n 40", result)
    props = {
        "model": get_prop(serial, "ro.product.model", result),
        "device": get_prop(serial, "ro.product.device", result),
        "release": get_prop(serial, "ro.build.version.release", result),
        "sdk": get_prop(serial, "ro.build.version.sdk", result),
        "debuggable": get_prop(serial, "ro.debuggable", result),
        "secure": get_prop(serial, "ro.secure", result),
    }
    result.summary = {"state": state.strip(), "properties": props}
    result.metrics = props
    result.save_text_file("battery.txt", battery)


def action_device_wait(args: argparse.Namespace, result: ActionResult) -> None:
    timeout = args.timeout
    record_run(result, adb_command(result.serial, "wait-for-device"), timeout=timeout)
    result.summary = {"state": "device"}
    result.metrics = {"timeout_seconds": timeout}


def action_device_core_info(args: argparse.Namespace, result: ActionResult) -> None:
    serial = result.serial
    batch_keys = [
        "ro.product.brand",
        "ro.product.manufacturer",
        "ro.product.model",
        "ro.product.device",
        "ro.product.board",
        "ro.hardware",
        "ro.product.cpu.abilist",
        "ro.build.fingerprint",
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.build.version.security_patch",
        "ro.build.type",
        "ro.build.tags",
        "ro.boot.verifiedbootstate",
        "ro.debuggable",
        "ro.secure",
        "sys.boot_completed",
    ]
    props = {key: get_prop(serial, key, result) for key in batch_keys}
    meminfo = shell_out(serial, "cat /proc/meminfo | head -n 10", result)
    cpuinfo = shell_out(serial, "cat /proc/cpuinfo | head -n 40", result)
    display_size = shell_out(serial, "wm size", result).strip()
    display_density = shell_out(serial, "wm density", result).strip()
    uptime = shell_out(serial, "uptime", result).strip()
    foreground = get_foreground_activity(serial, result)
    result.save_text_file("meminfo.txt", meminfo)
    result.save_text_file("cpuinfo.txt", cpuinfo)
    result.summary = {
        "identity": {
            "brand": props["ro.product.brand"],
            "manufacturer": props["ro.product.manufacturer"],
            "model": props["ro.product.model"],
            "device": props["ro.product.device"],
            "board": props["ro.product.board"],
            "hardware": props["ro.hardware"],
        },
        "android": {
            "release": props["ro.build.version.release"],
            "sdk": props["ro.build.version.sdk"],
            "security_patch": props["ro.build.version.security_patch"],
            "fingerprint": props["ro.build.fingerprint"],
            "build_type": props["ro.build.type"],
            "build_tags": props["ro.build.tags"],
        },
        "runtime": {
            "abi_list": props["ro.product.cpu.abilist"],
            "verified_boot_state": props["ro.boot.verifiedbootstate"],
            "debuggable": props["ro.debuggable"],
            "secure": props["ro.secure"],
            "boot_completed": props["sys.boot_completed"],
            "uptime": uptime,
            "display_size": display_size,
            "display_density": display_density,
            "foreground": foreground,
        },
    }
    mem_total = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
    result.metrics = {
        "mem_total_kb": int(mem_total.group(1)) if mem_total else 0,
        "cpuinfo_lines": len(cpuinfo.splitlines()),
    }


def action_device_storage(args: argparse.Namespace, result: ActionResult) -> None:
    partitions_raw = shell_out(result.serial, "cat /proc/partitions", result)
    df_raw = shell_out(result.serial, "df -h", result)
    mount_raw = shell_out(result.serial, "mount", result)
    by_name_raw = shell_out(result.serial, "ls -l /dev/block/by-name || true", result, check=False)
    result.save_text_file("proc-partitions.txt", partitions_raw)
    result.save_text_file("df.txt", df_raw)
    result.save_text_file("mount.txt", mount_raw)
    result.save_text_file("by-name.txt", by_name_raw)

    partitions: list[dict[str, Any]] = []
    for line in partitions_raw.splitlines()[2:]:
        parts = line.split()
        if len(parts) != 4:
            continue
        blocks = int(parts[2])
        partitions.append(
            {
                "name": parts[3],
                "blocks_kb": blocks,
                "size_human": bytes_to_human(blocks * 1024),
            }
        )
    partitions.sort(key=lambda item: item["blocks_kb"], reverse=True)

    filesystems: list[dict[str, str]] = []
    for line in df_raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        filesystems.append(
            {
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "use_percent": parts[4],
                "mounted_on": parts[5],
            }
        )
    result.summary = {
        "top_partitions": partitions[:15],
        "filesystems": filesystems[:20],
    }
    data_fs = next((fs for fs in filesystems if fs["mounted_on"] == "/data"), None)
    result.metrics = {
        "partition_count": len(partitions),
        "filesystem_count": len(filesystems),
        "data_use_percent": data_fs["use_percent"] if data_fs else "",
    }


def action_props_get(args: argparse.Namespace, result: ActionResult) -> None:
    value = get_prop(result.serial, args.key, result)
    result.summary = {"key": args.key, "value": value}
    result.metrics = {"value_length": len(value)}


def action_props_grep(args: argparse.Namespace, result: ActionResult) -> None:
    output = shell_out(result.serial, "getprop", result)
    lines = [line for line in output.splitlines() if args.pattern in line]
    result.save_text_file("matched-props.txt", "\n".join(lines) + ("\n" if lines else ""))
    result.summary = {"pattern": args.pattern, "matches": len(lines)}
    result.metrics = {"matches": len(lines)}


def action_pm_list(args: argparse.Namespace, result: ActionResult) -> None:
    output = shell_out(result.serial, "pm list packages", result)
    packages = [line.removeprefix("package:") for line in output.splitlines() if line.startswith("package:")]
    if args.pattern:
        packages = [item for item in packages if args.pattern in item]
    result.save_text_file("packages.txt", "\n".join(packages) + ("\n" if packages else ""))
    result.summary = {"pattern": args.pattern, "packages": packages[:50], "package_count": len(packages)}
    result.metrics = {"package_count": len(packages)}


def action_pm_path(args: argparse.Namespace, result: ActionResult) -> None:
    output = shell_out(result.serial, f"pm path {shlex_quote(args.package)}", result)
    paths = [line.removeprefix("package:") for line in output.splitlines() if line.startswith("package:")]
    result.summary = {"package": args.package, "paths": paths}
    result.metrics = {"path_count": len(paths)}


def action_pm_resolve(args: argparse.Namespace, result: ActionResult) -> None:
    output = shell_out(result.serial, f"cmd package resolve-activity --brief {shlex_quote(args.package)}", result)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    result.summary = {"package": args.package, "resolved": lines[-1] if lines else ""}
    result.metrics = {"line_count": len(lines)}


def action_pm_install(args: argparse.Namespace, result: ActionResult) -> None:
    apk = Path(args.apk_path).expanduser().resolve()
    if not apk.exists():
        raise AdbOpsError(f"APK does not exist: {apk}")
    before = shell_out(result.serial, f"pm path {shlex_quote(args.package)}", result, check=False) if args.package else ""
    install_cmd = adb_command(result.serial, "install")
    if args.replace:
        install_cmd.append("-r")
    if args.grant_all:
        install_cmd.append("-g")
    install_cmd.append(str(apk))
    stdout, _ = record_run(result, install_cmd)
    after = shell_out(result.serial, f"pm path {shlex_quote(args.package)}", result, check=False) if args.package else ""
    result.summary = {"apk": str(apk), "replace": args.replace, "package_hint": args.package, "stdout": stdout.strip()}
    result.metrics = {"before_present": bool(before.strip()), "after_present": bool(after.strip())}


def action_pm_uninstall(args: argparse.Namespace, result: ActionResult) -> None:
    before = shell_out(result.serial, f"pm path {shlex_quote(args.package)}", result, check=False)
    cmd = adb_command(result.serial, "uninstall")
    if args.keep_data:
        cmd.append("-k")
    cmd.append(args.package)
    stdout, _ = record_run(result, cmd)
    after = shell_out(result.serial, f"pm path {shlex_quote(args.package)}", result, check=False)
    result.summary = {"package": args.package, "stdout": stdout.strip()}
    result.metrics = {"before_present": bool(before.strip()), "after_present": bool(after.strip())}


def action_pm_clear(args: argparse.Namespace, result: ActionResult) -> None:
    stdout, _ = record_run(result, adb_command(result.serial, "shell", "pm", "clear", args.package))
    result.summary = {"package": args.package, "stdout": stdout.strip()}


def action_pm_grant(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "pm", "grant", args.package, args.permission))
    result.summary = {"package": args.package, "permission": args.permission}


def action_pm_revoke(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "pm", "revoke", args.package, args.permission))
    result.summary = {"package": args.package, "permission": args.permission}


def action_app_state(args: argparse.Namespace, result: ActionResult) -> None:
    pid = shell_out(result.serial, f"pidof {shlex_quote(args.package)}", result, check=False).strip()
    proc_state = shell_out(result.serial, f"dumpsys activity processes | grep -A 6 {shlex_quote(args.package)} || true", result)
    result.save_text_file("process-state.txt", proc_state)
    result.summary = {"package": args.package, "running": bool(pid), "pid": pid}
    result.metrics = {"running": int(bool(pid))}


def action_app_process(args: argparse.Namespace, result: ActionResult) -> None:
    pid = shell_out(result.serial, f"pidof {shlex_quote(args.package)}", result, check=False).strip()
    ps = shell_out(result.serial, f"ps -A | grep {shlex_quote(args.package)} || true", result)
    result.save_text_file("process-list.txt", ps)
    result.summary = {"package": args.package, "pid": pid}
    result.metrics = {"running": int(bool(pid))}


def resolve_activity(serial: str, package: str, result: ActionResult | None = None) -> str:
    output = shell_out(serial, f"cmd package resolve-activity --brief {shlex_quote(package)}", result)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise AdbOpsError(f"Unable to resolve launch activity for {package}")
    return lines[-1]


def action_app_start(args: argparse.Namespace, result: ActionResult) -> None:
    activity = args.activity or resolve_activity(result.serial, args.package, result)
    stdout, _ = record_run(result, adb_command(result.serial, "shell", "am", "start", "-n", activity))
    result.summary = {"package": args.package, "activity": activity, "stdout": stdout.strip()}


def parse_start_time(output: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in ["TotalTime", "WaitTime", "ThisTime"]:
        match = re.search(rf"{key}:\s*(\d+)", output)
        if match:
            metrics[key.lower()] = int(match.group(1))
    state_match = re.search(r"LaunchState:\s*(\w+)", output)
    if state_match:
        metrics["launch_state"] = state_match.group(1)
    return metrics


def action_app_start_time(args: argparse.Namespace, result: ActionResult) -> None:
    activity = args.activity or resolve_activity(result.serial, args.package, result)
    if not args.warm:
        record_run(result, adb_command(result.serial, "shell", "am", "force-stop", args.package))
    stdout, _ = record_run(result, adb_command(result.serial, "shell", "am", "start", "-W", "-n", activity))
    metrics = parse_start_time(stdout)
    result.summary = {"package": args.package, "activity": activity, "timing": metrics}
    result.metrics = metrics


def action_app_stop(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "am", "force-stop", args.package))
    result.summary = {"package": args.package, "stopped": True}


def action_app_resources(args: argparse.Namespace, result: ActionResult) -> None:
    pid = shell_out(result.serial, f"pidof {shlex_quote(args.package)}", result, check=False).strip()
    captures = {
        "meminfo.txt": f"dumpsys meminfo {shlex_quote(args.package)}",
        "cpuinfo.txt": "dumpsys cpuinfo",
        "top.txt": "top -b -n 1 | head -n 80",
        "gfxinfo.txt": f"dumpsys gfxinfo {shlex_quote(args.package)}",
        "netstats.txt": "dumpsys netstats",
    }
    for name, command in captures.items():
        result.save_text_file(name, shell_out(result.serial, command, result, check=False))
    if pid:
        su_exists = bool(maybe_shell(result.serial, "which su").strip())
        if su_exists:
            proc_snapshot = shell_out_su(
                result.serial,
                f"cat /proc/{pid}/status /proc/{pid}/stat /proc/{pid}/limits",
                result,
                check=False,
            )
            result.save_text_file("proc-status.txt", proc_snapshot)
            result.metrics["root_proc_snapshot"] = True
    meminfo = (result.artifact_dir / "meminfo.txt").read_text(encoding="utf-8")
    pss_match = re.search(r"TOTAL PSS:\s*([\d,]+)", meminfo)
    result.summary = {"package": args.package, "pid": pid}
    if pss_match:
        result.metrics["total_pss_kb"] = int(pss_match.group(1).replace(",", ""))


def action_app_video_codec(args: argparse.Namespace, result: ActionResult) -> None:
    metrics_dump = shell_out(result.serial, "dumpsys media.metrics", result, check=False)
    player_dump = shell_out(result.serial, "dumpsys media.player", result, check=False)
    session_dump = shell_out(result.serial, "dumpsys media_session", result, check=False)
    foreground = get_foreground_activity(result.serial, result)
    result.save_text_file("media-metrics.txt", metrics_dump)
    result.save_text_file("media-player.txt", player_dump)
    result.save_text_file("media-session.txt", session_dump)

    codec_entries: list[dict[str, Any]] = []
    for line in metrics_dump.splitlines():
        if "mediametrics_codec_reported" not in line or "mime:video/" not in line:
            continue
        payload = parse_simple_kv_blob(line[line.find("{") + 1 : line.rfind("}")])
        package_name = payload.get("package_name", "")
        if args.package and args.package not in package_name:
            continue
        codec_entries.append(
            {
                "timestamp_nanos": payload.get("timestamp_nanos", ""),
                "package_name": package_name,
                "app_uid": payload.get("app_uid", ""),
                "codec": payload.get("codec", ""),
                "mime": payload.get("mime", ""),
                "encoder": payload.get("encoder", ""),
                "width": payload.get("width", ""),
                "height": payload.get("height", ""),
                "frame_rate": payload.get("frame_rate", ""),
                "bitrate": payload.get("bitrate", ""),
                "lifetime_millis": payload.get("lifetime_millis", ""),
            }
        )
    codec_entries.reverse()
    limited_entries = codec_entries[: args.limit]
    active_packages_match = re.search(r"Audio playback .*?packages=([^\n]+)", session_dump, re.DOTALL)
    active_packages = active_packages_match.group(1).split() if active_packages_match else []
    result.summary = {
        "foreground": foreground,
        "filtered_package": args.package or "",
        "active_packages": active_packages,
        "video_codec_entries": limited_entries,
    }
    result.metrics = {
        "entry_count": len(codec_entries),
        "returned_count": len(limited_entries),
        "has_video_activity": bool(codec_entries),
    }


def action_ui_capture(args: argparse.Namespace, result: ActionResult) -> None:
    artifacts = capture_ui(result.serial, result)
    result.summary = {"captured": True}
    result.metrics = {"artifact_count": len(artifacts)}


def action_ui_compare(args: argparse.Namespace, result: ActionResult) -> None:
    ensure_pillow()
    capture_ui(result.serial, result)
    device_path = result.artifact_dir / "device.png"
    design_path = result.artifact_dir / "design.png"
    diff_path = result.artifact_dir / "diff.png"
    with Image.open(device_path) as device_image:
        width, height = device_image.size
    render_design_to_png(args.design, design_path, width, height)
    result.artifacts["design.png"] = str(design_path)
    metrics = compute_image_metrics(design_path, device_path, diff_path)
    result.artifacts["diff.png"] = str(diff_path)
    summary = {
        "design": args.design,
        "layout_xml": result.artifacts["layout.xml"],
        "metrics": metrics,
    }
    result.save_text_file("summary.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    result.summary = summary
    result.metrics = metrics


def filter_log_lines(lines: list[str], patterns: list[str]) -> list[str]:
    if not patterns:
        return lines
    regexes = [re.compile(pattern) for pattern in patterns]
    return [line for line in lines if any(regex.search(line) for regex in regexes)]


def fetch_logcat(result: ActionResult, patterns: list[str], lines: int, clear: bool) -> list[str]:
    if clear:
        record_run(result, adb_command(result.serial, "logcat", "-c"))
    stdout, _ = record_run(result, adb_command(result.serial, "logcat", "-d", "-v", "threadtime", "-t", str(lines)))
    raw_lines = stdout.splitlines()
    return filter_log_lines(raw_lines, patterns)


def action_log_capture(args: argparse.Namespace, result: ActionResult) -> None:
    lines = fetch_logcat(result, args.pattern or [], args.lines, args.clear)
    result.save_text_file("logcat.txt", "\n".join(lines) + ("\n" if lines else ""))
    result.summary = {"lines": len(lines), "patterns": args.pattern or []}
    result.metrics = {"line_count": len(lines)}


def action_log_grep(args: argparse.Namespace, result: ActionResult) -> None:
    patterns = [args.main_pattern] + (args.pattern or [])
    lines = fetch_logcat(result, patterns, args.lines, False)
    result.save_text_file("filtered-logcat.txt", "\n".join(lines) + ("\n" if lines else ""))
    result.summary = {"lines": len(lines), "patterns": patterns}
    result.metrics = {"line_count": len(lines)}


def action_log_analyze(args: argparse.Namespace, result: ActionResult) -> None:
    patterns = args.pattern or []
    lines = fetch_logcat(result, patterns, args.lines, False)
    severity_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    for line in lines:
        match = re.match(r"^\d\d-\d\d\s+\d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+([VDIWEF])\s+([^:]+):", line)
        if match:
            severity = match.group(1)
            tag = match.group(2).strip()
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = dict(sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:10])
    analysis = {"severity_counts": severity_counts, "top_tags": top_tags, "line_count": len(lines)}
    result.save_text_file("log-analysis.json", json.dumps(analysis, indent=2, ensure_ascii=False) + "\n")
    result.summary = analysis
    result.metrics = analysis


def action_input_tap(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "input", "tap", str(args.x), str(args.y)))
    result.summary = {"x": args.x, "y": args.y}


def action_input_swipe(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(
        result,
        adb_command(
            result.serial,
            "shell",
            "input",
            "swipe",
            str(args.x1),
            str(args.y1),
            str(args.x2),
            str(args.y2),
            str(args.duration),
        ),
    )
    result.summary = {"from": [args.x1, args.y1], "to": [args.x2, args.y2], "duration_ms": args.duration}


def action_input_text(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "input", "text", args.value))
    result.summary = {"text": args.value}


def action_input_keyevent(args: argparse.Namespace, result: ActionResult) -> None:
    record_run(result, adb_command(result.serial, "shell", "input", "keyevent", args.keycode))
    result.summary = {"keycode": args.keycode}


def set_wifi(serial: str, state: str, result: ActionResult) -> str:
    before = get_wifi_status(serial, result)
    if state == "status":
        after = before
    else:
        target = "enabled" if state == "on" else "disabled"
        shell_out(serial, f"cmd wifi set-wifi-enabled {target}", result)
        time.sleep(2)
        after = get_wifi_status(serial, result)
    result.metrics = {"before": before, "after": after}
    return after


def action_radio_wifi(args: argparse.Namespace, result: ActionResult) -> None:
    after = set_wifi(result.serial, args.state, result)
    result.summary = {"state": after}


def wait_for_bluetooth(serial: str, expected: str, result: ActionResult, timeout: int = 40) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = get_bluetooth_status(serial, result)
        if state == expected:
            return state
        time.sleep(2)
    return get_bluetooth_status(serial, result)


def action_radio_bluetooth(args: argparse.Namespace, result: ActionResult) -> None:
    before = get_bluetooth_status(result.serial, result)
    after = before
    if args.state != "status":
        command = "enable" if args.state == "on" else "disable"
        shell_out(result.serial, f"cmd bluetooth_manager {command}", result)
        after = wait_for_bluetooth(result.serial, args.state, result)
    result.summary = {"state": after}
    result.metrics = {"before": before, "after": after}


def action_system_root(args: argparse.Namespace, result: ActionResult) -> None:
    stdout = perform_root(result.serial, result)
    result.summary = {"stdout": stdout}


def action_system_unroot(args: argparse.Namespace, result: ActionResult) -> None:
    stdout, _ = record_run(result, adb_command(result.serial, "unroot"), check=False)
    record_run(result, adb_command(result.serial, "wait-for-device"))
    result.summary = {"stdout": stdout.strip()}


def action_system_remount(args: argparse.Namespace, result: ActionResult) -> None:
    remount = run_remount_strategy(result.serial, result)
    result.summary = {"strategy": remount["strategy"], "failures": remount["failures"]}
    result.metrics = {"strategy_count": remount["strategy_count"], "chosen": remount["strategy"]}


def action_system_build_prop_get(args: argparse.Namespace, result: ActionResult) -> None:
    build_prop_path = resolve_build_prop_path(result.serial, result, args.path)
    local_copy = result.artifact_dir / "build.prop"
    record_run(result, adb_command(result.serial, "pull", build_prop_path, str(local_copy)))
    result.artifacts["build.prop"] = str(local_copy)
    content = local_copy.read_text(encoding="utf-8", errors="replace")
    props = parse_build_prop(content)
    value = props.get(args.key, "")
    result.summary = {"path": build_prop_path, "key": args.key, "value": value}
    result.metrics = {"present": int(args.key in props), "line_count": len(content.splitlines())}


def action_system_build_prop_set(args: argparse.Namespace, result: ActionResult) -> None:
    build_prop_path = resolve_build_prop_path(result.serial, result, args.path)
    root_stdout = perform_root(result.serial, result)
    remount = run_remount_strategy(result.serial, result)
    local_copy = result.artifact_dir / "build.prop.before"
    record_run(result, adb_command(result.serial, "pull", build_prop_path, str(local_copy)))
    original = local_copy.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    updates: dict[str, str] = {}
    for assignment in args.assignment:
        if "=" not in assignment:
            raise AdbOpsError(f"Invalid build.prop assignment: {assignment}")
        key, value = assignment.split("=", 1)
        updates[key.strip()] = value.strip()

    pending = dict(updates)
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            normalized = key.strip()
            if normalized in pending:
                updated_lines.append(f"{normalized}={pending.pop(normalized)}")
                continue
        updated_lines.append(line)
    for key, value in pending.items():
        updated_lines.append(f"{key}={value}")
    edited_content = "\n".join(updated_lines) + "\n"
    edited_local = result.artifact_dir / "build.prop.after"
    edited_local.write_text(edited_content, encoding="utf-8")
    result.artifacts["build.prop.before"] = str(local_copy)
    result.artifacts["build.prop.after"] = str(edited_local)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{build_prop_path}.adb-android-ops.{timestamp}.bak"
    temp_remote = f"/data/local/tmp/adb-android-ops-build.prop-{timestamp}"
    shell_out_su(
        result.serial,
        f"cp {shlex_quote(build_prop_path)} {shlex_quote(backup_path)}",
        result,
    )
    record_run(result, adb_command(result.serial, "push", str(edited_local), temp_remote))
    shell_out_su(
        result.serial,
        " && ".join(
            [
                f"cp {shlex_quote(temp_remote)} {shlex_quote(build_prop_path)}",
                f"chmod 0644 {shlex_quote(build_prop_path)}",
                f"chown 0:0 {shlex_quote(build_prop_path)}",
                f"command -v restorecon >/dev/null 2>&1 && restorecon {shlex_quote(build_prop_path)} || true",
                "sync",
                f"rm -f {shlex_quote(temp_remote)}",
            ]
        ),
        result,
    )
    verify_local = result.artifact_dir / "build.prop.verify"
    record_run(result, adb_command(result.serial, "pull", build_prop_path, str(verify_local)))
    result.artifacts["build.prop.verify"] = str(verify_local)
    verified_props = parse_build_prop(verify_local.read_text(encoding="utf-8", errors="replace"))
    applied = {key: verified_props.get(key, "") for key in updates}
    mismatches = {key: {"expected": updates[key], "actual": applied[key]} for key in updates if updates[key] != applied[key]}
    if mismatches:
        raise AdbOpsError(f"build.prop verification failed: {json.dumps(mismatches, ensure_ascii=False)}")
    if args.reboot:
        reboot_ns = argparse.Namespace(mode="system")
        action_system_reboot(reboot_ns, result)
    result.summary = {
        "path": build_prop_path,
        "backup_path": backup_path,
        "root_stdout": root_stdout,
        "remount_strategy": remount["strategy"],
        "applied": applied,
    }
    result.metrics = {
        "assignment_count": len(updates),
        "verified_count": len(applied),
    }


def action_system_reboot(args: argparse.Namespace, result: ActionResult) -> None:
    before = {
        "boot_completed": get_prop(result.serial, "sys.boot_completed", result),
        "wifi": get_wifi_status(result.serial, result),
        "bluetooth": get_bluetooth_status(result.serial, result),
    }
    reboot_cmd = adb_command(result.serial, "reboot")
    if args.mode != "system":
        reboot_cmd.append(args.mode)
    record_run(result, reboot_cmd)
    if args.mode == "system":
        wait_for_boot(result.serial, result)
    after = {"boot_completed": get_prop(result.serial, "sys.boot_completed", result) if args.mode == "system" else "n/a"}
    result.summary = {"mode": args.mode, "before": before, "after": after}
    result.metrics = {"boot_completed_after": after["boot_completed"]}


def action_system_boot_status(args: argparse.Namespace, result: ActionResult) -> None:
    status = {
        "sys.boot_completed": get_prop(result.serial, "sys.boot_completed", result),
        "dev.bootcomplete": get_prop(result.serial, "dev.bootcomplete", result),
        "bootanim": get_prop(result.serial, "init.svc.bootanim", result),
        "uptime": shell_out(result.serial, "uptime", result).strip(),
    }
    result.summary = status
    result.metrics = status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ADB Android Ops toolbox")
    parser.add_argument("--serial", default="auto", help="Device serial or auto")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--out-dir", default=None, help="Artifact output directory")

    subparsers = parser.add_subparsers(dest="group", required=True)

    device = subparsers.add_parser("device")
    device_sub = device.add_subparsers(dest="action", required=True)
    device_list = device_sub.add_parser("list")
    device_list.set_defaults(handler=action_device_list)
    info = device_sub.add_parser("info")
    info.set_defaults(handler=action_device_info)
    wait = device_sub.add_parser("wait")
    wait.add_argument("--timeout", type=int, default=60)
    wait.set_defaults(handler=action_device_wait)
    core_info = device_sub.add_parser("core-info")
    core_info.set_defaults(handler=action_device_core_info)
    storage = device_sub.add_parser("storage")
    storage.set_defaults(handler=action_device_storage)
    props = subparsers.add_parser("props")
    props_sub = props.add_subparsers(dest="action", required=True)
    props_get = props_sub.add_parser("get")
    props_get.add_argument("key")
    props_get.set_defaults(handler=action_props_get)
    props_grep = props_sub.add_parser("grep")
    props_grep.add_argument("pattern")
    props_grep.set_defaults(handler=action_props_grep)

    pm = subparsers.add_parser("pm")
    pm_sub = pm.add_subparsers(dest="action", required=True)
    pm_list = pm_sub.add_parser("list")
    pm_list.add_argument("pattern", nargs="?")
    pm_list.set_defaults(handler=action_pm_list)
    pm_path = pm_sub.add_parser("path")
    pm_path.add_argument("package")
    pm_path.set_defaults(handler=action_pm_path)
    pm_resolve = pm_sub.add_parser("resolve")
    pm_resolve.add_argument("package")
    pm_resolve.set_defaults(handler=action_pm_resolve)
    pm_install = pm_sub.add_parser("install")
    pm_install.add_argument("apk_path")
    pm_install.add_argument("--package", help="Optional package hint for before/after checks")
    pm_install.add_argument("--replace", action="store_true")
    pm_install.add_argument("--grant-all", action="store_true")
    pm_install.set_defaults(handler=action_pm_install)
    pm_uninstall = pm_sub.add_parser("uninstall")
    pm_uninstall.add_argument("package")
    pm_uninstall.add_argument("--keep-data", action="store_true")
    pm_uninstall.set_defaults(handler=action_pm_uninstall)
    pm_clear = pm_sub.add_parser("clear")
    pm_clear.add_argument("package")
    pm_clear.set_defaults(handler=action_pm_clear)
    pm_grant = pm_sub.add_parser("grant")
    pm_grant.add_argument("package")
    pm_grant.add_argument("permission")
    pm_grant.set_defaults(handler=action_pm_grant)
    pm_revoke = pm_sub.add_parser("revoke")
    pm_revoke.add_argument("package")
    pm_revoke.add_argument("permission")
    pm_revoke.set_defaults(handler=action_pm_revoke)

    app = subparsers.add_parser("app")
    app_sub = app.add_subparsers(dest="action", required=True)
    for name, handler in [("state", action_app_state), ("process", action_app_process), ("stop", action_app_stop), ("resources", action_app_resources)]:
        sub = app_sub.add_parser(name)
        sub.add_argument("package")
        sub.set_defaults(handler=handler)
    app_start = app_sub.add_parser("start")
    app_start.add_argument("package")
    app_start.add_argument("--activity")
    app_start.set_defaults(handler=action_app_start)
    app_start_time = app_sub.add_parser("start-time")
    app_start_time.add_argument("package")
    app_start_time.add_argument("--activity")
    app_start_time.add_argument("--warm", action="store_true")
    app_start_time.set_defaults(handler=action_app_start_time)
    app_video_codec = app_sub.add_parser("video-codec")
    app_video_codec.add_argument("--package")
    app_video_codec.add_argument("--limit", type=int, default=10)
    app_video_codec.set_defaults(handler=action_app_video_codec)

    ui = subparsers.add_parser("ui")
    ui_sub = ui.add_subparsers(dest="action", required=True)
    ui_sub.add_parser("capture").set_defaults(handler=action_ui_capture)
    ui_compare = ui_sub.add_parser("compare")
    ui_compare.add_argument("--design", required=True)
    ui_compare.set_defaults(handler=action_ui_compare)

    log = subparsers.add_parser("log")
    log_sub = log.add_subparsers(dest="action", required=True)
    log_capture = log_sub.add_parser("capture")
    log_capture.add_argument("--pattern", action="append")
    log_capture.add_argument("--lines", type=int, default=2000)
    log_capture.add_argument("--clear", action="store_true")
    log_capture.set_defaults(handler=action_log_capture)
    log_grep = log_sub.add_parser("grep")
    log_grep.add_argument("main_pattern")
    log_grep.add_argument("--pattern", action="append")
    log_grep.add_argument("--lines", type=int, default=2000)
    log_grep.set_defaults(handler=action_log_grep)
    log_analyze = log_sub.add_parser("analyze")
    log_analyze.add_argument("--pattern", action="append")
    log_analyze.add_argument("--lines", type=int, default=2000)
    log_analyze.set_defaults(handler=action_log_analyze)

    input_parser = subparsers.add_parser("input")
    input_sub = input_parser.add_subparsers(dest="action", required=True)
    tap = input_sub.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.set_defaults(handler=action_input_tap)
    swipe = input_sub.add_parser("swipe")
    swipe.add_argument("x1", type=int)
    swipe.add_argument("y1", type=int)
    swipe.add_argument("x2", type=int)
    swipe.add_argument("y2", type=int)
    swipe.add_argument("--duration", type=int, default=300)
    swipe.set_defaults(handler=action_input_swipe)
    text = input_sub.add_parser("text")
    text.add_argument("value")
    text.set_defaults(handler=action_input_text)
    keyevent = input_sub.add_parser("keyevent")
    keyevent.add_argument("keycode")
    keyevent.set_defaults(handler=action_input_keyevent)

    radio = subparsers.add_parser("radio")
    radio_sub = radio.add_subparsers(dest="action", required=True)
    wifi = radio_sub.add_parser("wifi")
    wifi.add_argument("state", choices=["status", "on", "off"])
    wifi.set_defaults(handler=action_radio_wifi)
    bt = radio_sub.add_parser("bluetooth")
    bt.add_argument("state", choices=["status", "on", "off"])
    bt.set_defaults(handler=action_radio_bluetooth)

    system = subparsers.add_parser("system")
    system_sub = system.add_subparsers(dest="action", required=True)
    system_sub.add_parser("root").set_defaults(handler=action_system_root)
    system_sub.add_parser("unroot").set_defaults(handler=action_system_unroot)
    system_sub.add_parser("remount").set_defaults(handler=action_system_remount)
    build_prop_get = system_sub.add_parser("build-prop-get")
    build_prop_get.add_argument("key")
    build_prop_get.add_argument("--path")
    build_prop_get.set_defaults(handler=action_system_build_prop_get)
    build_prop_set = system_sub.add_parser("build-prop-set")
    build_prop_set.add_argument("assignment", nargs="+")
    build_prop_set.add_argument("--path")
    build_prop_set.add_argument("--reboot", action="store_true")
    build_prop_set.set_defaults(handler=action_system_build_prop_set)
    reboot = system_sub.add_parser("reboot")
    reboot.add_argument("--mode", choices=["system", "bootloader", "recovery"], default="system")
    reboot.set_defaults(handler=action_system_reboot)
    system_sub.add_parser("boot-status").set_defaults(handler=action_system_boot_status)
    return parser


def choose_out_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (Path.cwd() / "output" / "adb-android-ops" / timestamp).resolve()


def requires_serial(group: str, action: str) -> bool:
    return not (group == "device" and action == "list")


def format_text(result: dict[str, Any], artifact_dir: Path) -> str:
    lines = [
        f"ok: {result['ok']}",
        f"serial: {result['serial']}",
        f"artifacts: {artifact_dir}",
        f"summary: {json.dumps(result['summary'], ensure_ascii=False)}",
        f"metrics: {json.dumps(result['metrics'], ensure_ascii=False)}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    out_dir = choose_out_dir(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    serial = resolve_serial(args.serial, requires_serial(args.group, args.action))
    result = ActionResult(group=args.group, action=args.action, serial=serial, out_dir=out_dir)
    result.notes.append(f"adb_source={ADB_SOURCE}")
    result.notes.append(f"adb_binary={ADB}")
    try:
        handler = args.handler
        handler(args, result)
        payload = result.finalize()
        if args.format == "json":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(format_text(payload, result.artifact_dir))
        return 0
    except Exception as exc:
        result.ok = False
        result.summary = {"error": str(exc)}
        payload = result.finalize()
        if args.format == "json":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(format_text(payload, result.artifact_dir), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
