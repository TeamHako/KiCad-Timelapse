# KiCad Timelapse

A KiCad plugin that captures your board as you design it and turns the history into a timelapse video.

Click the toolbar button, press **Start recording**, and keep working. Every few seconds the plugin saves a snapshot of the board, unsaved edits included, but only when something changed. When you're done, **Render video** turns the snapshots into an MP4 using KiCad's own 3D renderer, in a top, angled or bottom view.

- Works in **KiCad 10** (also 8 and 9) on Windows, macOS and Linux.
- Snapshots stay next to your project, so you can render again later at a different view, size or speed.
- No Python packages to install. The only extra you need is ffmpeg.

## Setup (KiCad 10)

### 1. Install ffmpeg

The plugin uses ffmpeg to turn the rendered frames into a video.

| System | Command |
|---|---|
| Windows (PowerShell) | `winget install --id Gyan.FFmpeg -e` |
| macOS | `brew install ffmpeg` |
| Linux (Debian/Ubuntu) | `sudo apt install ffmpeg` |

Close and reopen KiCad afterwards, so it picks up the new PATH.

### 2. Install the plugin

**Option A: Plugin and Content Manager (recommended)**

1. Download `kicad-timelapse-<version>.zip` from [Releases](https://github.com/TeamHako/kicad-timelapse/releases). Don't unzip it.
2. Open KiCad and click **Plugin and Content Manager** in the main window.
3. Click **Install from File…** at the bottom left and choose the zip.
4. Click **Apply Pending Changes**, then close the window.

**Option B: copy the files**

1. Open the PCB Editor and go to **Tools → External Plugins → Open Plugin Directory**.
2. Make a folder there called `kicad-timelapse`.
3. Copy everything from this repo's `plugins/` folder into it (`__init__.py`, `timelapse_action.py`, `timelapse_core.py` and `icon.png`).

### 3. Load it

1. In the PCB Editor, go to **Tools → External Plugins → Refresh Plugins**.
2. A clock-and-board button appears in the top toolbar. It's also listed under **Tools → External Plugins → KiCad Timelapse**.
3. If the button doesn't show up, go to **Preferences → Preferences… → PCB Editor → Action Plugins** and tick **Show button** next to KiCad Timelapse.

## Using it

1. **Open your board and save it once.** The plugin stores snapshots next to the board file, so it needs to know where that is.
2. Click the **KiCad Timelapse** button.
3. Choose how often to take snapshots (every 15 seconds is a good start), then click **Start recording**.
4. Design as usual. You can close the plugin window: recording keeps going in the background. Click the button again to check on it.
5. Click **Stop recording** when you're done. Use **Snapshot now** at any time to capture a particular moment.
6. Pick a **View** (Top, Angled or Bottom) and the **Frames per second**, then click **Render video**. The first render takes a while, about a second or two per snapshot. When it finishes, the folder with the MP4 opens.

Recording continues across sessions: start it again tomorrow and new snapshots are numbered after yesterday's.

### Where files go

```
my-project/
├── my-board.kicad_pcb
└── timelapse/
    └── my-board/
        ├── 00001_20261021-143000.kicad_pcb   ← snapshots, in order
        ├── 00002_20261021-143015.kicad_pcb
        ├── frames/                           ← rendered PNGs, reused on the next render
        └── my-board-angled-20261021-160000.mp4
```

If the project is in git, add `timelapse/` to your `.gitignore`.

Delete snapshots you don't want in the video (a mistake you undid, say) before rendering. Delete the whole `timelapse/my-board` folder to start over.

### Tips

- **Speed:** frames per second sets how fast the video plays. 12 fps with snapshots every 15 s turns an hour of work into 20 seconds.
- **More views:** rendering again with a different view only renders the frames that view doesn't have yet.
- **3D models:** the video looks best when your footprints have 3D models, the same as KiCad's 3D viewer.

## Command line (optional)

`plugins/timelapse_core.py` also runs on its own. This is handy for rendering at a custom size, or for capturing a board every time you **save** it without opening the plugin.

On Windows, use the Python that comes with KiCad so it finds `kicad-cli` automatically:

```powershell
$py = "C:\Program Files\KiCad\10.0\bin\python.exe"
& $py plugins\timelapse_core.py watch  "C:\path\to\my-board.kicad_pcb"
& $py plugins\timelapse_core.py render "C:\path\to\my-board.kicad_pcb" --view angled --fps 24 --width 3840 --height 2160
```

On macOS and Linux:

```bash
python3 plugins/timelapse_core.py watch  ~/projects/my-board/my-board.kicad_pcb
python3 plugins/timelapse_core.py render ~/projects/my-board/my-board.kicad_pcb --view top --fps 12
```

| Command | What it does |
|---|---|
| `watch BOARD [--interval 5]` | Takes a snapshot each time the board file is saved, until Ctrl+C |
| `snapshot BOARD` | Takes one snapshot of the board as saved |
| `list BOARD` | Lists the snapshots |
| `render BOARD [--view top\|angled\|bottom] [--fps 12] [--width 1920] [--height 1080] [--hold 2] [-o out.mp4]` | Renders the video; `--hold` is how long the last frame stays on screen, in seconds |

`watch` only sees saved changes, while recording from the plugin also catches unsaved ones. You can use both on the same board: the snapshots go in the same folder.

## Troubleshooting

| Problem | Fix |
|---|---|
| "kicad-cli not found" | Set the `KICAD_CLI` environment variable to its full path, e.g. `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe` or `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, then restart KiCad |
| "ffmpeg not found" | Install it (step 1) and restart KiCad. Or set the `FFMPEG` environment variable to the full path of `ffmpeg` |
| The button doesn't appear | Refresh plugins (step 3). If it's still missing, open **Tools → Scripting Console** and check for an error mentioning `kicad-timelapse` |
| Parts render without 3D models | The models are missing in KiCad's 3D viewer too. Check **Preferences → Configure Paths** |
| "A different board was opened, so recording stopped" | Recording follows one board. Start it again on the new board |

## Compatibility

The in-editor recorder uses KiCad's Python action plugin API, which KiCad 8, 9 and 10 support. KiCad plans to remove that API in version 11. The `watch` and `render` commands don't depend on it and will keep working.

## Development

```bash
python3 -m unittest discover -s tests   # tests for the snapshot and render logic
python3 tools/build_zip.py              # builds dist/kicad-timelapse-<version>.zip for Releases
```

## License

[MIT](LICENSE)
