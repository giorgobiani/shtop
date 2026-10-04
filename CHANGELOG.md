# Changelog

## 1.2.0 — 2026-10-04

- Opens on the list of individual processes, each with its PID. Press `g`
  (or start with `-a` / `--apps`) for the grouped-by-app view, which now shows
  the PID of each app's main process too.

## 1.1.0 — 2026-10-04

- Refresh every 2 seconds by default instead of every second: calmer to
  read and lighter on the CPU. Graphs now sample every second and show twice
  as much history. `-i 1` (or `+` while running) brings back the old pace.

## 1.0.0 — 2026-10-04

First release.

- CPU, memory, network, storage, power and sensors, and every running app on
  one screen, with a plain-language status line at the top.
- Apps grouped by name with icons, CPU trend sparklines and a details view
  (CPU history, program, folder, start time, command line).
- Search, sorting, ending apps with confirmation, mouse support.
- Colours follow the current Omarchy theme live, `~/.config/shtop/colors.toml`,
  or the terminal's own palette.
- Nerd Font icons when one is installed, plain symbols otherwise
  (`--icons` / `--no-icons`).
- Smooth easing animations (`--no-animation` or `a` to turn them off).
