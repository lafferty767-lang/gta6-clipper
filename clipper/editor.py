"""Renders a raw clip into a vertical 9:16 short with burned-in word-by-word captions.

Layouts:
  split - the streamer's webcam on the top half, gameplay on the bottom half, captions on the seam
          (the webcam box comes from config, or is found automatically, see facecam.py)
  blur  - gameplay in the middle over a blurred, zoomed copy of itself (when no webcam is found)
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .config import Edit, Facecam
from .transcribe import Word

log = logging.getLogger(__name__)

BLUR_FG_HEIGHT = 840


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(out.stdout.strip())


# --- captions -------------------------------------------------------------------------
def _ts(t: float) -> str:
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").upper()


def chunk_words(words: list[Word], max_words: int = 3, max_gap: float = 0.6) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    for w in words:
        if chunks and len(chunks[-1]) < max_words and w.start - chunks[-1][-1].end <= max_gap:
            chunks[-1].append(w)
        else:
            chunks.append([w])
    return chunks


def build_ass(words: list[Word], cfg: Edit, y: int) -> str:
    x = cfg.width // 2
    size = int(cfg.width * 0.078)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.width}
PlayResY: {cfg.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cfg.font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for chunk in chunk_words(words):
        for i, w in enumerate(chunk):
            start = w.start
            end = chunk[i + 1].start if i + 1 < len(chunk) else max(w.end, w.start + 0.25)
            parts = []
            for j, other in enumerate(chunk):
                t = _esc(other.text)
                parts.append("{\\c&H00FFFF&}" + t + "{\\c&HFFFFFF&}" if j == i else t)
            text = "{\\pos(%d,%d)\\fscx105\\fscy105}" % (x, y) + " ".join(parts)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


# --- render ---------------------------------------------------------------------------
def render_vertical(raw: Path, offset: float, duration: float, out: Path, cfg: Edit,
                    facecam: Facecam | None, words: list[Word], credit: str | None) -> Path:
    work = out.parent
    W, H = cfg.width, cfg.height
    layout = cfg.layout
    if layout == "auto":
        layout = "split" if facecam else "blur"
    if layout == "split" and not facecam:
        log.warning("split layout needs a facecam box; falling back to blur")
        layout = "blur"

    if layout == "split":
        cam_h = cfg.facecam_height
        game_h = H - cam_h
        f = facecam
        sw, sh = probe_size(raw)
        # gameplay: the widest 1080:game_h slice of the frame, moved off the webcam where possible
        cw = min(sw, round(sh * W / game_h / 2) * 2)
        x0 = (sw - cw) / 2
        cam_l, cam_r = f.x * sw, (f.x + f.w) * sw
        if cam_r <= sw / 2 and x0 < cam_r:
            x0 = min(cam_r, sw - cw)
        elif cam_l >= sw / 2 and x0 + cw > cam_l:
            x0 = max(cam_l - cw, 0)
        graph = (
            f"[0:v]crop=iw*{f.w:.4f}:ih*{f.h:.4f}:iw*{f.x:.4f}:ih*{f.y:.4f},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h},setsar=1[cam];"
            f"[0:v]crop={cw}:{sh}:{int(x0)}:0,scale={W}:{game_h},setsar=1[game];"
            f"[cam][game]vstack=inputs=2,fps=30[v0]")
        cap_y = cam_h                      # captions sit on the seam between streamer and gameplay
        credit_y = H - 420                 # above TikTok/Shorts buttons and caption text
    else:
        fg_h = BLUR_FG_HEIGHT
        graph = (
            f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:4,"
            f"eq=brightness=-0.10,setsar=1[bg];"
            f"[b]scale=-2:{fg_h},crop='min(iw,{W})':ih,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30[v0]")
        cap_y = (H + fg_h) // 2 + 170
        credit_y = (H - fg_h) // 2 - 90

    last = "v0"
    if cfg.captions and words:
        (work / "captions.ass").write_text(build_ass(words, cfg, cap_y), encoding="utf-8")
        graph += f";[{last}]subtitles=captions.ass[v1]"
        last = "v1"
    if cfg.credit_overlay and credit:
        (work / "credit.txt").write_text(credit, encoding="utf-8")
        graph += (f";[{last}]drawtext=fontfile='{cfg.font_file}':textfile=credit.txt:fontsize={int(W * 0.035)}:"
                  f"fontcolor=white@0.9:box=1:boxcolor=black@0.45:boxborderw=14:x=(w-tw)/2:y={credit_y}[v2]")
        last = "v2"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", str(raw.resolve()),
           "-filter_complex", graph, "-map", f"[{last}]"]
    if has_audio(raw):
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "44100"]
        if cfg.loudnorm:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart", str(out.resolve())]
    subprocess.run(cmd, check=True, cwd=work)
    return out
