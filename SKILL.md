---
name: adb-android-ops
description: Use when the user asks to operate an Android device over adb for device discovery, serial selection, package or process management, system property inspection, install or uninstall flows, app launch timing, memory or CPU or network analysis, logcat capture and filtering, UI layout dumping, screenshot or design comparison, simulated touch input, Wi-Fi or Bluetooth control, remount, root, reboot, build.prop modification, partition or storage inspection, video codec analysis, or batch-formatted device core info on a physical device.
---

# ADB Android Ops

## Overview
Operate Android devices through a single bundled CLI instead of ad hoc adb commands. Prefer the tool when the user needs repeatable diagnostics, artifact capture, structured JSON results, invasive device operations without pausing for confirmation, or a fallback to bundled platform-tools when `adb` is missing from `PATH`.

## Quick Start
1. Confirm the target device is online. The CLI uses `adb` from `PATH` when available and falls back to the bundled platform-tools copy when it is not.
2. Run `python3 scripts/adb_ops.py --serial auto device list` to discover devices.
3. Use the same CLI for all operations:

```bash
python3 scripts/adb_ops.py [--serial SERIAL|auto] [--format text|json] [--out-dir DIR] <group> <action> [options]
```

4. Inspect artifacts under `output/adb-android-ops/<timestamp>/artifacts/<command-name>/`.

## Workflow
1. Resolve the device first.
   - Use `--serial auto` only when exactly one device is online.
   - If more than one device is attached, rerun with an explicit serial.
2. Prefer structured runs when artifacts matter.
   - Use `--format json` for automation, pipelines, or follow-up analysis.
   - Use text output for quick operator feedback.
3. Capture evidence before and after risky commands.
   - The tool does not ask for confirmation before `root`, `remount`, `wifi`, `bluetooth`, or `reboot`.
   - It always records executed commands, pre-state, post-state, and stderr.
4. For app diagnostics, target a package explicitly.
   - Launch timing uses `am start -W`.
   - Resource analysis captures memory, CPU, graphics, and network state.
5. For UI comparison, provide one of:
   - PNG or JPG design export
   - Local HTML file
   - HTTP or HTTPS URL
   - Figma export image or screenshot

## Command Groups
- `device`: list devices, inspect a device, or wait for availability.
- `device`: also includes `core-info` for batch-formatted key facts and `storage` for partitions plus space usage.
- `props`: read one property or grep the full property set.
- `pm`: list packages, resolve launch activities, inspect package paths, install, uninstall, clear data, grant, or revoke permissions.
- `app`: inspect running state, process info, launch, stop, measure startup, collect resource snapshots, or summarize recent video codec activity.
- `ui`: capture screenshot plus layout XML, or compare the current device UI to a design input.
- `log`: dump logcat, filter it, or summarize matching lines by severity and tag.
- `input`: send tap, swipe, text, and key events.
- `radio`: toggle or inspect Wi-Fi and Bluetooth.
- `system`: switch adbd root mode, try remount strategies, read or update `build.prop`, reboot, or inspect boot completion.

## Dependencies
- Required: `adb` on `PATH`
- Fallback: bundled official platform-tools under `assets/platform-tools/`
- Required for `ui compare`: `Pillow`
- Required for HTML rendering in `ui compare`: `playwright` Python package plus a Chromium browser install

Install HTML comparison dependencies when needed:

```bash
python3 -m pip install --user playwright
python3 -m playwright install chromium
```

## Rules
- Prefer the bundled CLI over writing one-off adb shell commands.
- If `adb` is missing from `PATH`, let the skill use the bundled platform-tools copy automatically.
- Keep `--out-dir` stable when collecting a full validation bundle.
- Treat Figma input as an exported image, not a live API integration.
- If Playwright is missing and the design input is HTML or a URL, stop with a clear dependency error. Do not silently fall back.
- Use `references/command-matrix.md` for the action map and required arguments.
- Use `references/validation-playbook.md` for the end-to-end validation sequence.

## Examples
```bash
python3 scripts/adb_ops.py --serial auto device info
python3 scripts/adb_ops.py --serial auto props get ro.build.version.release
python3 scripts/adb_ops.py --serial auto pm resolve com.fithub.launcher
python3 scripts/adb_ops.py --serial auto app start-time com.fithub.launcher
python3 scripts/adb_ops.py --serial auto ui compare --design assets/validation/reference.html
python3 scripts/adb_ops.py --serial auto log analyze --pattern com.fithub.launcher --pattern AndroidRuntime
python3 scripts/adb_ops.py --serial auto system remount
```
