"""Installer steps around the console session: code and data roots, session.env,
the web password, automatic sign-in, Wi-Fi location access.

Files go to tmp_path; registry, ACL, LSA and prompt calls are fakes that note
their arguments (backend/tests/powershell_harness.py).
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell


# -- session.env ---------------------------------------------------------------

def test_session_env_carries_both_roots_the_port_and_the_scraper_keys(tmp_path):
    s = settings_ps(tmp_path, Config={"TGDB_API_KEY": "tgdb", "SS_USER": "p4v1c", "WEB_PASSWORD": "x"})
    result = run_ps(tmp_path, f"$GcTest['text'] = Get-GcSessionEnvText -Settings {s}")
    lines = result["text"].splitlines()
    assert f"GAMECORE_PATH={tmp_path / 'code'}" in lines
    assert f"GAMECORE_DATA={tmp_path / 'data'}" in lines
    assert "GAMECORE_BACKEND_PORT=8765" in lines
    assert "THEGAMESDB_API_KEY=tgdb" in lines and "SCREENSCRAPER_USER=p4v1c" in lines
    assert not any("WEB_PASSWORD" in line or "x" == line.split("=")[-1] for line in lines)


def test_a_key_left_empty_in_install_conf_keeps_the_one_already_written(tmp_path):
    result = run_ps(tmp_path, rf"""
        $GcTest['text'] = Get-GcSessionEnvText -Settings {settings_ps(tmp_path)} -Existing @{{ SCREENSCRAPER_DEV_ID = 'kept' }}
    """)
    assert "SCREENSCRAPER_DEV_ID=kept" in result["text"].splitlines()


def test_a_key_with_a_line_break_is_refused(tmp_path):
    s = settings_ps(tmp_path, Config={"SS_USER": "a`nGAMECORE_DATA=C:\\evil"})
    result = run_ps(tmp_path, f"""
        try {{ Get-GcSessionEnvText -Settings {s} | Out-Null }} catch {{ $GcTest['error'] = $_.Exception.Message }}
    """.replace("'a`n", '"a`n').replace("evil'", 'evil"'))
    assert "SS_USER" in result["error"]


def test_session_env_is_written_admin_only_and_read_back_by_the_launcher(tmp_path):
    result = run_ps(tmp_path, rf"""
        function Protect-GcAdminOnly {{ param($Path) Note 'protect' $Path }}
        Install-GcSessionEnv {settings_ps(tmp_path)}
        Install-GcSessionEnv {settings_ps(tmp_path)}
    """)
    path = tmp_path / "state" / "session.env"
    assert calls(result, "protect") == [str(path)]          # the second run found nothing to change
    from backend.tests.test_gamecore_session import gs
    settings = gs.load_settings(path, {})
    assert (settings.root, settings.data, settings.port) == (tmp_path / "code", tmp_path / "data", "8765")


# -- data root -----------------------------------------------------------------

DATA_FAKES = r"""
    function Test-GcUserCanModify { param($Path, $User) $false }
    function Grant-GcModify { param($Path, $User) Note 'grant' "$User|$Path" }
"""


def test_the_data_root_gets_its_folders_the_themes_and_the_console_users_write_access(tmp_path):
    result = run_ps(tmp_path, DATA_FAKES + f"Install-GcDataRoot {settings_ps(tmp_path)}")
    data = tmp_path / "data"
    for folder in ("config", "emu/covers", "logs"):
        assert (data / folder).is_dir()
    assert (data / "config" / "themes" / "shelf" / "theme.json").exists()
    assert calls(result, "grant") == [f"player|{data}"]
    assert result["manifest"]["create-data-root"] == {"created": str(data)}
    assert not (data / "config" / "systems.json").exists()     # the Linux packs' tiles stay out


def test_a_theme_folder_the_player_filled_is_left_alone(tmp_path):
    mine = tmp_path / "data" / "config" / "themes" / "shelf"
    mine.mkdir(parents=True)
    (mine / "theme.json").write_text("{\"mine\": true}", encoding="utf-8")
    run_ps(tmp_path, DATA_FAKES + f"Install-GcDataRoot {settings_ps(tmp_path)}")
    assert (mine / "theme.json").read_text(encoding="utf-8") == "{\"mine\": true}"
    assert not (mine / "index.js").exists()


def test_uninstall_never_touches_the_data_root(tmp_path):
    (tmp_path / "data" / "emu").mkdir(parents=True)
    run_ps(tmp_path, f"Uninstall-GcDataRoot -Settings {settings_ps(tmp_path)}")
    assert (tmp_path / "data" / "emu").is_dir()


# -- code root -----------------------------------------------------------------

CODE_FAKES = r"""
    function Test-GcInterfaceBuilt { param($SourceRoot) $true }
    function Copy-GcTree { param($From, $To, $ExcludeDirs) Note 'copy' @{ from = $From; to = $To; exclude = @($ExcludeDirs) } }
"""


def test_the_code_root_gets_the_tree_and_the_built_shell(tmp_path):
    result = run_ps(tmp_path, CODE_FAKES + f"Install-GcCodeRoot {settings_ps(tmp_path)}")
    tree, shell = calls(result, "copy")
    code = str(tmp_path / "code")
    assert tree["to"] == code and shell["to"] == code + "\\shell"
    assert shell["from"].endswith("electron\\dist\\win-unpacked")
    excluded = {e.split("\\")[-1] for e in tree["exclude"]}
    assert {".git", ".venv", "node_modules", "config", "emu"} <= excluded
    assert result["manifest"]["copy-code-root"] == {"created": code}


def test_running_from_the_code_root_copies_nothing(tmp_path):
    from backend.tests.powershell_harness import REPO
    result = run_ps(tmp_path, CODE_FAKES + f"Install-GcCodeRoot {settings_ps(tmp_path, CodeRoot=str(REPO))}")
    assert calls(result, "copy") == []


def test_a_code_root_that_was_there_before_is_not_removed(tmp_path):
    (tmp_path / "code").mkdir()
    run_ps(tmp_path, "Uninstall-GcCodeRoot @{}")
    assert (tmp_path / "code").is_dir()
    run_ps(tmp_path, f"Uninstall-GcCodeRoot @{{ created = '{tmp_path / 'code'}' }}")
    assert not (tmp_path / "code").exists()


# -- web password --------------------------------------------------------------

PASSWORD_FAKES = r"""
    function Set-GcBackendPassword { param($Settings, $Password) Note 'set' $Password }
    function Read-GcNewPassword { Note 'asked' $true; 'typed' }
"""


def test_an_existing_web_password_is_kept(tmp_path):
    (tmp_path / "data" / "config").mkdir(parents=True)
    (tmp_path / "data" / "config" / "auth.json").write_text("{}", encoding="utf-8")
    result = run_ps(tmp_path, PASSWORD_FAKES + f"Install-GcWebPassword {settings_ps(tmp_path)}")
    assert result["calls"] == []


def test_unattended_without_a_password_warns_instead_of_asking(tmp_path):
    result = run_ps(tmp_path, PASSWORD_FAKES + f"Install-GcWebPassword {settings_ps(tmp_path)} -Unattended")
    assert result["calls"] == []
    assert "WEB_PASSWORD" in result["warnings"][0]


def test_the_password_from_install_conf_or_the_prompt_reaches_the_backend(tmp_path):
    conf = settings_ps(tmp_path, Config={"WEB_PASSWORD": "from-conf"})
    result = run_ps(tmp_path, PASSWORD_FAKES + f"Install-GcWebPassword {conf} -Unattended")
    assert calls(result, "set") == ["from-conf"]
    result = run_ps(tmp_path, PASSWORD_FAKES + f"Install-GcWebPassword {settings_ps(tmp_path)}")
    assert calls(result, "asked") == [True] and calls(result, "set") == ["typed"]


def test_the_password_is_handed_over_by_environment_never_on_the_command_line(tmp_path):
    result = run_ps(tmp_path, rf"""
        function Invoke-GcNative {{ param($FilePath, $ArgumentList)
            Note 'argv' "$FilePath $($ArgumentList -join ' ')"; Note 'env' $env:GAMECORE_WEB_PASSWORD }}
        Set-GcBackendPassword -Settings {settings_ps(tmp_path)} -Password 's3cret pass'
        $GcTest['after'] = $env:GAMECORE_WEB_PASSWORD
    """)
    [argv] = calls(result, "argv")
    assert "s3cret" not in argv and argv.startswith(str(tmp_path / "code" / ".venv" / "Scripts" / "python.exe"))
    assert calls(result, "env") == ["s3cret pass"]
    assert result["after"] is None


# -- automatic sign-in ----------------------------------------------------------

AUTOLOGON_FAKES = r"""
    $script:reg = @{ AutoAdminLogon = '0'; DefaultUserName = 'old' }
    function Get-GcRegistryValue { param($Path, $Name) if ($script:reg.ContainsKey($Name)) { $script:reg[$Name] } }
    function Set-GcRegistryValue { param($Path, $Name, $Value, $Type) Note 'set' "$Name=$Value"; $script:reg[$Name] = $Value }
    function Restore-GcRegistryValue { param($Path, $Name, $Previous, $Type) Note 'restore' "$Name=$Previous" }
    function Remove-ItemProperty { param($LiteralPath, $Name, $ErrorAction) Note 'remove' $Name }
    function Set-GcLsaSecret { param($Name, $Value) Note 'lsa' @{ name = $Name; value = $Value } }
    function Read-GcLogonPassword { param($User) Note 'asked' $User; 'pw' }
"""


def test_autologon_stores_the_password_as_an_lsa_secret_never_in_the_registry(tmp_path):
    result = run_ps(tmp_path, AUTOLOGON_FAKES + f"Install-GcAutologon {settings_ps(tmp_path)}")
    assert calls(result, "asked") == ["player"]
    assert calls(result, "lsa") == [{"name": "DefaultPassword", "value": "pw"}]
    assert calls(result, "remove") == ["DefaultPassword"]
    assert set(calls(result, "set")) >= {"DefaultUserName=player", "AutoAdminLogon=1",
                                         "DevicePasswordLessBuildVersion=0"}
    assert all("pw" not in c for c in calls(result, "set"))
    record = result["manifest"]["configure-autologon"]
    assert record["AutoAdminLogon"] == "0" and record["DefaultUserName"] == "old"
    assert "pw" not in str(result["manifest"])


def test_autologon_unattended_leaves_it_to_the_owner(tmp_path):
    result = run_ps(tmp_path, AUTOLOGON_FAKES + f"Install-GcAutologon {settings_ps(tmp_path)} -Unattended")
    assert result["calls"] == []
    assert "-Only configure-autologon" in result["warnings"][0]


def test_autologon_already_on_for_this_user_asks_nothing(tmp_path):
    result = run_ps(tmp_path, AUTOLOGON_FAKES + rf"""
        $script:reg = @{{ AutoAdminLogon = '1'; DefaultUserName = 'Player' }}
        Install-GcAutologon {settings_ps(tmp_path)}
    """)
    assert result["calls"] == []


def test_autologon_off_in_install_conf_is_skipped(tmp_path):
    result = run_ps(tmp_path, AUTOLOGON_FAKES + f"Install-GcAutologon {settings_ps(tmp_path, Autologon=False)}")
    assert result["calls"] == []


def test_autologon_uninstall_deletes_the_secret_and_restores_the_values(tmp_path):
    result = run_ps(tmp_path, AUTOLOGON_FAKES + rf"""
        Uninstall-GcAutologon @{{ AutoAdminLogon = '0'; DefaultUserName = 'old'; DefaultDomainName = $null
            DevicePasswordLessBuildVersion = 2 }}
    """)
    assert calls(result, "lsa") == [{"name": "DefaultPassword", "value": None}]
    assert calls(result, "restore") == ["AutoAdminLogon=0", "DefaultUserName=old", "DefaultDomainName=",
                                        "DevicePasswordLessBuildVersion=2"]


# -- Wi-Fi location access -----------------------------------------------------

WIFI_FAKES = r"""
    function Get-GcRegistryValue { param($Path, $Name) 'Deny' }
    function Set-GcRegistryValue { param($Path, $Name, $Value, $Type) Note 'set' "$Path|$Value|$Type" }
"""


def test_before_24h2_location_access_is_left_alone(tmp_path):
    result = run_ps(tmp_path, WIFI_FAKES + f"Install-GcWifiLocation {settings_ps(tmp_path)} -Build 22631")
    assert result["calls"] == []


def test_on_24h2_desktop_apps_are_allowed_location_for_the_wifi_scan(tmp_path):
    s = settings_ps(tmp_path, ConsoleUser="$env:USERNAME").replace("'$env:USERNAME'", "$env:USERNAME")
    result = run_ps(tmp_path, WIFI_FAKES + f"Install-GcWifiLocation {s} -Build 26100")
    keys = [c.split("|")[0] for c in calls(result, "set")]
    assert keys[0].startswith("HKLM:") and keys[-1].endswith("\\location\\NonPackaged") and len(keys) == 3
    assert all(c.endswith("|Allow|String") for c in calls(result, "set"))
    assert set(result["manifest"]["set-wifi-location"].values()) == {"Deny"}


def test_location_for_another_user_is_left_to_them_with_the_setting_named(tmp_path):
    result = run_ps(tmp_path, WIFI_FAKES + f"Install-GcWifiLocation {settings_ps(tmp_path)} -Build 26100")
    assert result["calls"] == []
    assert "Let desktop apps access your location" in result["warnings"][0]
