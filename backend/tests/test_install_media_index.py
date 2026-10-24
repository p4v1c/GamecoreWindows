"""`install/steps/build-media-index.ps1`: the step that gives a fresh install a
metadata source at all.

The build is a fake that notes how it was called and may write the index;
nothing downloads 234 MB. What is pinned, each a failure that happened on the
Linux box or would never be noticed:

  · it never fails the install: a missing synopsis is a degraded box, an
    aborted install is a machine neither installed nor clean;
  · both roots reach gamescrape, so the index lands in the data root (on Linux
    it once landed in ~/.cache and the tier was silently off);
  · a second run does not download it again;
  · a build that says it worked and wrote nothing is caught.
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell


def _fakes(tmp_path, *, writes: bool = True, fails: bool = False) -> str:
    index = tmp_path / "data" / "emu" / "gamescrape" / "launchbox.sqlite"
    write = (f"New-Item -ItemType Directory -Force '{index.parent}' | Out-Null; "
             f"[IO.File]::WriteAllText('{index}', 'sqlite')") if writes else ""
    fail = "throw 'network down'" if fails else ""
    return rf"""
        New-Item -ItemType Directory -Force '{tmp_path / "code" / "backend" / "services" / "gamemedia"}' | Out-Null
        [IO.File]::WriteAllText('{tmp_path / "code" / "backend" / "services" / "gamemedia" / "gamescrape.py"}', '')
        function Invoke-GcNative {{ param($FilePath, $ArgumentList)
            Note 'build' @{{ argv = @($FilePath) + @($ArgumentList); path = $env:GAMECORE_PATH
                data = $env:GAMECORE_DATA; utf8 = $env:PYTHONUTF8 }}
            {fail}
            {write} }}
    """


def test_it_builds_the_index_into_the_data_root(tmp_path):
    result = run_ps(tmp_path, _fakes(tmp_path) + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    [build] = calls(result, "build")
    code = tmp_path / "code"
    assert build["argv"] == [str(code / ".venv" / "Scripts" / "python.exe"),
                             str(code / "backend" / "services" / "gamemedia" / "gamescrape.py"), "--refresh"]
    assert (build["path"], build["data"], build["utf8"]) == (str(code), str(tmp_path / "data"), "1")
    assert result["warnings"] == []


def test_a_second_run_does_not_download_it_again(tmp_path):
    run_ps(tmp_path, _fakes(tmp_path) + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    result = run_ps(tmp_path, _fakes(tmp_path) + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    assert calls(result, "build") == [] and result["changes"] == []


def test_a_failed_build_never_fails_the_install(tmp_path):
    result = run_ps(tmp_path, _fakes(tmp_path, fails=True) + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    assert "works without it" in result["warnings"][0]
    assert "-Only build-media-index" in result["warnings"][0]


def test_a_build_that_reports_success_and_writes_nothing_is_caught(tmp_path):
    result = run_ps(tmp_path, _fakes(tmp_path, writes=False) + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    assert "wrote no index" in result["warnings"][0]


def test_media_index_off_in_install_conf_skips_it(tmp_path):
    result = run_ps(tmp_path, _fakes(tmp_path) +
                    f"Install-GcMediaIndex {settings_ps(tmp_path, Config={'MEDIA_INDEX': '0'})}")
    assert calls(result, "build") == [] and result["warnings"] == []


def test_a_missing_gamescrape_is_reported_not_fatal(tmp_path):
    result = run_ps(tmp_path, f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    assert "gamescrape.py not found" in result["warnings"][0]


def test_too_little_disk_skips_it_and_says_how_to_retry(tmp_path):
    result = run_ps(tmp_path, _fakes(tmp_path) + "function Get-GcFreeMegabytes { param($Path) 120 }\n"
                    + f"Install-GcMediaIndex {settings_ps(tmp_path)}")
    assert calls(result, "build") == []
    assert "120 MB free" in result["warnings"][0]
