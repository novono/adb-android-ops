# adb-android-ops

`adb-android-ops` is a Codex skill for operating Android devices over `adb` through one structured CLI instead of ad hoc shell commands.

Current version: `0.0.1`

## What It Covers

- Device discovery and serial selection
- System property inspection
- Package management with install, uninstall, clear, grant, and revoke flows
- App state, process checks, and cold-start timing
- Memory, CPU, graphics, and network diagnostics
- Partition layout and filesystem usage snapshots
- Batch-formatted device core information
- Screenshot capture, layout dump, and UI comparison against image or HTML inputs
- Logcat capture, filtering, and lightweight analysis
- Recent video codec usage analysis from media metrics and player state
- Simulated touch, swipe, text, and key events
- Wi-Fi and Bluetooth control
- Root, remount, reboot, boot-status, and build.prop editing

## Repository Layout

- `SKILL.md`: Codex skill definition and usage workflow
- `agents/openai.yaml`: Codex UI metadata
- `scripts/adb_ops.py`: single-entry CLI for all supported operations
- `references/command-matrix.md`: command inventory and argument map
- `references/validation-playbook.md`: end-to-end validation steps
- `assets/validation/reference.html`: HTML fixture for UI diff validation
- `assets/platform-tools/`: bundled official platform-tools for macOS, Linux, and Windows fallback use

## Requirements

- Python 3
- `Pillow` for image-based UI comparison
- `playwright` plus a Chromium install for HTML or URL rendering in `ui compare`

The CLI prefers `adb` from `PATH`. If it is not available, it automatically falls back to the bundled platform-tools copy for the current OS.

Recommended local setup for HTML diff support:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install Pillow playwright PyYAML
python -m playwright install chromium
```

## Usage

```bash
python3 scripts/adb_ops.py [--serial SERIAL|auto] [--format text|json] [--out-dir DIR] <group> <action> [options]
```

Examples:

```bash
python3 scripts/adb_ops.py --serial auto device info
python3 scripts/adb_ops.py --serial auto props get ro.build.version.release
python3 scripts/adb_ops.py --serial auto device core-info
python3 scripts/adb_ops.py --serial auto device storage
python3 scripts/adb_ops.py --serial auto app start-time com.fithub.launcher
python3 scripts/adb_ops.py --serial auto app video-codec --limit 5
python3 scripts/adb_ops.py --serial auto ui compare --design assets/validation/reference.html
python3 scripts/adb_ops.py --serial auto system build-prop-get ro.system.build.fingerprint
python3 scripts/adb_ops.py --serial auto system build-prop-set persist.adb_android_ops.validation=1
python3 scripts/adb_ops.py --serial auto system remount
```

## Validation

Run the skill validator:

```bash
python3 /Users/William/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/William/.codex/skills/adb-android-ops
```

For a full device validation workflow, use `references/validation-playbook.md`.

## Release Asset

The packaged skill archive used for GitHub releases is generated in the FitHub workspace root as:

`/Users/William/Codes/FitHub/adb-android-ops-skill.zip`
