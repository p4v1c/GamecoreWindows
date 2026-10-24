#!/usr/bin/env bash
# ================================================================
#  GameCore — Installation Script
#  Manjaro / Arch Linux · AMD, Intel or NVIDIA GPU · Flatpak
#  Idempotent: safe to run multiple times
# ================================================================
set -euo pipefail

RED='\033[1;31m'; GRN='\033[1;32m'; YLW='\033[1;33m'
BLU='\033[1;34m'; RST='\033[0m'

msg()  { echo -e "\n${BLU}──────────────────────────────────────${RST}\n${GRN}  $*${RST}"; }
ok()   { echo -e "  ${GRN}✓${RST} $*"; }
warn() { echo -e "  ${YLW}⚠  $*${RST}"; }
die()  { echo -e "\n${RED}[ERROR]${RST} $*" >&2; exit 1; }
info() { echo -e "  ${RST}$*"; }

pacman_optional() {
  record_new_pkgs "$1"
  pacman -S --noconfirm --needed "$1" 2>/dev/null && ok "$1" || warn "$1 not in repos — skipping"
}

# Machine-readable progress for the graphical installer: "@GC-PROGRESS@ <pct> <label>".
# Only emitted when the GUI sets GAMECORE_PROGRESS=1 — a plain CLI install
# stays clean. Percentages are hand-assigned milestones (minimal mode jumps).
progress() {  # progress <pct> <label…>
  if [[ "${GAMECORE_PROGRESS:-0}" == "1" ]]; then
    echo "@GC-PROGRESS@ $1 ${*:2}"
  fi
}

# Bounded git clone: a connection that stalls mid-transfer otherwise hangs
# git (and the whole install) forever — abort under 1 KB/s for 30 s, hard
# cap at 5 min, and never leave a half-written checkout behind.
git_clone() {  # git_clone <url> <dir>
  timeout 300 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 \
    clone -q "$1" "$2" && return 0
  rm -rf "$2"
  return 1
}

git_sync() {  # git_sync <dir> <label> — bring an EXISTING satellite checkout up to date
  # Cloning only when the directory is absent left every satellite pinned to
  # whatever commit it was first installed at, for ever: re-running the
  # installer printed "already present" and moved on, and OTA never touches
  # /opt outside GAMECORE_PATH. A fix shipped upstream reached nothing.
  #
  # Fast-forward only, and never over local changes — an installer that
  # stashes or resets behind the owner's back is exactly how a hand-applied
  # fix disappears. It says what it did not do instead.
  local dir="$1" label="$2" head
  [[ -d "$dir/.git" ]] || { warn "$label: $dir is not a git checkout — left untouched."; return 0; }
  if [[ -n "$(sudo -u "$USER_NAME" git -C "$dir" status --porcelain 2>/dev/null)" ]]; then
    warn "$label: local changes in $dir — NOT updated."
    info "  Keep them, then update:  git -C $dir stash && git -C $dir pull --ff-only"
    return 0
  fi
  if sudo -u "$USER_NAME" timeout 120 git -C "$dir" pull -q --ff-only 2>/dev/null; then
    head="$(sudo -u "$USER_NAME" git -C "$dir" log --oneline -1 2>/dev/null | cut -c1-60)"
    ok "$label up to date (${head:-unknown})."
  else
    warn "$label: could not fast-forward $dir — left at its current commit."
  fi
}

# ── Install manifest ─────────────────────────────────────────────
# install/uninstall.sh reads these to know what THIS install actually
# changed, so it can put the machine back without guessing. Without them
# an uninstaller cannot tell "we installed caddy" from "caddy was already
# here", and the only safe answer is then to remove nothing.
MANIFEST_DIR="/var/lib/gamecore"
MANIFEST="${MANIFEST_DIR}/manifest.env"
PKG_MANIFEST="${MANIFEST_DIR}/pacman-installed"
FLATPAK_MANIFEST="${MANIFEST_DIR}/flatpak-installed"
OVERRIDE_MANIFEST="${MANIFEST_DIR}/flatpak-overrides"

manifest_set() {  # manifest_set <KEY> <value>
  mkdir -p "$MANIFEST_DIR"; touch "$MANIFEST"
  sed -i "/^$1=/d" "$MANIFEST"
  printf '%s=%q\n' "$1" "$2" >> "$MANIFEST"
}

# Record only what pacman/flatpak did NOT already have. Called before the
# install so "already present" never ends up on the removal list.
manifest_owned() {  # manifest_owned <KEY> <0|1> — "did THIS install create it"
  # Never downgraded. A re-run finds what the first run created already there
  # and would record 0, erasing the uninstaller's only proof that the user
  # account, the venv, linger or the input group were GameCore's to remove.
  [[ "$2" == 0 ]] && grep -qx "$1=1" "$MANIFEST" 2>/dev/null && return 0
  manifest_set "$1" "$2"
}
record_new_pkgs() {  # record_new_pkgs <pkg...>
  local p
  mkdir -p "$MANIFEST_DIR"; touch "$PKG_MANIFEST"
  for p in "$@"; do
    pacman -Qq "$p" >/dev/null 2>&1 && continue
    grep -qxF "$p" "$PKG_MANIFEST" 2>/dev/null || echo "$p" >> "$PKG_MANIFEST"
  done
}

# Apps we changed the sandbox permissions of — a superset of the ones we
# installed, and the list the uninstaller must reset.
record_flatpak_override() {  # record_flatpak_override <app-id>
  mkdir -p "$MANIFEST_DIR"; touch "$OVERRIDE_MANIFEST"
  grep -qxF "$1" "$OVERRIDE_MANIFEST" 2>/dev/null || echo "$1" >> "$OVERRIDE_MANIFEST"
}

record_new_flatpak() {  # record_new_flatpak <app-id>
  mkdir -p "$MANIFEST_DIR"; touch "$FLATPAK_MANIFEST"
  grep -qxF "$1" "$FLATPAK_MANIFEST" 2>/dev/null || echo "$1" >> "$FLATPAK_MANIFEST"
}

# Is a Flatpak app installed? `flatpak list | grep -q <id>` matches
# substrings (org.DolphinEmu.dolphin-emu also matches a hypothetical
# …dolphin-emu-beta) — compare the application column exactly instead.
flatpak_installed() {  # flatpak_installed <app-id>
  flatpak list --app --columns=application 2>/dev/null | grep -qxF "$1"
}

# ── Unattended conf validation ───────────────────────────────────
#
# Everything in the conf is interpolated into something that then executes:
# systemd unit files, a sudoers drop-in, a Caddyfile, `useradd`, and
# `gamecore-addon install <name>` with root behind it.
#
# This is NOT a security boundary. The conf is `source`d — it is already
# arbitrary shell, and whoever writes it could simply put a command in it. It is
# a guard against the file being WRONG, which is a far more common event now
# that three different things write it: the graphical wizard, the ISO's guided
# install, and whoever is scripting more than one box.
#
# The failures it stops all share a shape — they do not appear here, they appear
# twenty minutes later, somewhere that does not mention the conf:
#   · a USER_NAME with a space writes a sudoers file `visudo -cf` rejects, at 84 %;
#   · a GAMECORE_PATH with a space produces `ExecStart=/opt/Game Core/.venv/…`,
#     which systemd splits into a command and an argument — the unit fails at
#     the first boot, long after the installer said "Installation complete";
#   · a WEB_PORT of "8765 " or "http" reaches Caddy and the backend, and the LAN
#     interface simply never answers.
conf_bad() { die "$CONF: $*"; }

_conf_path() {  # _conf_path <name> <value>
  local n="$1" v="$2"
  [[ "$v" == /* ]]  || conf_bad "$n must be an absolute path (got '$v')."
  [[ "$v" != "/" ]] || conf_bad "$n must not be / — that is the whole filesystem."
  [[ "$v" != */ ]]  || conf_bad "$n must not end in a slash (got '$v')."
  # The set that survives being interpolated UNQUOTED into a systemd unit line.
  [[ "$v" =~ ^[A-Za-z0-9_/.@+-]+$ ]] \
    || conf_bad "$n '$v' contains a character a systemd unit cannot carry unquoted."
}

_conf_tokens() {  # _conf_tokens <name> <value> — "all", empty, or space-separated ids
  local n="$1" v="$2" t
  [[ "$v" == "all" || -z "$v" ]] && return 0
  for t in $v; do
    # Same shape the wizard enforces on addon names before it writes them
    # (ADDON_NAME_RE in gamecore_installer.py) — these ids reach
    # `gamecore-addon install "$addon"` and the catalogue query.
    [[ "$t" =~ ^[a-z0-9][a-z0-9._-]*$ ]] \
      || conf_bad "$n contains '$t', which is not a valid pack or addon id."
  done
}

validate_conf() {
  [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]*$ ]] \
    || conf_bad "USER_NAME '$USER_NAME' is not a valid Linux username."

  _conf_path GAMECORE_PATH "$GAMECORE_PATH"
  # GAMECORE_DATA is resolved further down (it defaults to GAMECORE_PATH), so
  # only check it when the conf actually set it.
  [[ -n "${GAMECORE_DATA:-}" ]] && _conf_path GAMECORE_DATA "$GAMECORE_DATA"

  if [[ ! "$WEB_PORT" =~ ^[0-9]+$ ]] || (( WEB_PORT < 1 || WEB_PORT > 65535 )); then
    conf_bad "WEB_PORT '$WEB_PORT' is not a port number (1-65535)."
  fi

  _conf_tokens EMULATORS "${EMULATORS:-}"
  _conf_tokens APPS      "${APPS:-}"
  _conf_tokens ADDONS    "${ADDONS:-}"

  # Unknown keys — a WARNING and never fatal, because a fleet script may
  # legitimately carry its own variables in the same file.
  #
  # The accepted list is read from install.conf.example rather than typed here.
  # That file is the documentation for this format; a key documented but not
  # accepted, or accepted but never documented, is precisely the drift this
  # catches. `EMULATOR=rpcs3` instead of `EMULATORS=` is silent otherwise — the
  # variable keeps its "all" default and the box installs thirteen emulators
  # nobody asked for.
  local example="$SCRIPT_DIR/install.conf.example"
  if [[ -f "$example" ]]; then
    local allowed set_keys unknown=()
    allowed=$(grep -oE '^#?[A-Za-z_][A-Za-z0-9_]*=' "$example" | tr -d '#=' | sort -u || true)
    set_keys=$(grep -oE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$CONF" | tr -d ' =' | sort -u || true)
    local k
    for k in $set_keys; do
      grep -qxF "$k" <<<"$allowed" || unknown+=("$k")
    done
    if [[ ${#unknown[@]} -gt 0 ]]; then
      warn "$CONF sets keys this installer does not know: ${unknown[*]}"
      warn "  A typo here is silent — the real setting keeps its default."
      warn "  The accepted keys are documented in install/install.conf.example."
    fi
  fi
}

[[ $EUID -eq 0 ]] || die "Run with sudo: sudo bash install/arch.sh [--full|--minimal]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Install mode ─────────────────────────────────────────────────
#   --full           : GameCore + all emulators/apps (Flatpak) + curated configs
#   --minimal        : GameCore only — no emulator, no application
#   --unattended <f> : zero prompt — read everything from conf file <f>
#                      (written by install/installer-gui/. See
#                      install.conf.example)
MODE="${1:-}"
UNATTENDED=false
CONF=""
case "$MODE" in
  --full)    MODE="full" ;;
  --minimal) MODE="minimal" ;;
  --unattended)
    UNATTENDED=true; MODE=""
    CONF="${2:-}"
    [[ -n "$CONF" && -f "$CONF" ]] || die "usage: arch.sh --unattended <conf-file>"
    ;;
  "")        ;;  # asked interactively after the summary
  *)         die "Unknown option '$MODE' (use --full, --minimal or --unattended <conf>)" ;;
esac

# ── Detect distro ────────────────────────────────────────────────
IS_MANJARO=false
grep -qi "manjaro" /etc/os-release 2>/dev/null && IS_MANJARO=true

# ── Banner ───────────────────────────────────────────────────────
echo -e "\n${BLU}╔══════════════════════════════════════╗${RST}"
echo -e "${BLU}║     GameCore — Installer          ║${RST}"
echo -e "${BLU}╚══════════════════════════════════════╝${RST}"

# ── Configuration — conf file (unattended) or prompts ────────────
# EMULATORS / APPS: "all", or space-separated pack ids. The ids are whatever
# catalog/ holds — `scripts/catalog-query.py ids --kind emulator` lists them.
# No copy of that list lives in this file: one did, and it went stale the day a
# pack was added.
# ADDONS: space-separated gamecore-addons names installed at the end.
EMULATORS="all"
APPS="all"
ADDONS="rom-manager"
TWITCH_CLIENT_ID=""; TWITCH_CLIENT_SECRET=""; TGDB_API_KEY=""
# ScreenScraper — two credential levels, both required, see the prompt below.
SS_DEV_ID=""; SS_DEV_PASSWORD=""; SS_USER=""; SS_PASSWORD=""

LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')

if $UNATTENDED; then
  # shellcheck disable=SC1090
  source "$CONF"
  [[ -n "${USER_NAME:-}" ]] || die "USER_NAME missing in $CONF"
  GAMECORE_PATH="${GAMECORE_PATH:-/opt/GameCore}"
  WEB_PORT="${WEB_PORT:-8765}"
  MODE="${MODE:-full}"
  [[ "$MODE" == "full" || "$MODE" == "minimal" ]] || die "MODE must be full or minimal"
  APPS="${APPS-all}"
  validate_conf
else
  read -rp "  System username (e.g. pavic)         : " USER_NAME
  [[ -n "$USER_NAME" ]] || die "Username cannot be empty."
  read -rp "  Install path [default: /opt/GameCore] : " GAMECORE_PATH
  GAMECORE_PATH="${GAMECORE_PATH:-/opt/GameCore}"
  read -rp "  Web ROM port [default: 8765]          : " WEB_PORT
  WEB_PORT="${WEB_PORT:-8765}"

  if [[ -z "$MODE" ]]; then
    echo
    read -rp "  Install emulators & applications too? Full install / GameCore only (F/m) : " ANSWER
    [[ "$ANSWER" =~ ^[mM]$ ]] && MODE="minimal" || MODE="full"
  fi

  if [[ "$MODE" == "full" ]]; then
    echo
    info "EmberTV (Twitch on the TV) — leave empty to run in demo mode."
    info "Create the app at https://dev.twitch.tv/console/apps (redirect: http://localhost:8097)."
    read -rp  "  Twitch Client ID                      : " TWITCH_CLIENT_ID
    read -rsp "  Twitch Client Secret (hidden)         : " TWITCH_CLIENT_SECRET; echo
    read -rsp "  TheGamesDB API key (covers, optional) : " TGDB_API_KEY; echo

    echo
    info "ScreenScraper (box art, 3D boxes, screenshots, videos, synopses)."
    info "TWO accounts are needed and they are not the same thing:"
    info "  · developer  — asked for on the ScreenScraper forum, per software."
    info "                 The dev id is the PSEUDONYM, not the number in the"
    info "                 devinfos.php URL. Without it: 403."
    info "  · member     — your own account on screenscraper.fr. It carries the"
    info "                 daily quota and the thread count."
    info "Leave empty to skip: covers then resolve exactly as before."
    read -rp  "  ScreenScraper dev id                  : " SS_DEV_ID
    read -rsp "  ScreenScraper dev password (hidden)   : " SS_DEV_PASSWORD; echo
    read -rp  "  ScreenScraper member login            : " SS_USER
    read -rsp "  ScreenScraper member password (hidden): " SS_PASSWORD; echo
  fi
fi

want_emu() { [[ "$EMULATORS" == "all" || " $EMULATORS " == *" $1 "* ]]; }
want_app() { [[ "$MODE" == "full" ]] && [[ "$APPS" == "all" || " $APPS " == *" $1 "* ]]; }

# ── Where the player's data goes ─────────────────────────────────
#
# Defaults to the install directory, which is where every GameCore before this
# one kept it. That default is not laziness: the code that reads GAMECORE_DATA
# ships over OTA to boxes that already exist, and their data is inside the
# install. A default of /userdata would tell every one of them to look at an
# empty directory.
#
# Setting it to something else in the unattended config is what a NEW box does
# to get the split from day one — no migration needed, because there is nothing
# to migrate yet.
GAMECORE_DATA="${GAMECORE_DATA:-$GAMECORE_PATH}"

launcher_icon() {  # launcher_icon <install-dir> → an Icon= value
  local logo="$1/frontend/src/assets/logo.png"
  if [[ -f "$logo" ]]; then printf '%s\n' "$logo"; else printf '%s\n' "input-gaming"; fi
}

seed_data_tree() {  # seed_data_tree <from> <to> <user>
  # See the call site ("The player's tree, on day one") for the why.
  local from="$1" to="$2" user="$3" k src dst
  [[ -d "$from" ]] || return 0
  [[ "$(readlink -f "$from")" == "$(readlink -f "$to")" ]] && return 0
  for k in emu config assets/overlays assets/logos assets/art; do
    src="$from/$k"; dst="$to/$k"
    [[ -d "$src" ]] || continue
    # Absent or empty. `-A` so a dotfile counts as content; a directory that
    # holds anything at all is the player's.
    if [[ -e "$dst" && -n "$(ls -A "$dst" 2>/dev/null)" ]]; then
      continue
    fi
    mkdir -p "$dst"
    cp -a "$src/." "$dst/"
    chown -R "${user}:${user}" "$dst"
    ok "seeded $k → $dst"
  done
}

provision_userdata() {  # provision_userdata <dir> <user>
  # A btrfs subvolume when the filesystem offers one — it snapshots and gets
  # quota independently of the root, which is the whole point of separating the
  # data. A plain directory otherwise: correct everywhere, just less useful.
  #
  # This deliberately does NOT partition a disk. Repartitioning is the one
  # operation here that can destroy an unrelated filesystem, it cannot be
  # undone, and it cannot be made safe from a script that does not know what
  # else is on the device. An operator who wants /userdata on its own partition
  # mounts it there before running this, and the directory branch below finds it
  # already mounted and simply uses it.
  local dir="$1" user="$2"
  [[ "$dir" == "$GAMECORE_PATH" ]] && return 0   # not split; nothing to make

  if [[ -d "$dir" ]]; then
    ok "Data directory $dir already exists — left as it is."
  elif [[ "$(stat -f -c %T "$(dirname "$dir")" 2>/dev/null)" == "btrfs" ]] \
       && command -v btrfs >/dev/null; then
    btrfs subvolume create "$dir" >/dev/null \
      && ok "btrfs subvolume $dir created." \
      || { mkdir -p "$dir"; warn "btrfs subvolume failed — plain directory instead."; }
  else
    mkdir -p "$dir"
    ok "Data directory $dir created."
  fi
  chown -R "${user}:${user}" "$dir"
  # The layout backend/services/paths.py resolves against. Created up front so
  # a first boot never has to decide whether an absent directory means "empty"
  # or "broken".
  sudo -u "$user" mkdir -p "$dir/config" "$dir/emu" "$dir/assets/overlays" \
                           "$dir/assets/logos" "$dir/assets/art" "$dir/addons"
}

echo
msg "Summary"
info "User         : $USER_NAME"
info "Install path : $GAMECORE_PATH"
info "Data path    : $GAMECORE_DATA$([ "$GAMECORE_DATA" = "$GAMECORE_PATH" ] && echo ' (inside the install, as before)')"
info "API port     : $WEB_PORT"
info "Detected IP  : $LOCAL_IP"
info "Mode         : $MODE $([ "$MODE" = minimal ] && echo '(no emulators, no apps)' || echo '(emulators + apps + configs)')"
[[ "$MODE" == "full" ]] && info "Emulators    : ${EMULATORS:-none}"
[[ "$MODE" == "full" ]] && info "Apps         : ${APPS:-none}"
info "Addons       : ${ADDONS:-none}"
[[ "$MODE" == "full" ]] && info "EmberTV      : $([ -n "$TWITCH_CLIENT_ID" ] && echo 'live Twitch (credentials set)' || echo 'demo mode (no credentials)')"
[[ "$MODE" == "full" ]] && info "ScreenScraper: $([ -n "$SS_DEV_ID" ] && echo 'configured (hash matching + every media type)' || echo 'absent (covers by name only)')"
echo
if ! $UNATTENDED; then
  read -rp "  Continue? (y/N) " CONFIRM
  [[ "$CONFIRM" =~ ^[yY]$ ]] || die "Aborted."
fi

# ── User check ───────────────────────────────────────────────────
progress 2 "Checking user"
msg "Checking user"
USER_CREATED=0
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USER_NAME"
  USER_CREATED=1
  ok "User $USER_NAME created."
fi
ok "User $USER_NAME OK"
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)

# The uninstaller must never delete an account that predates GameCore.
manifest_set USER_NAME      "$USER_NAME"
manifest_set GAMECORE_PATH  "$GAMECORE_PATH"
manifest_set GAMECORE_DATA  "$GAMECORE_DATA"
manifest_set WEB_PORT       "$WEB_PORT"
manifest_set MODE           "$MODE"
manifest_owned USER_CREATED   "$USER_CREATED"
manifest_set INSTALLED_AT   "$(date -Iseconds)"
# Create the list files up front, even empty. "We installed no Flatpaks" and
# "there is no record of what we installed" must not look the same to the
# uninstaller: the first means remove nothing, the second means it is blind.
touch "$PKG_MANIFEST" "$FLATPAK_MANIFEST" "$OVERRIDE_MANIFEST"

# ── Data directory ───────────────────────────────────────────────
# A no-op unless GAMECORE_DATA was pointed somewhere else, which is what makes
# this safe to ship: an ordinary install still puts everything in one place.
provision_userdata "$GAMECORE_DATA" "$USER_NAME"

# ── Copy files ───────────────────────────────────────────────────
progress 4 "Copying GameCore files"
msg "Setting up $GAMECORE_PATH"
# The destination itself, not just its parent. `cp -r "$SRC/." "$GAMECORE_PATH"`
# used to create it on the way; the tar pipeline below cannot — `tar -C <dir>`
# needs <dir> to exist and exits before reading a single byte. Every FRESH
# install died here, at 4 %, with "tar: /opt/GameCore: Cannot open". Only an
# install onto an existing directory — i.e. a re-run — got past it, which is
# exactly the case anyone testing the change would have been in.
mkdir -p "$GAMECORE_PATH"
if [ "$PROJECT_ROOT" != "$GAMECORE_PATH" ]; then
  # Copy with the same exclusions update/linux.sh uses, not a bare `cp -r`.
  #
  # Re-running the installer from a checkout is a path this script recommends at
  # the end, and `cp -r "$PROJECT_ROOT/."` had no exclusions at all: a bezel
  # uploaded from the ROM manager (assets/overlays/pcsx2.png) was overwritten by
  # the repo's copy, with no backup and nothing said. Same for the 18
  # assets/logos/*.png, config/overlays.json and the installed themes.
  # uninstall.sh already preserves assets/ for exactly this reason.
  #
  # tar rather than rsync: this runs at 4 %, before `pacman -Syu`, and rsync is
  # not part of Arch's `base` — tar is. Same semantics as the old `cp -r "$SRC/."`
  # (merge into the destination, delete nothing), only with exclusions.
  tar -C "$PROJECT_ROOT" \
    --exclude='./.git' \
    --exclude='./.venv' \
    --exclude='./emu' \
    --exclude='./config' \
    --exclude='./assets/overlays' \
    --exclude='./assets/logos' \
    --exclude='./assets/art' \
    -cf - . \
    | tar -C "$GAMECORE_PATH" -xf - \
    || die "could not copy $PROJECT_ROOT → $GAMECORE_PATH"

  ok "Copied $PROJECT_ROOT → $GAMECORE_PATH (user content preserved)"
else
  ok "Already in place."
fi
chown -R "${USER_NAME}:${USER_NAME}" "$GAMECORE_PATH"

# ── The player's tree, on day one ────────────────────────────────
# What the DATA root needs before the first game, and never gets overwritten
# afterwards: config/overlays.json (the declared bezel geometry — without it
# Electron never starts the overlay monitor for any system), the bundled themes
# under config/themes/, the shipped bezels in assets/overlays/ (the per-console
# ones included) and assets/logos/. systems.json and apps.json are regenerated
# from install/generated/*.dist further down, so seeding the checkout's copies
# here costs nothing.
#
# This used to be a loop that copied each directory into GAMECORE_PATH when
# absent there. On the layout every box had before the split that is the same
# directory as GAMECORE_DATA and it worked. On a split install it seeded
# NOTHING: provision_userdata had already created the (empty) directories under
# GAMECORE_DATA, so "absent" was never true, and the files went — if anywhere —
# into the install, where nothing reads them. A fresh /userdata box booted, ran
# games, and drew no bezel around any of them, and nobody could say why. The
# reference box only had its overlays because its migration had copied them.
#
# So: into GAMECORE_DATA, and "seed when the directory is absent OR empty".
# A populated directory is the player's and is left exactly alone — a re-run
# never puts back a bezel someone deleted on purpose.
seed_data_tree "$PROJECT_ROOT" "$GAMECORE_DATA" "$USER_NAME"

# ── System packages ──────────────────────────────────────────────
# pacman is by far the longest phase of the install (a rolling-release
# upgrade plus ~25 packages). Without markers inside it the GUI's progress
# bar sits at 6 % for up to 40 minutes and looks hung.
progress 6 "Refreshing and upgrading system packages (this can take a while)"
msg "System packages"
pacman -Syu --noconfirm

progress 14 "Installing base packages (desktop, drivers, node, caddy)"
PKGS=(
  mesa
  base-devel git flatpak openssh
  python python-pip
  nodejs npm
  # ffmpeg: general media tooling. Stremio no longer needs it — its desktop
  # client plays through mpv and never transcodes (see docs/STREMIO.md) — but
  # nothing else replaces it on a media box, so it stays.
  ffmpeg
  plasma-desktop sddm xorg-xdpyinfo xorg-xrandr xorg-xset unclutter
  bluez bluez-utils
  caddy
  # mDNS: avahi answers for <hostname>.local, nss-mdns is what makes glibc ask
  # it. Both, always — nss-mdns alone resolves nothing and avahi alone is only
  # reachable by software that speaks mDNS itself. See the mDNS section below
  # for why these are installed rather than assumed from the distro.
  avahi nss-mdns
  # update/linux.sh does all of its file installation with rsync, and rsync is
  # not in Arch's `base` — an OTA on a box without it failed at the first step.
  rsync
)

# Kernel series — needed by both the headers and the Manjaro NVIDIA module,
# so it is computed before the GPU branch. `uname -r` is the RUNNING kernel;
# on a box that was upgraded but not rebooted the matching package may not
# exist any more, hence the availability check below.
KERNEL=$(uname -r)
KSHORT=""
if $IS_MANJARO; then
  KSHORT=$(echo "$KERNEL" | grep -oP '^\d+\.\d+' | tr -d '.')
  KRT=""; [[ $KERNEL == *-rt* ]] && KRT="-rt"
  if pacman -Si "linux${KSHORT}${KRT}-headers" >/dev/null 2>&1; then
    PKGS+=("linux${KSHORT}${KRT}-headers")
  else
    warn "linux${KSHORT}${KRT}-headers not in the repos — kernel headers skipped."
    warn "  (running kernel: $KERNEL — reboot into the installed kernel and re-run if you need DKMS.)"
  fi
else
  # Arch names a kernel's headers after the kernel package, and uname -r ends
  # with that flavour: 6.12.50-1-lts, 6.17.1-zen1-1-zen, ...-hardened.
  case "$KERNEL" in
    *-zen)      PKGS+=("linux-zen-headers") ;;
    *-lts)      PKGS+=("linux-lts-headers") ;;
    *-hardened) PKGS+=("linux-hardened-headers") ;;
    *)          PKGS+=("linux-headers") ;;
  esac
fi

# The lib32-* drivers live in [multilib], which Manjaro enables and Arch does
# not. Asking pacman for a package from a disabled repo is a "target not found"
# error, and under `set -euo pipefail` that ended the whole install mid-way —
# after the system upgrade and after the user account was created, before any
# service was configured. The box was left in a state that was neither a working
# install nor a clean machine.
#
# Not enabled automatically on purpose: /var/lib/gamecore is the record of what
# this install changed, and quietly editing /etc/pacman.conf would not be in it.
HAS_MULTILIB=false
if pacman-conf --repo-list 2>/dev/null | grep -qx multilib; then
  HAS_MULTILIB=true
fi

# `|| true` so a false HAS_MULTILIB is not the function's exit status — that
# alone would abort the install under `set -e`.
add_lib32() { $HAS_MULTILIB && PKGS+=("$@") || true; }

if ! $HAS_MULTILIB; then
  warn "[multilib] is not enabled — skipping the 32-bit Vulkan drivers."
  warn "  32-bit games (mostly under Steam/Proton) may not render."
  warn "  To enable it, uncomment the [multilib] section in /etc/pacman.conf,"
  warn "  run 'sudo pacman -Sy', then re-run this installer."
fi

# GPU drivers — detect every vendor present, not the first one found. A laptop
# or a desktop with its iGPU left on lists two: Intel or AMD for the panel and
# NVIDIA for the games. An if/elif chain installed the iGPU's driver only and
# left the card that runs the emulators without one.
GPU_INFO=$(lspci -nn 2>/dev/null | grep -Ei 'vga|3d|display' || true)
NVIDIA_REBOOT_NEEDED=false
GPU_FOUND=false
if echo "$GPU_INFO" | grep -qiE 'amd|radeon'; then
  PKGS+=(xf86-video-amdgpu vulkan-radeon)
  add_lib32 lib32-vulkan-radeon
  info "GPU detected: AMD (vulkan-radeon)"
  GPU_FOUND=true
fi
if echo "$GPU_INFO" | grep -qi 'intel'; then
  PKGS+=(vulkan-intel)
  add_lib32 lib32-vulkan-intel
  info "GPU detected: Intel (vulkan-intel)"
  GPU_FOUND=true
fi
if echo "$GPU_INFO" | grep -qi 'nvidia'; then
  GPU_FOUND=true
  # The generation decides the driver branch, and the PCI device id gives it:
  # every Turing-or-newer id (GTX 16xx / RTX 20 and up) is >= 0x1e00, which is
  # all the open module drives; Maxwell, Pascal and Volta (0x1340-0x1dff, GTX
  # 900 / 1000) need the 580xx branch; anything older has no NVIDIA driver for
  # a current kernel. nvidia-open on a GTX 1060 installs cleanly and boots to
  # a black screen.
  NV_DEV=$(echo "$GPU_INFO" | grep -i nvidia | grep -oiE '\[10de:[0-9a-f]{4}\]' | head -1 | cut -c7-10)
  NV_GEN=current
  if [[ -n "$NV_DEV" ]]; then
    if (( 16#$NV_DEV < 16#1340 )); then NV_GEN=unsupported
    elif (( 16#$NV_DEV < 16#1e00 )); then NV_GEN=580xx
    fi
  fi
  # NEVER pass the bare name `nvidia` on Manjaro: no package is called that.
  # ~20 packages merely *provide* it, and `--noconfirm` suppresses the
  # "N providers available" prompt, so pacman silently picks the first —
  # linux61-nvidia — which drags in the whole 6.1 LTS kernel and builds the
  # module for a kernel this box is not running. X then loads nvidia_drv.so,
  # finds no matching module, and the kiosk session never comes up.
  #
  # Arch dropped `nvidia` for `nvidia-open`, built for the stock `linux` kernel
  # only; any other kernel needs the DKMS flavour. A name that is not in the
  # repos made `pacman -S` fail, and under `set -e` that ended the install at
  # 14 %, after the upgrade and the user account. So every candidate is checked.
  NV_CANDS=()
  NV_UTILS=nvidia-utils
  case "$NV_GEN" in
    current)
      if $IS_MANJARO; then
        [[ -n "$KSHORT" ]] && NV_CANDS=("linux${KSHORT}${KRT}-nvidia" "linux${KSHORT}${KRT}-nvidia-open")
        NV_CANDS+=(nvidia-open-dkms nvidia-dkms)
      elif [[ $KERNEL == *-arch* ]]; then
        NV_CANDS=(nvidia-open nvidia nvidia-open-dkms nvidia-dkms)
      else
        NV_CANDS=(nvidia-open-dkms nvidia-dkms)
      fi ;;
    580xx)
      # Manjaro ships the branch; Arch only has it in the AUR, which pacman
      # cannot install — unless the box added a repo that carries it.
      NV_UTILS=nvidia-580xx-utils
      $IS_MANJARO && [[ -n "$KSHORT" ]] && NV_CANDS=("linux${KSHORT}${KRT}-nvidia-580xx")
      NV_CANDS+=(nvidia-580xx-dkms) ;;
  esac
  NV_PKG=""
  for cand in "${NV_CANDS[@]}"; do
    if pacman -Si "$cand" >/dev/null 2>&1; then NV_PKG="$cand"; break; fi
  done
  if [[ -n "$NV_PKG" ]]; then
    PKGS+=("$NV_PKG" "$NV_UTILS")
    add_lib32 "lib32-$NV_UTILS"
    # A DKMS module builds against the installed kernel and therefore needs
    # dkms plus the headers added above.
    [[ "$NV_PKG" == *-dkms ]] && PKGS+=(dkms)
    NVIDIA_REBOOT_NEEDED=true
    info "GPU detected: NVIDIA ($NV_PKG)"
  elif [[ "$NV_GEN" == unsupported ]]; then
    warn "GPU detected: NVIDIA 10de:$NV_DEV, older than any NVIDIA driver for this kernel."
    warn "  The open-source nouveau driver (mesa) runs it; expect low emulator speed."
  elif [[ "$NV_GEN" == 580xx ]]; then
    warn "GPU detected: NVIDIA 10de:$NV_DEV (GTX 900 / 1000 generation)."
    warn "  Its driver is the 580xx branch, which Arch only has in the AUR. After the"
    warn "  install:  yay -S nvidia-580xx-dkms nvidia-580xx-utils lib32-nvidia-580xx-utils"
    warn "  then reboot. Until then the open-source nouveau driver runs it."
  else
    warn "GPU detected: NVIDIA, but no driver package matches this kernel."
    warn "  Install it yourself before rebooting:  sudo mhwd -a pci nonfree 0300"
  fi
fi
if ! $GPU_FOUND; then
  if echo "$GPU_INFO" | grep -qiE 'vmware|virtualbox|virtio|qxl|bochs'; then
    # VM GPU — no hardware Vulkan; llvmpipe lets Vulkan apps at least start.
    PKGS+=(vulkan-swrast)
    info "GPU detected: virtual machine (software Vulkan via llvmpipe)"
  else
    warn "GPU not identified — installing mesa only (add your Vulkan driver manually)."
  fi
fi

# The whole GameCore stack is X11-only (overlays, fullscreen enforcer, the
# gamepad→keyboard bridge, the 1080p pin), so the kiosk needs a real X session.
#
# That session is the machine's own — plasma-desktop is in the package list
# above, and its X11 session is what the kiosk is hosted on.
#
# It used to be a bare openbox session installed for the purpose, and that was
# the wrong half of the requirement. GameCore draws over whatever is underneath,
# so hosting it on the desktop costs nothing — while closing the kiosk reveals
# something usable. Under openbox it revealed a window manager with no panel and
# no menu, which is what "Exit to Desktop gives me a black screen" was.

record_new_pkgs "${PKGS[@]}"
pacman -S --noconfirm --needed "${PKGS[@]}"
ok "System packages installed."

progress 20 "Optional packages"
# The X11 session Plasma is hosted on. Manjaro ships it as its own package;
# Arch's plasma-workspace carries it, and pacman_optional skips cleanly when the
# name does not exist. Without an X11 session file there is nothing for SDDM to
# auto-log into — the whole stack (overlays, fullscreen enforcer,
# gamecore-xsetup, the gamepad bridge) is X11 and cannot run on Wayland.
pacman_optional plasma-x11-session
# The console session's window manager. Recent Plasma ships KWin's X11 binary
# in its own package, and a machine running Plasma on Wayland does not have it:
# the reference box had kwin_wayland and no kwin_x11 at all. Without this the
# console session starts GameCore on a bare X server — the interface shows, the
# bezels stack at random, and a fullscreen emulator answers to nobody.
# Optional because older Plasma carries both binaries in `kwin` and has no such
# package to install.
pacman_optional kwin-x11
pacman_optional cpupower
# Microcode for the CPU this box has: amd-ucode on every box left Intel CPUs
# without their fixes. mkinitcpio's microcode hook loads whichever is present.
case "$(grep -m1 '^vendor_id' /proc/cpuinfo 2>/dev/null)" in
  *GenuineIntel*) pacman_optional intel-ucode ;;
  *AuthenticAMD*) pacman_optional amd-ucode ;;
esac
pacman_optional feh

# ── CPU governor ─────────────────────────────────────────────────
progress 22 "CPU governor"
msg "CPU governor"
if systemctl enable --now cpupower.service 2>/dev/null \
   && cpupower frequency-set -g performance 2>/dev/null; then
  ok "Performance mode set."
elif [ ! -d /sys/devices/system/cpu/cpu0/cpufreq ]; then
  warn "no cpufreq driver (VM or fixed-frequency CPU) — governor skipped."
else
  warn "cpupower not available."
fi

# ── GPU clock ────────────────────────────────────────────────────
# amdgpu's `auto` level idles an APU at 400-533 MHz between frames; the
# ramp-up queues frames and adds input lag while the frame rate looks fine
# (measured on a Radeon 680M under Ryujinx). The rule re-applies on every boot.
cat > /etc/udev/rules.d/99-gamecore-gpu.rules <<'UDEV'
SUBSYSTEM=="drm", KERNEL=="card[0-9]*", DRIVERS=="amdgpu", ATTR{device/power_dpm_force_performance_level}="high"
UDEV
ok "GPU clock rule written (amdgpu only, applied at the udev reload below)."

# ── Flatpak ──────────────────────────────────────────────────────
progress 24 "Flatpak / Flathub"
msg "Flatpak / Flathub"
flatpak remote-list 2>/dev/null | grep -q flathub \
  || flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
ok "Flathub ready."

# ── Emulators (full mode only) ───────────────────────────────────
# Declared outside the branch: the final recap reads them in minimal mode too.
# Whatever failed, by pack id, with the message the provider gave.
EMU_FAILED=()
APP_FAILED=()
RESTART_UNITS=()
if [[ "$MODE" == "full" ]]; then
  msg "Installing emulators"
  # One call, one pack at a time, everything the pack declares. There is no
  # list of emulators in this file any more — not the Flatpak ids, not the
  # sandbox flags, not the two non-Flatpak ones. `gamecore-provider.py` reads
  # catalog/<id>/pack.json and applies it:
  #
  #   packages   the extra system deps (fuse2: a type-2 AppImage mounts itself
  #              through libfuse2, and without it DuckStation exits with
  #              "dlopen(): error loading libfuse.so.2" and the PS1 tile is dead)
  #   install    flatpak, or a GitHub asset/archive for the two that are not on
  #              Flathub — same protections either way: the fixed release URL
  #              before the rate-limited API, a .part temp file, magic bytes,
  #              an optional sha256
  #   sandbox    the ROM dir + controller access, per pack
  #
  # What this replaced: a hand-written Flatpak loop, a hand-written override
  # loop, and `for _id in duckstation xenia`. That last one is why this is a
  # rewrite rather than a tidy-up — a third AppImage emulator would have been
  # declared in its pack, validated by check-catalog, offered in the wizard,
  # and never installed, without one line of output saying so.
  EMU_TOTAL=$(python3 "$GAMECORE_PATH/scripts/catalog-query.py" ids --kind emulator \
              | { [[ "$EMULATORS" == "all" ]] && cat \
                   || grep -xF -f <(tr ' ' '\n' <<<"$EMULATORS" | grep -v '^$'); } \
              | wc -l)
  EMU_I=0
  if [[ -n "${EMULATORS// /}" && "$EMU_TOTAL" -gt 0 ]]; then
    while IFS= read -r _line; do
      case "$_line" in
        # One per pack, before its results — the progress bar has to move during
        # the longest phase of the install or the GUI looks hung.
        PACK*) EMU_I=$((EMU_I + 1))
               progress $((25 + EMU_I * 25 / EMU_TOTAL)) "Installing ${_line#PACK }" ;;
        OK*)   ok   "${_line#OK }" ;;
        SAME*) info "${_line#SAME }" ;;
        FAIL*) warn "${_line#FAIL }"; EMU_FAILED+=("${_line#FAIL }") ;;
        UNIT*) RESTART_UNITS+=("${_line#UNIT }") ;;
        *)     [[ -n "$_line" ]] && info "$_line" ;;
      esac
    done < <(python3 "$GAMECORE_PATH/scripts/gamecore-provider.py" install \
               --kind emulator --select "$EMULATORS" \
               --user "$USER_NAME" --user-home "$USER_HOME" \
               --gamecore-path "$GAMECORE_PATH" --gamecore-data "$GAMECORE_DATA" 2>&1 || true)
  else
    info "No emulator selected — nothing to install."
  fi


  # ── Curated emulator configs (incl. controller bindings) ───────
  # -H so $HOME inside the script is the gaming user's, not root's: every
  # destination path in install-emu-configs.sh is derived from it.
  progress 56 "Emulator configs"
  msg "Emulator configs"
  if [ -d "$GAMECORE_PATH/catalog" ]; then
    sudo -u "$USER_NAME" -H env GAMECORE_PATH="$GAMECORE_PATH" \
      bash "$GAMECORE_PATH/install/steps/install-emu-configs.sh" \
      && ok "Curated configs deployed." || warn "Config deployment failed."
  else
    warn "catalog/ not found — skipping."
  fi

  # ── Living-room applications ────────────────────────────────────
  # Every app selected in APPS, installed from its own pack. Unchecked means
  # nothing is cloned, written or enabled for it.
  progress 58 "Living-room applications"
  msg "Living-room applications"
  # One directory per app. catalog/<app>/ carries what the app needs — the
  # git checkouts beside it, its config, its user unit, its post-install
  # ceremonies — and the applier honours the declarations. This block used to
  # be ~230 lines of EmberTV, Firefox profiles, certutil and Stremio written
  # out by hand, next to packs that already declared all of it and that nothing
  # read: that gap is how install/firefox-profiles/ could be deleted by a
  # refactor and only surface on a fresh box, months later, at 66 %.
  #
  # Steam and Stremio ride this too — install_flatpak does the install, the
  # sandbox override and both uninstall manifests — so they are no longer part
  # of the emulator Flatpak loop above.
  UNIT_DIR="$USER_HOME/.config/systemd/user"
  # As the user, not as root. This was the one place in the script that created
  # something under $USER_HOME as root, and it created the whole chain — so on a
  # distribution whose /etc/skel has no .config (Arch vanilla) with an account
  # the installer had just made with `useradd -m`, ~/.config itself ended up
  # root:root 0755. The Electron shell keeps its profile in
  # ~/.config/gamecore-electron and gamecore-ui.service runs as the user, so it
  # could not write it: the service failed and looped on Restart=on-failure, and
  # the kiosk never started at all — while the installer printed "Installation
  # complete!". The chown further down deliberately covers only .config/systemd
  # (see the comment there), so it never repaired this.
  install -d -o "$USER_NAME" -g "$USER_NAME" -m 755 "$UNIT_DIR/default.target.wants"
  # Keep the emulator units collected above; app units join the same list.

  APP_SEL=""
  for _app in $(python3 "$GAMECORE_PATH/scripts/catalog-query.py" ids --kind app); do
    want_app "$_app" && APP_SEL="$APP_SEL $_app"
  done
  if [[ -n "${APP_SEL// /}" ]]; then
    # Exported, never passed as arguments: /proc/<pid>/cmdline is world-readable
    # and one of these is a Twitch client secret. The pack names the keys it
    # wants under `secrets`; the applier reads only those.
    export TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET TGDB_API_KEY \
           SS_DEV_ID SS_DEV_PASSWORD SS_USER SS_PASSWORD
    APP_TOTAL=$(wc -w <<<"$APP_SEL"); APP_I=0
    while IFS= read -r _line; do
      case "$_line" in
        PACK*) APP_I=$((APP_I + 1))
               progress $((58 + APP_I * 12 / APP_TOTAL)) "Installing ${_line#PACK }" ;;
        OK*)   ok   "${_line#OK }" ;;
        SAME*) info "${_line#SAME }" ;;
        UNIT*) RESTART_UNITS+=("${_line#UNIT }") ;;
        FAIL*) warn "${_line#FAIL }"; APP_FAILED+=("${_line#FAIL }") ;;
        *)     [[ -n "$_line" ]] && info "$_line" ;;
      esac
    done < <(python3 "$GAMECORE_PATH/scripts/gamecore-provider.py" install \
               --kind app --select "$APP_SEL" \
               --user "$USER_NAME" --user-home "$USER_HOME" \
               --gamecore-path "$GAMECORE_PATH" --gamecore-data "$GAMECORE_DATA" 2>&1 || true)
    unset TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET
  else
    info "No living-room application selected."
  fi


  # Stremio is deliberately absent here: its interface reads the gamepad itself,
  # and its window is Wayland-native, which the bridge's X11 title detection
  # cannot see anyway. Only the Firefox kiosks need the translation.
  if want_app twitch || want_app youtube; then
  progress 64 "Gamepad TV bridge"
  # gamepad-tv-bridge — gamepad → keyboard for kiosk web apps
  if [ ! -d /opt/gamepad-tv-bridge ]; then
    git_clone https://github.com/p4v1c/gamepad-tv-bridge.git /opt/gamepad-tv-bridge \
      && ok "gamepad-tv-bridge cloned → /opt/gamepad-tv-bridge" || warn "gamepad-tv-bridge clone failed."
  else
    git_sync /opt/gamepad-tv-bridge "gamepad-tv-bridge"
  fi
  if [ -d /opt/gamepad-tv-bridge ]; then
    chown -R "${USER_NAME}:${USER_NAME}" /opt/gamepad-tv-bridge
    # ~/.venv is the most generic virtualenv path on Linux and the user may
    # already own one. Record whether WE created it, so the uninstaller knows
    # the difference between "delete this, it is ours" and "pip uninstall just
    # our package out of theirs".
    if [[ -d "$USER_HOME/.venv" ]]; then
      manifest_owned BRIDGE_VENV_CREATED 0
    else
      sudo -u "$USER_NAME" -H python3 -m venv "$USER_HOME/.venv" 2>/dev/null \
        && manifest_owned BRIDGE_VENV_CREATED 1 || manifest_owned BRIDGE_VENV_CREATED 0
    fi
    sudo -u "$USER_NAME" -H "$USER_HOME/.venv/bin/pip" install -q -e /opt/gamepad-tv-bridge \
      && ok "bridge installed in $USER_HOME/.venv (editable)." || warn "bridge pip install failed."
    # WantedBy=default.target, NOT graphical-session.target: with linger the
    # user manager starts at boot (before any graphical login), so the bridge
    # comes up regardless of how the Plasma session starts; Restart=on-failure
    # covers the window where X isn't up yet.
    cat > "$UNIT_DIR/gamepad-tv-bridge.service" <<'EOF'
[Unit]
Description=Gamepad TV Bridge — gamepad to keyboard daemon

[Service]
Type=simple
Environment=DISPLAY=:0
ExecStart=%h/.venv/bin/python -m gamepad_bridge start
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    ln -sf ../gamepad-tv-bridge.service "$UNIT_DIR/default.target.wants/gamepad-tv-bridge.service"
    rm -f "$UNIT_DIR/graphical-session.target.wants/gamepad-tv-bridge.service"
    RESTART_UNITS+=(gamepad-tv-bridge.service)
    ok "gamepad-tv-bridge.service installed (user unit)."
    # /dev/uinput access for key injection
    modprobe uinput 2>/dev/null || true
    echo "uinput" > /etc/modules-load.d/uinput.conf
    cat > /etc/udev/rules.d/99-uinput.rules <<'EOF'
KERNEL=="uinput", GROUP="input", MODE="0660"
EOF
    ok "uinput module + udev rule configured."
  fi
  fi  # twitch/youtube/stremio kiosk bridge

  # Only the directory we actually wrote into. A recursive chown of the whole
  # ~/.config silently rewrites the ownership of anything in there that
  # legitimately belonged to another uid, with no record and no way back.
  chown -R "${USER_NAME}:${USER_NAME}" "$USER_HOME/.config/systemd" 2>/dev/null || true

  # Record whether WE turned linger on, so the uninstaller does not switch it
  # off under a user who had it enabled for their own services.
  if loginctl show-user "$USER_NAME" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    manifest_owned LINGER_ENABLED 0
  else
    loginctl enable-linger "$USER_NAME" 2>/dev/null \
      && { manifest_owned LINGER_ENABLED 1; ok "user services will start at boot (linger)."; } || true
  fi

  # Make the freshly written user units effective NOW, not only after reboot:
  # the running user manager doesn't see manually symlinked units until a
  # daemon-reload, so embertv/gamepad-tv-bridge would stay dead post-install.
  USER_UID=$(id -u "$USER_NAME")
  if [ ${#RESTART_UNITS[@]} -gt 0 ]; then
    for _ in $(seq 1 10); do [ -S "/run/user/$USER_UID/bus" ] && break; sleep 1; done
    if [ -S "/run/user/$USER_UID/bus" ]; then
      sudo -u "$USER_NAME" XDG_RUNTIME_DIR="/run/user/$USER_UID" systemctl --user daemon-reload 2>/dev/null || true
      sudo -u "$USER_NAME" XDG_RUNTIME_DIR="/run/user/$USER_UID" \
        systemctl --user restart "${RESTART_UNITS[@]}" 2>/dev/null \
        && ok "user services started: ${RESTART_UNITS[*]}." \
        || warn "user services will start at next boot."
    else
      warn "no user bus yet — ${RESTART_UNITS[*]} will start at next boot."
    fi
  fi

else
  msg "Minimal mode — skipping emulators, applications and configs."
fi

# ── Home-grid tiles (config/apps.json + config/systems.json) ─────
# An unchecked emulator or app must not leave a dead tile on the TV.
#
# Both files are regenerated from the pristine catalogues in install/generated/*.dist
# on every run. Filtering in place used to be a one-way door: re-running the
# installer with MORE emulators selected could never bring back a tile a
# previous minimal run had deleted, because config/ is excluded from OTA and
# `cp -r` is skipped when the source already is the install dir. The .dist
# files live in install/, so they ride along with both the copy and the OTA
# rsync and stay current with each release.
progress 76 "Home-grid tiles"
msg "Home-grid tiles"
for pair in "apps.json" "systems.json"; do
  DIST="$GAMECORE_PATH/install/generated/${pair}.dist"
  LIVE="$GAMECORE_DATA/config/${pair}"
  if [[ -f "$DIST" ]]; then
    # First run only: on a second pass $LIVE is already our generated file, and
    # overwriting the backup would lose the operator's hand-edited original.
    [[ -f "$LIVE" && ! -e "${LIVE}.bak-install" ]] && cp -f "$LIVE" "${LIVE}.bak-install"
    cp -f "$DIST" "$LIVE"
  else
    warn "install/generated/${pair}.dist missing — filtering ${pair} in place."
  fi
done
# apps.json ships an @HOME@ token (it is generated from catalog/*/pack.json).
sed -i -e "s|@HOME@|$USER_HOME|g" "$GAMECORE_DATA/config/apps.json"

KEEP_APPS=""
for app in $(python3 "$GAMECORE_PATH/scripts/catalog-query.py" ids --kind app); do
  want_app "$app" && KEEP_APPS="$KEEP_APPS $app"
done
python3 - "$GAMECORE_DATA/config/apps.json" $KEEP_APPS <<'EOF'
import json, sys
path, keep = sys.argv[1], set(sys.argv[2:])
apps = json.load(open(path))
kept = [a for a in apps if a.get("id") in keep]
if len(kept) != len(apps):
    json.dump(kept, open(path, "w"), indent=2, ensure_ascii=False)
removed = [a.get("id") for a in apps if a.get("id") not in keep]
print(f"[app-tiles] kept: {', '.join(sorted(keep)) or 'none'}"
      + (f" — removed: {', '.join(removed)}" if removed else ""))
EOF
ok "apps.json filtered to the selected apps."

# systems.json: rewrite the launchers to what this box actually has, and drop
# the systems that were not selected. In minimal mode nothing is installed,
# so the emulator half of the grid goes entirely.
progress 78 "Systems → launchers"
if [[ "$MODE" == "minimal" ]]; then
  EMU_SEL=""
else
  EMU_SEL="$EMULATORS"
fi
bash "$GAMECORE_PATH/install/steps/flatpakify-systems.sh" "$GAMECORE_DATA" "$EMU_SEL" \
  && ok "systems.json adapted to the selected emulators." \
  || warn "flatpakify failed — check config/systems.json."
chown "${USER_NAME}:${USER_NAME}" \
  "$GAMECORE_DATA/config/apps.json" "$GAMECORE_DATA/config/systems.json" 2>/dev/null || true

# ── ROM directories ──────────────────────────────────────────────
progress 80 "ROM directories"
msg "ROM directories"
# From the catalogue: every pack declares its own roms.dir, so adding an
# emulator no longer means remembering to add a line here too. `covers` is not
# a system — it is where the cover pipeline caches art.
for d in $(python3 "$GAMECORE_PATH/scripts/catalog-query.py" rom-dirs) covers; do
  sudo -u "$USER_NAME" mkdir -p "$GAMECORE_DATA/emu/$d"
done
ok "ROM directories ready."

# ── Offline metadata index ─────────────────────────────────────────
# The LaunchBox tier: 185 000 games, no account, offline once built. The other
# tier (ScreenScraper) needs developer credentials most boxes do not have, so
# without this a fresh install has NO metadata source at all — no titles, no
# synopses, no covers, and nothing saying why.
#
# `--minimal` skips it: it is 234 MB, and a minimal install is the one that
# asked for less. The step prints the one command that adds it later.
#
# Never fatal — the step always exits 0 and reports. A missing description is a
# degraded box; an aborted installer at 80 % is a machine that is neither
# installed nor clean.
if [[ "$MODE" == "full" ]]; then
  progress 81 "Offline metadata index"
  msg "Offline metadata index"
  GAMECORE_DATA="$GAMECORE_DATA" \
  bash "$GAMECORE_PATH/install/steps/build-media-index.sh" \
       "$GAMECORE_PATH" "$USER_NAME"
fi

# ── Input group + udev rule (needed for evdev PS-button detection) ──
progress 82 "Gamepad input access"
msg "Gamepad input access"
# Record whether the membership is ours. `input` is required by plenty of
# unrelated software (Steam, retroarch, ydotool), so an uninstaller must not
# revoke it from a user who already had it.
if id -nG "$USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx input; then
  manifest_owned INPUT_GROUP_ADDED 0
  ok "$USER_NAME already in the 'input' group."
elif usermod -aG input "$USER_NAME"; then
  manifest_owned INPUT_GROUP_ADDED 1
  ok "$USER_NAME added to 'input' group."
else
  warn "Could not add to input group."
fi

# udev rule: make all gamepad/joystick event nodes group=input + world-readable
# This means the backend process can open them even before a re-login
cat > /etc/udev/rules.d/99-gamecore-input.rules <<'UDEV'
# GameCore — allow reading gamepad events for PS/guide button detection.
#
# MODE is 0660, never 0664: the second rule matches EVERY USB HID interface,
# which includes keyboards. World-readable /dev/input/event* would let any
# local uid — the caddy service user, any SSH session — read every keystroke
# on the machine, sudo passwords included. Group `input` is enough: the
# backend unit has SupplementaryGroups=input and the desktop user is a member.
KERNEL=="event*", SUBSYSTEM=="input", ATTRS{bInterfaceClass}=="03", MODE="0660", GROUP="input"
# Sony DualShock / DualSense (vendor 054c) — kept unconditional: a DualShock
# over Bluetooth does not always tag every child node as a joystick.
SUBSYSTEM=="input", ATTRS{idVendor}=="054c", MODE="0660", GROUP="input"
UDEV

# DualShock 4 over hidraw — needed by RPCS3's native DS4 pad handler
# (rumble, motion, correct mapping) over USB and Bluetooth.
cat > /etc/udev/rules.d/99-ds4-controllers.rules <<'UDEV'
# DualShock 4 over USB
KERNEL=="hidraw*", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="05c4", MODE="0666"
# DualShock 4 Wireless Adapter over USB
KERNEL=="hidraw*", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="0ba0", MODE="0666"
# DualShock 4 Slim over USB
KERNEL=="hidraw*", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="09cc", MODE="0666"
# DualShock 4 over Bluetooth
KERNEL=="hidraw*", KERNELS=="*054C:05C4*", MODE="0666"
# DualShock 4 Slim over Bluetooth
KERNEL=="hidraw*", KERNELS=="*054C:09CC*", MODE="0666"
UDEV
ok "DualShock 4 hidraw rules installed."

udevadm control --reload-rules 2>/dev/null && udevadm trigger 2>/dev/null && ok "udev rules reloaded." || warn "udev reload failed — reconnect controller."

# ── Addon manager ────────────────────────────────────────────────
progress 84 "Addon manager"
msg "Addon manager (gamecore-addon)"
install -m 755 "$GAMECORE_PATH/install/bin/gamecore-addon" /usr/local/bin/gamecore-addon
# Where the addons checkout lives. The CLI decides: an existing
# /opt/gamecore-addons keeps being used (every box installed before the split
# has one, with services pointing into it), otherwise it clones under
# $GAMECORE_DATA/addons/_repo — the checkout is mutable code, installed and
# removed by the player at runtime, and that belongs on the data side.
#
# So the directory is pre-created (owned by the user, so `gamecore-addon
# install` never needs root) ONLY where the CLI would look on this box: /opt on
# the layout every box had before the split, the data root once it is split.
# Pre-creating /opt/gamecore-addons on a split box would make the CLI prefer
# it, and a fresh /userdata box would carry mutable code in /opt again.
if [[ "$GAMECORE_DATA" == "$GAMECORE_PATH" ]]; then
  install -d -o "$USER_NAME" -g "$USER_NAME" /opt/gamecore-addons
  ok "gamecore-addon CLI installed (addons live in /opt/gamecore-addons)."
else
  install -d -o "$USER_NAME" -g "$USER_NAME" "$GAMECORE_DATA/addons"
  ok "gamecore-addon CLI installed (addons live in $GAMECORE_DATA/addons/_repo)."
fi

# ── Python backend ───────────────────────────────────────────────
progress 86 "Python backend (venv)"
msg "Python backend (venv)"
sudo -u "$USER_NAME" -H python3 -m venv "$GAMECORE_PATH/.venv"
sudo -u "$USER_NAME" -H "$GAMECORE_PATH/.venv/bin/pip" install -q \
  -r "$GAMECORE_PATH/backend/requirements.txt"
ok "Python dependencies installed."

# ── LAN login password (enforced by Caddy — see docs/SECURITY.md) ─
if [[ -f "$GAMECORE_DATA/config/auth.json" ]]; then
  ok "Web password already set — kept (reset: gamecore-addon auth-reset)."
else
  msg "Web access password (login at https://${LOCAL_IP}:8443)"
  WEB_PASSWORD="${WEB_PASSWORD:-}"
  if ! $UNATTENDED; then
    while true; do
      read -rsp "  Web password (cannot be empty) : " WEB_PASSWORD; echo
      [[ -n "$WEB_PASSWORD" ]] || { warn "Empty password refused."; continue; }
      read -rsp "  Confirm                        : " WEB_PASSWORD2; echo
      [[ "$WEB_PASSWORD" == "$WEB_PASSWORD2" ]] && break
      warn "Passwords differ — try again."
    done
  fi
  if [[ -n "$WEB_PASSWORD" ]]; then
    # The prefix assignment is for the forked python, which reads both from its
    # environment. The interpreter path is expanded by THIS shell, from the same
    # variable and the same value — two readers, one value.
    # shellcheck disable=SC2097,SC2098
    WEB_PASSWORD="$WEB_PASSWORD" GAMECORE_PATH="$GAMECORE_PATH" \
      GAMECORE_DATA="$GAMECORE_DATA" \
      "$GAMECORE_PATH/.venv/bin/python3" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["GAMECORE_PATH"])
from backend.services import auth
auth.set_password(os.environ["WEB_PASSWORD"])
PYEOF
    chown "$USER_NAME:$USER_NAME" "$GAMECORE_DATA/config/auth.json" "$GAMECORE_DATA/config/auth_secret"
    ok "Web password set (config/auth.json, survives OTA updates)."
  else
    warn "No WEB_PASSWORD in conf — LAN stays locked; run 'gamecore-addon auth-reset'."
  fi
fi


# ── systemd service ──────────────────────────────────────────────
progress 88 "systemd services"
msg "systemd service"

cat > /etc/systemd/system/gamecore-backend.service <<EOF
[Unit]
Description=GameCore — FastAPI Backend
After=network.target display-manager.service

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
SupplementaryGroups=input
Environment=GAMECORE_PATH=$GAMECORE_PATH
Environment=GAMECORE_DATA=$GAMECORE_DATA
Environment=GAMECORE_BACKEND_PORT=$WEB_PORT
WorkingDirectory=$GAMECORE_PATH
# Wait (max 20 s, always exit 0) for an X display that ANSWERS, not just a
# socket: on a cold boot the backend starts before X, and the first socket
# (X0) can appear before the only usable display (X1). Each socket is tried
# with each cookie location, as session.py's probe does.
#
# The doubled dollar is load-bearing: systemd expands the braced form in an
# Exec line itself, before bash sees it. Undoubled, DISPLAY became ":" and the
# loop always ran its full 20 s at boot.
# NOTE: this comment is inside the heredoc that writes the unit: keep it free
# of sigils, they would be expanded on the way in.
ExecStartPre=/bin/bash -c 'command -v xdpyinfo >/dev/null || exit 0; for i in \$(seq 1 20); do for s in /tmp/.X11-unix/X*; do [ -S "\$s" ] || continue; for c in /run/user/\$(id -u)/xauth_* /tmp/xauth_* "\$HOME/.Xauthority" ""; do DISPLAY=":\$\${s##*/X}" XAUTHORITY="\$c" xdpyinfo >/dev/null 2>&1 && exit 0; done; done; sleep 1; done; exit 0'
ExecStart=$GAMECORE_PATH/.venv/bin/python3 -m uvicorn backend.main:app --host 127.0.0.1 --port $WEB_PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Scraper credentials — one drop-in, mode 600, outside the repo. They are
# secrets: THEGAMESDB_API_KEY is a key, and the four ScreenScraper values are
# two accounts, one of which (the developer one) is shared by every user of the
# same softname and gets revoked if it leaks.
#
# Written as a single file rather than one per key: two drop-ins with the same
# name overwrite each other, and two different names are two places to look when
# a scrape stops working.
# One `Environment=` line systemd reads back exactly: quoted, so a space does
# not cut the value; `\` and `"` escaped, and `%` doubled, because systemd
# expands %h, %u... in Environment= and a password holding "%h" reached the
# backend as the home directory.
sd_env() {  # sd_env <NAME> <value>
  local v="$2"
  v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//%/%%}"
  printf 'Environment="%s=%s"\n' "$1" "$v"
}
if [[ -n "$TGDB_API_KEY" || -n "$SS_DEV_ID" ]]; then
  mkdir -p /etc/systemd/system/gamecore-backend.service.d
  {
    echo "[Service]"
    [[ -n "$TGDB_API_KEY"    ]] && sd_env THEGAMESDB_API_KEY "$TGDB_API_KEY"
    [[ -n "$SS_DEV_ID"       ]] && sd_env SCREENSCRAPER_DEV_ID "$SS_DEV_ID"
    [[ -n "$SS_DEV_PASSWORD" ]] && sd_env SCREENSCRAPER_DEV_PASSWORD "$SS_DEV_PASSWORD"
    [[ -n "$SS_USER"         ]] && sd_env SCREENSCRAPER_USER "$SS_USER"
    [[ -n "$SS_PASSWORD"     ]] && sd_env SCREENSCRAPER_PASSWORD "$SS_PASSWORD"
  } > /etc/systemd/system/gamecore-backend.service.d/override.conf
  chmod 600 /etc/systemd/system/gamecore-backend.service.d/override.conf
  [[ -n "$TGDB_API_KEY" ]] && ok "TheGamesDB API key configured (local drop-in, never in git)."
  if [[ -n "$SS_DEV_ID" ]]; then
    ok "ScreenScraper credentials configured (local drop-in, never in git)."
    [[ -n "$SS_USER" ]] || warn "No ScreenScraper member account: the developer credentials alone give a level-0 quota."
  fi
fi

# ── the console session ──────────────────────────────────────────
#
# There is no system-wide `gamecore-ui.service` any more, and its absence is
# the point. It was `WantedBy=graphical.target`, drawing Electron over whatever
# session the machine had logged into — which is why the owner watched the
# desktop, its panel and its wallpaper for several seconds at every boot.
#
# The interface is a USER unit belonging to a session of its own now. One
# script installs it, and the OTA calls the same one: a migration written apart
# from the installation is a migration that drifts from it.
bash "$GAMECORE_PATH/install/steps/setup-gamecore-session.sh" \
     "$USER_NAME" "$GAMECORE_PATH" "$GAMECORE_DATA" "$WEB_PORT" \
  || die "the console session could not be installed"
ok "Console session installed (gamecore.desktop + user units)."

# ── SDDM auto-login ──────────────────────────────────────────────
progress 90 "SDDM auto-login"
msg "SDDM auto-login"
# The kiosk is hosted on the machine's own X11 desktop session.
#
# The choice is made by gamecore-session-select, from the source tree — it is
# installed to /usr/local/bin further down. One implementation of the ranking,
# so the installer and the desktop escape hatch cannot disagree about which
# session this machine has.
# The session to auto-log into is ours. `pick-desktop` still runs, and what it
# answers is recorded rather than used: it is the way BACK — the session
# `gamecore-session-select desktop` returns to — and a box with no X11 desktop
# installed has no way back, which is worth saying now rather than discovering
# from a sofa.
DESKTOP_SESSION=$(bash "$GAMECORE_PATH/install/bin/gamecore-session-select" pick-desktop --x11 2>/dev/null || true)
manifest_set DESKTOP_SESSION "$DESKTOP_SESSION"
KIOSK_SESSION=gamecore
manifest_set KIOSK_SESSION "$KIOSK_SESSION"
[[ -n "$DESKTOP_SESSION" ]] \
  || warn "No X11 desktop session is installed — 'gamecore-session-select desktop' will have nowhere to go."

if [[ ! -f /usr/share/xsessions/gamecore.desktop ]]; then
  warn "No gamecore.desktop in /usr/share/xsessions — auto-login NOT configured."
  warn "  GameCore cannot run on Wayland: the overlays, the fullscreen enforcer,"
  warn "  gamecore-xsetup and the gamepad bridge all need X11. Install a desktop"
  warn "  with an X11 session and re-run:"
  warn "      sudo pacman -S plasma-desktop && sudo bash install/arch.sh"
else
  mkdir -p /etc/sddm.conf.d
  # The filename matters. SDDM reads /etc/sddm.conf.d/* in name order and the
  # LAST file wins. Manjaro Plasma ships kde_settings.conf, which carries its
  # own [Autologin] with Session=plasma — the WAYLAND session — and 'k' sorts
  # after 'a', so a drop-in called autologin.conf is silently overridden and
  # the box boots into Wayland with the kiosk never starting. 'zz-' sorts last.
  cat > /etc/sddm.conf.d/zz-gamecore-autologin.conf <<EOF
[Autologin]
User=$USER_NAME
Session=$KIOSK_SESSION
Relogin=true
EOF

  # Sort order alone is not enough: the Login Screen KCM rewrites
  # kde_settings.conf whenever someone opens it, and a future KDE could name
  # its file something that sorts after 'zz'. Strip the competing keys, and
  # keep a backup so the uninstaller can put the user's own autologin back.
  KDE_SDDM_CONF=/etc/sddm.conf.d/kde_settings.conf
  # The backup lives with the manifest, NOT next to the original: SDDM reads
  # every file in /etc/sddm.conf.d/ regardless of extension, so a .pre-gamecore
  # copy left there would itself be parsed as configuration.
  KDE_SDDM_BACKUP="${MANIFEST_DIR}/kde_settings.conf.pre-gamecore"
  if [[ -f "$KDE_SDDM_CONF" ]] && grep -q '^\[Autologin\]' "$KDE_SDDM_CONF"; then
    mkdir -p "$MANIFEST_DIR"
    [[ -f "$KDE_SDDM_BACKUP" ]] || cp "$KDE_SDDM_CONF" "$KDE_SDDM_BACKUP"
    manifest_set KDE_SDDM_BACKUP "$KDE_SDDM_BACKUP"
    python3 - "$KDE_SDDM_CONF" <<'PY'
import sys
path = sys.argv[1]
out, in_autologin = [], False
for line in open(path, encoding="utf-8", errors="replace"):
    stripped = line.strip()
    if stripped.startswith("["):
        in_autologin = stripped.lower() == "[autologin]"
    elif in_autologin and stripped.split("=", 1)[0].strip() in ("User", "Session", "Relogin"):
        continue          # GameCore's zz- drop-in owns these now
    out.append(line)
open(path, "w", encoding="utf-8").writelines(out)
PY
    manifest_set KDE_SDDM_CONF_PATCHED 1
    ok "Competing [Autologin] keys removed from kde_settings.conf (backup kept)."
  fi
  ok "SDDM auto-login as $USER_NAME → $KIOSK_SESSION, with the kiosk over it."
  info "  Closing GameCore drops you on that desktop. Kiosk off for good: gamecore-session-select desktop"
fi

# The graphical UI applies the saved mode once X is available.
# gamecore-xsetup without --session is a no-op for obsolete SDDM hooks.
install -m755 "$GAMECORE_PATH/install/bin/gamecore-xsetup" /usr/local/bin/gamecore-xsetup

# The desktop escape hatch. The box auto-logs into its desktop with the kiosk
# over it; this turns the kiosk off — for the ten minutes a year someone needs a
# file manager — without editing SDDM drop-ins as root and without leaving
# gamecore-ui to redraw over the desktop at the next boot.
install -m755 "$GAMECORE_PATH/install/bin/gamecore-session-select" /usr/local/bin/gamecore-session-select
mkdir -p /etc/sddm.conf.d
cat > /etc/sddm.conf.d/zz-gamecore-display.conf <<EOF
[X11]
DisplayCommand=/usr/local/bin/gamecore-xsetup
EOF
ok "Display mode is applied by the UI session (saved preference, otherwise 1080p)."

systemctl daemon-reload
systemctl enable sddm.service
systemctl enable gamecore-backend.service
# The interface is a user unit of the console session — started by
# `gamecore-session`, not by graphical.target. Enabling anything system-wide
# here is what used to put a second Electron over the first.
ok "Services enabled."

# ── Caddy reverse-proxy — the only GameCore port exposed to the LAN ──
msg "Caddy reverse-proxy (HTTPS :8443)"
# Catch-all site + on-demand internal certs: no per-box templating for the
# *address*, so the box can change IP/network without touching this file.
# The backend port IS templated: the shipped Caddyfile says 8765 everywhere
# and a box installed on another port would 502 on every LAN request.
mkdir -p /etc/caddy
# "Was Caddy already serving something of the user's?" — deliberately NOT
# `systemctl is-active` and NOT "the file exists":
#   · the caddy package OWNS /etc/caddy/Caddyfile, so on a fresh install the
#     stock file is always there. Treating it as the user's site made every
#     clean install print a scary "an existing Caddyfile was saved" and, worse,
#     made the uninstaller leave caddy enabled with its CA private key on disk.
#   · is-active is true on the SECOND run of this idempotent installer, because
#     the first run started it — so GameCore would record its own service as
#     pre-existing.
# Ours iff we just installed the package, or the file is the packaged default.
CADDY_PREEXISTING=false
if ! grep -qxF caddy "$PKG_MANIFEST" 2>/dev/null \
   && [[ -f /etc/caddy/Caddyfile ]] \
   && ! grep -q 'GameCore' /etc/caddy/Caddyfile \
   && pacman -Qkk caddy 2>/dev/null | grep -q '/etc/caddy/Caddyfile'; then
  # caddy predates this install AND its config has been modified by someone.
  CADDY_PREEXISTING=true
  CADDY_BACKUP="/etc/caddy/Caddyfile.pre-gamecore"
  [[ -e "$CADDY_BACKUP" ]] || cp /etc/caddy/Caddyfile "$CADDY_BACKUP"
  manifest_set CADDYFILE_BACKUP "$CADDY_BACKUP"
  warn "An existing /etc/caddy/Caddyfile was saved as $CADDY_BACKUP."
fi
$CADDY_PREEXISTING && manifest_set CADDY_WAS_ACTIVE active \
                   || manifest_set CADDY_WAS_ACTIVE inactive
sed "s|127\.0\.0\.1:8765|127.0.0.1:${WEB_PORT}|g" \
  "$GAMECORE_PATH/install/system/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable caddy.service
# restart, not `enable --now`: on a re-run caddy is already active and
# `--now` is a no-op, so it would keep serving the previous config.
systemctl restart caddy.service || warn "caddy failed to start — check /etc/caddy/Caddyfile"
# Install Caddy's root CA into the system trust store so the box itself
# (kiosk browser, Firefox) gets no TLS warning. `caddy trust` needs the
# admin API of the freshly started service — retry while it comes up.
CADDY_TRUSTED=0
for _ in $(seq 1 15); do
  if caddy trust >/dev/null 2>&1; then CADDY_TRUSTED=1; break; fi
  sleep 1
done
if [[ $CADDY_TRUSTED -eq 1 ]]; then
  ok "Caddy up on :8443 — root CA installed in the system trust store."
else
  warn "Caddy started but 'caddy trust' failed — run 'sudo caddy trust' once."
fi

# ── mDNS — how the LAN finds the port Caddy just opened ──────────
# Without this the box is reachable only by IP, and the IP comes from DHCP: the
# address written on a sticky note stops working at the next lease. Everything a
# phone or laptop reaches — https://…:8443/roms/, /saves/, /rpcs3/ — is behind
# that one address.
#
# Unlike networkmanager (see the header: the distro is expected to bring it),
# avahi is installed by GameCore rather than assumed. Three reasons, and this is
# the paragraph to read before deciding otherwise:
#
#   1. nmcli is a dependency GameCore CONSUMES; mDNS is a capability it ADDS.
#      Nothing else on the box wants it.
#   2. Its absence is SILENT. `nmcli: not found` shows up in Settings → Wi-Fi;
#      a name that does not resolve produces no message anywhere, and cannot
#      even be observed from the box itself.
#   3. No distro gets this right by default. Arch ships neither package; Manjaro
#      ships avahi and leaves the unit disabled — which is exactly the state the
#      production box was found in ("Daemon not running" in the journal).
msg "mDNS (avahi) — reaching the box by name"
systemctl enable --now avahi-daemon.service 2>/dev/null \
  && ok "avahi-daemon enabled — the box answers to $(uname -n).local." \
  || warn "avahi-daemon failed to start — the box stays reachable by IP only."

# nss-mdns being INSTALLED resolves nothing. glibc only consults it when
# nsswitch.conf names it, and the package does not edit that file. Skipping this
# leaves a running daemon that advertises correctly and a box that still cannot
# be reached by name — a failure indistinguishable from the service being off,
# which is why it gets its own step rather than a line inside the one above.
#
# Surgical, not templated: the hosts: line differs between Arch and Manjaro and
# grows entries over time (mymachines, resolve, myhostname). Overwriting it with
# a known-good line is how you delete the one entry that box needed. So the
# entry is inserted before the first resolver that would answer NXDOMAIN for a
# .local name, and re-running is a no-op.
NSS=/etc/nsswitch.conf
if [[ ! -f $NSS ]]; then
  warn "$NSS does not exist — mDNS name resolution not wired up."
elif grep -qE '^\s*hosts:.*\bmdns' "$NSS"; then
  ok "nsswitch.conf already consults mDNS."
else
  NSS_BACKUP="${NSS}.pre-gamecore"
  [[ -e $NSS_BACKUP ]] || cp "$NSS" "$NSS_BACKUP"
  manifest_set NSSWITCH_BACKUP "$NSS_BACKUP"
  # [NOTFOUND=return] stops the lookup for .local only — mdns_minimal answers
  # nothing else, so ordinary DNS below is untouched. Without the guard a slow
  # or absent mDNS responder delays every failed lookup on the box.
  #
  # Inserted before the FIRST of resolve/dns, and awk rather than sed because
  # that word matters: ERE has no lazy quantifier, so the obvious
  # `s/(hosts:.*)(resolve|dns)/` matches greedily and lands the entry AFTER
  # resolve. systemd-resolved then answers .local first and mdns_minimal is
  # never consulted — the file looks patched and nothing resolves.
  if awk '
      /^[[:space:]]*hosts:/ && !done {
        for (i = 2; i <= NF; i++)
          if ($i == "resolve" || $i == "dns") { at = i; break }
        if (!at) at = NF + 1              # neither present: append at the end
        line = $1
        for (i = 2; i < at; i++) line = line " " $i
        line = line " mdns_minimal [NOTFOUND=return]"
        for (i = at; i <= NF; i++) line = line " " $i
        print line; done = 1; next
      }
      { print }
    ' "$NSS" > "${NSS}.gamecore-new" \
     && grep -qE '^\s*hosts:.*\bmdns_minimal' "${NSS}.gamecore-new" \
     && cat "${NSS}.gamecore-new" > "$NSS"; then
    rm -f "${NSS}.gamecore-new"
    ok "nsswitch.conf now consults mDNS (backup: $NSS_BACKUP)."
  else
    # Restore rather than leave a half-edited nsswitch.conf: a malformed hosts:
    # line breaks name resolution for the WHOLE box, not just .local.
    rm -f "${NSS}.gamecore-new"
    cp "$NSS_BACKUP" "$NSS"
    warn "Could not add mdns_minimal to $NSS — left unchanged. The box stays"
    warn "  reachable by IP; add it by hand to resolve $(uname -n).local."
  fi
fi

# ── Bluetooth ────────────────────────────────────────────────────
progress 92 "Bluetooth, power & desktop launcher"
msg "Bluetooth"
systemctl enable --now bluetooth.service
ok "Bluetooth service enabled."

# ── Power management (reboot/shutdown from UI) ───────────────────
msg "Sudoers — power management"
cat > /etc/sudoers.d/gamecore-power <<EOF
$USER_NAME ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot
# Gamepad hotplug (backend/services/launch.py) — the only udevadm this needs.
# Enumerated like the governor rule below: unrestricted, it also granted
# "udevadm control", which reloads and can replace the device rules.
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/udevadm trigger
# Desktop escape hatch — turn the kiosk on or off over the machine's desktop.
# Enumerated with its arguments rather than left open: the script writes an
# SDDM drop-in as root, so "any argument" is not a thing to hand out.
# The console session's own way out, "desktop --restart-dm", is a separate
# command line and lives in /etc/sudoers.d/gamecore-session, written by
# install/steps/setup-gamecore-session.sh so that updates carry it too.
$USER_NAME ALL=(root) NOPASSWD: /usr/local/bin/gamecore-session-select gamecore, /usr/local/bin/gamecore-session-select desktop
# Desktop launcher (gamecore-launcher.sh) — start GameCore from the desktop
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start gamecore-backend.service, /usr/bin/systemctl start gamecore-ui.service
# Standby (backend/services/standby.py) — drop the governor while the screen
# is off. Enumerated, not wildcarded: only the two governors GameCore uses.
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/cpupower frequency-set -g powersave, /usr/bin/cpupower frequency-set -g performance
EOF
chmod 440 /etc/sudoers.d/gamecore-power
if visudo -cf /etc/sudoers.d/gamecore-power >/dev/null; then
  ok "Sudoers rules created (power + udevadm + governor + GameCore start for $USER_NAME)."
else
  rm -f /etc/sudoers.d/gamecore-power
  warn "sudoers validation failed — power menu and standby governor will not work."
fi

# ── Desktop launcher (clickable "GameCore" icon) ─────────────────
msg "Desktop launcher"
chmod +x "$GAMECORE_PATH/install/bin/gamecore-launcher"
DESKTOP_DIR=$(sudo -u "$USER_NAME" bash -lc 'xdg-user-dir DESKTOP 2>/dev/null' || true)
[[ -n "$DESKTOP_DIR" && -d "$DESKTOP_DIR" ]] || DESKTOP_DIR="$USER_HOME/Desktop"
APPS_DIR="$USER_HOME/.local/share/applications"
sudo -u "$USER_NAME" mkdir -p "$DESKTOP_DIR" "$APPS_DIR"
# The GameCore logo, not the theme's generic gamepad. It is the file the
# frontend bundles (frontend/src/assets/logo.png), which the OTA keeps in sync
# under the install — a code path, stable across updates. The generic icon
# stays as the fallback for a tree that somehow lacks it.
LAUNCHER_ICON="$(launcher_icon "$GAMECORE_PATH")"
LAUNCHER_DESKTOP="[Desktop Entry]
Type=Application
Name=GameCore
Comment=Lancer l'interface GameCore
Exec=$GAMECORE_PATH/install/bin/gamecore-launcher
Icon=$LAUNCHER_ICON
Terminal=true
Categories=Game;"
echo "$LAUNCHER_DESKTOP" | sudo -u "$USER_NAME" tee "$APPS_DIR/gamecore.desktop" >/dev/null
echo "$LAUNCHER_DESKTOP" | sudo -u "$USER_NAME" tee "$DESKTOP_DIR/GameCore.desktop" >/dev/null
sudo -u "$USER_NAME" chmod +x "$DESKTOP_DIR/GameCore.desktop"
# KDE: mark the desktop file as trusted so it runs on click without a warning
sudo -u "$USER_NAME" gio set "$DESKTOP_DIR/GameCore.desktop" metadata::trusted true 2>/dev/null || true
ok "Desktop launcher installed ($DESKTOP_DIR/GameCore.desktop)."

# OTA update: detached restart unit + narrow sudoers rule
bash "$GAMECORE_PATH/install/steps/setup-update-permissions.sh" "$USER_NAME" \
  && ok "OTA restart permissions installed." || warn "OTA restart setup failed."

# ── SSH ──────────────────────────────────────────────────────────
msg "SSH"
systemctl enable --now sshd
ok "SSH active."

# ── Node / frontend + Electron ───────────────────────────────────
# Deliberately the LAST thing before addons. Both steps pull ~150 MB
# from the network (npm registry + the Electron binary from GitHub) and
# are by far the most likely to fail on a flaky connection. When they ran
# earlier, a single failed download aborted the script under `set -e`
# before a single systemd unit, sudoers rule, udev rule, Caddyfile or
# autologin drop-in existed — leaving a machine with packages installed
# and nothing wired up. Now everything system-facing is already in place
# and a re-run only has to redo this part.
# ── Node / frontend ──────────────────────────────────────────────
progress 93 "Building the frontend"
msg "Node frontend build"
cd "$GAMECORE_PATH/frontend"
sudo -u "$USER_NAME" -H npm install
# Built rather than trusted: the sources were just copied over the top of any
# existing dist/ by the file-copy step, and a dist/ older than the sources
# beside it is the exact failure the OTA packaging comment warns about.
sudo -u "$USER_NAME" -H npm run build
cd "$SCRIPT_DIR"
ok "Frontend built → frontend/dist/"

# ── Electron ─────────────────────────────────────────────────────
progress 96 "Electron shell"
msg "Electron shell"
cd "$GAMECORE_PATH/electron"
# `|| warn`, not a hard failure: the electron package downloads a ~100 MB
# binary from GitHub in its postinstall, and a transient failure there used to
# abort the install. The explicit provisioning below exists precisely to
# recover from a missing binary — so let control reach it.
ELECTRON_DIR="$GAMECORE_PATH/electron/node_modules/electron"
sudo -u "$USER_NAME" -H npm install \
  || warn "npm install reported an error — trying explicit Electron provisioning."

# The electron npm package downloads its actual binary from a postinstall
# script (node install.js). On machines with hardened npm (ignore-scripts,
# @lavamoat/allow-scripts, …) that step is silently skipped, leaving
# node_modules/electron with no binary → "Electron failed to install
# correctly" at runtime. Provision the binary explicitly so the install never
# depends on the postinstall running.
if [[ ! -x "$ELECTRON_DIR/dist/electron" ]]; then
  warn "Electron binary missing (npm postinstall was skipped) — downloading it directly."
  # `EV=$(…)` under set -e exits on failure BEFORE its own `|| die` can run, so
  # the version lookup is guarded rather than chained. When npm died early
  # there is no node_modules/electron/package.json at all: fall back to the
  # major pinned in electron/package.json.
  EV=""
  if [[ -f "$ELECTRON_DIR/package.json" ]]; then
    EV="$(sudo -u "$USER_NAME" -H node -p "require('$ELECTRON_DIR/package.json').version" 2>/dev/null || true)"
  fi
  if [[ -z "$EV" ]]; then
    EV="$(python3 -c '
import json, re, sys
spec = json.load(open(sys.argv[1]))["dependencies"]["electron"]
m = re.search(r"[0-9][0-9.]*", spec)
print(m.group(0) if m else "")
' "$GAMECORE_PATH/electron/package.json" 2>/dev/null || true)"
    [[ -n "$EV" ]] && warn "Electron version taken from package.json: $EV"
  fi
  [[ -n "$EV" ]] || die "Could not determine the Electron version — re-run the installer once the network is back."
  case "$(uname -m)" in
    x86_64)  EARCH=x64 ;;
    aarch64) EARCH=arm64 ;;
    armv7l)  EARCH=armv7l ;;
    *)       EARCH=x64 ;;
  esac
  EZIP="electron-v${EV}-linux-${EARCH}.zip"
  EURL="https://github.com/electron/electron/releases/download/v${EV}/${EZIP}"
  TMPDIR_E="$(mktemp -d)"
  info "Downloading $EZIP …"
  # Download AND extract as root (root owns TMPDIR_E); chown the result to the
  # user afterwards. Doing the extract as the user fails because it cannot read
  # root's mktemp dir.
  if curl -fL --connect-timeout 15 --speed-limit 1024 --speed-time 30 -o "$TMPDIR_E/$EZIP" "$EURL"; then
    mkdir -p "$ELECTRON_DIR/dist"
    if bsdtar -xf "$TMPDIR_E/$EZIP" -C "$ELECTRON_DIR/dist" 2>/dev/null \
       || unzip -oq "$TMPDIR_E/$EZIP" -d "$ELECTRON_DIR/dist"; then
      # printf (not echo) — a trailing newline in path.txt makes Electron spawn
      # "…/dist/electron\n" → ENOENT.
      printf electron > "$ELECTRON_DIR/path.txt"
      chown -R "$USER_NAME:$USER_NAME" "$ELECTRON_DIR"
      ok "Electron $EV binary installed → dist/."
    else
      die "Failed to extract the Electron binary (install 'libarchive' for bsdtar, or 'unzip')."
    fi
  else
    die "Failed to download Electron $EV from GitHub — check the machine's network."
  fi
  rm -rf "$TMPDIR_E"
fi

# start-ui.sh execs node_modules/.bin/electron. That symlink is created by npm,
# so it is missing exactly when the binary had to be provisioned by hand above —
# the unit would then fail with "No such file or directory" on every boot.
if [[ -x "$ELECTRON_DIR/cli.js" || -f "$ELECTRON_DIR/cli.js" ]] \
   && [[ ! -e "$GAMECORE_PATH/electron/node_modules/.bin/electron" ]]; then
  mkdir -p "$GAMECORE_PATH/electron/node_modules/.bin"
  ln -sf ../electron/cli.js "$GAMECORE_PATH/electron/node_modules/.bin/electron"
  chmod +x "$ELECTRON_DIR/cli.js"
  chown -h "$USER_NAME:$USER_NAME" "$GAMECORE_PATH/electron/node_modules/.bin/electron"
  ok "node_modules/.bin/electron symlink restored."
fi

# chrome-sandbox must be a root-owned SUID binary, otherwise Electron refuses
# to start under an unprivileged user on some setups. Do this AFTER the
# chown -R above, which would otherwise hand it back to the user.
if [[ -f "$ELECTRON_DIR/dist/chrome-sandbox" ]]; then
  chown root:root "$ELECTRON_DIR/dist/chrome-sandbox"
  chmod 4755 "$ELECTRON_DIR/dist/chrome-sandbox"
fi
cd "$SCRIPT_DIR"
ok "Electron dependencies installed."

# ── Addons ───────────────────────────────────────────────────────
# Selected gamecore-addons modules (rom-manager by default — everyone
# wants the browser ROM upload). Each runs as a user-level service.
if [[ -n "$ADDONS" ]]; then
  progress 98 "Addons ($ADDONS)"
  msg "Addons ($ADDONS)"
  USER_UID=$(id -u "$USER_NAME")
  if loginctl show-user "$USER_NAME" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    grep -q '^LINGER_ENABLED=' "$MANIFEST" 2>/dev/null || manifest_owned LINGER_ENABLED 0
  else
    loginctl enable-linger "$USER_NAME" 2>/dev/null && manifest_owned LINGER_ENABLED 1 || true
  fi
  # systemctl --user needs the user manager's bus — wait for it briefly
  for _ in $(seq 1 10); do [ -S "/run/user/$USER_UID/bus" ] && break; sleep 1; done
  # From /, not from wherever the installer was started: the GUI runs arch.sh
  # through pkexec, whose working directory is /root, and git refuses to work
  # in a directory the player cannot read ("Invalid path '/root/...'"), so the
  # addon clone failed on every graphical install.
  cd /
  for addon in $ADDONS; do
    if sudo -u "$USER_NAME" \
         env GAMECORE_PATH="$GAMECORE_PATH" GAMECORE_DATA="$GAMECORE_DATA" \
             GAMECORE_BACKEND_PORT="$WEB_PORT" \
             XDG_RUNTIME_DIR="/run/user/$USER_UID" \
         /usr/local/bin/gamecore-addon install "$addon"; then
      ok "addon '$addon' installed."
    else
      warn "addon '$addon' failed — run later: gamecore-addon install $addon"
    fi
  done
fi

# ── Final summary ────────────────────────────────────────────────
progress 100 "Installation complete"
echo
echo -e "${BLU}╔══════════════════════════════════════╗${RST}"
echo -e "${BLU}║     Installation complete!           ║${RST}"
echo -e "${BLU}╚══════════════════════════════════════╝${RST}"
echo
ok "Web (LAN)     → https://${LOCAL_IP}:8443/roms  (login required)"
ok "Backend API   → http://127.0.0.1:${WEB_PORT}  (loopback only)"
ok "SSH           → ssh ${USER_NAME}@${LOCAL_IP}"
echo
echo -e "${YLW}  Next steps:${RST}"
echo "  1. Reboot — GameCore launches automatically."
echo "  2. Upload ROMs at https://${LOCAL_IP}:8443/roms  (drag & drop)"
echo "  3. Only manual step left: copy BIOS/firmwares (PS1/PS2/PS3, DS/3DS, Switch keys)."
echo
if [[ ! -f /usr/share/xsessions/gamecore.desktop ]]; then
  warn "The console session was not installed — GameCore will NOT start after the reboot."
  warn "  Re-run:  sudo bash install/arch.sh"
  echo
elif [[ -z "${DESKTOP_SESSION:-}" ]]; then
  warn "No X11 desktop is installed besides GameCore's own session."
  warn "  'sudo gamecore-session-select desktop' will have nowhere to go."
  warn "  If you want that way out:  sudo pacman -S plasma-desktop"
  echo
fi
if $NVIDIA_REBOOT_NEEDED; then
  warn "NVIDIA driver installed — the reboot is mandatory before the X11 session works."
  echo
fi
# Surfaced here on purpose: these warnings scroll past in the middle of a very
# long log, and the only other symptom is a tile that has quietly disappeared
# from the grid.
#
# Generic, and it has to be: this used to be one hand-written block per
# emulator, naming DuckStation and Xenia. A third one that failed to download
# said nothing at all here — the same shape of bug as the `for _id in
# duckstation xenia` loop this replaced. The provider already said what broke
# and what to do about it; this only makes sure it is the last thing read.
if [ ${#EMU_FAILED[@]} -gt 0 ] || [ ${#APP_FAILED[@]} -gt 0 ]; then
  warn "Not everything selected was installed — those tiles are missing from the grid:"
  for _f in ${EMU_FAILED[@]+"${EMU_FAILED[@]}"} ${APP_FAILED[@]+"${APP_FAILED[@]}"}; do
    warn "  · $_f"
  done
  warn "  Re-running this installer retries them; nothing else is affected."
  echo
fi
echo -e "${YLW}  To remove GameCore later:${RST}"
echo "  sudo bash $GAMECORE_PATH/install/uninstall.sh --dry-run   # see what would go"
echo "  sudo bash $GAMECORE_PATH/install/uninstall.sh             # ROMs and config kept"
echo
