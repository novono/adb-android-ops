# Command Matrix

## Global CLI

```bash
python3 scripts/adb_ops.py [--serial SERIAL|auto] [--format text|json] [--out-dir DIR] <group> <action> [options]
```

- `--serial auto`: resolve the only online device; fail if zero or more than one device is attached
- `--format text|json`: choose operator output mode
- `--out-dir DIR`: default is `output/adb-android-ops/<timestamp>/`

## device
- `device list`
- `device info`
- `device wait [--timeout SECONDS]`

## props
- `props get <key>`
- `props grep <pattern>`

## pm
- `pm list [pattern]`
- `pm path <package>`
- `pm resolve <package>`
- `pm install <apk_path> [--replace] [--grant-all]`
- `pm uninstall <package> [--keep-data]`
- `pm clear <package>`
- `pm grant <package> <permission>`
- `pm revoke <package> <permission>`

## app
- `app state <package>`
- `app process <package>`
- `app start <package> [--activity ACTIVITY]`
- `app start-time <package> [--activity ACTIVITY] [--warm]`
- `app stop <package>`
- `app resources <package>`

## ui
- `ui capture`
- `ui compare --design <path-or-url>`

Notes:
- `--design` accepts local PNG or JPG, local HTML, HTTP or HTTPS URL, or a Figma-exported image.
- HTML and URL inputs require Playwright.

## log
- `log capture [--pattern VALUE]... [--lines N] [--clear]`
- `log grep <pattern> [--pattern VALUE]... [--lines N]`
- `log analyze [--pattern VALUE]... [--lines N]`

## input
- `input tap <x> <y>`
- `input swipe <x1> <y1> <x2> <y2> [--duration MS]`
- `input text <value>`
- `input keyevent <keycode>`

## radio
- `radio wifi <status|on|off>`
- `radio bluetooth <status|on|off>`

## system
- `system root`
- `system unroot`
- `system remount`
- `system reboot [--mode system|bootloader|recovery]`
- `system boot-status`
