import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins"))
import timelapse_core as core  # noqa: E402


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Path(self.tmp.name) / "proj" / "hako.kicad_pcb"
        self.board.parent.mkdir()
        self.board.write_text("(kicad_pcb v1)")

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_is_next_to_the_board(self):
        self.assertEqual(core.snapshot_dir(self.board), self.board.parent.resolve() / "timelapse" / "hako")

    def test_unchanged_board_is_not_kept_twice(self):
        first = core.snapshot_file(self.board)
        self.assertIsNotNone(first)
        self.assertIsNone(core.snapshot_file(self.board))
        self.assertEqual(core.list_snapshots(core.snapshot_dir(self.board)), [first])
        self.assertFalse((core.snapshot_dir(self.board) / ".pending.kicad_pcb").exists())

    def test_changes_are_numbered_in_order(self):
        core.snapshot_file(self.board)
        self.board.write_text("(kicad_pcb v2)")
        core.snapshot_file(self.board)
        self.board.write_text("(kicad_pcb v3)")
        core.snapshot_file(self.board)
        names = [p.name[:5] for p in core.list_snapshots(core.snapshot_dir(self.board))]
        self.assertEqual(names, ["00001", "00002", "00003"])

    def test_numbering_continues_after_a_gap(self):
        folder = core.snapshot_dir(self.board)
        folder.mkdir(parents=True)
        (folder / "00007_20260101-000000.kicad_pcb").write_text("old")
        snap = core.snapshot_file(self.board)
        self.assertTrue(snap.name.startswith("00008_"))

    def test_other_files_are_ignored(self):
        folder = core.snapshot_dir(self.board)
        folder.mkdir(parents=True)
        (folder / "notes.kicad_pcb").write_text("x")
        (folder / "frames").mkdir()
        self.assertEqual(core.list_snapshots(folder), [])


class EncodeTests(unittest.TestCase):
    def test_concat_list_holds_the_last_frame(self):
        frames = [Path("/f/00001.png"), Path("/f/00002.png")]
        text = core.concat_list(frames, fps=10, hold=3)
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "ffconcat version 1.0")
        self.assertIn("duration 0.100000", lines[2])
        self.assertEqual(lines[4], "duration 3.000000")
        self.assertEqual(lines[-1], lines[-3])  # last file repeated so its duration counts

    def test_quotes_in_paths_are_escaped(self):
        text = core.concat_list([Path("/it's/a.png")], fps=12, hold=1)
        self.assertIn(r"file '/it'\''s/a.png'", text)

    def test_unknown_view_is_rejected(self):
        with self.assertRaises(ValueError):
            core.render_video("x.kicad_pcb", view="sideways")

    def test_render_needs_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            board = Path(d) / "b.kicad_pcb"
            board.write_text("x")
            with self.assertRaisesRegex(RuntimeError, "No snapshots"):
                core.render_video(board)


class ToolTests(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "my-kicad-cli"
            exe.write_text("")
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"KICAD_CLI": str(exe)}):
                self.assertEqual(core.find_kicad_cli(), str(exe))

    def test_render_calls_kicad_cli_with_the_view(self):
        with tempfile.TemporaryDirectory() as d:
            snap = Path(d) / "00001_x.kicad_pcb"
            snap.write_text("x")
            calls = []
            with mock.patch.object(core, "_run", side_effect=lambda cmd: calls.append(cmd)):
                core.render_frames([snap], Path(d) / "frames", "kicad-cli", Path(d), view="angled", width=800, height=600)
            cmd = calls[0]
            self.assertEqual(cmd[:3], ["kicad-cli", "pcb", "render"])
            self.assertIn("--perspective", cmd)
            self.assertEqual(cmd[cmd.index("--width") + 1], "800")
            self.assertIn(f"KIPRJMOD={Path(d)}", cmd)
            self.assertEqual(cmd[-1], str(snap))


if __name__ == "__main__":
    unittest.main()
