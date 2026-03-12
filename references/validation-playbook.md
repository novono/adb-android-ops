# Validation Playbook

## Local Checks
1. Validate the skill structure.

```bash
python3 /Users/William/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/William/.codex/skills/adb-android-ops
```

2. Confirm the CLI loads.

```bash
python3 scripts/adb_ops.py --help
python3 scripts/adb_ops.py device list --help
python3 scripts/adb_ops.py ui compare --help
```

## Device Validation Sequence
Target serial used during creation: `0123456789ABCDEF`

1. Device discovery
```bash
python3 scripts/adb_ops.py --serial auto device list
python3 scripts/adb_ops.py --serial auto device info
```

2. Property reads
```bash
python3 scripts/adb_ops.py --serial auto props get ro.build.version.release
python3 scripts/adb_ops.py --serial auto props get ro.build.version.sdk
python3 scripts/adb_ops.py --serial auto props get ro.debuggable
```

3. Package inspection
```bash
python3 scripts/adb_ops.py --serial auto pm list fithub
python3 scripts/adb_ops.py --serial auto pm resolve com.fithub.launcher
python3 scripts/adb_ops.py --serial auto pm path com.fithub.launcher
```

4. APK reinstall
```bash
./gradlew :apps:app-launcher:assembleDefaultDebug
python3 scripts/adb_ops.py --serial auto pm install /Users/William/Codes/FitHub/apps/app-launcher/build/outputs/apk/sign8390Default/debug/app-launcher-sign8390-default-debug.apk --replace
```

5. App diagnostics
```bash
python3 scripts/adb_ops.py --serial auto app state com.fithub.launcher
python3 scripts/adb_ops.py --serial auto app process com.fithub.launcher
python3 scripts/adb_ops.py --serial auto app start-time com.fithub.launcher
python3 scripts/adb_ops.py --serial auto app resources com.fithub.launcher
```

6. UI evidence
```bash
python3 scripts/adb_ops.py --serial auto ui capture
python3 scripts/adb_ops.py --serial auto ui compare --design assets/validation/reference.html
```

7. Logs
```bash
python3 scripts/adb_ops.py --serial auto log capture --pattern com.fithub.launcher --pattern AndroidRuntime --pattern ActivityManager
python3 scripts/adb_ops.py --serial auto log grep com.fithub.launcher --pattern AndroidRuntime --pattern ActivityManager
python3 scripts/adb_ops.py --serial auto log analyze --pattern com.fithub.launcher --pattern AndroidRuntime --pattern ActivityManager
```

8. Input
```bash
python3 scripts/adb_ops.py --serial auto input keyevent HOME
python3 scripts/adb_ops.py --serial auto input keyevent BACK
python3 scripts/adb_ops.py --serial auto input tap 640 360
```

9. Radio control
```bash
python3 scripts/adb_ops.py --serial auto radio wifi off
python3 scripts/adb_ops.py --serial auto radio wifi on
python3 scripts/adb_ops.py --serial auto radio bluetooth off
python3 scripts/adb_ops.py --serial auto radio bluetooth on
```

10. System operations
```bash
python3 scripts/adb_ops.py --serial auto system remount
python3 scripts/adb_ops.py --serial auto system reboot --mode system
python3 scripts/adb_ops.py --serial auto system boot-status
```

11. Post-reboot regression
```bash
python3 scripts/adb_ops.py --serial auto device info
python3 scripts/adb_ops.py --serial auto app start-time com.fithub.launcher
```

## Artifact Expectations
- Every command writes:
  - `artifacts/<command-name>/stdout.txt`
  - `artifacts/<command-name>/stderr.txt`
  - `artifacts/<command-name>/result.json`
- `ui capture` also writes screenshot, layout XML, and window summary.
- `ui compare` also writes `design.png`, `device.png`, `diff.png`, and `summary.json`.
