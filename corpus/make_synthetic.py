"""Generate a ground-truth-labeled synthetic screencast of a fake machine-control GUI.

Why synthetic: stage 2 (golden/dross) needs a clip where we *know* the right labels.
We script the noise — a mouse-wander stretch and a wrong-tab-then-revert excursion — so
the test can assert the classifier recovers exactly the golden actions.

Output (into this directory):
    labeled_demo.mp4   the screencast
    labeled_demo.labels.json   [{start, end, kind, note}] ground truth

Run: ../.venv/bin/python make_synthetic.py
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 960, 600
FPS = 10
HERE = Path(__file__).parent / "synthetic"
HERE.mkdir(exist_ok=True)

BG = (236, 239, 242)
PANEL = (255, 255, 255)
ACCENT = (33, 99, 168)
TEXT = (40, 44, 52)
OK = (40, 160, 90)


def _font(size: int):
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(cand).exists():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


F_TITLE = _font(28)
F_BODY = _font(20)
F_SMALL = _font(16)


def base_window(active_tab: str) -> Image.Image:
    """The chrome: title bar + tab strip. `active_tab` in {Main, Settings}."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 48], fill=ACCENT)
    d.text((20, 10), "LabRig Controller v2", font=F_TITLE, fill=(255, 255, 255))
    for i, tab in enumerate(("Main", "Settings")):
        x0 = 20 + i * 140
        on = tab == active_tab
        d.rectangle([x0, 56, x0 + 130, 92], fill=(PANEL if on else (210, 216, 222)))
        d.text((x0 + 16, 62), tab, font=F_BODY, fill=(ACCENT if on else TEXT))
    d.rectangle([20, 100, W - 20, H - 20], fill=PANEL, outline=(200, 205, 210))
    return img


def draw_main(pump: bool = False, valve: bool = False, flow: int = 0,
              heater: bool = False, temp: int = 0, logging: bool = False) -> Image.Image:
    img = base_window("Main")
    d = ImageDraw.Draw(img)
    d.text((44, 116), "Main Control", font=F_TITLE, fill=TEXT)

    # Prominent status banner: a real control GUI announces the current operation boldly.
    # Each consequential action repaints this whole bar, which is the unambiguous visible
    # delta the keyframer keys on (and which mouse-wander never produces). The banner shows
    # the most-recently-engaged subsystem (priority cascade), so every golden step flips it.
    GREEN = (40, 150, 85)
    if logging:
        banner, bcol = "RUN LOGGING - RECORDING", GREEN
    elif temp > 0:
        banner, bcol = f"TEMP {temp}°C - HOLDING", GREEN
    elif heater:
        banner, bcol = "HEATER ON - WARMING", GREEN
    elif flow > 0:
        banner, bcol = f"FLOW {flow} mL/min - STEADY", GREEN
    elif valve:
        banner, bcol = "VALVE OPEN", GREEN
    elif pump:
        banner, bcol = "PUMP RUNNING", GREEN
    else:
        banner, bcol = "SYSTEM IDLE", (120, 124, 130)
    d.rectangle([44, 150, W - 44, 196], fill=bcol)
    d.text((60, 160), banner, font=F_TITLE, fill=(255, 255, 255))

    rows = [
        ("Pump", "ON" if pump else "OFF", pump),
        ("Valve", "OPEN" if valve else "CLOSED", valve),
        ("Flow setpoint", f"{flow} mL/min", flow > 0),
        ("Heater", "ON" if heater else "OFF", heater),
        ("Temp setpoint", f"{temp}°C", temp > 0),
        ("Data logging", "ON" if logging else "OFF", logging),
    ]
    for i, (label, val, good) in enumerate(rows):
        y = 212 + i * 44
        d.text((44, y), label, font=F_BODY, fill=TEXT)
        col = OK if good else (150, 60, 60)
        light = OK if good else (200, 70, 70)
        d.ellipse([286, y - 2, 314, y + 26], fill=light, outline=(60, 60, 60))
        box_bg = (228, 245, 234) if good else (248, 232, 232)
        d.rectangle([330, y - 4, 540, y + 32], fill=box_bg, outline=(210, 214, 218))
        d.text((342, y), val, font=F_BODY, fill=col)
    d.rectangle([44, 486, 220, 536], fill=ACCENT)
    d.text((70, 498), "Start Pump", font=F_BODY, fill=(255, 255, 255))
    d.rectangle([240, 486, 416, 536], fill=ACCENT)
    d.text((266, 498), "Open Valve", font=F_BODY, fill=(255, 255, 255))
    return img


def draw_settings() -> Image.Image:
    img = base_window("Settings")
    d = ImageDraw.Draw(img)
    d.text((44, 124), "Settings (wrong panel)", font=F_TITLE, fill=TEXT)
    for i, line in enumerate(("Units: metric", "Theme: light", "Logging: on")):
        d.text((44, 190 + i * 50), line, font=F_BODY, fill=TEXT)
    return img


def cursor(img: Image.Image, x: int, y: int) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    d.polygon([(x, y), (x, y + 22), (x + 6, y + 16), (x + 12, y + 26),
               (x + 16, y + 24), (x + 10, y + 14), (x + 18, y + 14)],
              fill=(20, 20, 20), outline=(255, 255, 255))
    return img


# Screenplay: (duration_s, frame_state_factory, cursor_fn, kind, note).
# cursor_fn(progress 0..1) -> (x, y). kind is the ground-truth label for the stretch.
def screenplay():
    main0 = lambda: draw_main()
    main_pump = lambda: draw_main(pump=True)
    main_valve = lambda: draw_main(pump=True, valve=True)
    main_flow = lambda: draw_main(pump=True, valve=True, flow=50)
    main_heat = lambda: draw_main(pump=True, valve=True, flow=50, heater=True)
    main_temp = lambda: draw_main(pump=True, valve=True, flow=50, heater=True, temp=65)
    main_log = lambda: draw_main(pump=True, valve=True, flow=50, heater=True, temp=65, logging=True)
    settings = draw_settings

    still = lambda x, y: (lambda p: (x, y))

    def wander(p):
        cx, cy = 600, 300
        return (int(cx + 120 * math.cos(p * 6 * math.pi)),
                int(cy + 90 * math.sin(p * 6 * math.pi)))

    # 7 golden (consequential, each repaints the banner) interleaved with 3 dross. The
    # settings excursion + revert + wander form one dross stretch around t=6-13 so the
    # classifier has noise to reject (and a clean backtrack to detect).
    return [
        (2.0, main0,      still(140, 200), "golden", "initial state: all subsystems idle"),
        (2.0, main_pump,  still(130, 506), "golden", "Start Pump -> PUMP RUNNING"),
        (2.0, main_valve, still(330, 506), "golden", "Open Valve -> VALVE OPEN"),
        (2.0, settings,   still(160, 70),  "dross",  "wrong tab: Settings (excursion start)"),
        (2.0, main_valve, still(40, 70),   "dross",  "back to Main, same state (revert)"),
        (3.0, main_valve, wander,          "dross",  "mouse wander, no UI change"),
        (2.0, main_flow,  still(420, 250), "golden", "Set Flow setpoint 50 mL/min"),
        (2.0, main_heat,  still(420, 350), "golden", "Enable Heater -> HEATER ON"),
        (5.0, main_temp,  still(420, 386), "golden", "Set Temp setpoint 65 C (hold to stabilize)"),
        (2.0, main_log,   still(420, 432), "golden", "Engage Data logging -> RUN LOGGING"),
    ]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="procap_syn_"))
    labels = []
    fidx = 0
    t = 0.0
    try:
        for dur, factory, curfn, kind, note in screenplay():
            start = t
            n = int(dur * FPS)
            base = factory()
            for k in range(n):
                p = k / max(1, n - 1)
                x, y = curfn(p)
                frame = cursor(base, x, y)
                frame.save(tmp / f"f_{fidx:06d}.png")
                fidx += 1
            t += dur
            labels.append({"start": round(start, 2), "end": round(t, 2),
                           "kind": kind, "note": note})

        out = HERE / "labeled_demo.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS),
             "-i", str(tmp / "f_%06d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out)],
            check=True,
        )
        (HERE / "labeled_demo.labels.json").write_text(json.dumps(labels, indent=2))
        print(f"wrote {out} ({fidx} frames, {t:.0f}s) + labels "
              f"({sum(1 for l in labels if l['kind']=='golden')} golden stretches)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
