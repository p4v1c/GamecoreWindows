"""Every call into the operating system, one module per concern.

Services call these names instead of `os.symlink`, `/proc` or a Win32 API, so
the Windows edition replaces a Linux mechanism in one place. Nothing outside
this package checks `os.name`, imports `ctypes.windll`, `winreg` or `win32*`
(`gamecore-review` greps for it).

| Module          | Concern                                   | Replaces (Linux)              |
|-----------------|-------------------------------------------|-------------------------------|
| `filesystem.py` | directory links                           | `os.symlink`, `os.readlink`   |
| `processes.py`  | boot identity, process start time         | `/proc` reads                 |
| `media_types.py`| content types of what is served           | `/etc/mime.types`             |
| `audio.py`      | audio outputs, volume                     | `wpctl`                       |
| `com.py`        | COM on worker threads, WMI queries        | —                             |
| `network.py`    | adapters, addresses, wired or Wi-Fi       | `nmcli dev show`              |
| `wifi.py`       | Wi-Fi scan, join, leave, saved profiles   | `nmcli dev wifi`              |
| `radios.py`     | Wi-Fi and Bluetooth radio switches        | `nmcli radio`, rfkill         |
| `bluetooth.py`  | paired devices, scan, pair, unpair        | `bluetoothctl`                |
| `display.py`    | display modes, monitor active             | `xrandr`, `kscreen-doctor`    |
| `power.py`      | screen power, idle timers, power plan     | `xset`, PowerDevil, `cpupower`|
| `volumes.py`    | external disks, safe removal              | `lsblk`, `udisksctl`          |
| `gamepads.py`   | pads through SDL2, their events, battery  | evdev, `/dev/input`, sysfs    |
| `usb.py`        | what is on the USB bus                    | `/sys/bus/usb/devices`        |

Later port steps add a module per concern (`docs/dev-log/windows-port.md`).
"""
