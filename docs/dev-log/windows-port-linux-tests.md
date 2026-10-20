# Windows port — Linux-only tests, by the step that removes them

Tests of Linux code the port has not replaced yet. Each is skipped on Windows
with its step named (`linux_until_port`, [`../TESTING.md`](../TESTING.md)) and is
**deleted with that code** in that step's PR, row included.
`backend/tests/test_linux_until_port.py` fails when this table and the source
disagree.

Not here: tests of Linux code that only reads `os.getuid`/`os.geteuid`
(Bluetooth, audio, installer), which run on Windows through the
`posix_boundary` fixture (`backend/tests/posix_boundary.py`) until steps 5
and 7. Step 3 removed its process half (`os.killpg`, the signals): the
process tests now run on Job Objects.

Measured on 20/10/2026 (step 4): 371 test items in 51 files, by step:

| Step | What replaces the Linux code | Items | What they need |
|---|---|---|---|
| 5 | pads (SDL), battery, storage, Bluetooth | 22 | sysfs trees whose names hold `:`; `lsblk`/`udisksctl` and `/run/media` links; an interactive `bluetoothctl` |
| 6 | Rest mode: sleep, wake by pad | 12 | bash helper over sysfs; sudoers and systemd units |
| 7 | installer, logon task, CLIs; the Linux packs until they are rewritten for Windows | 299 | 144 are the Linux packs (Flatpak, uinput, `fcntl`, `/proc`); the rest run `arch.sh`, the session scripts and the CLIs in bash against systemd, SDDM, sudoers |
| 8 | OTA updater and release workflow | 26 | `update/linux.sh` in bash, rsync, `systemctl` |
| 9 | addon CLI (`gamecore-addons-windows`) | 9 | the bash `gamecore-addon` CLI |
| 10 | Linux data migration scripts, replaced by `scripts/import-linux-data.py` | 3 | file symlinks, POSIX modes, `geteuid` in a child |

"all (N)" marks a whole file (`pytestmark`, or `linux_until_port_module` for
the files Windows cannot import); otherwise the marked tests are named and the
rest of the file runs on Windows.

| Step | File | Marked tests | Linux mechanism |
|---|---|---|---|
| 5 | `backend/tests/test_battery.py` | `test_a_mouse_battery_is_not_a_controller` | sysfs `power_supply` names hold `:` |
| 5 | `backend/tests/test_bluetooth_router.py` | `test_pairing_registers_an_agent_and_confirms_the_passkey` | pairing drives an interactive `bluetoothctl` session |
| 5 | `backend/tests/test_configgen_sdl2_probe.py` | `test_two_installed_branches_resolve_to_the_one_flatpak_run_starts` | Flatpak `current` symlink |
| 5 | `backend/tests/test_storage.py` | `test_the_link_points_at_wherever_the_disk_landed`, `test_replugging_repoints_the_link_instead_of_failing`, `test_a_link_left_over_from_a_previous_session_is_found`, `test_a_romspath_under_the_stable_link_resolves_onto_the_disk`, `test_the_scanner_reads_nothing_from_a_disk_that_has_gone`, `test_a_busy_disk_reports_why_rather_than_a_generic_failure` | `lsblk`/`udisksctl`, `/run/media` symlinks |
| 5 | `backend/tests/test_storage_monitor.py` | `test_a_disk_plugged_in_is_mounted_linked_and_announced`, `test_the_mount_point_is_re_read_rather_than_guessed`, `test_a_disk_that_arrives_already_mounted_still_gets_its_link`, `test_a_websocket_that_throws_does_not_stop_disks_being_noticed` | `lsblk`/`udisksctl`, `/run/media` symlinks |
| 5 | `backend/tests/test_storage_router.py` | `test_the_listing_describes_what_a_romspath_should_point_at` | `/run/media` symlinks |
| 5 | `backend/tests/test_sysinfo_router.py` | `test_a_laptop_battery_is_not_a_controller`, `test_a_model_name_is_preferred_over_the_sysfs_directory_name` | sysfs `power_supply` names hold `:` |
| 5 | `backend/tests/test_usb_devices.py` | `test_inventory_reads_devices_and_skips_their_interfaces`, `test_inventory_ignores_a_node_whose_ids_are_unreadable`, `test_declared_adapter_present_is_reported_present`, `test_declared_adapter_absent_is_reported_absent_with_the_packs_note`, `test_case_does_not_decide_whether_a_device_is_found`, `test_launch_notice_is_empty_when_the_device_is_there` | sysfs USB tree (`3-1:1.0`) |
| 6 | `backend/tests/test_rest_mode_install.py` | `test_the_step_installs_every_piece_and_enables_the_unit`, `test_the_sudoers_rule_grants_suspend_and_nothing_else`, `test_a_second_run_changes_nothing`, `test_the_ota_migration_path_installs_them_too` | sudoers, systemd units |
| 6 | `backend/tests/test_wake_setup_helper.py` | `test_the_adapter_and_the_pads_are_armed_and_nothing_else`, `test_the_helper_and_the_backend_agree_on_what_a_pad_is`, `test_a_second_run_writes_nothing`, `test_udev_mode_arms_one_device` | bash `gamecore-wake-setup`, sysfs |
| 7 | `backend/tests/test_boot_review_fixes.py` | `test_graphical_start_applies_only_the_confirmed_mode` | bash `gamecore-xsetup` (7) and `update/linux.sh` (8) |
| 7 | `backend/tests/test_catalog_consumers.py` | `test_the_prune_resolves_the_token_instead_of_reading_it_as_an_app_id`, `test_the_prune_still_drops_a_tile_whose_whole_list_is_absent`, `test_the_shell_side_sandbox_grants_the_data_root_too` | Flatpak sandbox flags, bash `flatpakify-systems.sh` |
| 7 | `backend/tests/test_console_session_lockdown.py` | `test_the_window_manager_is_given_a_configuration_of_the_sessions_own`, `test_alt_tab_and_the_meta_key_are_taken_away`, `test_the_window_manager_leaves_the_handovers_to_the_theme`, `test_openbox_is_given_a_keyboard_with_nothing_on_it`, `test_openbox_is_told_which_file_to_read`, `test_the_lockdown_is_reported_so_a_journal_can_show_it`, `test_the_auto_login_drop_in_is_readable_without_root`, `test_a_file_it_cannot_read_is_said_out_loud_rather_than_skipped`, `test_status_still_answers_when_everything_is_readable`, `test_the_launcher_can_arm_the_console_session`, `test_the_launcher_refuses_an_argument_it_does_not_know`, `test_the_return_path_is_granted_without_a_password` | X session scripts run by bash, SDDM, openbox/KWin |
| 7 | `backend/tests/test_desktop_trip.py` | `test_a_trip_points_the_display_manager_at_the_desktop`, `test_a_trip_leaves_something_to_come_back_by`, `test_a_trip_says_it_is_temporary`, `test_a_plain_desktop_switch_is_still_permanent`, `test_a_move_cancels_a_trip_that_had_not_happened_yet`, `test_arming_by_hand_settles_a_pending_trip`, `test_the_round_trip_ends_where_it_started`, `test_the_trip_is_granted_without_a_password`, `test_the_installer_puts_the_unit_where_it_will_run` | SDDM autologin, systemd units |
| 7 | `backend/tests/test_emu_cli_data_root.py` | all (6) | bash `gamecore-emu` CLI, `systemctl` |
| 7 | `backend/tests/test_gamecore_session.py` | `test_the_session_writes_its_environment_where_the_backend_can_read_it`, `test_the_user_manager_is_told_and_then_untold`, `test_it_starts_the_session_target_and_stops_it_on_the_way_out`, `test_without_a_display_it_says_so_instead_of_starting`, `test_a_compositor_that_exits_cleanly_ends_the_session`, `test_a_compositor_that_keeps_crashing_does_not_end_the_session`, `test_the_interface_stopping_ends_the_session`, `test_openbox_is_taken_when_there_is_no_kwin_for_x11`, `test_kwin_is_preferred_over_the_fallback`, `test_no_window_manager_at_all_is_said_out_loud_and_survivable` | bash `gamecore-session`, systemd user manager |
| 7 | `backend/tests/test_host_access_gamemode.py` | all (3) | Unix groups, `geteuid` |
| 7 | `backend/tests/test_install_media_index.py` | `test_it_builds_the_index_and_passes_gamecore_path`, `test_a_second_run_does_not_download_it_again`, `test_a_failed_build_never_fails_the_install`, `test_a_build_that_reports_success_and_writes_nothing_is_caught`, `test_the_skip_variable_is_honoured_and_says_how_to_undo_it`, `test_a_missing_gamescrape_is_reported_not_fatal` | bash `install/steps/build-media-index.sh` |
| 7 | `backend/tests/test_installer_applier.py` | `test_tokens_expand_in_dest_and_in_templates`, `test_an_enabled_unit_is_installed_and_wanted_by_default_target`, `test_reinstalling_over_an_existing_symlink_is_not_an_error`, `test_a_step_that_hangs_is_bounded_by_its_timeout`, `test_a_step_receives_the_tokens_as_environment`, `test_steps_run_in_declaration_order` | systemd units, POSIX modes, steps run by bash |
| 7 | `backend/tests/test_installer_gpu.py` | all (9) | `arch.sh` GPU driver choice (lspci, pacman) |
| 7 | `backend/tests/test_installer_manifest.py` | all (2) | `arch.sh` manifest |
| 7 | `backend/tests/test_installer_providers.py` | `test_the_emulator_policy_is_the_default`, `test_the_sandbox_is_told_where_the_data_is_when_it_has_moved` | Flatpak sandbox flags |
| 7 | `backend/tests/test_installer_secrets.py` | all (1) | systemd drop-in quoting |
| 7 | `backend/tests/test_installer_seeds_data_tree.py` | `test_a_fresh_split_install_gets_its_bezels_and_geometry`, `test_a_populated_directory_is_the_players_and_is_left_alone`, `test_the_old_layout_is_untouched_by_the_change`, `test_what_the_old_loop_did_on_a_split_box_which_is_nothing`, `test_the_shortcut_uses_the_shipped_logo_and_falls_back_to_the_theme_icon` | `arch.sh` functions run in bash |
| 7 | `backend/tests/test_layout_packs.py` | `test_pack_installs_daemon_service_and_seed`, `test_provider_uses_named_users_home_not_roots`, `test_start_services_does_not_reinstall_the_emulator`, `test_service_restart_reloads_the_gaming_users_manager`, `test_ui_install_starts_each_daemon_after_its_configuration`, `test_general_installer_collects_emulator_services` | systemd user manager, `pwd`, bash |
| 7 | `backend/tests/test_newcomer_survives.py` | `test_the_installer_bridge_installs_the_newcomer` | `gamecore-provider.py` imports `pwd` |
| 7 | `backend/tests/test_readonly_root.py` | all (5) | rsync + POSIX modes; Program Files ACLs come with the installer |
| 7 | `backend/tests/test_session_install.py` | `test_it_installs_the_session_the_entry_and_the_units`, `test_the_unit_carries_the_paths_the_operator_chose`, `test_running_it_twice_changes_nothing_the_second_time`, `test_a_missing_source_stops_rather_than_reporting_success`, `test_arming_the_console_session_points_the_autologin_at_it`, `test_going_back_to_the_desktop_names_a_session_that_exists`, `test_arming_a_box_that_was_never_migrated_refuses_and_says_how`, `test_leaving_with_no_desktop_installed_refuses_rather_than_stranding`, `test_arming_retires_the_legacy_kiosk_without_killing_the_current_session`, `test_preparation_installs_the_complete_ota_chain`, `test_preparation_keeps_a_file_by_file_undo`, `test_arming_without_a_window_manager_is_refused`, `test_arming_with_only_openbox_says_what_that_costs`, `test_leaving_returns_to_the_desktop_the_box_was_using`, `test_the_console_is_never_recorded_as_the_desktop_to_return_to`, `test_a_desktop_uninstalled_since_the_arming_is_not_used`, `test_a_box_whose_only_desktop_is_wayland_can_still_leave`, `test_the_backup_is_not_written_where_sddm_reads_it`, `test_a_stale_gamecore_file_that_would_win_is_retired`, `test_a_competing_file_we_may_not_touch_is_reported_and_fails`, `test_leaving_with_restart_dm_actually_restarts_the_display_manager`, `test_leaving_without_the_flag_still_restarts_nothing`, `test_a_switch_that_did_not_take_restarts_nothing`, `test_an_option_nobody_declared_is_refused`, `test_the_sudoers_rule_for_leaving_is_installed_by_the_update_step` | SDDM, X sessions, systemd |
| 7 | `backend/tests/test_session_migrate_helper.py` | `test_the_installer_s_own_record_is_used_when_it_is_there`, `test_a_box_with_no_manifest_asks_the_installation_itself`, `test_neither_source_is_refused_with_the_command_to_run`, `test_a_path_that_holds_no_installation_is_refused`, `test_the_data_root_defaults_to_the_install_like_everywhere_else`, `test_check_resolves_and_does_nothing`, `test_the_restart_works_on_a_box_with_no_manifest`, `test_the_manifest_still_wins_when_it_is_there`, `test_no_user_anywhere_is_said_rather_than_guessed` | bash `gamecore-session-migrate`, sudoers |
| 7 | `backend/tests/test_sudoers_rules_name_real_commands.py` | all (22) | sudoers rules `arch.sh` writes; the Electron `systemctl poweroff/reboot` grant is orphaned since the shell runs `shutdown.exe` |
| 7 | `catalog/lutris/tests/test_defaults.py` | all (9) | Linux pack: `fcntl` |
| 7 | `catalog/lutris/tests/test_fullscreen.py` | `test_the_lutris_tile_fullscreens_the_game_window` | Linux pack: X11 `WM_CLASS` matching; the Windows enforcer fullscreens the job's own window |
| 7 | `catalog/lutris/tests/test_proton.py` | `test_a_verified_build_lands_in_runners_wine` | Linux pack: symlinks in a Wine tree |
| 7 | `catalog/lutris/tests/test_session.py` | all (16) | Linux pack: `fcntl`, uinput |
| 7 | `catalog/lutris/tests/test_xpad.py` | all (15) | Linux pack: `fcntl`, uinput |
| 7 | `catalog/melonds/tests/test_layout_multiplayer.py` | `test_the_launcher_marks_melonds_with_the_player_count` | Linux pack: `/proc` environ |
| 7 | `catalog/melonds/tests/test_mouse_touch.py` | all (12) | Linux pack: `fcntl`, evdev, uinput |
| 7 | `catalog/melonds/tests/test_multiplayer_runtime.py` | all (12) | Linux pack: `fcntl`, AT-SPI |
| 7 | `catalog/melonds/tests/test_multiplayer_setup.py` | `test_device_ids_join_hidraw_and_event_nodes_of_one_pad` | Linux pack: sysfs hidraw |
| 7 | `catalog/melonds/tests/test_profile_saves.py` | `test_another_profile_points_player_one_saves_and_states_at_its_folder`, `test_switching_profiles_never_keeps_the_previous_folder`, `test_a_later_player_folder_creates_its_instance_section` | Linux pack: melonDS ini paths |
| 7 | `catalog/rpcs3/tests/test_smart_launch.py` | all (40) | Linux pack: `fcntl` |
| 7 | `catalog/switch/tests/test_eden_saves.py` | `test_a_profile_reads_its_own_eden_folder`, `test_eden_stopped_mid_profile_gives_the_primarys_parked_folder`, `test_a_profile_import_lands_in_the_profiles_folders` | Linux pack: symlinks in the Ryujinx tree |
| 8 | `backend/tests/test_boot_review_fixes.py` | `test_ota_restarts_the_owner_of_the_ui`, `test_ota_requires_the_one_time_preparation` | bash `gamecore-xsetup` (7) and `update/linux.sh` (8) |
| 8 | `backend/tests/test_ota_failure_restores.py` | all (2) | `update/linux.sh` with rsync |
| 8 | `backend/tests/test_ota_merge_roots.py` | `test_a_new_system_whose_emulator_is_absent_stays_off_the_grid`, `test_the_updater_reads_the_data_root_from_the_backend_unit_when_the_shell_has_none`, `test_the_updater_keeps_an_explicit_data_root_and_the_old_default` | `update/linux.sh`, flatpak/pacman stubs, `systemctl` |
| 8 | `backend/tests/test_ota_over_old_layout.py` | all (5) | `update/linux.sh` with rsync, `git archive` |
| 8 | `backend/tests/test_ota_permission_drift.py` | `test_every_granted_privilege_is_one_the_updater_can_report`, `test_the_updater_still_parses_and_lints` | sudoers rules, `update/linux.sh` |
| 8 | `backend/tests/test_update.py` | `test_a_second_apply_is_refused_with_409`, `test_apply_is_allowed_again_once_the_previous_run_finished`, `test_killing_the_shell_takes_its_children_with_it` | bash `update/linux.sh`, process group kill |
| 8 | `backend/tests/test_update_retires_shared.py` | `test_the_orphan_is_moved_out_of_the_themes_directory`, `test_it_is_recoverable_rather_than_gone`, `test_running_it_twice_is_not_an_error`, `test_a_second_run_does_not_destroy_the_first_snapshot_it_cannot_replace`, `test_a_shared_directory_this_project_did_not_ship_is_left_alone`, `test_nothing_else_under_themes_is_touched` | bash `update/linux.sh` |
| 9 | `backend/tests/test_addon_contract.py` | all (9) | bash `gamecore-addon` CLI, systemd units |
| 10 | `backend/tests/test_migration.py` | `test_a_symlink_is_named_and_not_copied`, `test_a_destination_this_user_cannot_create_is_planned_but_not_written` | `migrate-userdata.py` (`/opt` to `/userdata`), POSIX modes |
| 10 | `backend/tests/test_system_split.py` | `test_the_command_dry_runs_by_default` | `split-systems.py` refuses root with `geteuid` |
