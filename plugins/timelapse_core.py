#!/usr/bin/env python3
"""
KiCad Timelapse core: snapshots, rendering and video encoding.

Standard library only, so the same file runs inside KiCad (imported by the
action plugin) and from a terminal:

    python timelapse_core.py watch  path/to/board.kicad_pcb
    python timelapse_core.py list   path/to/board.kicad_pcb
    python timelapse_core.py render path/to/board.kicad_pcb --view angled

Snapshots are plain copies of the board, kept in a "timelapse/<board name>"
folder next to the board, numbered in order. A snapshot is only kept when it
differs from the one before it. Rendering turns each snapshot into a PNG with
`kicad-cli pcb render` (cached, so re-renders are quick) and stitches the
PNGs into an MP4 with ffmpeg.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

SNAPSHOT_GLOB = "[0-9][0-9][0-9][0-9][0-9]_*.kicad_pcb"
SNAPSHOT_RE = re.compile(r"^(\d{5})_")

# kicad-cli pcb render arguments for each view.
VIEWS = {
    "top": ["--side", "top"],
    "bottom": ["--side", "bottom"],
    "angled": ["--side", "top", "--perspective", "--rotate", "-40,0,30", "--zoom", "1"],
}

Progress = Callable[[int, int], None]
Log = Callable[[str], None]


# ---------------------------------------------------------------- snapshots

def snapshot_dir(board_path: str | os.PathLike) -> Path:
    """Where a board's snapshots live: <board folder>/timelapse/<board name>/."""
    board = Path(board_path).resolve()
    return board.parent / "timelapse" / board.stem


def list_snapshots(folder: str | os.PathLike) -> list[Path]:
    """Snapshots in capture order."""
    return sorted(Path(folder).glob(SNAPSHOT_GLOB))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _next_name(folder: Path) -> Path:
    snaps = list_snapshots(folder)
    n = int(SNAPSHOT_RE.match(snaps[-1].name).group(1)) + 1 if snaps else 1
    return folder / f"{n:05d}_{time.strftime('%Y%m%d-%H%M%S')}.kicad_pcb"


def keep_if_changed(candidate: str | os.PathLike, folder: str | os.PathLike) -> Optional[Path]:
    """Move `candidate` into `folder` as the next snapshot, unless it matches
    the latest snapshot byte for byte, in which case it's deleted.
    Returns the new snapshot's path, or None if nothing changed."""
    candidate, folder = Path(candidate), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    snaps = list_snapshots(folder)
    if snaps and _digest(snaps[-1]) == _digest(candidate):
        candidate.unlink()
        return None
    target = _next_name(folder)
    os.replace(candidate, target)
    return target


def snapshot_file(board_path: str | os.PathLike) -> Optional[Path]:
    """Snapshot the board as last saved to disk."""
    folder = snapshot_dir(board_path)
    folder.mkdir(parents=True, exist_ok=True)
    pending = folder / ".pending.kicad_pcb"
    shutil.copyfile(board_path, pending)
    return keep_if_changed(pending, folder)


def watch(board_path: str | os.PathLike, interval: float = 5.0, log: Log = print) -> None:
    """Snapshot the board every time it's saved, until Ctrl+C."""
    board = Path(board_path)
    log(f"Watching {board} (every {interval:g}s). Snapshots go to {snapshot_dir(board)}. Ctrl+C to stop.")
    last_mtime = None
    try:
        while True:
            try:
                mtime = board.stat().st_mtime
            except FileNotFoundError:
                mtime = None
            if mtime is not None and mtime != last_mtime:
                last_mtime = mtime
                time.sleep(0.5)  # let KiCad finish writing
                snap = snapshot_file(board)
                if snap:
                    log(f"  saved {snap.name}")
            time.sleep(interval)
    except KeyboardInterrupt:
        log(f"Stopped. {len(list_snapshots(snapshot_dir(board)))} snapshots.")


# ---------------------------------------------------------------- tools

def _first_existing(paths) -> Optional[str]:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def find_kicad_cli() -> Optional[str]:
    """kicad-cli from $KICAD_CLI, next to the running Python (KiCad's own on
    Windows and macOS), PATH, or the usual install folders."""
    exe = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
    here = os.path.dirname(sys.executable)
    candidates = [
        os.environ.get("KICAD_CLI"),
        os.path.join(here, exe),
        os.path.join(here, "..", "..", "..", "MacOS", exe),
        shutil.which("kicad-cli"),
    ]
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            candidates += sorted(glob.glob(os.path.join(root, "KiCad", "*", "bin", exe)), reverse=True)
            candidates += sorted(glob.glob(os.path.join(root, "Programs", "KiCad", "*", "bin", exe)), reverse=True)
    elif sys.platform == "darwin":
        candidates.append("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    else:
        candidates += ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"]
    return _first_existing(candidates)


def find_ffmpeg() -> Optional[str]:
    """ffmpeg from $FFMPEG or PATH (plus WinGet's links folder on Windows)."""
    candidates = [os.environ.get("FFMPEG"), shutil.which("ffmpeg")]
    if os.name == "nt":
        candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe"))
    return _first_existing(candidates)


def _run(cmd: list[str]) -> None:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flashing from inside KiCad
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-8:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed:\n" + "\n".join(tail))


# ---------------------------------------------------------------- render

def render_frames(snapshots: list[Path], frames_dir: Path, kicad_cli: str, project_dir: Path,
                  view: str = "top", width: int = 1920, height: int = 1080,
                  progress: Optional[Progress] = None) -> list[Path]:
    """One PNG per snapshot. Frames newer than their snapshot are reused."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, snap in enumerate(snapshots, 1):
        frame = frames_dir / (snap.stem + ".png")
        if not frame.exists() or frame.stat().st_mtime < snap.stat().st_mtime:
            _run([kicad_cli, "pcb", "render", *VIEWS[view],
                  "--width", str(width), "--height", str(height),
                  "--quality", "basic", "--background", "opaque",
                  # snapshots live in a subfolder, so point ${KIPRJMOD} back at the project
                  "-D", f"KIPRJMOD={project_dir}",
                  "-o", str(frame), str(snap)])
        frames.append(frame)
        if progress:
            progress(i, len(snapshots))
    return frames


def concat_list(frames: list[Path], fps: float, hold: float) -> str:
    """ffmpeg concat-demuxer script: each frame for 1/fps s, the last for `hold` s."""
    lines = ["ffconcat version 1.0"]
    for i, frame in enumerate(frames):
        last = i == len(frames) - 1
        path = frame.resolve().as_posix().replace("'", r"'\''")
        lines += [f"file '{path}'", f"duration {max(hold, 1 / fps) if last else 1 / fps:.6f}"]
    # the demuxer ignores the last entry's duration unless the file is repeated
    lines.append(lines[-2])
    return "\n".join(lines) + "\n"


def encode_video(frames: list[Path], out_path: Path, ffmpeg: str, fps: float = 12, hold: float = 2.0) -> Path:
    listing = out_path.with_suffix(".ffconcat")
    listing.write_text(concat_list(frames, fps, hold), encoding="utf-8")
    try:
        _run([ffmpeg, "-y", "-loglevel", "error", "-safe", "0", "-f", "concat", "-i", str(listing),
              "-vf", f"fps={fps:g},pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
              "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart", str(out_path)])
    finally:
        listing.unlink(missing_ok=True)
    return out_path


def render_video(board_path: str | os.PathLike, out_path: Optional[str | os.PathLike] = None,
                 view: str = "top", fps: float = 12, width: int = 1920, height: int = 1080,
                 hold: float = 2.0, progress: Optional[Progress] = None, log: Log = print) -> Path:
    """Render every snapshot of a board and encode them into an MP4."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}")
    board = Path(board_path).resolve()
    folder = snapshot_dir(board)
    snaps = list_snapshots(folder)
    if not snaps:
        raise RuntimeError(f"No snapshots in {folder} yet. Record or watch the board first.")
    kicad_cli = find_kicad_cli()
    if not kicad_cli:
        raise RuntimeError("kicad-cli not found. Set the KICAD_CLI environment variable to its full path.")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it (see the README) or set the FFMPEG environment variable.")

    log(f"Rendering {len(snaps)} snapshots ({view}, {width}x{height})...")
    frames = render_frames(snaps, folder / "frames" / f"{view}_{width}x{height}", kicad_cli, board.parent,
                           view, width, height, progress)
    out = Path(out_path) if out_path else folder / f"{board.stem}-{view}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    log("Encoding video...")
    encode_video(frames, out, ffmpeg, fps, hold)
    log(f"Done: {out}")
    return out


def open_folder(path: str | os.PathLike) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------- CLI

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="timelapse_core.py", description="KiCad Timelapse: snapshot a board and render a timelapse.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="snapshot the board every time it's saved")
    w.add_argument("board")
    w.add_argument("--interval", type=float, default=5.0, help="seconds between checks (default 5)")

    s = sub.add_parser("snapshot", help="take one snapshot of the board as saved")
    s.add_argument("board")

    l = sub.add_parser("list", help="list snapshots")
    l.add_argument("board")

    r = sub.add_parser("render", help="render snapshots into an MP4")
    r.add_argument("board")
    r.add_argument("-o", "--output", help="output .mp4 (default: in the snapshot folder)")
    r.add_argument("--view", choices=sorted(VIEWS), default="top")
    r.add_argument("--fps", type=float, default=12)
    r.add_argument("--width", type=int, default=1920)
    r.add_argument("--height", type=int, default=1080)
    r.add_argument("--hold", type=float, default=2.0, help="seconds to hold the final frame (default 2)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "watch":
            watch(args.board, args.interval)
        elif args.cmd == "snapshot":
            snap = snapshot_file(args.board)
            print(f"saved {snap}" if snap else "unchanged since the last snapshot")
        elif args.cmd == "list":
            snaps = list_snapshots(snapshot_dir(args.board))
            for snap in snaps:
                print(snap.name)
            print(f"{len(snaps)} snapshots in {snapshot_dir(args.board)}")
        elif args.cmd == "render":
            def bar(i: int, n: int) -> None:
                print(f"\r  frame {i}/{n}", end="" if i < n else "\n", flush=True)
            render_video(args.board, args.output, args.view, args.fps, args.width, args.height, args.hold, bar)
    except (RuntimeError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
