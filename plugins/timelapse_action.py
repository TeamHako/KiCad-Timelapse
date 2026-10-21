"""
KiCad Timelapse action plugin for the PCB editor (KiCad 8, 9 and 10).

Opens a small window that records snapshots of the open board on a timer,
including unsaved changes, and renders them into a video. Recording keeps
going after the window is closed; click the toolbar button again to check on
it or stop it.
"""
import os
import threading

import pcbnew
import wx

from . import timelapse_core as core

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def save_snapshot(board):
    """Write the board as it is in the editor, unsaved edits included, and
    keep it if it changed. Returns the new snapshot's path, or None."""
    folder = core.snapshot_dir(board.GetFileName())
    folder.mkdir(parents=True, exist_ok=True)
    pending = str(folder / ".pending.kicad_pcb")

    # SaveBoard must skip project settings: without that flag it re-saves the
    # project next to the snapshot and KiCad starts treating that copy as the
    # project. Only call the overloads that take the flag.
    io_mgr = getattr(pcbnew, "PCB_IO_MGR", None)
    fmt = getattr(io_mgr, "KICAD_SEXP", getattr(pcbnew, "PCB_IO_MGR_KICAD_SEXP", None))
    attempts = [(pending, board, fmt, True)] if fmt is not None else []
    attempts.append((pending, board, True))
    for args in attempts:
        try:
            ok = pcbnew.SaveBoard(*args)
            break
        except (TypeError, NotImplementedError):
            continue
    else:
        raise RuntimeError("This KiCad version can't save a snapshot without touching the project. "
                           "Use the watch command from the README instead.")
    if ok is False or not os.path.exists(pending):
        raise RuntimeError("KiCad couldn't write the snapshot.")
    return core.keep_if_changed(pending, folder)


class Recorder(wx.Timer):
    """Takes a snapshot every `interval` seconds while running."""

    def __init__(self):
        super().__init__()
        self.board_path = None
        self.taken = 0
        self.message = ""
        self.on_update = None

    @property
    def recording(self):
        return self.IsRunning()

    def start(self, board, interval):
        self.board_path = board.GetFileName()
        self.taken = 0
        self.Start(int(interval * 1000))
        self.capture()

    def stop(self, message="Stopped."):
        self.Stop()
        self._update(message)

    def Notify(self):
        self.capture()

    def capture(self):
        board = pcbnew.GetBoard()
        if board is None or board.GetFileName() != self.board_path:
            self.stop("A different board was opened, so recording stopped.")
            return
        try:
            snap = save_snapshot(board)
        except Exception as e:  # never let a timer tick raise inside KiCad
            self.stop(f"Recording stopped: {e}")
            return
        if snap:
            self.taken += 1
            self._update(f"Saved {snap.name}")
        else:
            self._update("No changes since the last snapshot.")

    def _update(self, message):
        self.message = message
        if self.on_update:
            self.on_update()


RECORDER = None


class TimelapseDialog(wx.Dialog):
    def __init__(self, parent, board):
        super().__init__(parent, title="KiCad Timelapse", style=wx.DEFAULT_DIALOG_STYLE)
        self.board_path = board.GetFileName()
        self.folder = core.snapshot_dir(self.board_path)

        self.status = wx.StaticText(self)
        self.detail = wx.StaticText(self)
        self.interval = wx.SpinCtrl(self, min=5, max=600, initial=15)
        self.toggle = wx.Button(self)
        snap_now = wx.Button(self, label="Snapshot now")
        self.view = wx.Choice(self, choices=["Top", "Angled", "Bottom"])
        self.view.SetSelection(0)
        self.fps = wx.SpinCtrl(self, min=1, max=60, initial=12)
        self.render_btn = wx.Button(self, label="Render video")
        open_btn = wx.Button(self, label="Open folder")
        self.gauge = wx.Gauge(self, range=100)
        close = wx.Button(self, wx.ID_CLOSE, "Close")

        rec = wx.StaticBoxSizer(wx.VERTICAL, self, "Record")
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="Every"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        row.Add(self.interval, 0, wx.RIGHT, 6)
        row.Add(wx.StaticText(self, label="seconds"), 0, wx.ALIGN_CENTER_VERTICAL)
        rec.Add(row, 0, wx.ALL, 6)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.toggle, 0, wx.RIGHT, 6)
        buttons.Add(snap_now)
        rec.Add(buttons, 0, wx.ALL, 6)

        out = wx.StaticBoxSizer(wx.VERTICAL, self, "Render")
        grid = wx.FlexGridSizer(2, 2, 6, 8)
        grid.Add(wx.StaticText(self, label="View"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.view)
        grid.Add(wx.StaticText(self, label="Frames per second"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.fps)
        out.Add(grid, 0, wx.ALL, 6)
        render_row = wx.BoxSizer(wx.HORIZONTAL)
        render_row.Add(self.render_btn, 0, wx.RIGHT, 6)
        render_row.Add(open_btn)
        out.Add(render_row, 0, wx.ALL, 6)
        out.Add(self.gauge, 0, wx.EXPAND | wx.ALL, 6)

        top = wx.BoxSizer(wx.VERTICAL)
        top.Add(self.status, 0, wx.ALL, 10)
        top.Add(self.detail, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        top.Add(rec, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        top.Add(out, 0, wx.EXPAND | wx.ALL, 10)
        top.Add(close, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizerAndFit(top)
        self.SetMinSize((420, -1))

        self.toggle.Bind(wx.EVT_BUTTON, self.on_toggle)
        snap_now.Bind(wx.EVT_BUTTON, self.on_snapshot)
        self.render_btn.Bind(wx.EVT_BUTTON, self.on_render)
        open_btn.Bind(wx.EVT_BUTTON, lambda e: self.open_folder())
        close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.Bind(wx.EVT_CLOSE, self.on_close)

        RECORDER.on_update = lambda: wx.CallAfter(self.refresh)
        self.refresh()

    def refresh(self):
        if not self:  # window already destroyed
            return
        count = len(core.list_snapshots(self.folder))
        mine = RECORDER.recording and RECORDER.board_path == self.board_path
        self.status.SetLabel(("● Recording" if mine else "Not recording") + f" · {count} snapshots")
        self.detail.SetLabel(RECORDER.message or f"Snapshots are saved to {self.folder}")
        self.toggle.SetLabel("Stop recording" if mine else "Start recording")
        self.interval.Enable(not mine)
        self.Layout()

    def on_toggle(self, _):
        if RECORDER.recording:
            RECORDER.stop()
        else:
            board = pcbnew.GetBoard()
            if not board.GetFileName():
                wx.MessageBox("Save the board once first, so the plugin knows where to put snapshots.",
                              "KiCad Timelapse", wx.ICON_INFORMATION, self)
                return
            RECORDER.start(board, self.interval.GetValue())
        self.refresh()

    def on_snapshot(self, _):
        try:
            snap = save_snapshot(pcbnew.GetBoard())
            RECORDER.message = f"Saved {snap.name}" if snap else "No changes since the last snapshot."
        except Exception as e:
            RECORDER.message = f"Snapshot failed: {e}"
        self.refresh()

    def on_render(self, _):
        view = self.view.GetStringSelection().lower()
        fps = self.fps.GetValue()
        self.render_btn.Disable()
        self.gauge.SetValue(0)
        RECORDER.message = "Rendering…"
        self.refresh()

        def progress(i, n):
            wx.CallAfter(self.gauge.SetValue, int(90 * i / n))

        def work():
            try:
                out = core.render_video(self.board_path, view=view, fps=fps, progress=progress, log=lambda m: None)
                wx.CallAfter(self.render_done, f"Saved {out.name}", None)
            except Exception as e:
                wx.CallAfter(self.render_done, "Render failed.", str(e))

        threading.Thread(target=work, daemon=True).start()

    def render_done(self, message, error):
        if not self:
            return
        self.render_btn.Enable()
        self.gauge.SetValue(0 if error else 100)
        RECORDER.message = message
        self.refresh()
        if error:
            wx.MessageBox(error, "KiCad Timelapse", wx.ICON_ERROR, self)
        else:
            self.open_folder()

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        core.open_folder(self.folder)

    def on_close(self, _):
        RECORDER.on_update = None
        self.Destroy()


class TimelapsePlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "KiCad Timelapse"
        self.category = "Timelapse"
        self.description = "Record your board as you design it and render a timelapse video"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(PLUGIN_DIR, "icon.png")

    def Run(self):
        global RECORDER
        if RECORDER is None:
            RECORDER = Recorder()
        TimelapseDialog(wx.FindWindowByName("PcbFrame"), pcbnew.GetBoard()).Show()
