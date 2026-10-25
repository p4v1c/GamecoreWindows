# Windows port — Linux-only tests, by the step that removes them

Tests of Linux code the port has not replaced yet. Each is skipped on Windows
with its step named (`linux_until_port`, [`../TESTING.md`](../TESTING.md)) and is
**deleted with that code** in that step's PR, row included.
`backend/tests/test_linux_until_port.py` fails when this table and the source
disagree.

Not here: tests of Linux code that only reads `os.getuid`/`os.geteuid`
(the installer), which run on Windows through the `posix_boundary` fixture
(`backend/tests/posix_boundary.py`) until the packs are rewritten. Step 3 removed its process half (`os.killpg`, the signals): the
process tests now run on Job Objects.

Measured on 25/10/2026 (step 10), by step. Step 7
removed its installer and console-session items with their code (`arch.sh`,
`uninstall.sh`, the X/SDDM session scripts) or replaced them
(`test_gamecore_session.py`, `test_install_media_index.py`); the Linux packs'
items moved to 11. Step 8 removed its 24 items with `update/linux.sh` and the
sudoers step, or ported them (`test_update.py`, `test_ota_merge_roots.py`).
Step 9 ported its 9 items with the CLI (`test_addon_contract.py`, now run
against the Python CLI and real PowerShell hooks). Step 10 removed
`test_migration.py` with `scripts/migrate-userdata.py` (replaced by
`scripts/import-linux-data.py`) and ported `split-systems.py`'s dry-run test
(no `geteuid` on Windows). 142 items in 20 files remain, all under 11:

| Step | What replaces the Linux code | Items | What they need |
|---|---|---|---|
| 11 | not a port step: the Linux packs and their install pipeline, rewritten for Windows after the port (owner, 19/10) | 142 | Flatpak, uinput, `fcntl`, `/proc`, systemd user units, the bash `gamecore-emu` CLI |

"all (N)" marks a whole file (`pytestmark`, or `linux_until_port_module` for
the files Windows cannot import); otherwise the marked tests are named and the
rest of the file runs on Windows.

| Step | File | Marked tests | Linux mechanism |
|---|---|---|---|
| 11 | `backend/tests/test_configgen_sdl2_probe.py` | `test_two_installed_branches_resolve_to_the_one_flatpak_run_starts` | Flatpak `current` symlink: the Linux packs' emulator lookup, kept until the packs are rewritten |
| 11 | `backend/tests/test_catalog_consumers.py` | `test_the_prune_resolves_the_token_instead_of_reading_it_as_an_app_id`, `test_the_prune_still_drops_a_tile_whose_whole_list_is_absent`, `test_the_shell_side_sandbox_grants_the_data_root_too` | Flatpak sandbox flags, bash `flatpakify-systems.sh` |
| 11 | `backend/tests/test_emu_cli_data_root.py` | all (6) | bash `gamecore-emu` CLI, `systemctl` |
| 11 | `backend/tests/test_host_access_gamemode.py` | all (3) | Unix groups, `geteuid` |
| 11 | `backend/tests/test_installer_applier.py` | `test_tokens_expand_in_dest_and_in_templates`, `test_an_enabled_unit_is_installed_and_wanted_by_default_target`, `test_reinstalling_over_an_existing_symlink_is_not_an_error`, `test_a_step_that_hangs_is_bounded_by_its_timeout`, `test_a_step_receives_the_tokens_as_environment`, `test_steps_run_in_declaration_order` | systemd units, POSIX modes, steps run by bash |
| 11 | `backend/tests/test_installer_providers.py` | `test_the_emulator_policy_is_the_default`, `test_the_sandbox_is_told_where_the_data_is_when_it_has_moved` | Flatpak sandbox flags |
| 11 | `backend/tests/test_layout_packs.py` | `test_pack_installs_daemon_service_and_seed`, `test_provider_uses_named_users_home_not_roots`, `test_start_services_does_not_reinstall_the_emulator`, `test_service_restart_reloads_the_gaming_users_manager`, `test_ui_install_starts_each_daemon_after_its_configuration`, `test_general_installer_collects_emulator_services` | systemd user manager, `pwd`, bash |
| 11 | `backend/tests/test_newcomer_survives.py` | `test_the_installer_bridge_installs_the_newcomer` | `gamecore-provider.py` imports `pwd` |
| 11 | `catalog/lutris/tests/test_defaults.py` | all (9) | Linux pack: `fcntl` |
| 11 | `catalog/lutris/tests/test_fullscreen.py` | `test_the_lutris_tile_fullscreens_the_game_window` | Linux pack: X11 `WM_CLASS` matching; the Windows enforcer fullscreens the job's own window |
| 11 | `catalog/lutris/tests/test_proton.py` | `test_a_verified_build_lands_in_runners_wine` | Linux pack: symlinks in a Wine tree |
| 11 | `catalog/lutris/tests/test_session.py` | all (16) | Linux pack: `fcntl`, uinput |
| 11 | `catalog/lutris/tests/test_xpad.py` | all (15) | Linux pack: `fcntl`, uinput |
| 11 | `catalog/melonds/tests/test_layout_multiplayer.py` | `test_the_launcher_marks_melonds_with_the_player_count` | Linux pack: `/proc` environ |
| 11 | `catalog/melonds/tests/test_mouse_touch.py` | all (12) | Linux pack: `fcntl`, evdev, uinput |
| 11 | `catalog/melonds/tests/test_multiplayer_runtime.py` | all (12) | Linux pack: `fcntl`, AT-SPI |
| 11 | `catalog/melonds/tests/test_multiplayer_setup.py` | `test_device_ids_join_hidraw_and_event_nodes_of_one_pad` | Linux pack: sysfs hidraw |
| 11 | `catalog/melonds/tests/test_profile_saves.py` | `test_another_profile_points_player_one_saves_and_states_at_its_folder`, `test_switching_profiles_never_keeps_the_previous_folder`, `test_a_later_player_folder_creates_its_instance_section` | Linux pack: melonDS ini paths |
| 11 | `catalog/rpcs3/tests/test_smart_launch.py` | all (40) | Linux pack: `fcntl` |
| 11 | `catalog/switch/tests/test_eden_saves.py` | `test_a_profile_reads_its_own_eden_folder`, `test_eden_stopped_mid_profile_gives_the_primarys_parked_folder`, `test_a_profile_import_lands_in_the_profiles_folders` | Linux pack: symlinks in the Ryujinx tree |
