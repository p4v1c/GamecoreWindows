"""The installer picks a GPU driver that exists, for every GPU in the box.

`install/arch.sh` reads `lspci` and adds driver packages to the one
`pacman -S` that installs the system. Two ways it went wrong:

- Arch dropped the `nvidia` package for `nvidia-open`. The installer still
  asked for `nvidia`, pacman answered "target not found", and under `set -e`
  every NVIDIA install on Arch stopped at 14 %, after the system upgrade and
  the user account.
- An if/elif chain took the first vendor only, so a box with its Intel or AMD
  iGPU listed before an NVIDIA card got no driver for the card.

The GPU block is cut out of arch.sh and run in bash with a fake `lspci` and a
fake `pacman -Si` that knows a fixed list of package names.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_until_port(7)

ARCH = Path(__file__).resolve().parents[2] / "install" / "arch.sh"

LINES = {
    "amd": "03:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. "
           "[AMD/ATI] Rembrandt [Radeon 680M] [1002:1681]",
    "intel": "00:02.0 VGA compatible controller [0300]: Intel Corporation "
             "Alder Lake-P GT2 [Iris Xe Graphics] [8086:46a6]",
    "nvidia": "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation "
              "GA106 [GeForce RTX 3060] [10de:2504]",
    "vm": "00:01.0 VGA compatible controller [0300]: Red Hat, Inc. Virtio 1.0 GPU [1af4:1050]",
    "pascal": "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation "
              "GP106 [GeForce GTX 1060 6GB] [10de:1c03]",
    "kepler": "01:00.0 VGA compatible controller [0300]: NVIDIA Corporation "
              "GK104 [GeForce GTX 760] [10de:1187]",
}

# What the Arch repos hold today: no bare `nvidia`.
ARCH_REPO = ["nvidia-open", "nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils", "dkms"]
MANJARO_REPO = ["linux618-nvidia", "linux618-nvidia-open", "nvidia-open-dkms", "nvidia-utils",
                "lib32-nvidia-utils", "dkms", "linux618-nvidia-580xx", "nvidia-580xx-dkms",
                "nvidia-580xx-utils", "lib32-nvidia-580xx-utils"]


def _gpu_block() -> str:
    text = ARCH.read_text(encoding="utf-8")
    start = text.index("# GPU drivers — detect")
    end = text.index("# The whole GameCore stack is X11-only")
    return text[start:end]


def _packages(gpus: list[str], *, repo: list[str], kernel: str, manjaro: bool = False) -> list[str]:
    kshort = "".join(kernel.split(".")[:2]) if manjaro else ""
    script = f"""
set -euo pipefail
lspci() {{ printf '%s\\n' {' '.join(repr(LINES[g]) for g in gpus) or "''"}; }}
pacman() {{ [[ "$1" == "-Si" ]] && [[ " {' '.join(repo)} " == *" $2 "* ]]; }}
info() {{ :; }}; warn() {{ :; }}
HAS_MULTILIB=true
add_lib32() {{ $HAS_MULTILIB && PKGS+=("$@") || true; }}
IS_MANJARO={'true' if manjaro else 'false'}; KERNEL={kernel}; KSHORT={kshort}; KRT=""
PKGS=()
{_gpu_block()}
printf '%s\\n' "${{PKGS[@]}}"
"""
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_nvidia_on_arch_gets_a_package_the_repos_have():
    pkgs = _packages(["nvidia"], repo=ARCH_REPO, kernel="6.17.1-arch1-1")
    assert "nvidia" not in pkgs
    assert "nvidia-open" in pkgs and "nvidia-utils" in pkgs


@pytest.mark.parametrize("kernel", ["6.17.1-zen1-1-zen", "6.12.50-1-lts"])
def test_a_non_stock_kernel_gets_the_dkms_module(kernel):
    pkgs = _packages(["nvidia"], repo=ARCH_REPO, kernel=kernel)
    assert "nvidia-open-dkms" in pkgs and "dkms" in pkgs


def test_manjaro_keeps_its_kernel_module():
    pkgs = _packages(["nvidia"], repo=MANJARO_REPO, kernel="6.18.49-1-MANJARO", manjaro=True)
    assert "linux618-nvidia" in pkgs


@pytest.mark.parametrize("igpu, igpu_pkg", [("intel", "vulkan-intel"), ("amd", "vulkan-radeon")])
def test_a_hybrid_box_gets_both_drivers(igpu, igpu_pkg):
    pkgs = _packages([igpu, "nvidia"], repo=ARCH_REPO, kernel="6.17.1-arch1-1")
    assert igpu_pkg in pkgs and "nvidia-open" in pkgs


def test_a_vm_gets_software_vulkan_and_nothing_else():
    assert _packages(["vm"], repo=ARCH_REPO, kernel="6.17.1-arch1-1") == ["vulkan-swrast"]


def test_no_gpu_line_adds_nothing():
    assert _packages([], repo=ARCH_REPO, kernel="6.17.1-arch1-1") == []


def test_a_gtx_1000_never_gets_the_open_module():
    """nvidia-open does not drive Pascal: installed, it boots to a black screen."""
    arch = _packages(["pascal"], repo=ARCH_REPO, kernel="6.17.1-arch1-1")
    assert not any("nvidia" in p for p in arch)          # AUR only on Arch: warned, not installed
    manjaro = _packages(["pascal"], repo=MANJARO_REPO, kernel="6.18.49-1-MANJARO", manjaro=True)
    assert manjaro == ["linux618-nvidia-580xx", "nvidia-580xx-utils", "lib32-nvidia-580xx-utils"]


def test_a_card_older_than_maxwell_gets_no_nvidia_driver():
    for repo, manjaro in ((ARCH_REPO, False), (MANJARO_REPO, True)):
        assert _packages(["kepler"], repo=repo, kernel="6.18.49-1-MANJARO", manjaro=manjaro) == []


def _ucode(vendor: str) -> list[str]:
    text = ARCH.read_text(encoding="utf-8")
    block = text[text.index("# Microcode for the CPU this box has"):]
    block = block[:block.index("esac") + 4]
    script = ("pacman_optional() { echo \"$1\"; }\n"
              + block.replace("/proc/cpuinfo", "/dev/stdin"))
    out = subprocess.run(["bash", "-c", script], input=f"vendor_id\t: {vendor}\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_microcode_follows_the_cpu_vendor():
    assert _ucode("GenuineIntel") == ["intel-ucode"]
    assert _ucode("AuthenticAMD") == ["amd-ucode"]
    assert _ucode("VIA VIA VIA ") == []
