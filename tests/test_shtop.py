"""Tests for shtop. Standard library only: python -m unittest discover -s tests"""

import importlib.machinery
import importlib.util
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "shtop"


def load_shtop():
    loader = importlib.machinery.SourceFileLoader("shtop", str(SCRIPT))
    spec = importlib.util.spec_from_loader("shtop", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


shtop = load_shtop()

GRUVBOX = """
mode = "dark"
accent = "#7daea3"
background = "#282828"
foreground = "#d4be98"
red = "#ea6962"
"""


class FormattingTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(shtop.fmt_bytes(0), "0 B")
        self.assertEqual(shtop.fmt_bytes(1023), "1023 B")
        self.assertEqual(shtop.fmt_bytes(1536), "1.5 KB")
        self.assertEqual(shtop.fmt_bytes(250 * 1024 ** 2), "250 MB")
        self.assertEqual(shtop.fmt_bytes(3.8 * 1024 ** 3), "3.8 GB")
        self.assertEqual(shtop.fmt_rate(2048), "2.0 KB/s")

    def test_durations(self):
        self.assertEqual(shtop.fmt_dur(42), "42s")
        self.assertEqual(shtop.fmt_dur(5 * 60 + 3), "5m")
        self.assertEqual(shtop.fmt_dur(3 * 3600 + 12 * 60), "3h 12m")
        self.assertEqual(shtop.fmt_dur(2 * 86400 + 4 * 3600), "2d 4h")
        self.assertEqual(shtop.fmt_dur(-5), "0s")

    def test_trunc(self):
        self.assertEqual(shtop.trunc("hello", 10), "hello")
        self.assertEqual(shtop.trunc("hello world", 6), "hello…")
        self.assertEqual(shtop.trunc("hello", 0), "")


class ColourTests(unittest.TestCase):
    def test_hex(self):
        self.assertEqual(shtop.hex2rgb("#7aa2f7"), (0x7A, 0xA2, 0xF7))
        self.assertEqual(shtop.hex2rgb("fff"), (255, 255, 255))

    def test_mix_clamps(self):
        a, b = (0, 0, 0), (200, 100, 50)
        self.assertEqual(shtop.mix(a, b, 0), a)
        self.assertEqual(shtop.mix(a, b, 1), b)
        self.assertEqual(shtop.mix(a, b, 2), b)
        self.assertEqual(shtop.mix(a, b, -1), a)
        self.assertEqual(shtop.mix(a, b, 0.5), (100, 50, 25))

    def test_severity_scale(self):
        t = shtop.Theme()
        self.assertEqual(t.sev(0.1), t.green)
        self.assertEqual(t.sev(1.0), t.red)
        self.assertNotIn(t.sev(0.7), (t.green, t.red))


class PaletteTests(unittest.TestCase):
    def reply(self, fg, bg, colors=None, bits=16):
        def enc(hexcolor):
            r, g, b = shtop.hex2rgb(hexcolor)
            if bits == 16:
                return f"rgb:{r * 257:04x}/{g * 257:04x}/{b * 257:04x}"
            return f"rgb:{r:02x}/{g:02x}/{b:02x}"

        out = f"\x1b]10;{enc(fg)}\x1b\\\x1b]11;{enc(bg)}\x07"
        for i, c in (colors or {}).items():
            out += f"\x1b]4;{i};{enc(c)}\x1b\\"
        return out + "\x1b[?62;22c"

    def test_parses_16_and_8_bit_replies(self):
        for bits in (16, 8):
            p = shtop.parse_palette(self.reply("#c0caf5", "#1a1b26", {1: "#f7768e", 4: "#7aa2f7"}, bits))
            self.assertEqual(p["foreground"], "#c0caf5")
            self.assertEqual(p["background"], "#1a1b26")
            self.assertEqual(p["red"], "#f7768e")
            self.assertEqual(p["accent"], "#7aa2f7")
            self.assertEqual(p["mode"], "dark")

    def test_light_terminal(self):
        p = shtop.parse_palette(self.reply("#383a42", "#fafafa"))
        self.assertEqual(p["mode"], "light")

    def test_needs_foreground_and_background(self):
        self.assertIsNone(shtop.parse_palette(""))
        self.assertIsNone(shtop.parse_palette("\x1b]10;rgb:ffff/ffff/ffff\x1b\\"))

    def test_unreadable_colours_are_replaced(self):
        # a "blue" identical to the background would vanish; the fallback is used instead
        p = shtop.parse_palette(self.reply("#ffffff", "#000000", {4: "#000000"}))
        self.assertEqual(p["blue"], shtop.FALLBACK["blue"])


class ThemeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.user = base / "config" / "shtop" / "colors.toml"
        self.state = base / "omarchy"
        self.omarchy = self.state / "theme" / "colors.toml"
        self.name = self.state / "theme.name"
        self.patches = [
            mock.patch.object(shtop, "USER_THEME", str(self.user)),
            mock.patch.object(shtop, "OMARCHY_STATE", str(self.state)),
            mock.patch.object(shtop, "OMARCHY_THEME", str(self.omarchy)),
            mock.patch.object(shtop, "OMARCHY_THEME_NAME", str(self.name)),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("SHTOP_THEME", None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_builtin_default(self):
        t = shtop.Theme()
        self.assertEqual(t.kind, "default")
        self.assertEqual(t.bg, shtop.hex2rgb(shtop.FALLBACK["background"]))

    def test_terminal_palette_when_no_files(self):
        t = shtop.Theme({"background": "#000000", "foreground": "#eeeeee", "mode": "dark"})
        self.assertEqual(t.kind, "terminal")
        self.assertEqual(t.bg, (0, 0, 0))
        self.assertEqual(t.name, "terminal colors")

    def test_omarchy_beats_terminal_and_is_named(self):
        self.write(self.omarchy, GRUVBOX)
        self.write(self.name, "gruvbox")
        t = shtop.Theme({"background": "#000000", "foreground": "#eeeeee"})
        self.assertEqual(t.kind, "omarchy")
        self.assertEqual(t.name, "Gruvbox")
        self.assertEqual(t.bg, shtop.hex2rgb("#282828"))
        # keys the theme leaves out come from the fallback, orange is derived
        self.assertEqual(t.cyan, shtop.hex2rgb(shtop.FALLBACK["cyan"]))
        self.assertEqual(t.orange, shtop.mix(t.red, t.yellow, 0.5))

    def test_user_file_beats_omarchy_and_env_beats_both(self):
        self.write(self.omarchy, GRUVBOX)
        self.write(self.user, 'background = "#101010"\n')
        self.assertEqual(shtop.Theme().bg, (16, 16, 16))
        env_file = Path(self.tmp.name) / "env.toml"
        self.write(env_file, 'background = "#202020"\n')
        os.environ["SHTOP_THEME"] = str(env_file)
        self.assertEqual(shtop.Theme().bg, (32, 32, 32))

    def test_follows_theme_changes(self):
        self.write(self.omarchy, GRUVBOX)
        t = shtop.Theme()
        self.assertFalse(t.changed())
        self.write(self.omarchy, GRUVBOX.replace("#282828", "#111111") + "\n")
        self.assertTrue(t.changed())
        t.load()
        self.assertEqual(t.bg, (17, 17, 17))
        self.assertFalse(t.changed())

    def test_ignores_the_instant_omarchy_swaps_directories(self):
        self.write(self.omarchy, GRUVBOX)
        t = shtop.Theme({"background": "#000000", "foreground": "#eeeeee"})
        self.omarchy.unlink()
        self.assertFalse(t.changed())

    def test_broken_file_falls_back_quietly(self):
        self.write(self.user, "this is = not [valid toml")
        t = shtop.Theme()
        self.assertEqual(t.bg, shtop.hex2rgb(shtop.FALLBACK["background"]))


class ProcessNameTests(unittest.TestCase):
    def test_names(self):
        name = shtop.Procs._name
        self.assertEqual(name("python3", ["/usr/bin/python3", "/home/u/bin/myscript", "--x"]), "myscript")
        self.assertEqual(name("python3", ["python3", "-m", "http.server"]), "http.server")
        self.assertEqual(name("python3.14", ["/usr/bin/python3.14", "-u", "tool.py"]), "tool.py")
        self.assertEqual(name("bash", ["bash", "-c", "sleep 1"]), "bash")
        self.assertEqual(name("bash", ["-bash"]), "bash")
        self.assertEqual(name("sshd", ["sshd: shota [priv]"]), "sshd")
        self.assertEqual(name("chromium", ["/usr/lib/chromium/chromium --type=renderer"]), "chromium")
        self.assertEqual(name("kworker/0:1", []), "kworker/0:1")

    def test_group_adds_up(self):
        def proc(pid, name, cpu, mem, state="S", user="me"):
            p = shtop.Proc()
            p.pid, p.ppid, p.start, p.name, p.cmd, p.kernel = pid, 1, 0, name, name, False
            p.cpu, p.mem, p.threads, p.user, p.uid, p.state = cpu, mem, 2, user, 1000, state
            p.procs, p.count, p.key = [p], 1, pid
            return p

        groups = {g.name: g for g in shtop.group([
            proc(10, "firefox", 0.1, 100), proc(11, "firefox", 0.2, 50, "R"), proc(12, "bash", 0, 5, user="root")])}
        ff = groups["firefox"]
        self.assertEqual(ff.count, 2)
        self.assertAlmostEqual(ff.cpu, 0.3)
        self.assertEqual(ff.mem, 150)
        self.assertEqual(ff.state, "R")
        self.assertEqual(ff.pid, 10)
        self.assertEqual(groups["bash"].user, "root")


class KeyParsingTests(unittest.TestCase):
    def test_keys(self):
        keys = shtop.parse_keys("\x1b[A\x1b[B\x1b[5~\x1b[3~\x1b[3;2~\x1bOP\r\x7fq\x03")
        self.assertEqual(keys, ["up", "down", "pgup", "delete", "shift-delete", "f1",
                                "enter", "backspace", "q", "ctrl-c"])

    def test_lone_escape_and_mouse(self):
        keys = shtop.parse_keys("\x1b\x1b[<0;12;7M\x1b[<64;1;1M\x1b[<0;12;7m")
        self.assertEqual(keys[0], "esc")
        self.assertEqual(keys[1], ("mouse", 0, 12, 7, True))
        self.assertEqual(keys[2], ("mouse", 64, 1, 1, True))
        self.assertFalse(keys[3][4])

    def test_unknown_sequences_are_dropped(self):
        self.assertEqual(shtop.parse_keys("\x1b[99Zx"), ["x"])


class IconTests(unittest.TestCase):
    def tearDown(self):
        shtop.use_icons(True)

    def test_plain_fallback(self):
        t = shtop.Theme()
        shtop.use_icons(False)
        self.assertEqual(shtop.ICON["CPU"], shtop.PLAIN["CPU"])
        self.assertEqual((shtop.CAP_L, shtop.CAP_R), ("▐", "▌"))
        self.assertEqual(shtop.app_icon("firefox", False, t), ("●", t.orange))
        for glyph in list(shtop.ICON.values()) + shtop.BAT_ICONS:
            self.assertFalse(0xE000 <= ord(glyph[0]) <= 0xF8FF or ord(glyph[0]) >= 0xF0000, glyph)

    def test_nerd_icons(self):
        t = shtop.Theme()
        shtop.use_icons(True)
        self.assertEqual(shtop.app_icon("firefox", False, t)[0], "")
        self.assertEqual(shtop.app_icon("sh", False, t)[0], "")
        # short names only match exactly
        self.assertEqual(shtop.app_icon("gocryptfs", False, t)[0], "●")


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shtop.App(animate=False)
        cls.app.sample_fast()
        cls.app.sample()
        time.sleep(0.3)
        cls.app.sample_fast()
        cls.app.sample()

    def tearDown(self):
        shtop.use_icons(True)
        self.app.overlay = None

    def text(self):
        return "\n".join("".join(row) for row in self.app.scr.ch)

    def test_every_size_and_overlay(self):
        for icons in (True, False):
            shtop.use_icons(icons)
            for w, h in ((40, 10), (60, 15), (80, 24), (100, 30), (120, 40), (160, 50), (250, 70)):
                for overlay in (None, "help", "detail", "confirm"):
                    with self.subTest(icons=icons, size=(w, h), overlay=overlay):
                        self.app.scr.resize(w, h)
                        self.app.overlay = overlay
                        if overlay == "confirm":
                            self.app.confirm = (self.app.current(), signal.SIGTERM)
                        self.app.render()
                        self.assertEqual(len(self.app.scr.ch), h)
                        self.assertTrue(all(len(r) == w for r in self.app.scr.ch))

    def test_panels_appear(self):
        self.app.scr.resize(150, 45)
        self.app.render()
        text = self.text()
        for title in ("CPU", "Memory", "Network", "Storage", "Apps"):
            self.assertIn(title, text)

    def test_too_small_message(self):
        self.app.scr.resize(40, 10)
        self.app.render()
        self.assertIn("needs a bit more room", self.text())

    def test_filter_and_grouping(self):
        app = self.app
        app.filter = "zzz-no-such-process"
        app.build_rows()
        self.assertEqual(app.rows, [])
        app.filter = ""
        app.grouped = False
        app.build_rows()
        flat = len(app.rows)
        app.grouped = True
        app.build_rows()
        self.assertLessEqual(len(app.rows), flat)

    def test_screen_only_resends_changed_rows(self):
        scr = shtop.Screen(20, 3)
        written = []
        with mock.patch.object(shtop.os, "write", side_effect=lambda fd, data: written.append(data) or len(data)):
            scr.put(0, 0, "hello")
            scr.flush()
            first = b"".join(written)
            written.clear()
            scr.flush()
            unchanged = b"".join(written)
        self.assertIn(b"hello", first)
        self.assertNotIn(b"hello", unchanged)


class CommandLineTests(unittest.TestCase):
    def test_interval_bounds(self):
        self.assertEqual(shtop.parse_args(["-i", "2"]).interval, 2.0)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            shtop.parse_args(["-i", "0.1"])
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            shtop.parse_args(["--dump", "wide"])

    def test_versions_agree(self):
        pkgbuild = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text()
        self.assertIn(f"pkgver={shtop.VERSION}\n", pkgbuild)
        self.assertIn(f'"shtop {shtop.VERSION}"', (ROOT / "shtop.1").read_text())
        self.assertIn(f"## {shtop.VERSION} ", (ROOT / "CHANGELOG.md").read_text())

    def test_version(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), f"shtop {shtop.VERSION}")

    def test_dump_prints_one_frame(self):
        env = dict(os.environ, SHTOP_DUMP_SAMPLES="1")
        out = subprocess.run([sys.executable, str(SCRIPT), "--dump", "90x25", "--no-icons"],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out.stdout).rstrip("\n").split("\n")
        self.assertEqual(len(plain), 25)
        self.assertTrue(all(len(line) == 90 for line in plain))

    def test_refuses_without_terminal(self):
        out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=30)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("terminal", out.stderr)


@unittest.skipUnless(sys.platform.startswith("linux"), "needs a Linux pty")
class InteractiveTests(unittest.TestCase):
    """Runs shtop in a pseudo-terminal that answers colour queries like a real terminal."""

    def run_session(self, keys, home):
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), TERM="xterm-256color")
        env.pop("SHTOP_THEME", None)
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(SCRIPT), "--no-icons"], env)
        import fcntl
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        out = b""

        def pump(seconds):
            nonlocal out
            end = time.time() + seconds
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        return
                    out += chunk
                    if b"\x1b[c" in chunk:  # the colour query: answer as a terminal would
                        os.write(fd, b"\x1b]10;rgb:dddd/dddd/dddd\x1b\\\x1b]11;rgb:1010/2020/3030\x1b\\"
                                     b"\x1b]4;4;rgb:3333/9999/ffff\x1b\\\x1b[?62;22c")

        pump(1.5)
        for k in keys:
            os.write(fd, k.encode())
            pump(0.12)
        os.write(fd, b"q")
        pump(1.0)
        _, status = os.waitpid(pid, 0)
        return status, out.decode(errors="ignore")

    def test_session_with_terminal_palette(self):
        keys = ["?", "x", "j", "j", "\r", "\x1b", "/", "s", "h", "\x7f", "\x1b", "g", "s", "c", "m", "r",
                "t", "a", "+", "-", "\x1b[6~", "\x1b[H", "\x1b[<65;10;30M", "\x1b[<0;10;30M", "\x1b[<0;10;30M",
                "\x1b", "\x1b[3~", "n", "\x1b[3;2~", "\x1b"]
        with tempfile.TemporaryDirectory() as home:
            status, out = self.run_session(keys, home)
        self.assertEqual(status, 0)
        self.assertNotIn("Traceback", out)
        self.assertIn("terminal colors", re.sub(r"\x1b\[[0-9;]*m", "", out))
        self.assertIn("38;2;51;153;255", out)  # the terminal's blue is used as the accent
        self.assertTrue(out.rstrip().endswith("\x1b[?1049l"), "terminal state is restored on exit")


if __name__ == "__main__":
    unittest.main()
