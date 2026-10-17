#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 M. Kalantary
# SPDX-License-Identifier: Apache-2.0
"""
GHOST-VISION  v4.0  "OPS CONSOLE"
WiFi CSI motion & presence sensing  ·  4-node operator terminal

IN PLAIN WORDS
    This is the program you look at. The four sensor boards send it
    measurements of how the WiFi in a room is being disturbed; this program
    decides whether someone is moving, shows roughly where on a map of the
    room, and keeps a record. No boards yet? Run it with --sim and a virtual
    person will walk around a virtual room so you can see everything working.

    New here? Start with docs/GUIDE.md.

WHAT THIS IS
    A four-node WiFi sensing console. Each ESP32-S3 node reports how much the
    WiFi channel between itself and the sensing router is being disturbed. The
    console turns that into motion / presence decisions, a tracked COARSE zone
    estimate with an honest uncertainty radius, and a live target readout.

WHAT IT HONESTLY IS NOT  --  read this first
    · Not an imaging system. There is no picture, outline or skeleton in CSI.
    · Not a biometric sensor. Height, build, age, sex, identity, heart rate:
      none of that is recoverable from ESP32 amplitude CSI. The TARGET READOUT
      panel deliberately lists those fields as NOT MEASURABLE so that nobody
      ever implies otherwise.
    · Not a bug / spy-device detector, and it cannot tell whether a person is
      carrying a phone. CSI is blind to a passive receiver.
    · Not a position fix. The tracked marker is a filtered likelihood centroid
      with a drawn uncertainty ring. The ring is the honest answer; the dot is
      just its middle.
    · Not a people counter. Two people produce one meaningless blob between
      them, and the console says so when the solution looks ambiguous.

REQUIRED
    pip install numpy pyserial PyQt5
OPTIONAL
    pip install opencv-python      # enables the OPTICAL webcam panel

RUN
    python ghost_vision_ops.py            # normal
    python ghost_vision_ops.py --sim      # no hardware: 4 virtual nodes, virtual walker
    python ghost_vision_ops.py --noboot   # skip the boot sequence
    python ghost_vision_ops.py --scale 1.2  # make the whole UI bigger
"""

import io
import os
import re
import sys
import csv
import json
import math
import time
import wave
import random
import socket
import struct
import tempfile
import argparse
import collections

import numpy as np

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import cv2
    HAVE_CV2 = True
except Exception:
    cv2 = None
    HAVE_CV2 = False

from PyQt5 import QtWidgets, QtCore, QtGui

APP_VER = "4.0"
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "ghost_vision_settings.json")
ROOM_PATH = os.path.join(HERE, "ghost_vision_room.json")
NODE_COUNT = 4

# --------------------------------------------------------------------------
# PALETTE  --  dark operations theme
#   Near-black base, cold steel panels, teal as the instrument colour, amber
#   as the infrastructure colour, red reserved strictly for a real detection.
# --------------------------------------------------------------------------
VOID = "#04070a"          # deepest background, behind everything
BG = "#070b10"            # app background
PANEL = "#0d131b"         # card background
PANEL_2 = "#090e15"       # inset / well background
PANEL_3 = "#111a24"       # raised element
BORDER = "#1b2735"        # normal border
BORDER_HI = "#2c4257"     # highlighted border
BORDER_HOT = "#3d5f7d"    # focus / hover border

TEXT = "#e2ecf6"          # primary text
TEXT_2 = "#b6c6d8"        # secondary text
MUTED = "#7f95b0"         # labels
DIM = "#4d6076"           # captions, hints
GHOST = "#2b3948"         # nearly invisible structure

TEAL = "#3ad5c0"          # instrument / OK / active
TEAL_DK = "#12332f"
GREEN = "#3fbf5f"         # clear
RED = "#ff4d5a"           # detection / danger
RED_DK = "#2c1016"
AMBER = "#f0b429"         # infrastructure / caution
AMBER_DK = "#2b2109"
BLUE = "#4d92ff"          # informational / warming
PURPLE = "#a97bff"        # calibration / experimental
CYAN = "#5fe3ff"          # target / track
ORANGE = "#ff8a3d"
GRID = "#111d29"
GRID_HI = "#18293a"

MONO = "'JetBrains Mono','Consolas','DejaVu Sans Mono','Courier New',monospace"
SANS = "'Segoe UI Semibold','Segoe UI','Inter','DejaVu Sans',sans-serif"
COND = "'Bahnschrift','Segoe UI','Inter','DejaVu Sans',sans-serif"

# --------------------------------------------------------------------------
# UI SCALE
#   Everything sizeable goes through fs() / px() so the whole console can be
#   enlarged from one place. v3's biggest usability problem was 9 px text.
# --------------------------------------------------------------------------
UI_SCALE = 1.0


def fs(n):
    """Font size in px, scaled."""
    return max(7, int(round(n * UI_SCALE)))


def px(n):
    """Any pixel dimension, scaled."""
    return int(round(n * UI_SCALE))


STATE_COLORS = {
    "OFFLINE": DIM,
    "NO SIGNAL": RED,
    "WARMING UP": BLUE,
    "CALIBRATING": PURPLE,
    "STANDBY": DIM,
    "AREA CLEAR": GREEN,
    "UNSETTLED": AMBER,
    "MOTION DETECTED": RED,
    "PRESENCE (STILL)": AMBER,
}
STATE_RANK = {
    "OFFLINE": 0, "STANDBY": 1, "NO SIGNAL": 2, "WARMING UP": 3, "CALIBRATING": 4,
    "AREA CLEAR": 5, "UNSETTLED": 6, "PRESENCE (STILL)": 7, "MOTION DETECTED": 8,
}
STATE_SHORT = {
    "OFFLINE": "OFFLINE", "NO SIGNAL": "NO SIG", "WARMING UP": "WARMING",
    "CALIBRATING": "CAL", "STANDBY": "STBY", "AREA CLEAR": "CLEAR",
    "UNSETTLED": "UNSETTLED", "MOTION DETECTED": "MOTION",
    "PRESENCE (STILL)": "PRESENCE",
}




# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
DEFAULTS = {
    "baud": 921600,
    "link_mode": "udp",          # "udp" = wireless nodes, "serial" = USB
    "udp_data_port": 8266,       # nodes -> console (CSI)
    "udp_disc_port": 8267,       # console -> nodes (announcement)
    "udp_router_ip": "10.8.0.1",  # used only to work out which NIC to announce from
    "common_mode": 0.85,         # how much across-node common mode to remove (0-1)
    "timing_tol": 1.6,           # reject a window stretched this much past nominal
    "rate_guard": True,          # ignore a node whose packet rate has collapsed
    "strips_grid": True,         # show the four node strips 2x2 instead of stacked
    "window": 20,
    "smooth": 5,
    "gain": 2.0,
    "threshold": 25.0,
    "exit_ratio": 0.60,
    "hits_needed": 3,
    "clear_needed": 10,
    "presence_secs": 20.0,
    "cal_secs": 30,
    "cal_delay": 20,
    "site_name": "",
    "operator": "",
    "case_ref": "",
    "sound_kind": "Sonar ping",
    "sound_clear": False,
    "sound_ui": True,
    "sound_states": True,
    "volume": 0.7,
    "adapt": True,
    "ports": ["", "", "", ""],
    "node_thresholds": [25.0, 25.0, 25.0, 25.0],
    # Sibling-path thresholds, keyed "tx-rx" (tx 1..4). Router paths (tx 0)
    # keep using node_thresholds so existing calibration is not disturbed.
    "path_thresholds": {},
    "path_cal_time": {},
    # How many paths must agree before the system calls MOTION. A single path
    # firing alone is the phantom signature; two crossing paths is a body.
    "path_confirm": 2,
    "use_paths": True,          # sixteen paths for DETECTION -- this works
    # Which paths the POSITION solver uses. "router" is the proven four-path
    # behaviour. "all" uses all sixteen: better in theory, but it assumes every
    # path responds equally to a body at a given distance from its line, and
    # that is false -- a short wall-hugging path like N3>N4 barely reacts to
    # someone crossing the room while a long diagonal reacts strongly.
    # Correcting that needs per-path sensitivity MEASURED from real walks.
    # Until that exists, the honest default is the one that was proven.
    "solver_paths": "router",
    "node_cal_time": ["", "", "", ""],
    "fresnel_sigma": 0.85,
    "armed": False,
    # ---- v4 additions ----
    "ui_scale": 1.0,
    "boot_anim": True,
    "track_enable": True,
    "track_q": 0.55,           # tracker process noise: higher = more responsive
    "track_r": 0.65,           # tracker measurement noise: higher = smoother
    "track_hold": 5.0,         # seconds a track coasts with no evidence
    "track_trail": 45,         # breadcrumb points kept
    "grid_cm": 8,              # zone solver grid resolution, centimetres
    "show_heat": True,
    "show_paths": True,
    "show_rings": True,
    "show_trail": True,
    "show_dossier": True,
    "show_scan": True,
    "show_grid": True,
    "cam_enable": False,
    "cam_index": 0,
    "cam_mirror": True,
    "cam_fps": 15,
}


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in cfg:
                cfg[k] = v
        if cfg.get("sound_kind") == "Threat tone":      # renamed in v4.0
            cfg["sound_kind"] = "Warning tone"
    except Exception:
        pass
    for key, fill in (("ports", ""), ("node_thresholds", 25.0), ("node_cal_time", "")):
        val = list(cfg.get(key, []))
        while len(val) < NODE_COUNT:
            val.append(fill)
        cfg[key] = val[:NODE_COUNT]
    return cfg


def save_settings(cfg):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


def fmt_dur(s):
    s = int(max(0, s))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm %02ds" % (s // 60, s % 60)
    return "%dh %02dm" % (s // 3600, (s % 3600) // 60)


def compass(deg):
    """Degrees (0 = north / up the map) to a 16-point compass name."""
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# ==========================================================================
# ROOM MODEL  --  everything in metres
# ==========================================================================
# Each entry:
#   (display name, default w, default h, fill, outline, metal?, glyph, shape,
#    accent)
#
# Colours are deliberately MATERIAL-LIKE so the plan reads at a glance:
#   wood = warm brown, fabric = muted blue/olive, metal/appliance = steel,
#   glass = pale cyan, greenery = green, plumbing = porcelain,
#   infrastructure (node/router) = instrument teal / amber.
OBJ_TYPES = {
    "node":      ("SENSOR NODE",    0.30, 0.30, "#08201f", TEAL,      False, "N",     "node",   TEAL),
    "router":    ("SENSING ROUTER", 0.35, 0.25, "#241c06", AMBER,     False, "((o))", "router", AMBER),

    "wall":      ("INTERIOR WALL",  3.00, 0.15, "#2b3340", "#54637a", False, "",      "rect",   "#6b7b92"),
    "pillar":    ("PILLAR",         0.40, 0.40, "#2b3340", "#54637a", False, "",      "rect",   "#6b7b92"),
    "door":      ("DOOR",           0.90, 0.12, "#3a2a16", "#c08b3e", False, "DOOR",  "door",   "#c08b3e"),
    "window":    ("WINDOW",         1.20, 0.12, "#123449", "#57b6e8", False, "WIN",   "window", "#57b6e8"),
    "curtain":   ("CURTAIN",        1.40, 0.18, "#3a2233", "#b06d9a", False, "CURT",  "rect",   "#b06d9a"),
    "stairs":    ("STAIRS",         1.00, 2.00, "#2a2723", "#8a7f6a", False, "STAIRS", "stairs", "#8a7f6a"),

    "desk":      ("DESK",           1.40, 0.70, "#42301c", "#c08b4a", False, "DESK",  "wood",   "#c08b4a"),
    "table":     ("TABLE",          1.20, 0.80, "#42301c", "#c08b4a", False, "TABLE", "wood",   "#c08b4a"),
    "chair":     ("CHAIR",          0.50, 0.50, "#3a2c1c", "#a67c48", False, "CH",    "chair",  "#a67c48"),
    "sofa":      ("SOFA",           2.00, 0.85, "#22384c", "#5f96c4", False, "SOFA",  "sofa",   "#5f96c4"),
    "bed":       ("BED",            2.00, 1.50, "#2b2a44", "#8f88c8", False, "BED",   "bed",    "#8f88c8"),
    "rug":       ("RUG / CARPET",   2.20, 1.60, "#3a2430", "#a06a86", False, "RUG",   "rug",    "#a06a86"),
    "wardrobe":  ("WARDROBE",       1.20, 0.60, "#3d2c1a", "#b07f42", False, "WARD",  "wood",   "#b07f42"),
    "shelf":     ("SHELF / RACK",   1.00, 0.35, "#3a2b18", "#b08a4a", True,  "SHELF", "shelf",  "#b08a4a"),
    "cabinet":   ("CABINET",        0.90, 0.45, "#3a2b18", "#b08a4a", True,  "CAB",   "wood",   "#b08a4a"),
    "safe":      ("SAFE",           0.55, 0.50, "#20262c", "#8ea0b2", True,  "SAFE",  "safe",   "#8ea0b2"),

    "monitor":   ("MONITOR",        0.60, 0.20, "#0d2733", "#3fd0e8", True,  "MON",   "screen", "#3fd0e8"),
    "computer":  ("COMPUTER",       0.45, 0.45, "#16202a", "#6fa8c8", True,  "PC",    "rect",   "#6fa8c8"),
    "tv":        ("TELEVISION",     1.20, 0.15, "#0d2733", "#3fd0e8", True,  "TV",    "screen", "#3fd0e8"),
    "appliance": ("APPLIANCE",      0.70, 0.70, "#232a31", "#9fb2c4", True,  "APP",   "steel",  "#9fb2c4"),
    "fridge":    ("FRIDGE",         0.70, 0.70, "#252d35", "#b6c8d8", True,  "FRIDGE", "steel", "#b6c8d8"),
    "stove":     ("STOVE / OVEN",   0.65, 0.65, "#2a1e1e", "#d1603f", True,  "STOVE", "stove",  "#d1603f"),
    "radiator":  ("RADIATOR",       0.90, 0.15, "#2c221c", "#c07a4a", True,  "RAD",   "shelf",  "#c07a4a"),
    "aircon":    ("AIR CON UNIT",   0.90, 0.25, "#1c2a30", "#7fd0d8", True,  "A/C",   "steel",  "#7fd0d8"),
    "mirror":    ("MIRROR",         0.80, 0.10, "#1a2a33", "#9fdce8", True,  "MIRR",  "window", "#9fdce8"),
    "sink":      ("SINK",           0.60, 0.45, "#1c2c33", "#8fc8d8", True,  "SINK",  "porc",   "#8fc8d8"),

    "plant":     ("PLANT",          0.45, 0.45, "#14301c", "#4fbf6a", False, "*",     "plant",  "#4fbf6a"),
    "person_ref": ("REFERENCE MARK", 0.50, 0.50, "#2b1c33", PURPLE,   False, "REF",   "ref",    PURPLE),
    "zone_mark": ("ZONE LABEL",     0.80, 0.40, "#1c2430", "#7f95b0", False, "ZONE",  "rect",   "#7f95b0"),
}

# grouped for the palette UI so 30 buttons don't become a wall
PALETTE_GROUPS = [
    ("SENSING KIT", ["node", "router", "person_ref", "zone_mark"]),
    ("STRUCTURE",   ["wall", "pillar", "door", "window", "curtain", "stairs"]),
    ("FURNITURE",   ["desk", "table", "chair", "sofa", "bed", "rug",
                     "wardrobe", "shelf", "cabinet", "safe"]),
    ("METAL / ELECTRONICS", ["monitor", "computer", "tv", "appliance", "fridge",
                             "stove", "radiator", "aircon", "mirror", "sink"]),
    ("SOFT",        ["plant"]),
]
PALETTE_ORDER = [k for _, ks in PALETTE_GROUPS for k in ks]


class RoomObject:
    __slots__ = ("kind", "x", "y", "w", "h", "rot", "label", "metal", "node_index")

    def __init__(self, kind, x, y, w=None, h=None, rot=0.0, label="",
                 metal=None, node_index=0):
        spec = OBJ_TYPES.get(kind, OBJ_TYPES["table"])
        self.kind = kind
        self.x = float(x)
        self.y = float(y)
        self.w = float(spec[1] if w is None else w)
        self.h = float(spec[2] if h is None else h)
        self.rot = float(rot)
        self.label = label or ""
        self.metal = spec[5] if metal is None else bool(metal)
        self.node_index = int(node_index)

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @staticmethod
    def from_dict(d):
        return RoomObject(d.get("kind", "table"), d.get("x", 1), d.get("y", 1),
                          d.get("w"), d.get("h"), d.get("rot", 0),
                          d.get("label", ""), d.get("metal"), d.get("node_index", 0))

    def center(self):
        return self.x + self.w / 2.0, self.y + self.h / 2.0

    def corners(self):
        cx, cy = self.center()
        a = math.radians(self.rot)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for dx, dy in ((-self.w / 2, -self.h / 2), (self.w / 2, -self.h / 2),
                       (self.w / 2, self.h / 2), (-self.w / 2, self.h / 2)):
            pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
        return pts

    def contains(self, mx, my):
        cx, cy = self.center()
        a = math.radians(-self.rot)
        dx, dy = mx - cx, my - cy
        lx = dx * math.cos(a) - dy * math.sin(a)
        ly = dx * math.sin(a) + dy * math.cos(a)
        return abs(lx) <= self.w / 2 and abs(ly) <= self.h / 2

    def name(self):
        return self.label or OBJ_TYPES.get(self.kind, ("OBJECT",))[0]


class Room:
    def __init__(self):
        self.width = 6.0
        self.height = 4.5
        self.name = ""
        self.objects = []
        self.default_layout()

    def default_layout(self):
        self.objects = []
        w, h = self.width, self.height
        m = 0.25
        for i, (x, y) in enumerate([(m, m), (w - m - 0.3, m),
                                    (w - m - 0.3, h - m - 0.3), (m, h - m - 0.3)]):
            self.objects.append(RoomObject("node", x, y, node_index=i + 1,
                                           label="NODE %d" % (i + 1)))
        self.objects.append(RoomObject("router", w / 2 - 0.175, h / 2 - 0.125,
                                       label="ROUTER"))

    def nodes(self):
        out = {}
        for o in self.objects:
            if o.kind == "node" and 1 <= o.node_index <= NODE_COUNT:
                out[o.node_index] = o
        return out

    def router(self):
        for o in self.objects:
            if o.kind == "router":
                return o
        return None

    def furniture(self):
        return [o for o in self.objects if o.kind not in ("node", "router")]

    def nearest_object(self, x, y, exclude=("node", "router", "wall", "rug", "zone_mark")):
        """Nearest mapped item to a point -- used for the honest 'beside X' line."""
        best, bd = None, 1e9
        for o in self.objects:
            if o.kind in exclude:
                continue
            cx, cy = o.center()
            d = math.hypot(cx - x, cy - y) - 0.5 * max(o.w, o.h)
            if d < bd:
                best, bd = o, d
        if best is None:
            return None, 0.0
        return best, max(0.0, bd)

    def to_dict(self):
        return {"width": self.width, "height": self.height, "name": self.name,
                "objects": [o.to_dict() for o in self.objects]}

    def load_dict(self, d):
        self.width = float(d.get("width", 6.0))
        self.height = float(d.get("height", 4.5))
        self.name = d.get("name", "")
        self.objects = [RoomObject.from_dict(o) for o in d.get("objects", [])]

    def save(self, path=ROOM_PATH):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            return True
        except Exception:
            return False

    def load(self, path=ROOM_PATH):
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.load_dict(json.load(f))
            return True
        except Exception:
            return False

    def geometry_score(self):
        """
        0-100 rough quality of the node/router geometry for zone solving.
        Not a measurement -- a design check. Rewards paths that cross at wide
        angles and cover the room; punishes near-parallel or stacked paths.
        """
        r = self.router()
        nodes = self.nodes()
        if r is None or len(nodes) < 2:
            return 0, ["need a router and at least two nodes on the map"]
        rx, ry = r.center()
        angs = []
        notes = []
        for i, n in nodes.items():
            nx, ny = n.center()
            angs.append((i, math.degrees(math.atan2(ny - ry, nx - rx)) % 180.0))
        spread = 0.0
        pairs = 0
        for a in range(len(angs)):
            for b in range(a + 1, len(angs)):
                d = abs(angs[a][1] - angs[b][1])
                d = min(d, 180 - d)
                spread += d
                pairs += 1
                if d < 18:
                    notes.append("node %d and node %d sit on nearly the same line "
                                 "from the router — they see the same slice of room"
                                 % (angs[a][0], angs[b][0]))
        mean_sep = spread / max(1, pairs)
        score = clamp(mean_sep / 60.0, 0.0, 1.0) * 70.0
        dists = [math.hypot(n.center()[0] - rx, n.center()[1] - ry)
                 for n in nodes.values()]
        if dists:
            var = (max(dists) - min(dists)) / max(0.5, max(dists))
            score += clamp(1.0 - abs(var - 0.35), 0, 1) * 30.0
        return int(clamp(score, 0, 100)), notes

    def warnings(self):
        out = []
        nodes = self.nodes()
        r = self.router()
        if len(nodes) < NODE_COUNT:
            out.append("only %d of %d nodes placed on the map" % (len(nodes), NODE_COUNT))
        if r is None:
            out.append("no sensing router placed — zone estimate is disabled")
        metals = [o for o in self.objects if o.metal]
        for idx, n in nodes.items():
            nx, ny = n.center()
            for mo in metals:
                mx, my = mo.center()
                if math.hypot(nx - mx, ny - my) < 0.6:
                    out.append("node %d is within 60 cm of %s — metal reflects and distorts"
                               % (idx, OBJ_TYPES[mo.kind][0].lower()))
                    break
        if r is not None and nodes:
            rx, ry = r.center()
            for idx, n in nodes.items():
                nx, ny = n.center()
                d = math.hypot(nx - rx, ny - ry)
                if d < 0.8:
                    out.append("node %d is only %.1f m from the router — too close to sense well"
                               % (idx, d))
                if d > 12.0:
                    out.append("node %d is %.1f m from the router — expect a weak, noisy link"
                               % (idx, d))
        for idx, n in nodes.items():
            nx, ny = n.center()
            if nx < 0 or ny < 0 or nx > self.width or ny > self.height:
                out.append("node %d is outside the room rectangle" % idx)
        _, geo_notes = self.geometry_score()
        out.extend(geo_notes)
        return out



# ==========================================================================
# SOUND  --  everything synthesised at runtime. No files, no extra packages.
#   v4 adds a real little DSP toolbox (noise, filters, FM, comb reverb,
#   tremolo) and a full event bank: boot, arm, disarm, lock, loss, calibration,
#   UI feedback. Alert tones are selectable and all are generated from maths.
# ==========================================================================
SR = 44100

ALERT_SOUNDS = [
    "Off",
    "Sonar ping",
    "Radar lock",
    "Two-tone alert",
    "Warning tone",
    "Sub pulse",
    "Deep alarm",
    "Sweep alarm",
    "Klaxon",
    "Chirp",
    "Pulse rise",
    "Comms buzz",
    "Geiger burst",
    "Bell strike",
    "Soft chime",
]


# ---- primitives ----------------------------------------------------------
def _t(dur, sr=SR):
    return np.arange(max(1, int(dur * sr))) / float(sr)


def _sine(f, dur, phase=0.0):
    return np.sin(2 * np.pi * f * _t(dur) + phase)


def _sweep(f0, f1, dur, sr=SR, log=False):
    n = max(1, int(dur * sr))
    if log and f0 > 0 and f1 > 0:
        f = np.geomspace(f0, f1, n)
    else:
        f = np.linspace(f0, f1, n)
    return np.sin(2 * np.pi * np.cumsum(f) / sr)


def _square(f, dur, duty=0.5):
    return np.where((_t(dur) * f) % 1.0 < duty, 1.0, -1.0)


def _saw(f, dur):
    return 2.0 * ((_t(dur) * f) % 1.0) - 1.0


def _noise(dur, seed=None):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(max(1, int(dur * SR)))


def _env(dur, decay, attack=0.004, sr=SR):
    t = _t(dur, sr)
    return np.minimum(1.0, t / max(1e-5, attack)) * np.exp(-t / max(1e-4, decay))


def _adsr(dur, a=0.01, d=0.08, s=0.5, r=0.2):
    n = max(1, int(dur * SR))
    na, nd = int(a * SR), int(d * SR)
    nr = int(r * SR)
    ns = max(0, n - na - nd - nr)
    out = np.concatenate([
        np.linspace(0, 1, max(1, na)),
        np.linspace(1, s, max(1, nd)),
        np.full(ns, s),
        np.linspace(s, 0, max(1, nr)),
    ])
    return out[:n] if out.size >= n else np.pad(out, (0, n - out.size))


def _lp(sig, cutoff, sr=SR):
    """One-pole low-pass. Cheap, and exactly the colour we want."""
    a = math.exp(-2.0 * math.pi * cutoff / sr)
    out = np.empty_like(sig)
    y = 0.0
    for i in range(sig.size):
        y = (1 - a) * sig[i] + a * y
        out[i] = y
    return out


def _lp_fast(sig, cutoff, sr=SR):
    """Vectorised approximation of _lp for long buffers (FFT brick-ish)."""
    n = sig.size
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec *= 1.0 / (1.0 + (freqs / max(1.0, cutoff)) ** 2) ** 0.5
    return np.fft.irfft(spec, n)


def _hp_fast(sig, cutoff, sr=SR):
    n = sig.size
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    r = (freqs / max(1.0, cutoff))
    spec *= r / (1.0 + r ** 2) ** 0.5
    return np.fft.irfft(spec, n)


def _bp_fast(sig, f0, q=4.0, sr=SR):
    n = sig.size
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    bw = max(1.0, f0 / q)
    spec *= np.exp(-((freqs - f0) ** 2) / (2 * bw * bw))
    return np.fft.irfft(spec, n)


def _fm(carrier, ratio, index, dur, sr=SR):
    """Simple 2-operator FM -- gives metallic / bell / alarm timbres."""
    t = _t(dur, sr)
    mod = np.sin(2 * np.pi * carrier * ratio * t)
    return np.sin(2 * np.pi * carrier * t + index * mod)


def _delay(sig, ms, feedback=0.35, mix=0.35, sr=SR):
    d = int(ms * sr / 1000.0)
    if d <= 0 or d >= sig.size:
        return sig
    out = sig.astype(np.float64).copy()
    tap = sig.astype(np.float64).copy()
    for _ in range(4):
        tap = np.concatenate([np.zeros(d), tap[:-d]]) * feedback
        out += tap * mix
    return out


def _reverb(sig, amount=0.3, size=1.0):
    """Cheap Schroeder-ish tail from three combs. Adds room to alerts."""
    out = sig.astype(np.float64).copy()
    for ms, fb in ((29.7 * size, 0.72), (37.1 * size, 0.68), (41.1 * size, 0.65)):
        out += _delay(sig, ms, feedback=fb, mix=amount * 0.5)
    return out


def _trem(sig, rate, depth=0.5, sr=SR):
    t = np.arange(sig.size) / sr
    return sig * (1.0 - depth + depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t)))


def _seq(*parts):
    """Concatenate signals; a float means 'that many seconds of silence'."""
    out = []
    for p in parts:
        if isinstance(p, (int, float)):
            out.append(np.zeros(max(1, int(p * SR))))
        else:
            out.append(np.asarray(p, dtype=np.float64))
    return np.concatenate(out) if out else np.zeros(1)


def _mix(*parts):
    n = max(p.size for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:p.size] += p
    return out


def _wav_bytes(sig, gain=1.0):
    sig = np.asarray(sig, dtype=np.float64)
    peak = float(np.max(np.abs(sig))) + 1e-9
    sig = sig / peak * 0.62 * clamp(gain, 0.0, 1.5)
    fade = min(int(0.006 * SR), max(1, sig.size // 8))
    sig[-fade:] *= np.linspace(1, 0, fade)
    sig[:min(64, sig.size)] *= np.linspace(0, 1, min(64, sig.size))
    pcm = np.clip(sig * 32767, -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---- the bank ------------------------------------------------------------
def synth(kind):
    k = kind

    # ------------- selectable alert tones -------------
    if k == "Sonar ping":
        s = (_sweep(920, 840, 0.55) * _env(0.55, 0.16) +
             0.25 * _sweep(1840, 1680, 0.55) * _env(0.55, 0.07))
        return _wav_bytes(_reverb(s, 0.34, 1.3))

    if k == "Radar lock":
        blips = []
        for i, f in enumerate((880, 1180, 1560)):
            blips.append(_sine(f, 0.055) * _env(0.055, 0.02, 0.002))
            blips.append(0.028)
        tail = _sine(1560, 0.42) * _env(0.42, 0.13, 0.003) * 0.55
        return _wav_bytes(_reverb(_seq(*blips, tail), 0.30))

    if k == "Two-tone alert":
        a = _sine(640, 0.11) * _env(0.11, 0.09, 0.006)
        b = _sine(960, 0.17) * _env(0.17, 0.11, 0.006)
        return _wav_bytes(_seq(a, 0.02, b))

    if k == "Warning tone":
        s = _fm(196, 1.41, 5.5, 0.85) * _adsr(0.85, 0.02, 0.15, 0.55, 0.35)
        s = _trem(s, 7.5, 0.45)
        return _wav_bytes(_reverb(_lp_fast(s, 2600), 0.35, 1.6))

    if k == "Sub pulse":
        s = (_sweep(210, 140, 0.34) * _env(0.34, 0.11) +
             0.32 * _sweep(420, 280, 0.34) * _env(0.34, 0.05))
        return _wav_bytes(s)

    if k == "Deep alarm":
        s = _fm(88, 2.0, 7.0, 1.1) * _adsr(1.1, 0.04, 0.25, 0.6, 0.4)
        s = _mix(s, 0.35 * _sweep(150, 96, 1.1) * _env(1.1, 0.5))
        return _wav_bytes(_reverb(_lp_fast(s, 1400), 0.4, 1.8))

    if k == "Sweep alarm":
        up = _sweep(520, 1180, 0.42, log=True)
        dn = _sweep(1180, 520, 0.42, log=True)
        s = _seq(up, dn) * _adsr(0.84, 0.02, 0.1, 0.85, 0.15)
        return _wav_bytes(_reverb(_lp_fast(s, 4200), 0.25))

    if k == "Klaxon":
        parts = []
        for f in (494, 370, 494, 370):
            parts.append(_lp_fast(_square(f, 0.13, 0.42), 2400) * _adsr(0.13, 0.006, 0.03, 0.8, 0.03))
        return _wav_bytes(_reverb(_seq(*parts), 0.22))

    if k == "Chirp":
        return _wav_bytes(_sweep(420, 1750, 0.16, log=True) * _env(0.16, 0.09, 0.003))

    if k == "Pulse rise":
        parts = []
        for i, f in enumerate((520, 660, 820, 1040)):
            parts.append(_sine(f, 0.07) * _env(0.07, 0.03, 0.003) * (0.6 + 0.13 * i))
            parts.append(0.045)
        return _wav_bytes(_reverb(_seq(*parts), 0.3))

    if k == "Comms buzz":
        n = _bp_fast(_noise(0.36, 11), 1500, 3.0)
        s = _trem(n, 28.0, 0.85) * _adsr(0.36, 0.008, 0.05, 0.7, 0.12)
        return _wav_bytes(_reverb(s, 0.2))

    if k == "Geiger burst":
        out = np.zeros(int(0.7 * SR))
        rng = np.random.default_rng(4)
        for _ in range(14):
            p = rng.integers(0, out.size - 400)
            click = _hp_fast(_noise(0.008, int(p) % 999), 2200) * _env(0.008, 0.0016, 0.0004)
            out[p:p + click.size] += click * rng.uniform(0.5, 1.0)
        return _wav_bytes(_reverb(out, 0.25))

    if k == "Bell strike":
        s = (_fm(523, 1.41, 3.2, 1.3) * _env(1.3, 0.42, 0.002) +
             0.4 * _fm(1046, 2.83, 1.6, 1.3) * _env(1.3, 0.18, 0.002))
        return _wav_bytes(_reverb(s, 0.42, 2.0))

    if k == "Soft chime":
        s = (_sine(1046, 0.95) * _env(0.95, 0.32) +
             0.6 * _sine(1568, 0.95) * _env(0.95, 0.24))
        return _wav_bytes(_reverb(s, 0.35, 1.5))

    # ------------- system / state events -------------
    if k == "boot_rise":
        s = _mix(_sweep(48, 190, 2.0, log=True) * _adsr(2.0, 0.5, 0.6, 0.7, 0.7),
                 0.30 * _sweep(96, 380, 2.0, log=True) * _adsr(2.0, 0.7, 0.5, 0.6, 0.7),
                 0.10 * _lp_fast(_noise(2.0, 3), 900) * _adsr(2.0, 0.9, 0.4, 0.5, 0.6))
        return _wav_bytes(_reverb(s, 0.35, 2.0), 0.8)

    if k == "boot_step":
        s = _hp_fast(_noise(0.035, 7), 1800) * _env(0.035, 0.010, 0.001)
        s = _mix(s * 0.6, _sine(2100, 0.05) * _env(0.05, 0.014, 0.001) * 0.45)
        return _wav_bytes(s, 0.55)

    if k == "boot_done":
        parts = []
        for f in (392, 523, 784):
            parts.append(_sine(f, 0.16) * _env(0.16, 0.10, 0.004))
            parts.append(0.03)
        s = _seq(*parts, _mix(_sine(784, 0.7) * _env(0.7, 0.30, 0.01),
                              0.5 * _sine(1176, 0.7) * _env(0.7, 0.22, 0.01)))
        return _wav_bytes(_reverb(s, 0.42, 1.8))

    if k == "arm":
        thud = _sweep(140, 62, 0.42, log=True) * _env(0.42, 0.16)
        lock = _hp_fast(_noise(0.05, 21), 2500) * _env(0.05, 0.016, 0.001)
        tone_a = _sine(740, 0.10) * _env(0.10, 0.06, 0.004)
        tone_b = _sine(494, 0.30) * _env(0.30, 0.16, 0.004)
        s = _seq(tone_a, 0.015, _mix(tone_b, thud * 0.8, lock * 0.5))
        return _wav_bytes(_reverb(s, 0.30, 1.4))

    if k == "disarm":
        tone_a = _sine(494, 0.10) * _env(0.10, 0.06, 0.004)
        tone_b = _sine(740, 0.24) * _env(0.24, 0.13, 0.004)
        air = _lp_fast(_noise(0.30, 33), 1200) * _env(0.30, 0.10, 0.02) * 0.28
        return _wav_bytes(_reverb(_seq(tone_a, 0.015, _mix(tone_b, air)), 0.28))

    if k == "connect":
        parts = []
        for f in (620, 930):
            parts.append(_sine(f, 0.07) * _env(0.07, 0.035, 0.003))
            parts.append(0.02)
        return _wav_bytes(_reverb(_seq(*parts), 0.22), 0.75)

    if k == "disconnect":
        parts = []
        for f in (930, 620):
            parts.append(_sine(f, 0.07) * _env(0.07, 0.035, 0.003))
            parts.append(0.02)
        return _wav_bytes(_reverb(_seq(*parts), 0.22), 0.75)

    if k == "warm_done":
        s = _seq(_sine(660, 0.09) * _env(0.09, 0.05, 0.003), 0.02,
                 _sine(990, 0.22) * _env(0.22, 0.12, 0.003))
        return _wav_bytes(_reverb(s, 0.3), 0.7)

    if k == "cal_start":
        s = _mix(_sweep(300, 700, 0.55, log=True) * _adsr(0.55, 0.05, 0.15, 0.6, 0.2),
                 0.25 * _lp_fast(_noise(0.55, 5), 1500) * _adsr(0.55, 0.1, 0.2, 0.4, 0.2))
        return _wav_bytes(_reverb(s, 0.3, 1.4), 0.8)

    if k == "cal_tick":
        return _wav_bytes(_sine(1240, 0.045) * _env(0.045, 0.018, 0.002), 0.45)

    if k == "cal_last":
        return _wav_bytes(_sine(1660, 0.09) * _env(0.09, 0.04, 0.002), 0.6)

    if k == "cal_done":
        parts = []
        for f in (523, 659, 784, 1046):
            parts.append(_sine(f, 0.11) * _env(0.11, 0.07, 0.004))
            parts.append(0.015)
        return _wav_bytes(_reverb(_seq(*parts), 0.38, 1.6))

    if k == "cal_fail":
        s = _mix(_lp_fast(_square(146, 0.45, 0.5), 1100) * _adsr(0.45, 0.01, 0.1, 0.6, 0.2),
                 0.4 * _sweep(300, 150, 0.45) * _env(0.45, 0.18))
        return _wav_bytes(_reverb(s, 0.25))

    if k == "clear":
        s = _seq(_sine(880, 0.09) * _env(0.09, 0.06, 0.005), 0.015,
                 _sine(587, 0.20) * _env(0.20, 0.13, 0.005))
        return _wav_bytes(_reverb(s * 0.6, 0.3))

    if k == "lock":
        parts = []
        for f in (1200, 1600):
            parts.append(_sine(f, 0.04) * _env(0.04, 0.016, 0.002))
            parts.append(0.018)
        s = _seq(*parts, _sine(1900, 0.16) * _env(0.16, 0.06, 0.002) * 0.7)
        return _wav_bytes(_delay(_reverb(s, 0.25), 78, 0.3, 0.3), 0.7)

    if k == "lost":
        s = _sweep(1500, 520, 0.28, log=True) * _env(0.28, 0.11, 0.003)
        return _wav_bytes(_reverb(s * 0.7, 0.28), 0.65)

    if k == "error":
        s = _mix(_lp_fast(_square(110, 0.30, 0.35), 900) * _adsr(0.30, 0.004, 0.05, 0.8, 0.1),
                 0.5 * _lp_fast(_noise(0.30, 13), 600) * _env(0.30, 0.09))
        return _wav_bytes(s, 0.85)

    if k == "record_start":
        return _wav_bytes(_reverb(_seq(_sine(880, 0.06) * _env(0.06, 0.03, 0.003), 0.03,
                                       _sine(880, 0.16) * _env(0.16, 0.09, 0.003)), 0.2), 0.6)

    if k == "record_stop":
        return _wav_bytes(_reverb(_sine(660, 0.20) * _env(0.20, 0.10, 0.004), 0.2), 0.6)

    if k in ("ui_click", "tick"):
        s = _hp_fast(_noise(0.012, 2), 3000) * _env(0.012, 0.004, 0.0006)
        return _wav_bytes(_mix(s, 0.5 * _sine(2600, 0.014) * _env(0.014, 0.005, 0.0005)), 0.35)

    if k == "ui_tab":
        return _wav_bytes(_sine(1500, 0.035) * _env(0.035, 0.014, 0.002), 0.4)

    if k == "ui_toggle":
        return _wav_bytes(_seq(_sine(900, 0.025) * _env(0.025, 0.01, 0.002), 0.012,
                               _sine(1350, 0.05) * _env(0.05, 0.02, 0.002)), 0.42)

    if k == "go":
        return _wav_bytes(_reverb(_sweep(500, 1000, 0.26, log=True) * _env(0.26, 0.14, 0.004), 0.3), 0.8)

    if k == "shutdown":
        s = _mix(_sweep(320, 60, 1.1, log=True) * _adsr(1.1, 0.02, 0.3, 0.5, 0.5),
                 0.3 * _lp_fast(_noise(1.1, 17), 700) * _env(1.1, 0.35, 0.02))
        return _wav_bytes(_reverb(s, 0.35, 1.8), 0.8)

    return None


class SoundBank:
    """
    Renders on demand, caches the bytes, plays without blocking the UI.
    Windows uses winsound from memory; everything else drops a temp wav and
    uses QSound. Failing both, a system beep.
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.cache = {}
        self.tmp = {}
        self.enabled = True
        self._last = {}

    def volume(self):
        try:
            return clamp(float(self.cfg.get("volume", 0.7)), 0.0, 1.0)
        except Exception:
            return 0.7

    def prewarm(self, kinds):
        for k in kinds:
            try:
                self.render(k)
            except Exception:
                pass

    def render(self, kind):
        if kind not in self.cache:
            self.cache[kind] = synth(kind)
        return self.cache[kind]

    def play(self, kind, throttle=0.0):
        if not self.enabled or not kind or kind == "Off":
            return
        if self.volume() <= 0.001:
            return
        now = time.time()
        if throttle and now - self._last.get(kind, 0) < throttle:
            return
        self._last[kind] = now
        try:
            raw = self.render(kind)
        except Exception:
            raw = None
        if raw is None:
            return
        if sys.platform.startswith("win"):
            try:
                import winsound
                winsound.PlaySound(raw, winsound.SND_MEMORY | winsound.SND_ASYNC)
                return
            except Exception:
                pass
        try:
            from PyQt5.QtMultimedia import QSound
            if kind not in self.tmp:
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="gv_")
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                self.tmp[kind] = path
            QSound.play(self.tmp[kind])
            return
        except Exception:
            pass
        try:
            QtWidgets.QApplication.beep()
        except Exception:
            pass

    def ui(self, kind="ui_click"):
        if self.cfg.get("sound_ui", True):
            self.play(kind, throttle=0.05)

    def state(self, kind):
        if self.cfg.get("sound_states", True):
            self.play(kind)

    def cleanup(self):
        for p in self.tmp.values():
            try:
                os.remove(p)
            except Exception:
                pass



# ==========================================================================
# CSI PARSING + DATA SOURCES
# ==========================================================================
class CSIParser:
    def __init__(self):
        self.cols = None
        self.good = 0
        self.bad = 0
        self.header_seen = False

    def parse(self, line):
        line = line.strip()
        if not line:
            return None
        if "[" not in line and "rssi" in line.lower() and "," in line:
            names = [n.strip().lower() for n in line.split(",")]
            self.cols = {n: i for i, n in enumerate(names)}
            self.header_seen = True
            return None
        i = line.find("[")
        j = line.rfind("]")
        if i < 0 or j <= i:
            return None
        head = [h.strip() for h in line[:i].rstrip(", ").split(",")]
        try:
            vals = np.array(line[i + 1:j].replace(",", " ").split(), dtype=np.float32)
        except ValueError:
            self.bad += 1
            return None
        if vals.size < 16:
            self.bad += 1
            return None
        vals = vals[: (vals.size // 2) * 2].reshape(-1, 2)
        amp = np.sqrt(vals[:, 0] ** 2 + vals[:, 1] ** 2)
        meta = {"rssi": None, "channel": None}
        try:
            if self.cols:
                for key, col in (("rssi", "rssi"), ("channel", "channel")):
                    idx = self.cols.get(col)
                    if idx is not None and idx < len(head):
                        meta[key] = int(float(head[idx]))
            elif len(head) > 3 and head[0].upper().startswith("CSI"):
                meta["rssi"] = int(float(head[3]))
        except (ValueError, TypeError):
            pass
        self.good += 1
        return amp, meta


class SerialWorker(QtCore.QThread):
    batch = QtCore.pyqtSignal(int, list)
    failed = QtCore.pyqtSignal(int, str)
    opened = QtCore.pyqtSignal(int, str)

    def __init__(self, index, port, baud, parser):
        super().__init__()
        self.index, self.port, self.baud, self.parser = index, port, baud, parser
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if serial is None:
            self.failed.emit(self.index, "pyserial is not installed")
            return
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.15)
            ser.reset_input_buffer()
        except Exception as e:
            self.failed.emit(self.index, "%s: %s" % (self.port, e))
            return
        self.opened.emit(self.index, self.port)
        buf, last = [], time.time()
        try:
            while not self._stop:
                try:
                    raw = ser.readline()
                except Exception as e:
                    self.failed.emit(self.index, "read error: %s" % e)
                    break
                if raw:
                    pkt = self.parser.parse(raw.decode("utf-8", errors="ignore"))
                    if pkt:
                        buf.append(pkt)
                now = time.time()
                if buf and (len(buf) >= 24 or now - last > 0.05):
                    self.batch.emit(self.index, buf)
                    buf, last = [], now
        finally:
            try:
                ser.close()
            except Exception:
                pass


class UdpWorker(QtCore.QThread):
    """
    ONE receiver for ALL wireless nodes.

    Serial needs a worker per port. UDP does not: every node sends to the same
    socket and stamps its own identity into byte 3 of the datagram, so a single
    thread serves all four.

    Node identity comes from the PACKET, never from the source IP. A node that
    picks up a different DHCP lease is still the same node -- which is exactly
    what kills the old "boards swapped USB sockets" class of bug.

    Wire format produced by the node firmware (little-endian):
        0     'G'
        1     'V'
        2     protocol version (1)
        3     node id (1..4)
        4     number of records in this datagram
        5     bytes of CSI per record (128)
        6-7   reserved
        8-11  uint32 sequence number of the FIRST record
      then per record, 2 + csi_len bytes:
        int8 rssi, 1 pad byte, then csi_len bytes of int8 I/Q pairs

    Also announces the console on the discovery port so nodes can find it.
    """
    batch = QtCore.pyqtSignal(int, list)
    pbatch = QtCore.pyqtSignal(int, int, list)     # tx, rx, records
    failed = QtCore.pyqtSignal(int, str)
    opened = QtCore.pyqtSignal(int, str)
    lost = QtCore.pyqtSignal(int)

    HDR = 12
    MAGIC = b"GV"
    HELLO = b"GHOSTVISION"
    ANNOUNCE_EVERY = 2.0     # seconds. See note in _announce_targets.
    SILENT_AFTER = 4.0       # seconds without a packet before a node is "lost"

    def __init__(self, data_port=8266, disc_port=8267, router_ip="10.8.0.1"):
        super().__init__()
        self.data_port = int(data_port)
        self.disc_port = int(disc_port)
        self.router_ip = router_ip
        self._stop = False
        self.bind_ip = None
        self.use_link = 0          # 0 = router link, the proven one
        self.link_seen = {}        # (node, link) -> records seen

    def stop(self):
        self._stop = True

    # ---- discovery ----
    def _local_ip(self):
        """
        Which of our addresses would reach the sensing router? No packet is
        sent -- connect() on a UDP socket only fixes a destination.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.router_ip, 9))
            return s.getsockname()[0]
        except OSError:
            return None
        finally:
            s.close()

    def _announce_socket(self):
        """
        Bind announcements to the SENSING adapter explicitly.

        255.255.255.255 is the limited broadcast address and Windows sends it
        out exactly one interface, chosen by the routing table. The sensing
        Ethernet adapter deliberately has no default gateway, so Windows picks
        the internet-facing adapter instead and every announcement vanishes
        into the mobile connection. Binding to the sensing address and also
        sending to the subnet broadcast is what makes discovery work at all.
        """
        ip = self._local_ip()
        if not ip:
            return None, []
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((ip, 0))
        except OSError:
            s.close()
            return None, []
        self.bind_ip = ip
        subnet_bcast = ".".join(ip.split(".")[:3] + ["255"])
        return s, [subnet_bcast, "255.255.255.255"]

    # ---- decode ----
    def _decode(self, data):
        """
        Yield (node_id, (amp, meta)) for every record we want.

        PROTOCOL 2 CARRIES MULTIPLE SENSING PATHS PER NODE.
        Firmware v2 tags each record with a link id: 0 = router, 1..4 = which
        sibling node transmitted it. A node now reports up to four different
        physical paths, interleaved in the same stream.

        An Engine measures how much its window wobbles. A window holding
        records from four DIFFERENT paths wobbles enormously whether or not
        anyone is in the room -- the paths simply have different channel
        responses. Feeding mixed links into one Engine would not improve
        accuracy, it would destroy it.

        So every (receiving node, transmitting link) pair gets its OWN Engine
        downstream -- sixteen in all -- and records are yielded tagged with
        both. Each pair is also counted in link_seen so a silent path is
        visible. A node's own link is skipped: a node cannot hear itself.
        """
        if len(data) < self.HDR or data[0:2] != self.MAGIC:
            return
        ver, node, n_recs, csi_len = data[2], data[3], data[4], data[5]
        if ver not in (1, 2) or csi_len < 16 or node < 1 or node > NODE_COUNT:
            return
        rec_bytes = 2 + csi_len
        for k in range(n_recs):
            off = self.HDR + k * rec_bytes
            if off + rec_bytes > len(data):
                return
            rssi = struct.unpack_from("<b", data, off)[0]
            link = data[off + 1] if ver >= 2 else 0
            if link > NODE_COUNT:
                continue
            self.link_seen[(node, link)] = self.link_seen.get((node, link), 0) + 1
            if link == node:
                continue                      # a node cannot hear itself
            raw = np.frombuffer(data, dtype=np.int8, count=csi_len, offset=off + 2)
            iq = raw.reshape(-1, 2).astype(np.float32)
            amp = np.sqrt(iq[:, 0] ** 2 + iq[:, 1] ** 2)
            yield node, link, (amp, {"rssi": int(rssi), "channel": None})

    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.data_port))
            sock.settimeout(0.1)
        except Exception as e:
            self.failed.emit(0, "cannot listen on UDP %d: %s  "
                                "(another copy of the console may be running)"
                             % (self.data_port, e))
            return

        ann, targets = self._announce_socket()
        if ann is None:
            self.failed.emit(0, "cannot announce the console — no route to %s. "
                                "Is the Ethernet cable to the sensing router in?"
                             % self.router_ip)

        bufs = {}
        last_seen = {}
        live = set()
        last_flush = time.time()
        last_ann = 0.0
        t_start = time.time()
        reported = False

        try:
            while not self._stop:
                now = time.time()

                if ann is not None and now - last_ann >= self.ANNOUNCE_EVERY:
                    last_ann = now
                    for dst in targets:
                        try:
                            ann.sendto(self.HELLO, (dst, self.disc_port))
                        except OSError:
                            pass

                try:
                    data, addr = sock.recvfrom(8192)
                except socket.timeout:
                    data = None
                except OSError:
                    data = None

                if data:
                    for node, link, pkt in self._decode(data):
                        if node not in live:
                            live.add(node)
                            self.opened.emit(node, addr[0])
                        last_seen[node] = now
                        bufs.setdefault((link, node), []).append(pkt)

                now = time.time()
                if now - last_flush >= 0.05:
                    for (link, node), b in bufs.items():
                        if not b:
                            continue
                        # DEFECT FOUND AND FIXED: this also emitted `batch`
                        # for link 0, and the console listens to BOTH signals.
                        # Every router record was therefore pushed into the
                        # same Engine twice. The window filled with duplicate
                        # pairs, so 20 samples covered only half as much real
                        # time -- the channel had less chance to change, the
                        # measured wobble fell, and nodes went half-deaf.
                        # `pbatch` already carries link 0; `batch` stays for
                        # the serial and simulation sources only.
                        self.pbatch.emit(link, node, b)
                    bufs = {}
                    last_flush = now

                    if not reported and (now - t_start) > 12.0:
                        reported = True
                        peers = sorted(k for k in self.link_seen if k[1] != 0)
                        if peers:
                            self.failed.emit(-1, "multi-link firmware detected: %d "
                                                 "sibling paths alive alongside the "
                                                 "%d router paths."
                                             % (len(peers),
                                                len([k for k in self.link_seen
                                                     if k[1] == 0])))

                    # A wireless node cannot report its own death. Silence is
                    # the only symptom we get, so treat it as one.
                    for node in list(live):
                        if now - last_seen.get(node, 0) > self.SILENT_AFTER:
                            live.discard(node)
                            self.lost.emit(node)
        finally:
            for s in (sock, ann):
                try:
                    if s is not None:
                        s.close()
                except Exception:
                    pass


class SimWorker(QtCore.QThread):
    """
    Four virtual nodes plus a virtual occupant.

    The occupant's effect on each node comes from the same geometry the map
    uses: a node reacts when the walker is near the straight line between that
    node and the router. v4 gives the walker smooth, eased motion with variable
    speed, realistic pauses and micro-motion while seated, so the tracker and
    the dossier can be judged honestly before hardware exists.

    This proves the plumbing and the display. It is NOT evidence about physics.
    """
    batch = QtCore.pyqtSignal(int, list)
    walker = QtCore.pyqtSignal(float, float, bool)

    def __init__(self, room, sigma=0.85):
        super().__init__()
        self.room = room
        self.sigma = sigma
        self._stop = False
        self.t0 = time.time()
        self._prev = None

    def stop(self):
        self._stop = True

    @staticmethod
    def _ease(u):
        u = clamp(u, 0.0, 1.0)
        return u * u * (3 - 2 * u)

    def _path_point(self, t):
        """
        90 s scenario:
          0-6    empty
          6-34   patrols the perimeter with eased corners and a mid pause
          34-39  crosses to a seat
          39-66  seated, micro-motion only
          66-72  stands and walks to the door
          72-90  empty
        """
        cycle = t % 90.0
        w, h = self.room.width, self.room.height
        pad = 0.75
        pts = [(pad, pad), (w - pad, pad), (w - pad, h - pad), (pad, h - pad)]

        if cycle < 6:
            return None, False
        if cycle < 34:
            u = (cycle - 6) / 28.0
            if 0.42 < u < 0.52:                       # stops to look at something
                seg = 0.42 * 4.0
                side = int(seg)
                f = self._ease(seg - side)
                a, b = pts[side % 4], pts[(side + 1) % 4]
                jx = 0.02 * math.sin(t * 2.3)
                return (a[0] + (b[0] - a[0]) * f + jx,
                        a[1] + (b[1] - a[1]) * f + jx), False
            per = u * 4.0
            side = int(per)
            f = self._ease(per - side)
            a, b = pts[side % 4], pts[(side + 1) % 4]
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), True
        if cycle < 39:
            u = self._ease((cycle - 34) / 5.0)
            a = (pad, h - pad)
            b = (w * 0.63, h * 0.46)
            return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u), True
        if cycle < 66:
            j = 0.035 * math.sin(t * 1.7) + 0.015 * math.sin(t * 0.43)
            return (w * 0.63 + j, h * 0.46 + j * 0.7), False
        if cycle < 72:
            u = self._ease((cycle - 66) / 6.0)
            a = (w * 0.63, h * 0.46)
            b = (pad, pad)
            return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u), True
        return None, False

    @staticmethod
    def _seg_dist(px, py, ax, ay, bx, by):
        vx, vy = bx - ax, by - ay
        L = vx * vx + vy * vy
        if L < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L))
        return math.hypot(px - (ax + t * vx), py - (ay + t * vy))

    def run(self):
        rng = np.random.default_rng(8)
        base = 20 + 14 * np.abs(np.sin(np.linspace(0, 3.2, 56)))
        phase = 0.0
        while not self._stop:
            t = time.time() - self.t0
            pos, moving = self._path_point(t)
            r = self.room.router()
            nodes = self.room.nodes()
            if pos:
                self.walker.emit(pos[0], pos[1], moving)
            else:
                self.walker.emit(-1, -1, False)

            speed = 0.0
            if pos and self._prev:
                speed = math.hypot(pos[0] - self._prev[0], pos[1] - self._prev[1]) / 0.06
            self._prev = pos

            for idx in range(1, NODE_COUNT + 1):
                act = 0.0
                if pos and r and idx in nodes:
                    nx, ny = nodes[idx].center()
                    rx, ry = r.center()
                    d = self._seg_dist(pos[0], pos[1], nx, ny, rx, ry)
                    strength = math.exp(-(d * d) / (2 * self.sigma * self.sigma))
                    # a body near the node itself also disturbs it, less strongly
                    dn = math.hypot(pos[0] - nx, pos[1] - ny)
                    near = 0.35 * math.exp(-(dn * dn) / (2 * 1.1 * 1.1))
                    drive = 1.15 * clamp(0.35 + speed * 0.9, 0.3, 1.6) if moving else 0.08
                    act = (strength + near) * drive
                out = []
                for _ in range(5):
                    phase += 0.15
                    amp = base * (1 + 0.006 * rng.standard_normal(56))
                    if act > 0.001:
                        amp = amp * (1 + act * 0.05 *
                                     np.sin(np.linspace(0, 6, 56) + phase) *
                                     (1 + 0.6 * rng.standard_normal(56)))
                    out.append((amp.astype(np.float32), {"rssi": -44 - idx * 3, "channel": 6}))
                self.batch.emit(idx, out)
            self.msleep(60)


class CameraWorker(QtCore.QThread):
    """
    OPTICAL panel source. This is an OPERATOR AID ONLY -- it is a normal
    webcam and it has nothing whatsoever to do with the CSI sensing chain.
    Nothing from this feed influences detection, and the product's
    'camera-free sensing' claim only holds if you say so plainly when this
    panel is on screen.
    """
    frame = QtCore.pyqtSignal(QtGui.QImage)
    failed = QtCore.pyqtSignal(str)
    info = QtCore.pyqtSignal(str)

    def __init__(self, index=0, fps=15, mirror=True):
        super().__init__()
        self.index = int(index)
        self.fps = int(clamp(fps, 1, 30))
        self.mirror = bool(mirror)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not HAVE_CV2:
            self.failed.emit("opencv-python is not installed — run:  pip install opencv-python")
            return
        cap = None
        try:
            backends = []
            if sys.platform.startswith("win"):
                backends = [getattr(cv2, "CAP_DSHOW", 0), getattr(cv2, "CAP_MSMF", 0)]
            for be in (backends + [0]):
                try:
                    cap = cv2.VideoCapture(self.index, be) if be else cv2.VideoCapture(self.index)
                    if cap is not None and cap.isOpened():
                        break
                    if cap is not None:
                        cap.release()
                        cap = None
                except Exception:
                    cap = None
            if cap is None or not cap.isOpened():
                self.failed.emit("could not open camera %d — is another app using it?" % self.index)
                return
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            except Exception:
                pass
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            self.info.emit("camera %d open — %dx%d" % (self.index, w, h))
            delay = max(20, int(1000 / self.fps))
            while not self._stop:
                ok, img = cap.read()
                if not ok or img is None:
                    self.msleep(80)
                    continue
                if self.mirror:
                    img = cv2.flip(img, 1)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                hh, ww, ch = img.shape
                qimg = QtGui.QImage(img.data, ww, hh, ch * ww,
                                    QtGui.QImage.Format_RGB888).copy()
                self.frame.emit(qimg)
                self.msleep(delay)
        except Exception as e:
            self.failed.emit("camera error: %s" % e)
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass


def list_cameras(max_probe=5):
    """Best-effort enumeration. Slow on some systems, so it is on demand only."""
    found = []
    if not HAVE_CV2:
        return found
    for i in range(max_probe):
        cap = None
        try:
            cap = cv2.VideoCapture(i, getattr(cv2, "CAP_DSHOW", 0)) \
                if sys.platform.startswith("win") else cv2.VideoCapture(i)
            if cap is not None and cap.isOpened():
                found.append(i)
        except Exception:
            pass
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
    return found



# ==========================================================================
# PER-NODE DETECTION ENGINE
# ==========================================================================
class Engine:
    """
    One instance per node. Pipeline (unchanged from v3 where it was proven --
    do not casually retune this):
      1. amplitude per subcarrier from the I/Q pairs
      2. drop dead subcarriers, divide each packet by its own mean (kills the
         radio's automatic gain jumps, which otherwise look exactly like motion)
      3. per-subcarrier wobble over a sliding window; take the MEDIAN across
         subcarriers so one glitching subcarrier cannot fake an alarm
      4. baseline learned only while the room is judged clear, so a person
         standing still never becomes "normal"
      5. relative rise over baseline -> 0-100 score -> enter/exit thresholds
         with consecutive-hit counting

    v4 adds derived, honest read-outs on top of the same score:
      · slope        -- is the disturbance growing or fading right now
      · micro        -- residual low-amplitude wobble (EXPERIMENTAL)
      · snr          -- how far above measured room noise, in sigmas
      · quality      -- link health 0-1, used to weight the zone solver
    None of these are new physics. They are descriptions of the same one number.
    """
    WARM_SAMPLES = 200          # ~10 s at the 20 Hz tick

    def __init__(self, cfg, index, tx=0):
        """
        index = the RECEIVING node (1..4).
        tx    = the TRANSMITTER: 0 for the router, 1..4 for a sibling node.

        tx=0 engines are the original four and read/write node_thresholds, so
        every existing calibration and settings file still applies unchanged.
        Sibling engines use path_thresholds, keyed "tx-rx".
        """
        self.cfg = cfg
        self.index = index
        self.tx = int(tx)
        self.pkey = "%d-%d" % (self.tx, index)
        self.label = ("router>N%d" % index) if self.tx == 0 else \
                     ("N%d>N%d" % (self.tx, index))
        if self.tx == 0:
            self.threshold = float(cfg["node_thresholds"][index - 1])
        else:
            self.threshold = float(cfg.get("path_thresholds", {})
                                      .get(self.pkey, 25.0))
        self.S = 0
        self.win = collections.deque(maxlen=int(cfg["window"]))
        self.win_t = collections.deque(maxlen=int(cfg["window"]))
        self.smooth = collections.deque(maxlen=int(cfg["smooth"]))
        self.stretched = False
        self.degraded = False
        self.base_hist = collections.deque(maxlen=900)
        self.rate_hist = collections.deque(maxlen=120)
        self.elev = collections.deque(maxlen=1400)
        self.trace = collections.deque([0.0] * 240, maxlen=240)
        self.baseline = None
        self.base_sd = 1.0
        self.frozen = False

        self.level = 0.0
        self.prev_level = 0.0
        self.slope = 0.0
        self.micro = 0.0
        self.snr = 0.0
        self.quality = 0.0
        self.raw_metric = 0.0
        self.sigma = None
        self.peak = 0.0
        self.hits = 0.0
        self.clears = 0.0
        self.detected = False
        self.presence = False
        self._pres_until = 0.0
        self.state = "OFFLINE"
        self.confidence = 0
        self.flags = []

        self.pkt_total = 0
        self.pkt_count = 0
        self.rate = 0
        self.last_rate_t = time.time()
        self.last_pkt_t = 0.0
        self.rssi = None
        self.connected = False
        self.port = ""

        self.motion_since = None
        self.last_motion_end = None
        self.clear_since = time.time()
        self.events = 0
        self.sat_since = None

        self.cal_active = False
        self.cal_end = 0.0
        self.cal_scores = []
        self.cal_result = None

    # ---- structural tunables ----
    def set_window(self, n):
        self.win = collections.deque(list(self.win)[-int(n):], maxlen=int(n))
        self.win_t = collections.deque(list(self.win_t)[-int(n):], maxlen=int(n))

    def set_smooth(self, n):
        self.smooth = collections.deque(list(self.smooth)[-int(n):], maxlen=int(n))

    def reset_baseline(self):
        self.baseline = None
        self.base_hist.clear()

    def reset_session(self):
        self.peak = 0.0
        self.events = 0

    # ---- ingest ----
    def push(self, packets):
        t = time.time()
        for amp, meta in packets:
            if amp.size != self.S:
                self.S = amp.size
                self.win.clear()
                self.win_t.clear()
            self.win.append(amp)
            self.win_t.append(t)
            if meta.get("rssi") is not None:
                self.rssi = meta["rssi"]
        n = len(packets)
        self.pkt_total += n
        self.pkt_count += n
        if n:
            self.last_pkt_t = time.time()

    # ---- one tick ----
    def tick(self):
        now = time.time()
        if now - self.last_rate_t >= 1.0:
            self.rate = int(self.pkt_count / (now - self.last_rate_t))
            self.pkt_count = 0
            self.last_rate_t = now
            if self.rate > 0:
                self.rate_hist.append(self.rate)

        w = int(self.cfg["window"])

        # ---- GATE 1: timing stretch ---------------------------------------
        # The window is a fixed NUMBER of packets, so the real time it spans
        # depends on the packet rate. Channel variance grows with observation
        # time, so a rate dip inflates the wobble metric with nothing moving.
        # Measured dips to ~26 pkt/s stretch a 20-packet window from 0.40 s to
        # 0.77 s -- easily enough to cross a trigger. Rather than retune the
        # proven maths, refuse to score a window that spans far more time than
        # this node's own normal.
        self.stretched = False
        if len(self.win_t) >= 2 and len(self.rate_hist) >= 5:
            span = self.win_t[-1] - self.win_t[0]
            typical = float(np.median(self.rate_hist))
            if typical > 1.0:
                nominal = (len(self.win_t) - 1) / typical
                tol = float(self.cfg.get("timing_tol", 1.6))
                if nominal > 0 and span > nominal * tol:
                    self.stretched = True

        # ---- GATE 2: collapsed packet rate --------------------------------
        # The console already FLAGGED this condition; it just never acted on
        # it. A node whose link has degraded is not evidence about the room.
        self.degraded = False
        if self.cfg.get("rate_guard", True) and self.rate_hist and self.rate > 0:
            typical = float(np.median(self.rate_hist))
            if typical >= 5 and self.rate < 0.45 * typical:
                self.degraded = True

        # DEFECT FOUND AND FIXED: these were one flag. The timing gate set
        # ready=False, and _state() turns "not ready" into WARMING UP -- so
        # every time the gate fired, the node visibly dropped back to WARMING
        # UP for a moment. The gate is only supposed to skip SCORING one
        # window, not reset what the node reports about itself.
        have_window = len(self.win) >= w and self.S > 0
        ready = have_window and not self.stretched
        self.prev_level = self.level

        if ready:
            mat = np.asarray(self.win, dtype=np.float32)
            mean_s = mat.mean(axis=0)
            live = mean_s > (0.05 * mean_s.max() + 1e-6)
            if live.sum() >= 8:
                mat = mat[:, live]
            norm = mat / (mat.mean(axis=1, keepdims=True) + 1e-6)
            self.sigma = norm.std(axis=0)
            m = float(np.median(self.sigma))
            self.raw_metric = m if self.raw_metric == 0 else \
                self.raw_metric + 0.35 * (m - self.raw_metric)

            if self.baseline is None:
                self.baseline = self.raw_metric
            quiet = self.state in ("AREA CLEAR", "WARMING UP", "CALIBRATING")
            stuck = (self.state == "UNSETTLED" and not self.detected and
                     now - self.clear_since > 120)
            if (not self.frozen and (self.cal_active or quiet or stuck) and
                    (self.cfg["adapt"] or len(self.base_hist) < 60)):
                self.base_hist.append(self.raw_metric)
                target = float(np.percentile(self.base_hist, 35))
                self.baseline += 0.03 * (target - self.baseline)
                if len(self.base_hist) > 30:
                    self.base_sd = max(1e-6, float(np.std(self.base_hist)))

            rel = max(0.0, (self.raw_metric - self.baseline) / (self.baseline + 1e-9))
            score = 100.0 * (1.0 - math.exp(-rel * float(self.cfg["gain"])))
            self.smooth.append(score)
            self.level = sum(self.smooth) / len(self.smooth)
            self.peak = max(self.peak, self.level)
            self.snr = (self.raw_metric - (self.baseline or 0)) / (self.base_sd + 1e-9)

        self.trace.append(self.level)

        # ---- derived descriptors (same number, described differently) ----
        self.slope += 0.25 * ((self.level - self.prev_level) - self.slope)
        recent = list(self.trace)[-40:]
        if len(recent) >= 12:
            arr = np.asarray(recent, dtype=float)
            trend = np.linspace(arr[0], arr[-1], arr.size)
            self.micro = float(np.std(arr - trend))
        q = 1.0
        if self.rate_hist:
            q *= clamp(self.rate / max(8.0, float(np.median(self.rate_hist))), 0.0, 1.0)
        if not self.cfg["node_cal_time"][self.index - 1]:
            q *= 0.7
        if self.state in ("NO SIGNAL", "OFFLINE", "WARMING UP", "CALIBRATING"):
            q = 0.0
        self.quality = q

        warmed = ready and len(self.base_hist) >= self.WARM_SAMPLES
        enter = float(self.threshold)
        leave = enter * float(self.cfg["exit_ratio"])

        if warmed and not self.cal_active and not self.degraded:
            if self.level >= enter:
                self.hits += 1.0
                self.clears = 0.0
            elif self.level <= leave:
                self.clears += 1.0
                self.hits = 0.0
            else:
                self.hits = max(0.0, self.hits - 0.25)
                self.clears += 0.25

            if not self.detected and self.hits >= int(self.cfg["hits_needed"]):
                self.detected = True
                self.motion_since = now
                self.events += 1
            elif self.detected and self.clears >= int(self.cfg["clear_needed"]):
                self.detected = False
                self.motion_since = None
                self.last_motion_end = now
                self.clear_since = now

            self.elev.append((now, self.level > leave))
            span = float(self.cfg["presence_secs"])
            rec = [v for t, v in self.elev if now - t <= span]
            frac = (sum(rec) / len(rec)) if rec else 0.0
            after = self.last_motion_end and (now - self.last_motion_end) < 180
            raw_presence = bool(len(rec) >= span * 12 and frac > 0.55 and
                                self.level > leave and (self.detected or after))
            if raw_presence:
                self.presence = True
                self._pres_until = now + 8.0
            elif now > getattr(self, "_pres_until", 0.0):
                self.presence = False

        if self.cal_active:
            if ready and now > self.cal_end - float(self.cfg["cal_secs"]) * 0.6:
                self.cal_scores.append(self.level)
            if now >= self.cal_end:
                self._finish_cal()

        self._state(now, have_window)
        self._flags(now)
        self._conf(enter)
        return self.state

    def dynamics(self):
        if self.state in ("OFFLINE", "NO SIGNAL", "WARMING UP", "CALIBRATING"):
            return "—"
        if self.slope > 0.9:
            return "RISING"
        if self.slope < -0.9:
            return "FALLING"
        return "STEADY"

    def _state(self, now, ready):
        if not self.connected:
            self.state = "OFFLINE"
        elif self.last_pkt_t == 0 or now - self.last_pkt_t > 3.0:
            self.state = "NO SIGNAL"
        elif self.cal_active:
            self.state = "CALIBRATING"
        elif not ready or len(self.base_hist) < self.WARM_SAMPLES:
            self.state = "WARMING UP"
        elif self.detected:
            quiet_now = self.level < self.threshold
            self.state = "PRESENCE (STILL)" if (self.presence and quiet_now) else "MOTION DETECTED"
        elif self.presence:
            self.state = "PRESENCE (STILL)"
        else:
            leave = self.threshold * float(self.cfg["exit_ratio"])
            self.state = "UNSETTLED" if self.level > leave else "AREA CLEAR"

    def warm_frac(self):
        return clamp(len(self.base_hist) / float(self.WARM_SAMPLES), 0.0, 1.0)

    def _flags(self, now):
        f = []
        if self.level > 96:
            self.sat_since = self.sat_since or now
            if now - self.sat_since > 15:
                f.append(("SCALE MAXED", AMBER))
        else:
            self.sat_since = None
        if self.connected and self.rate_hist and self.rate > 0:
            typical = float(np.median(self.rate_hist))
            if typical >= 5 and self.rate < 0.45 * typical:
                f.append(("LINK DEGRADED", AMBER))
        if self.stretched:
            f.append(("TIMING STRETCHED", AMBER))
        if self.degraded:
            f.append(("RATE COLLAPSED — NOT SCORING", AMBER))
        if self.connected and 0 < self.rate < 8:
            f.append(("LOW PACKET RATE", AMBER))
        if self.detected and self.motion_since and now - self.motion_since > 300:
            f.append(("HELD 5+ MIN — RECALIBRATE", RED))
        if not self.cfg["node_cal_time"][self.index - 1]:
            f.append(("NOT CALIBRATED", BLUE))
        if self.frozen:
            f.append(("BASELINE FROZEN", PURPLE))
        self.flags = f

    def _conf(self, enter):
        if self.state in ("OFFLINE", "NO SIGNAL", "WARMING UP", "CALIBRATING"):
            self.confidence = 0
            return
        margin = (self.level - enter) / max(enter, 1e-6)
        if not self.detected:
            margin = -margin
        c = 50 + 50 * math.tanh(margin * 1.6)
        q = 1.0
        if self.rate_hist:
            q *= min(1.0, self.rate / max(8.0, float(np.median(self.rate_hist))))
        if not self.cfg["node_cal_time"][self.index - 1]:
            q *= 0.75
        self.confidence = int(max(0, min(99, c * q)))

    # ---- calibration ----
    def start_cal(self):
        self.cal_active = True
        self.cal_scores = []
        self.cal_end = time.time() + float(self.cfg["cal_secs"])
        self.frozen = False
        self.reset_baseline()
        self.peak = 0.0

    def cancel_cal(self):
        self.cal_active = False
        self.cal_scores = []

    def _finish_cal(self):
        self.cal_active = False
        s = np.array(self.cal_scores, dtype=float)
        if s.size < 15:
            self.cal_result = (None, "not enough data — check the link")
            return
        mu, sd = float(s.mean()), float(s.std())
        p99 = float(np.percentile(s, 99))
        if p99 > 60 or sd > 25:
            self.cal_result = (None, "room was not quiet (avg %.0f, peaks %.0f) — "
                                     "nothing changed" % (mu, p99))
            return
        if p99 < 0.05 and sd < 0.05:
            # A real radio never sits at exactly zero. This is a frozen or
            # repeating stream -- calibrating on it would arm a dead node.
            self.cal_result = (None, "no channel variation at all — the stream looks "
                                     "frozen or repeating. Check the node is really "
                                     "sending fresh CSI, then recalibrate")
            return
        thr = round(min(85.0, max(8.0, mu + 5.0 * sd, p99 * 1.35)), 1)
        self.threshold = thr
        if self.tx == 0:
            self.cfg["node_thresholds"][self.index - 1] = thr
            self.cfg["node_cal_time"][self.index - 1] = time.strftime("%Y-%m-%d %H:%M")
        else:
            self.cfg.setdefault("path_thresholds", {})[self.pkey] = thr
            self.cfg.setdefault("path_cal_time", {})[self.pkey] = \
                time.strftime("%Y-%m-%d %H:%M")
        self.peak = 0.0
        self.hits = self.clears = 0.0
        self.detected = False
        self.presence = False
        self.motion_since = None
        self.clear_since = time.time()
        self.cal_result = (thr, "quiet: avg %.1f, spread %.1f, worst %.1f (%d samples)"
                                % (mu, sd, p99, s.size))

    def assessment(self):
        now = time.time()
        if self.state == "OFFLINE":
            return "Node %d is not connected." % self.index
        if self.state == "NO SIGNAL":
            return ("Node %d: port open, no CSI arriving. Wrong firmware, not joined to the "
                    "sensing WiFi, or wrong baud rate." % self.index)
        if self.state == "CALIBRATING":
            return "Node %d: learning the empty room, %ds left." % (
                self.index, int(max(0, self.cal_end - now)))
        if self.state == "WARMING UP":
            return ("Node %d: baseline %d%% learned. Nothing can trigger yet."
                    % (self.index, int(100 * self.warm_frac())))
        rssi = ("%d dBm" % self.rssi) if self.rssi is not None else "?"
        link = "%d pkt/s, %s, %d subcarriers" % (self.rate, rssi, self.S)
        if self.state == "AREA CLEAR":
            return ("Node %d: energy %.0f, below the clear line %.0f. Quiet for %s. Link: %s."
                    % (self.index, self.level, self.threshold * self.cfg["exit_ratio"],
                       fmt_dur(now - self.clear_since), link))
        if self.state == "UNSETTLED":
            return ("Node %d: energy %.0f is above room noise but under the trigger %.0f. "
                    "A fan, a pet, or movement in the next room looks like this. Not reported "
                    "as motion. Link: %s." % (self.index, self.level, self.threshold, link))
        if self.state == "MOTION DETECTED":
            return ("Node %d: energy %.0f held above %.0f. Motion continuous for %s. This says "
                    "something is moving in this node's sensing path — not who or how many. "
                    "Link: %s." % (self.index, self.level, self.threshold,
                                   fmt_dur(now - (self.motion_since or now)), link))
        if self.state == "PRESENCE (STILL)":
            since = self.motion_since or self.last_motion_end or now
            return ("Node %d: the channel has not settled back to the empty-room baseline since "
                    "motion %s ago, with no strong movement now. Reads as present and still. "
                    "EXPERIMENTAL — an open window or a fan can look identical. Link: %s."
                    % (self.index, fmt_dur(now - since), link))
        return ""



# ==========================================================================
# ZONE SOLVER  --  four scalars -> one coarse likelihood field
# ==========================================================================
class ZoneSolver:
    """
    The physics it leans on: a node's reading rises when a body is near the
    straight path between that node and the sensing router (the Fresnel zone),
    and rises a little when a body is simply close to the node itself.

    v4 changes the maths from v3's "add the loud bands, subtract the quiet
    bands" to a proper pattern match:

        · each node's live reading is normalised against the loudest node, so
          what is compared is the SHAPE of the response across the array, not
          absolute energy (which drifts with gain and range);
        · every grid cell predicts what each node SHOULD be reading if the
          disturbance were there;
        · the cell score is the likelihood of the observed pattern given that
          prediction, weighted by each node's link quality.

    That single change is what removes most of v3's blob wander, and it gives
    two things v3 could not produce: a real uncertainty radius from the spread
    of the solution, and an explicit AMBIGUOUS warning when a second, equally
    good answer exists somewhere else in the room (which is exactly what
    happens in a symmetric room, or with only two active nodes).

    Its honest accuracy is unchanged: metre-scale, degrading badly below three
    active nodes. It is a ZONE, not a coordinate. Two people still produce one
    meaningless blob between them.
    """
    ZONES = [["NORTH-WEST", "NORTH", "NORTH-EAST"],
             ["WEST", "CENTRE", "EAST"],
             ["SOUTH-WEST", "SOUTH", "SOUTH-EAST"]]

    def __init__(self):
        self.cache_key = None
        self.seg_fields = {}
        self.node_fields = {}
        self.gx = None
        self.gy = None
        self.shape = (1, 1)
        self.cell = 0.10

    def _geometry_key(self, room, grid_cm):
        r = room.router()
        nodes = room.nodes()
        return (round(room.width, 3), round(room.height, 3), int(grid_cm),
                tuple(sorted((i,) + tuple(round(v, 3) for v in n.center())
                             for i, n in nodes.items())),
                tuple(round(v, 3) for v in r.center()) if r else None)

    def _seg_distance(self, ax, ay, bx, by):
        """Distance from every grid cell to the segment A-B."""
        gx, gy = self.gx, self.gy
        vx, vy = bx - ax, by - ay
        L = vx * vx + vy * vy
        if L < 1e-9:
            return np.full(self.shape, 9.9, dtype=np.float32)
        t = np.clip(((gx - ax) * vx + (gy - ay) * vy) / L, 0.0, 1.0)
        return np.hypot(gx - (ax + t * vx), gy - (ay + t * vy)).astype(np.float32)

    def _rebuild(self, room, grid_cm):
        """
        Build a distance field for EVERY sensing path, keyed (tx, rx):
            (0, rx)  router -> node rx      -- the original four
            (tx, rx) node tx -> node rx     -- the twelve sibling paths

        The four router paths all radiate from one point, so a corner none of
        them crosses is a blind spot no amount of maths can fill. The sibling
        paths criss-cross the room, which is where the extra positional
        information actually comes from.
        """
        cell = max(0.03, grid_cm / 100.0)
        nx = int(clamp(room.width / cell, 24, 190))
        ny = int(clamp(room.height / cell, 24, 190))
        xs = (np.arange(nx) + 0.5) * room.width / nx
        ys = (np.arange(ny) + 0.5) * room.height / ny
        gx, gy = np.meshgrid(xs, ys)
        self.gx, self.gy, self.shape = gx, gy, (ny, nx)
        self.cell = room.width / nx
        self.seg_fields = {}
        self.node_fields = {}
        nodes = room.nodes()
        r = room.router()

        pts = {}
        if r is not None:
            pts[0] = r.center()
        for idx, n in nodes.items():
            pts[idx] = n.center()

        for rx_i, (bx, by) in pts.items():
            if rx_i == 0:
                continue                      # the router is never a receiver
            self.node_fields[rx_i] = np.hypot(gx - bx, gy - by).astype(np.float32)
            for tx_i, (ax, ay) in pts.items():
                if tx_i == rx_i:
                    continue
                self.seg_fields[(tx_i, rx_i)] = self._seg_distance(ax, ay, bx, by)

    @staticmethod
    def _empty(reason, usable=0, active=0):
        return {"heat": None, "pos": None, "radius": None, "zone": reason,
                "reason": reason, "usable": usable, "active": active,
                "ambiguous": False, "alt": None, "quality": 0.0, "peak": 0.0,
                "regions": 0, "region_state": "NO ACTIVITY",
                "region_note": "", "spread": 0.0}

    def _label_regions(self, mask):
        """
        Count separated blobs in a boolean mask. Plain breadth-first flood fill
        -- no scipy dependency, and the grid is only a few thousand cells.
        Returns a list of (cell_count, centre_y, centre_x).
        """
        ny, nx = mask.shape
        seen = np.zeros_like(mask, dtype=bool)
        out = []
        for sy in range(ny):
            for sx in range(nx):
                if not mask[sy, sx] or seen[sy, sx]:
                    continue
                stack = [(sy, sx)]
                seen[sy, sx] = True
                cells = []
                while stack:
                    y, x = stack.pop()
                    cells.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny2, nx2 = y + dy, x + dx
                        if 0 <= ny2 < ny and 0 <= nx2 < nx \
                                and mask[ny2, nx2] and not seen[ny2, nx2]:
                            seen[ny2, nx2] = True
                            stack.append((ny2, nx2))
                ys = sum(c[0] for c in cells) / len(cells)
                xs = sum(c[1] for c in cells) / len(cells)
                out.append((len(cells), ys, xs))
        out.sort(reverse=True)
        return out

    def _regions(self, idxs, obs, room):
        """
        ACTIVITY REGIONS -- how many separate places is something happening?

        This is NOT a people counter and must never be presented as one.
        Bodies do not add up: a second person standing behind the first is in
        their radio shadow, so "how much" cannot be inverted into "how many".
        Two people also create phantom crossings where their paths intersect,
        indistinguishable from real ones using amplitude alone.

        What IS honest: paint only the paths that are actually disturbed, see
        where they cross, and count separated clusters. One cluster usually
        means one mover. Two well-separated clusters mean activity in two
        places, which usually means two movers -- but a strong reflection or an
        oscillating fan reads the same way. Many paths hot at once means the
        room is busy and nothing can be resolved.
        """
        hot = [(pk, w) for pk, w in zip(idxs, obs) if w > 0.18]
        if not hot:
            return 0, "NO ACTIVITY", "", 0.0

        two_sig = 2.0 * 0.85 * 0.85
        acc = np.zeros(self.shape, dtype=np.float32)
        for pk, w in hot:
            acc += w * np.exp(-(self.seg_fields[pk] ** 2) / two_sig)
        m = float(acc.max())
        if m < 1e-6:
            return 0, "NO ACTIVITY", "", 0.0
        acc /= m

        mask = acc > 0.62
        spread = float(mask.mean())
        min_cells = max(4, int(0.012 * mask.size))
        blobs = [b for b in self._label_regions(mask) if b[0] >= min_cells]
        n = len(blobs)

        # Tuned against simulated occupancy: one person disturbs ~20% of the
        # room area, two people ~38%, three ~90%. Half the room lit at once is
        # the point where separate regions stop being distinguishable.
        if spread > 0.50:
            return n, "WIDESPREAD ACTIVITY", (
                "Most of the room is disturbed at once. Several movers are "
                "likely, but they cannot be separated — do not read this as a "
                "count."), spread
        if n >= 2:
            return n, "%d SEPARATED REGIONS" % n, (
                "Activity in %d places at once. Usually means %d or more "
                "movers, but a strong reflection can also do this. Regions "
                "are not people." % (n, n)), spread
        return 1, "ONE ACTIVITY REGION", "", spread

    def compute(self, room, engines, sigma=0.85, grid_cm=8, common_mode=0.85,
                use_all_paths=False):
        key = self._geometry_key(room, grid_cm)
        if key != self.cache_key:
            self.cache_key = key
            self._rebuild(room, grid_cm)
        if not self.seg_fields:
            return self._empty("NO ROUTER PLACED" if room.router() is None
                               else "NO NODES PLACED")

        # ---- gather the observation vector, one entry per PATH ----
        obs, tol, idxs = [], [], []
        usable = 0
        for pk in self.seg_fields:
            if not use_all_paths and pk[0] != 0:
                continue                       # proven four-path behaviour
            e = engines.get(pk)
            if e is None and pk[0] == 0:
                e = engines.get(pk[1])        # tolerate the old int-keyed dict
            if e is None or not e.connected or e.state in (
                    "OFFLINE", "NO SIGNAL", "WARMING UP", "CALIBRATING"):
                continue
            usable += 1
            leave = e.threshold * float(e.cfg["exit_ratio"])
            w = clamp((e.level - leave) / max(8.0, 100.0 - leave), 0.0, 1.0)
            obs.append(w)
            tol.append(0.30 / max(0.25, e.quality))
            idxs.append(pk)

        if usable < 2:
            return self._empty("INSUFFICIENT DATA", usable, 0)

        obs = np.asarray(obs, dtype=np.float32)
        wmax = float(obs.max())
        active = int((obs > 0.05).sum())
        if wmax < 0.04 or active == 0:
            return self._empty("AREA CLEAR", usable, 0)
        if active < 2:
            # One band alone cannot fix a position -- anywhere along it fits
            # equally well. v3 drew a blob here anyway; that was the single
            # biggest source of confidently wrong answers. Say so instead.
            return self._empty("LOW EVIDENCE — 1 NODE", usable, active)

        norm = obs / wmax
        two_sig = 2.0 * sigma * sigma
        near_sig = 2.0 * 1.25 * 1.25

        # ---- build every node's predicted response for every cell ----
        # FAULT FOUND AND FIXED: the "body is near this node" bump is added per
        # PATH. With four router paths each node collected one bump. With
        # sixteen paths, four of them end at each node, so every node collected
        # FOUR stacked bumps and became a magnet -- the solution snapped to the
        # nearest node instead of following the person. In multi-path mode the
        # term is dropped entirely: a body near a node already sits on the line
        # of every path that ends there, so the segment field covers it.
        near_w = 0.0 if use_all_paths else 0.30
        models = []
        for pk in idxs:
            band = np.exp(-(self.seg_fields[pk] ** 2) / two_sig)
            if near_w:
                band = band + near_w * np.exp(-(self.node_fields[pk[1]] ** 2) / near_sig)
            models.append(np.clip(band, 0.0, 1.0))
        M = np.stack(models)                      # (paths, ny, nx)

        # ---- remove the common mode before comparing -----------------------
        # The router sits at the END of every node's segment, so the cell at
        # the router predicts "every node loud at once". That is a common-mode
        # prediction, and common mode carries NO positional information -- a
        # body near the AP, a gain shift and a burst of interference all look
        # identical. Left in, it makes the router a permanent attractor: the
        # marker visibly slides toward the AP whenever several nodes light up
        # together. Measured pull with perfect data was +0.94 m.
        #
        # Subtracting the across-node mean from BOTH sides compares the SHAPE
        # of the response instead of its level. At the router the centred model
        # is flat zero, so it only wins when the observation is genuinely flat
        # too -- which is the honest "cannot localise" case.
        # FAULT FOUND AND FIXED: common-mode removal exists because the router
        # sits at the end of all four router paths, so "everything loud" meant
        # "body near the AP" and dragged the marker there. Once sixteen paths
        # criss-cross the room there is no such shared endpoint, and
        # "everything loud at once" means someone is standing in the MIDDLE --
        # which is signal, not noise. Stripping it out was deleting the answer.
        cm = float(clamp(common_mode, 0.0, 1.0))
        if use_all_paths:
            cm *= 0.35
        obs_c = norm - cm * float(norm.mean())
        M_c = M - cm * M.mean(axis=0, keepdims=True)

        ll = np.zeros(self.shape, dtype=np.float32)
        for k in range(len(idxs)):
            resid = obs_c[k] - M_c[k]
            ll -= (resid * resid) / (2.0 * tol[k] * tol[k])

        ll -= ll.max()
        H = np.exp(ll)
        peak = float(H.max())
        if peak < 1e-9:
            return self._empty("NO CROSSING", usable, active)
        H /= peak

        # ---- primary solution: peak, then a LOCAL centroid around it ----
        pi = int(np.argmax(H))
        py, pxi = divmod(pi, self.shape[1])
        pxm, pym = float(self.gx[py, pxi]), float(self.gy[py, pxi])
        d2 = (self.gx - pxm) ** 2 + (self.gy - pym) ** 2
        local = np.where((d2 < 1.35 ** 2) & (H > 0.35), H, 0.0)
        tot = float(local.sum()) + 1e-9
        cx = float((self.gx * local).sum() / tot)
        cy = float((self.gy * local).sum() / tot)

        # ---- uncertainty: spread of the local solution, honestly reported ----
        var = float((local * ((self.gx - cx) ** 2 + (self.gy - cy) ** 2)).sum() / tot)
        radius = math.sqrt(max(var, 1e-6)) * 1.55
        if active <= 2:
            radius *= 1.45
        if usable <= 2:
            radius *= 1.35
        radius = clamp(radius, 0.30, 3.0)

        # ---- ambiguity: is there a second, nearly-as-good answer elsewhere? ----
        masked = np.where(d2 < 2.0 ** 2, 0.0, H)
        alt = None
        ambiguous = False
        if masked.size:
            sm = float(masked.max())
            if sm > 0.72:
                ai = int(np.argmax(masked))
                ay, ax = divmod(ai, self.shape[1])
                alt = (float(self.gx[ay, ax]), float(self.gy[ay, ax]))
                ambiguous = True

        col = int(clamp(cx / room.width * 3, 0, 2))
        row = int(clamp(cy / room.height * 3, 0, 2))
        zone = self.ZONES[row][col]

        quals = [engines[i].quality for i in idxs if i in engines]
        qual = float(np.mean(quals)) if quals else 0.0
        qual *= clamp(active / 3.0, 0.35, 1.0)
        if ambiguous:
            qual *= 0.6

        nreg, rstate, rnote, spread = self._regions(idxs, obs, room)

        return {"heat": H, "pos": (cx, cy), "radius": radius, "zone": zone,
                "reason": "", "usable": usable, "active": active,
                "ambiguous": ambiguous, "alt": alt,
                "quality": clamp(qual, 0.0, 1.0), "peak": wmax,
                "regions": nreg, "region_state": rstate,
                "region_note": rnote, "spread": spread}


# ==========================================================================
# TARGET TRACKER  --  constant-velocity Kalman filter
# ==========================================================================
def motion_class(speed):
    """Speed in m/s -> a plain-language band. Always an estimate."""
    if speed < 0.06:
        return "STATIONARY", GREEN
    if speed < 0.30:
        return "MICRO-MOTION", TEAL
    if speed < 0.80:
        return "SLOW WALK", AMBER
    if speed < 1.70:
        return "WALKING", ORANGE
    return "FAST MOVE", RED


class TargetTracker:
    """
    Smooths the solver's noisy centroid into something that moves like a body.

    State: position and velocity. The filter predicts where the target should
    be, then pulls that prediction toward the new measurement by an amount set
    by how good the measurement is. That is what removes the frame-to-frame
    jitter that made v3's blob unreadable, and it produces speed and heading
    for free.

    It does NOT add information. A smooth track over a bad solution is a smooth
    bad track — which is why the uncertainty ring is drawn from the solver's
    spread and not from the filter's own optimism.
    """
    STATES = ("IDLE", "ACQUIRING", "LOCKED", "COASTING")

    def __init__(self, cfg):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.x = None                 # [px, py, vx, vy]
        self.P = None
        self.state = "IDLE"
        self.updates = 0
        self.last_meas = 0.0
        self.last_t = time.time()
        self.acquired_t = None
        self.trail = collections.deque(maxlen=int(self.cfg.get("track_trail", 45)))
        self.travelled = 0.0
        self.zone = None
        self._pending_zone = None
        self._pending_since = 0.0
        self.zone_since = 0.0
        self.zone_changes = 0
        self.track_id = 0
        self.speed = 0.0
        self.heading = 0.0
        self.meas_radius = 1.0
        self.evidence = 0
        self.usable = 0
        self.lost_reason = ""

    # ---- filter maths ----
    def _predict(self, dt):
        q = float(self.cfg.get("track_q", 0.55))
        F = np.array([[1, 0, dt, 0],
                      [0, 1, 0, dt],
                      [0, 0, 1, 0],
                      [0, 0, 0, 1]], dtype=float)
        dt2 = dt * dt
        dt3 = dt2 * dt / 2.0
        dt4 = dt2 * dt2 / 4.0
        Qb = np.array([[dt4, 0, dt3, 0],
                       [0, dt4, 0, dt3],
                       [dt3, 0, dt2, 0],
                       [0, dt3, 0, dt2]], dtype=float) * (q * q)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Qb
        # velocity bleed: a body that stops should not keep drifting
        self.x[2] *= 0.90
        self.x[3] *= 0.90

    def _update(self, z, r):
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        R = np.eye(2) * (r * r)
        y = np.asarray(z, dtype=float) - H @ self.x
        S = H @ self.P @ H.T + R
        try:
            K = self.P @ H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            return
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P

    # ---- public ----
    def step(self, solution, room):
        now = time.time()
        dt = clamp(now - self.last_t, 0.005, 0.5)
        self.last_t = now
        hold = float(self.cfg.get("track_hold", 3.0))
        meas = solution.get("pos") if solution else None
        rad = solution.get("radius") or 1.0
        qual = solution.get("quality", 0.0) if solution else 0.0

        if meas is not None and qual > 0.05:
            r = float(clamp(rad * float(self.cfg.get("track_r", 0.65)) /
                            max(0.25, qual), 0.15, 4.0))
            self.meas_radius = rad
            if self.x is None:
                self.x = np.array([meas[0], meas[1], 0.0, 0.0], dtype=float)
                self.P = np.diag([rad ** 2, rad ** 2, 1.0, 1.0])
                self.updates = 1
                self.state = "ACQUIRING"
                self.acquired_t = now
                self.track_id += 1
                self.travelled = 0.0
                self.trail.clear()
                self.zone = None
            else:
                prev = (self.x[0], self.x[1])
                self._predict(dt)
                self._update(meas, r)
                self.updates += 1
                self.travelled += math.hypot(self.x[0] - prev[0], self.x[1] - prev[1])
            self.last_meas = now
            self.evidence = solution.get("active", 0)
            self.usable = solution.get("usable", 0)
            self.state = "LOCKED" if self.updates >= 6 else "ACQUIRING"
        elif self.x is not None:
            if now - self.last_meas > hold:
                self.reset_soft("no evidence for %.1f s" % (now - self.last_meas))
                return self.report(room)
            self._predict(dt)
            self.state = "COASTING"
        else:
            self.state = "IDLE"
            return self.report(room)

        # clamp inside the room so the marker never floats through a wall
        if self.x is not None:
            self.x[0] = clamp(self.x[0], -0.2, room.width + 0.2)
            self.x[1] = clamp(self.x[1], -0.2, room.height + 0.2)
            self.speed = float(math.hypot(self.x[2], self.x[3]))
            if self.speed > 0.05:
                self.heading = math.degrees(math.atan2(self.x[2], -self.x[3])) % 360.0
            self.trail.append((now, float(self.x[0]), float(self.x[1])))
            col = int(clamp(self.x[0] / room.width * 3, 0, 2))
            row = int(clamp(self.x[1] / room.height * 3, 0, 2))
            z = ZoneSolver.ZONES[row][col]
            # hysteresis: a new zone name has to hold for a second before it
            # is adopted, otherwise the label flickers on a boundary
            if self.zone is None:
                self.zone = z
                self._pending_zone = z
                self.zone_since = now
            elif z != self.zone:
                if z != self._pending_zone:
                    self._pending_zone = z
                    self._pending_since = now
                elif now - self._pending_since > 1.0:
                    if self.zone is not None:
                        self.zone_changes += 1
                    self.zone = z
                    self.zone_since = now
            else:
                self._pending_zone = z
        return self.report(room)

    def reset_soft(self, reason=""):
        self.x = None
        self.P = None
        self.state = "IDLE"
        self.updates = 0
        self.speed = 0.0
        self.lost_reason = reason
        self.trail.clear()
        self.zone = None
        self._pending_zone = None

    def report(self, room):
        now = time.time()
        if self.x is None:
            return {"active": False, "state": "IDLE"}
        cls, col = motion_class(self.speed)
        near, near_d = room.nearest_object(self.x[0], self.x[1])
        return {
            "active": True,
            "state": self.state,
            "id": self.track_id,
            "pos": (float(self.x[0]), float(self.x[1])),
            "vel": (float(self.x[2]), float(self.x[3])),
            "speed": self.speed,
            "heading": self.heading,
            "class": cls,
            "class_color": col,
            "radius": self.meas_radius,
            "age": now - (self.acquired_t or now),
            "updates": self.updates,
            "travelled": self.travelled,
            "zone": self.zone or "—",
            "zone_dwell": now - (self.zone_since or now),
            "zone_changes": self.zone_changes,
            "evidence": self.evidence,
            "usable": self.usable,
            "stale": max(0.0, now - self.last_meas),
            "near": near.name() if near else None,
            "near_d": near_d,
            "trail": list(self.trail),
        }



# ==========================================================================
# UI ATOMS  --  every size goes through fs()/px() so the whole console scales
# ==========================================================================
def card_css(bg=PANEL, radius=10, border=BORDER):
    return "background:%s;border:1px solid %s;border-radius:%dpx;" % (bg, border, px(radius))


def button(text, color=PANEL_3, fg=TEXT, bold=False, height=None, accent=None,
           tip=None, big=False):
    b = QtWidgets.QPushButton(text)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setMinimumHeight(px(height if height else (40 if big else 32)))
    if tip:
        b.setToolTip(tip)
    edge = accent or BORDER_HI
    b.setStyleSheet(
        "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
        "padding:%dpx %dpx;font-size:%dpx;font-family:%s;%s}"
        "QPushButton:hover{background:%s;border:1px solid %s;color:%s;}"
        "QPushButton:pressed{background:%s;}"
        "QPushButton:disabled{color:%s;background:%s;border:1px solid %s;}"
        % (color, fg, edge, px(7), px(7), px(12), fs(13 if big else 12), SANS,
           "font-weight:800;letter-spacing:%dpx;" % max(1, px(1)) if bold else "font-weight:600;",
           PANEL_2, BORDER_HOT, fg,
           VOID, DIM, PANEL_2, BORDER))
    return b


def section(title, color=None):
    l = QtWidgets.QLabel("▍ " + title)
    l.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                    "padding-top:%dpx;padding-bottom:%dpx;font-family:%s;background:none;"
                    "border:none;"
                    % (color or MUTED, fs(11), max(1, px(2)), px(8), px(2), SANS))
    return l


def caption(text, color=DIM, size=11, wrap=True):
    l = QtWidgets.QLabel(text)
    l.setWordWrap(wrap)
    l.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;background:none;border:none;"
                    % (color, fs(size), SANS))
    return l


def mono(text, color=TEXT, size=12, bold=False):
    l = QtWidgets.QLabel(text)
    l.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;background:none;border:none;%s"
                    % (color, fs(size), MONO, "font-weight:700;" if bold else ""))
    return l


def spin(lo, hi, val, step=0.1, decimals=2, suffix=" m"):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setValue(val)
    s.setSuffix(suffix)
    s.setMinimumHeight(px(30))
    s.setStyleSheet(
        "QDoubleSpinBox{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
        "padding:%dpx %dpx;font-size:%dpx;font-family:%s;}"
        "QDoubleSpinBox:focus{border:1px solid %s;}"
        "QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:%dpx;background:%s;"
        "border:none;}"
        % (PANEL_2, TEXT, BORDER, px(6), px(4), px(8), fs(13), MONO,
           BORDER_HOT, px(18), PANEL_3))
    return s


def combo_css():
    return ("QComboBox{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx %dpx;font-size:%dpx;font-family:%s;min-height:%dpx;}"
            "QComboBox:hover{border:1px solid %s;}"
            "QComboBox::drop-down{border:none;width:%dpx;}"
            "QComboBox::down-arrow{image:none;border-left:%dpx solid transparent;"
            "border-right:%dpx solid transparent;border-top:%dpx solid %s;margin-right:%dpx;}"
            "QComboBox QAbstractItemView{background:%s;color:%s;border:1px solid %s;"
            "selection-background-color:%s;selection-color:%s;outline:none;"
            "font-size:%dpx;padding:%dpx;}"
            % (PANEL_2, TEXT, BORDER, px(6), px(5), px(9), fs(13), MONO, px(28),
               BORDER_HOT, px(22), px(4), px(4), px(5), MUTED, px(6),
               PANEL, TEXT, BORDER_HI, TEAL_DK, TEAL, fs(13), px(4)))


def check_css():
    return ("QCheckBox{color:%s;font-size:%dpx;spacing:%dpx;font-family:%s;padding:%dpx 0;}"
            "QCheckBox:hover{color:%s;}"
            "QCheckBox::indicator{width:%dpx;height:%dpx;border:1px solid %s;"
            "border-radius:%dpx;background:%s;}"
            "QCheckBox::indicator:checked{background:%s;border:1px solid %s;}"
            % (TEXT_2, fs(12), px(8), SANS, px(3), TEXT,
               px(15), px(15), BORDER_HI, px(4), PANEL_2, TEAL, TEAL))


def edit_css():
    return ("QLineEdit{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx %dpx;font-size:%dpx;font-family:%s;min-height:%dpx;}"
            "QLineEdit:focus{border:1px solid %s;}"
            % (PANEL_2, TEXT, BORDER, px(6), px(6), px(9), fs(13), SANS, px(26),
               BORDER_HOT))


class Chip(QtWidgets.QLabel):
    """Small status pill. Readable at a glance from across a desk."""

    def __init__(self, text="", color=DIM, size=11, bold=True):
        super().__init__(text)
        self._size = size
        self._bold = bold
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.set(text, color)

    def set(self, text, color=DIM, bg=None):
        self.setText(str(text))
        c = QtGui.QColor(color)
        back = bg or "rgba(%d,%d,%d,38)" % (c.red(), c.green(), c.blue())
        self.setStyleSheet(
            "color:%s;background:%s;border:1px solid rgba(%d,%d,%d,120);"
            "border-radius:%dpx;padding:%dpx %dpx;font-size:%dpx;letter-spacing:%dpx;"
            "font-family:%s;%s"
            % (color, back, c.red(), c.green(), c.blue(), px(5), px(3), px(8),
               fs(self._size), max(1, px(1)), SANS,
               "font-weight:800;" if self._bold else ""))


class Card(QtWidgets.QFrame):
    """Headline metric tile. v4: much bigger numbers, optional sub-line."""

    def __init__(self, label, value="—", color=TEXT, unit="", big=26, sub=""):
        super().__init__()
        self.setStyleSheet(card_css())
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(px(13), px(9), px(13), px(10))
        v.setSpacing(px(1))
        self.lab = QtWidgets.QLabel(label)
        self.lab.setStyleSheet("color:%s;font-size:%dpx;letter-spacing:%dpx;border:none;"
                               "background:none;font-weight:700;font-family:%s;"
                               % (MUTED, fs(11), max(1, px(2)), SANS))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(px(4))
        self.val = QtWidgets.QLabel(value)
        self._big = big
        self._color = color
        self.val.setStyleSheet(self._vcss(color))
        u = QtWidgets.QLabel(unit)
        u.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                        "padding-bottom:%dpx;font-family:%s;" % (DIM, fs(12), px(4), SANS))
        row.addWidget(self.val)
        row.addWidget(u)
        row.addStretch()
        v.addWidget(self.lab)
        v.addLayout(row)
        self.sub = QtWidgets.QLabel(sub)
        self.sub.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                               "font-family:%s;" % (DIM, fs(11), MONO))
        v.addWidget(self.sub)

    def _vcss(self, color):
        return ("color:%s;font-size:%dpx;font-weight:800;border:none;background:none;"
                "font-family:%s;letter-spacing:%dpx;"
                % (color, fs(self._big), MONO, max(0, px(1))))

    def set(self, text, color=None, sub=None):
        self.val.setText(str(text))
        if color and color != self._color:
            self._color = color
            self.val.setStyleSheet(self._vcss(color))
        if sub is not None:
            self.sub.setText(sub)


class Slider(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal(float)

    def __init__(self, label, lo, hi, val, decimals=0, suffix="", tip=None):
        super().__init__()
        self.k = 10 ** decimals
        self.decimals = decimals
        self.suffix = suffix
        if tip:
            self.setToolTip(tip)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, px(2), 0, px(2))
        v.setSpacing(px(2))
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        lab = QtWidgets.QLabel(label)
        lab.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;background:none;"
                          % (TEXT_2, fs(12), SANS))
        self.out = QtWidgets.QLabel()
        self.out.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;font-family:%s;"
                               "background:none;" % (TEAL, fs(13), MONO))
        top.addWidget(lab)
        top.addStretch()
        top.addWidget(self.out)
        v.addLayout(top)
        self.s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.s.setMinimum(int(lo * self.k))
        self.s.setMaximum(int(hi * self.k))
        self.s.setValue(int(val * self.k))
        self.s.setCursor(QtCore.Qt.PointingHandCursor)
        self.s.setStyleSheet(
            "QSlider::groove:horizontal{height:%dpx;background:%s;border-radius:%dpx;}"
            "QSlider::sub-page:horizontal{background:%s;border-radius:%dpx;}"
            "QSlider::handle:horizontal{background:%s;border:%dpx solid %s;width:%dpx;"
            "height:%dpx;margin:-%dpx 0;border-radius:%dpx;}"
            "QSlider::handle:horizontal:hover{background:%s;}"
            % (px(5), PANEL_2, px(3), TEAL, px(3), TEXT, max(1, px(2)), TEAL_DK,
               px(15), px(15), px(6), px(8), CYAN))
        self.s.valueChanged.connect(self._emit)
        v.addWidget(self.s)
        self._show(val)

    def _show(self, val):
        self.out.setText(("%%.%df%%s" % self.decimals) % (val, self.suffix))

    def _emit(self, raw):
        val = raw / self.k
        self._show(val)
        self.changed.emit(val)

    def setValue(self, val):
        self.s.blockSignals(True)
        self.s.setValue(int(val * self.k))
        self.s.blockSignals(False)
        self._show(val)


class Meter(QtWidgets.QWidget):
    """Horizontal energy bar with a marked trigger line. Bigger than v3's."""

    def __init__(self, height=16):
        super().__init__()
        self.value = 0.0
        self.trigger = 25.0
        self.clear = 15.0
        self.color = TEAL
        self.setFixedHeight(px(height))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set(self, value, trigger, clear, color):
        self.value, self.trigger, self.clear, self.color = value, trigger, clear, color
        self.update()

    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = px(3)
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(QtGui.QColor(PANEL_2))
        q.drawRoundedRect(QtCore.QRectF(0, 0, w, h), r, r)
        # ticks every 10%
        q.setPen(QtGui.QPen(QtGui.QColor(GRID_HI), 1))
        for i in range(1, 10):
            x = w * i / 10.0
            q.drawLine(QtCore.QPointF(x, h * 0.62), QtCore.QPointF(x, h))
        # fill
        fw = w * clamp(self.value / 100.0, 0, 1)
        if fw > 1:
            g = QtGui.QLinearGradient(0, 0, fw, 0)
            c = QtGui.QColor(self.color)
            c0 = QtGui.QColor(c)
            c0.setAlpha(110)
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c)
            q.setBrush(QtGui.QBrush(g))
            q.setPen(QtCore.Qt.NoPen)
            q.drawRoundedRect(QtCore.QRectF(0, 0, fw, h), r, r)
        # clear + trigger markers
        for val, col, wd in ((self.clear, MUTED, 1), (self.trigger, RED, max(2, px(2)))):
            x = w * clamp(val / 100.0, 0, 1)
            q.setPen(QtGui.QPen(QtGui.QColor(col), wd))
            q.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))
        q.setPen(QtGui.QPen(QtGui.QColor(BORDER)))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawRoundedRect(QtCore.QRectF(0.5, 0.5, w - 1, h - 1), r, r)
        q.end()


# --------------------------------------------------------------------------
# reveal animation -- panels fade and rise into place, one after another
# --------------------------------------------------------------------------
class Reveal:
    _keep = []

    @staticmethod
    def one(widget, delay=0, dur=340, rise=14):
        try:
            eff = QtWidgets.QGraphicsOpacityEffect(widget)
            eff.setOpacity(0.0)
            widget.setGraphicsEffect(eff)
        except Exception:
            return
        anim = QtCore.QPropertyAnimation(eff, b"opacity", widget)
        anim.setDuration(dur)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        group = QtCore.QParallelAnimationGroup(widget)
        group.addAnimation(anim)

        if rise:
            pos = QtCore.QPropertyAnimation(widget, b"pos", widget)
            start = widget.pos() + QtCore.QPoint(0, px(rise))
            pos.setDuration(dur)
            pos.setStartValue(start)
            pos.setEndValue(widget.pos())
            pos.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            group.addAnimation(pos)

        def go():
            group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        def clear_effect():
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass

        group.finished.connect(clear_effect)
        Reveal._keep.append(group)
        QtCore.QTimer.singleShot(int(delay), go)

    @staticmethod
    def stagger(widgets, start=90, step=55, dur=340, rise=14):
        d = start
        for w in widgets:
            if w is None:
                continue
            Reveal.one(w, delay=d, dur=dur, rise=rise)
            d += step
        return d



# ==========================================================================
# HEAT COLOUR TABLE for the tactical overlay
# ==========================================================================
def _build_heat_lut():
    stops = [(0.00, (0, 0, 0, 0)),
             (0.14, (10, 60, 78, 22)),
             (0.32, (18, 128, 140, 72)),
             (0.52, (58, 213, 192, 118)),
             (0.70, (240, 180, 41, 168)),
             (0.86, (255, 138, 61, 208)),
             (1.00, (255, 77, 90, 240))]
    pos = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], dtype=float)
    x = np.linspace(0, 1, 256)
    lut = np.zeros((256, 4))
    for c in range(4):
        lut[:, c] = np.interp(x, pos, cols[:, c])
    return lut.astype(np.uint8)


HEAT_LUT = _build_heat_lut()


def qc(hexcol, alpha=None):
    c = QtGui.QColor(hexcol)
    if alpha is not None:
        c.setAlpha(int(alpha))
    return c


class RoomCanvas(QtWidgets.QWidget):
    """Shared metre<->pixel mapping and room rendering."""

    def __init__(self, room):
        super().__init__()
        self.room = room
        self.margin = px(42)
        self.scale = 60.0
        self.ox = 0.0
        self.oy = 0.0
        self.setMinimumSize(px(420), px(300))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    # ---- mapping ----
    def fit(self):
        w = max(1, self.width() - 2 * self.margin)
        h = max(1, self.height() - 2 * self.margin)
        self.scale = min(w / max(0.5, self.room.width), h / max(0.5, self.room.height))
        self.ox = (self.width() - self.room.width * self.scale) / 2.0
        self.oy = (self.height() - self.room.height * self.scale) / 2.0

    def p(self, x, y):
        return QtCore.QPointF(self.ox + x * self.scale, self.oy + y * self.scale)

    def m(self, px_, py_):
        return ((px_ - self.ox) / self.scale, (py_ - self.oy) / self.scale)

    # ---- backdrop ----
    def draw_backdrop(self, q, base=PANEL_2):
        rect = self.rect()
        g = QtGui.QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.75)
        g.setColorAt(0.0, qc(base))
        g.setColorAt(1.0, qc(VOID))
        q.fillRect(rect, QtGui.QBrush(g))

    def draw_scanlines(self, q, alpha=12, step=3):
        q.setPen(QtGui.QPen(qc("#000000", alpha), 1))
        y = 0
        while y < self.height():
            q.drawLine(0, y, self.width(), y)
            y += px(step)

    # ---- grid ----
    def draw_grid(self, q, labels=True):
        room = self.room
        pen_min = QtGui.QPen(qc(GRID), 1)
        pen_maj = QtGui.QPen(qc(GRID_HI), 1)
        step = 0.5 if self.scale > 55 else 1.0
        fnt = QtGui.QFont("Consolas", fs(9))
        x = 0.0
        while x <= room.width + 1e-6:
            major = abs(x - round(x)) < 1e-6
            q.setPen(pen_maj if major else pen_min)
            q.drawLine(self.p(x, 0), self.p(x, room.height))
            if labels and major:
                q.setPen(QtGui.QPen(qc(DIM)))
                q.setFont(fnt)
                q.drawText(self.p(x, 0) + QtCore.QPointF(px(3), -px(7)), "%d" % round(x))
            x += step
        y = 0.0
        while y <= room.height + 1e-6:
            major = abs(y - round(y)) < 1e-6
            q.setPen(pen_maj if major else pen_min)
            q.drawLine(self.p(0, y), self.p(room.width, y))
            if labels and major:
                q.setPen(QtGui.QPen(qc(DIM)))
                q.setFont(fnt)
                q.drawText(self.p(0, y) + QtCore.QPointF(-px(20), px(5)), "%d" % round(y))
            y += step

    def draw_room_outline(self, q, color=BORDER_HI, width=2):
        rect = QtCore.QRectF(self.p(0, 0), self.p(self.room.width, self.room.height))
        q.setPen(QtGui.QPen(qc(color), px(width)))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawRect(rect)
        # inner hairline for a drafted look
        q.setPen(QtGui.QPen(qc(color, 70), 1))
        q.drawRect(rect.adjusted(px(4), px(4), -px(4), -px(4)))

    # ======================================================================
    # OBJECT RENDERING -- material-coded so the plan reads at a glance
    # ======================================================================
    def draw_object(self, q, o, ghost=False, selected=False, level=None, labels=True):
        spec = OBJ_TYPES.get(o.kind, OBJ_TYPES["table"])
        name, _, _, fill, outline, _, glyph, shape, accent = spec
        c = self.p(*o.center())
        wpx = max(2.0, o.w * self.scale)
        hpx = max(2.0, o.h * self.scale)
        q.save()
        q.translate(c)
        q.rotate(o.rot)
        rect = QtCore.QRectF(-wpx / 2, -hpx / 2, wpx, hpx)

        if o.kind == "node":
            self._draw_node(q, rect, o, ghost, level)
        elif o.kind == "router":
            self._draw_router(q, rect, ghost, level)
        else:
            a = 110 if ghost else 255
            fc = qc(fill, a if not ghost else 90)
            oc = qc(outline, a if not ghost else 120)
            self._draw_shape(q, rect, shape, fc, oc, accent, o, ghost)
            if o.metal and not ghost:
                q.setPen(QtGui.QPen(qc("#c8a05a", 110), 1, QtCore.Qt.DashLine))
                q.setBrush(QtCore.Qt.NoBrush)
                q.drawRect(rect.adjusted(px(2), px(2), -px(2), -px(2)))
            if labels and not ghost and min(wpx, hpx) > px(26):
                q.setPen(QtGui.QPen(qc(TEXT, 205)))
                q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
                q.drawText(rect, QtCore.Qt.AlignCenter, o.label or glyph or name[:5])
        q.restore()

        if selected:
            q.setPen(QtGui.QPen(qc(TEAL), max(1, px(1)), QtCore.Qt.DashLine))
            q.setBrush(QtCore.Qt.NoBrush)
            pts = [self.p(*pt) for pt in o.corners()]
            q.drawPolygon(QtGui.QPolygonF(pts))

    def _draw_shape(self, q, rect, shape, fc, oc, accent, o, ghost):
        pen = QtGui.QPen(oc, max(1.0, px(1.4)))
        q.setPen(pen)
        # base fill with a slight vertical gradient so things look solid
        g = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = QtGui.QColor(fc)
        top = top.lighter(128)
        top.setAlpha(fc.alpha())
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, fc)
        q.setBrush(QtGui.QBrush(g))

        r = min(rect.width(), rect.height())

        if shape == "wood":
            q.drawRoundedRect(rect, px(2), px(2))
            q.setPen(QtGui.QPen(qc(accent, 55), 1))
            n = max(2, int(rect.width() / px(11)))
            for i in range(1, n):
                x = rect.left() + rect.width() * i / n
                q.drawLine(QtCore.QPointF(x, rect.top() + px(2)),
                           QtCore.QPointF(x, rect.bottom() - px(2)))

        elif shape == "chair":
            q.drawRoundedRect(rect, px(3), px(3))
            q.setPen(QtGui.QPen(qc(accent, 170), max(1.0, px(2))))
            q.drawLine(QtCore.QPointF(rect.left() + px(2), rect.top() + px(2)),
                       QtCore.QPointF(rect.right() - px(2), rect.top() + px(2)))

        elif shape == "sofa":
            q.drawRoundedRect(rect, px(5), px(5))
            q.setPen(QtGui.QPen(qc(accent, 150), max(1.0, px(2))))
            back = QtCore.QRectF(rect.left() + px(2), rect.top() + px(2),
                                 rect.width() - px(4), rect.height() * 0.28)
            q.setBrush(qc(accent, 45))
            q.drawRoundedRect(back, px(3), px(3))
            q.setBrush(QtCore.Qt.NoBrush)
            for f in (0.34, 0.66):
                x = rect.left() + rect.width() * f
                q.drawLine(QtCore.QPointF(x, rect.top() + rect.height() * 0.35),
                           QtCore.QPointF(x, rect.bottom() - px(2)))

        elif shape == "bed":
            q.drawRoundedRect(rect, px(4), px(4))
            q.setBrush(qc(accent, 60))
            q.setPen(QtGui.QPen(qc(accent, 150), 1))
            pillow = QtCore.QRectF(rect.left() + px(4), rect.top() + px(3),
                                   rect.width() - px(8), rect.height() * 0.22)
            q.drawRoundedRect(pillow, px(3), px(3))
            q.setBrush(QtCore.Qt.NoBrush)
            q.drawLine(QtCore.QPointF(rect.left() + px(3),
                                      rect.top() + rect.height() * 0.34),
                       QtCore.QPointF(rect.right() - px(3),
                                      rect.top() + rect.height() * 0.34))

        elif shape == "rug":
            c2 = QtGui.QColor(fc)
            c2.setAlpha(int(fc.alpha() * 0.55))
            q.setBrush(c2)
            q.setPen(QtGui.QPen(qc(accent, 130), max(1.0, px(1.6)), QtCore.Qt.DashLine))
            q.drawRoundedRect(rect, px(4), px(4))
            q.setPen(QtGui.QPen(qc(accent, 60), 1))
            q.setBrush(QtCore.Qt.NoBrush)
            q.drawRoundedRect(rect.adjusted(px(5), px(5), -px(5), -px(5)), px(3), px(3))

        elif shape == "shelf":
            q.drawRect(rect)
            q.setPen(QtGui.QPen(qc(accent, 120), 1))
            n = max(2, int(rect.width() / px(14)))
            for i in range(1, n):
                x = rect.left() + rect.width() * i / n
                q.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))

        elif shape == "screen":
            q.drawRoundedRect(rect, px(2), px(2))
            face = rect.adjusted(px(2), px(2), -px(2), -px(2))
            gg = QtGui.QLinearGradient(face.topLeft(), face.bottomRight())
            gg.setColorAt(0.0, qc(accent, 150))
            gg.setColorAt(1.0, qc(accent, 60))
            q.setBrush(QtGui.QBrush(gg))
            q.setPen(QtCore.Qt.NoPen)
            q.drawRect(face)
            q.setPen(QtGui.QPen(qc(accent, 200), 1))
            q.setBrush(QtCore.Qt.NoBrush)
            q.drawRect(face)

        elif shape == "steel":
            q.drawRoundedRect(rect, px(3), px(3))
            q.setPen(QtGui.QPen(qc(accent, 70), 1))
            n = 4
            for i in range(1, n):
                y = rect.top() + rect.height() * i / n
                q.drawLine(QtCore.QPointF(rect.left() + px(3), y),
                           QtCore.QPointF(rect.right() - px(3), y))

        elif shape == "stove":
            q.drawRoundedRect(rect, px(3), px(3))
            q.setPen(QtGui.QPen(qc(accent, 190), max(1.0, px(1.4))))
            q.setBrush(qc(accent, 40))
            rr = min(rect.width(), rect.height()) * 0.20
            for sx, sy in ((-0.24, -0.24), (0.24, -0.24), (-0.24, 0.24), (0.24, 0.24)):
                cx = rect.center().x() + rect.width() * sx
                cy = rect.center().y() + rect.height() * sy
                q.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)

        elif shape == "porc":
            q.drawRoundedRect(rect, px(4), px(4))
            q.setPen(QtGui.QPen(qc(accent, 170), 1))
            q.setBrush(qc(accent, 35))
            q.drawEllipse(rect.adjusted(px(4), px(4), -px(4), -px(4)))

        elif shape == "safe":
            q.drawRoundedRect(rect, px(2), px(2))
            q.setPen(QtGui.QPen(qc(accent, 200), max(1.0, px(1.6))))
            q.setBrush(QtCore.Qt.NoBrush)
            rr = min(rect.width(), rect.height()) * 0.26
            q.drawEllipse(rect.center(), rr, rr)
            q.drawLine(rect.center() + QtCore.QPointF(0, -rr),
                       rect.center() + QtCore.QPointF(0, rr))

        elif shape == "window":
            glow = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
            glow.setColorAt(0.0, qc(accent, 120))
            glow.setColorAt(1.0, qc(accent, 40))
            q.setBrush(QtGui.QBrush(glow))
            q.drawRect(rect)
            q.setPen(QtGui.QPen(qc(accent, 210), max(1.0, px(1.6))))
            q.drawLine(QtCore.QPointF(rect.center().x(), rect.top()),
                       QtCore.QPointF(rect.center().x(), rect.bottom()))

        elif shape == "door":
            q.drawRect(rect)
            q.setPen(QtGui.QPen(qc(accent, 150), max(1.0, px(1.2)), QtCore.Qt.DotLine))
            q.setBrush(QtCore.Qt.NoBrush)
            q.drawArc(QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.width()),
                      0, 90 * 16)

        elif shape == "stairs":
            q.drawRect(rect)
            q.setPen(QtGui.QPen(qc(accent, 140), 1))
            n = max(3, int(rect.height() / px(9)))
            for i in range(1, n):
                y = rect.top() + rect.height() * i / n
                q.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))

        elif shape == "plant":
            q.setBrush(qc("#2a1a10", fc.alpha()))
            q.drawEllipse(rect.adjusted(rect.width() * 0.28, rect.height() * 0.45,
                                        -rect.width() * 0.28, -px(1)))
            q.setBrush(qc(accent, min(210, fc.alpha() + 60)))
            q.setPen(QtGui.QPen(qc(accent, 220), 1))
            leaf = min(rect.width(), rect.height()) * 0.30
            for ang in range(0, 360, 72):
                a = math.radians(ang)
                cx = rect.center().x() + math.cos(a) * leaf * 0.75
                cy = rect.center().y() - rect.height() * 0.10 + math.sin(a) * leaf * 0.55
                q.drawEllipse(QtCore.QPointF(cx, cy), leaf * 0.52, leaf * 0.38)

        elif shape == "ref":
            path = QtGui.QPainterPath()
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.center().x(), rect.bottom())
            path.lineTo(rect.left(), rect.center().y())
            path.closeSubpath()
            q.drawPath(path)

        else:
            q.drawRect(rect)

    def _draw_node(self, q, rect, o, ghost, level):
        r = max(px(12.0), min(rect.width(), rect.height()) / 2)
        col = qc(TEAL)
        lv = 0.0
        st = "OFFLINE"
        if level is not None:
            st = level.get("state", "OFFLINE")
            col = qc(STATE_COLORS.get(st, DIM))
            lv = clamp(level.get("level", 0) / 100.0, 0, 1)
        if ghost:
            col.setAlpha(130)

        if lv > 0.01 and not ghost:
            for i in range(3):
                ring = QtGui.QColor(col)
                ring.setAlpha(int((26 + 80 * lv) / (i + 1)))
                q.setPen(QtGui.QPen(ring, max(1.0, px(1.4))))
                q.setBrush(QtCore.Qt.NoBrush)
                rr = r + px(6) + px(24) * lv * (i + 1) * 0.6
                q.drawEllipse(QtCore.QRectF(-rr, -rr, 2 * rr, 2 * rr))

        # body: hexagon reads as "device", not "dot"
        hexa = QtGui.QPolygonF()
        for k in range(6):
            a = math.radians(60 * k - 90)
            hexa.append(QtCore.QPointF(r * math.cos(a), r * math.sin(a)))
        g = QtGui.QRadialGradient(QtCore.QPointF(0, 0), r)
        g.setColorAt(0.0, qc(PANEL_3))
        g.setColorAt(1.0, qc(VOID))
        q.setBrush(QtGui.QBrush(g))
        q.setPen(QtGui.QPen(col, max(1.5, px(2))))
        q.drawPolygon(hexa)
        q.setPen(QtGui.QPen(col))
        q.setFont(QtGui.QFont("Consolas", max(fs(9), int(r * 0.8)), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(-r, -r, 2 * r, 2 * r), QtCore.Qt.AlignCenter,
                   str(o.node_index or "?"))

    def _draw_router(self, q, rect, ghost, level=None):
        col = qc(AMBER, 130 if ghost else 255)
        body = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
        body.setColorAt(0.0, qc("#302507"))
        body.setColorAt(1.0, qc("#1a1405"))
        q.setBrush(QtGui.QBrush(body))
        q.setPen(QtGui.QPen(col, max(1.5, px(2))))
        q.drawRoundedRect(rect, px(3), px(3))
        # antennas
        q.setPen(QtGui.QPen(col, max(1.0, px(1.6))))
        for f in (0.22, 0.5, 0.78):
            x = rect.left() + rect.width() * f
            q.drawLine(QtCore.QPointF(x, rect.top()),
                       QtCore.QPointF(x, rect.top() - px(9)))
        # broadcast rings
        q.setBrush(QtCore.Qt.NoBrush)
        for i in range(1, 4):
            a = QtGui.QColor(col)
            a.setAlpha(max(16, 100 - i * 26))
            q.setPen(QtGui.QPen(a, max(1.0, px(1.2))))
            rr = max(rect.width(), rect.height()) / 2 + i * px(8)
            q.drawEllipse(QtCore.QRectF(-rr, -rr, 2 * rr, 2 * rr))

    # ---- HUD furniture ----
    def draw_corner_brackets(self, q, color=BORDER_HI, size=22, inset=6, thick=2):
        w, h = self.width(), self.height()
        s, i = px(size), px(inset)
        q.setPen(QtGui.QPen(qc(color), px(thick)))
        for cx, cy, dx, dy in ((i, i, 1, 1), (w - i, i, -1, 1),
                               (i, h - i, 1, -1), (w - i, h - i, -1, -1)):
            q.drawLine(int(cx), int(cy), int(cx + dx * s), int(cy))
            q.drawLine(int(cx), int(cy), int(cx), int(cy + dy * s))

    def draw_edge_ticks(self, q, color=GHOST, every=10):
        """Ruler ticks around the frame -- pure instrument dressing."""
        q.setPen(QtGui.QPen(qc(color), 1))
        w, h = self.width(), self.height()
        step = px(every)
        x = 0
        n = 0
        while x < w:
            ln = px(7) if n % 5 == 0 else px(3)
            q.drawLine(x, 0, x, ln)
            q.drawLine(x, h, x, h - ln)
            x += step
            n += 1
        y = 0
        n = 0
        while y < h:
            ln = px(7) if n % 5 == 0 else px(3)
            q.drawLine(0, y, ln, y)
            q.drawLine(w, y, w - ln, y)
            y += step
            n += 1

    def draw_scale_bar(self, q, color=MUTED):
        """A 1 m bar so a screenshot is self-documenting."""
        L = self.scale * 1.0
        if L < px(18):
            return
        x0 = self.width() - px(24) - L
        y0 = self.height() - px(22)
        q.setPen(QtGui.QPen(qc(color), max(1.0, px(1.6))))
        q.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x0 + L, y0))
        for x in (x0, x0 + L):
            q.drawLine(QtCore.QPointF(x, y0 - px(4)), QtCore.QPointF(x, y0 + px(4)))
        q.setFont(QtGui.QFont("Consolas", fs(10), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(x0, y0 - px(20), L, px(15)),
                   QtCore.Qt.AlignCenter, "1 m")

    def draw_compass(self, q, color=MUTED):
        cx = px(30)
        cy = px(34)
        r = px(15)
        q.setPen(QtGui.QPen(qc(color, 150), 1))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        q.setPen(QtGui.QPen(qc(TEAL), max(1.0, px(1.8))))
        q.drawLine(QtCore.QPointF(cx, cy + r * 0.55), QtCore.QPointF(cx, cy - r * 0.85))
        q.drawLine(QtCore.QPointF(cx - px(4), cy - r * 0.45),
                   QtCore.QPointF(cx, cy - r * 0.85))
        q.drawLine(QtCore.QPointF(cx + px(4), cy - r * 0.45),
                   QtCore.QPointF(cx, cy - r * 0.85))
        q.setPen(QtGui.QPen(qc(color)))
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(cx - px(10), cy - r - px(17), px(20), px(14)),
                   QtCore.Qt.AlignCenter, "N")



# ==========================================================================
# TACTICAL VIEW  --  the live room display
# ==========================================================================
class TacticalView(RoomCanvas):
    def __init__(self, room, cfg):
        super().__init__(room)
        self.cfg = cfg
        self.heat = None
        self._rgba = None
        self._img = None
        self.solution = None
        self.track = {"active": False, "state": "IDLE"}
        self.zone = "STANDBY"
        self.node_state = {}
        self.sweep = 0.0
        self.pulse = 0.0
        self.flow = 0.0
        self.armed = False
        self.usable = 0
        self.active = 0
        self.sim_truth = None
        self.system_state = "OFFLINE"
        self.setMinimumSize(px(520), px(380))

    # ---- data in ----
    def set_solution(self, sol, track, node_state, system_state):
        self.solution = sol
        self.track = track or {"active": False, "state": "IDLE"}
        self.node_state = node_state
        self.system_state = system_state
        self.usable = sol.get("usable", 0) if sol else 0
        self.active = sol.get("active", 0) if sol else 0
        self.zone = (track.get("zone") if track and track.get("active")
                     else (sol.get("zone") if sol else "STANDBY"))
        self.heat = sol.get("heat") if sol else None
        self._img = None
        self.update()

    def advance(self, dt):
        self.sweep = (self.sweep + dt * 46.0) % 360.0
        self.pulse = (self.pulse + dt * 2.0) % (2 * math.pi)
        self.flow = (self.flow + dt * 34.0) % 1000.0

    # ---- heat bitmap ----
    def _heat_image(self):
        if self.heat is None or not self.cfg.get("show_heat", True):
            return None
        if self._img is not None:
            return self._img
        H = np.clip(self.heat, 0, 1)
        idx = (H * 255).astype(np.uint8)
        rgba = HEAT_LUT[idx]
        rgba = np.ascontiguousarray(rgba)
        self._rgba = rgba
        h, w = H.shape
        img = QtGui.QImage(rgba.data, w, h, 4 * w, QtGui.QImage.Format_RGBA8888)
        self._img = img.copy()
        return self._img

    # ---- paint ----
    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        q.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.draw_backdrop(q)
        self.fit()

        if self.cfg.get("show_grid", True):
            self.draw_grid(q, labels=True)
        self.draw_room_outline(q)
        self._draw_range_rings(q)
        self._draw_furniture(q)
        self._draw_paths(q)
        self._draw_heat(q)
        if self.cfg.get("show_scan", True):
            self._draw_sweep(q)
        self._draw_nodes(q)
        self._draw_sim_truth(q)
        self._draw_alt(q)
        self._draw_trail(q)
        self._draw_target(q)
        self._draw_hud(q)
        if self.cfg.get("show_dossier", True):
            self._draw_dossier(q)
        self.draw_scale_bar(q)
        self.draw_compass(q)
        self.draw_edge_ticks(q)
        self.draw_corner_brackets(q, TEAL if self.armed else BORDER_HI)
        self.draw_scanlines(q, alpha=10)
        q.end()

    # ---- layers ----
    def _draw_range_rings(self, q):
        if not self.cfg.get("show_rings", True):
            return
        r = self.room.router()
        if r is None:
            return
        c = self.p(*r.center())
        q.setPen(QtGui.QPen(qc(AMBER, 26), 1, QtCore.Qt.DotLine))
        q.setBrush(QtCore.Qt.NoBrush)
        for m in (1, 2, 3, 4, 5, 6, 8, 10):
            rr = m * self.scale
            if rr > max(self.width(), self.height()):
                break
            q.drawEllipse(c, rr, rr)
            if m in (2, 4, 6, 8, 10):
                q.setPen(QtGui.QPen(qc(AMBER, 60)))
                q.setFont(QtGui.QFont("Consolas", fs(8)))
                q.drawText(QtCore.QPointF(c.x() + rr - px(16), c.y() - px(3)), "%dm" % m)
                q.setPen(QtGui.QPen(qc(AMBER, 26), 1, QtCore.Qt.DotLine))

    def _draw_furniture(self, q):
        for o in self.room.objects:
            if o.kind in ("node", "router"):
                continue
            self.draw_object(q, o, ghost=True, labels=True)

    def _draw_paths(self, q):
        if not self.cfg.get("show_paths", True):
            return
        r = self.room.router()
        if r is None:
            return
        rp = self.p(*r.center())
        for idx, n in self.room.nodes().items():
            st = self.node_state.get(idx, {})
            state = st.get("state", "OFFLINE")
            lv = clamp(st.get("level", 0) / 100.0, 0, 1)
            col = qc(STATE_COLORS.get(state, DIM))
            npt = self.p(*n.center())
            # dim base line
            base = QtGui.QColor(col)
            base.setAlpha(int(28 + 70 * lv))
            q.setPen(QtGui.QPen(base, max(1.0, px(1.2 + 2.2 * lv))))
            q.drawLine(npt, rp)
            # animated flow dashes toward the router when the link is hot
            if lv > 0.05:
                pen = QtGui.QPen(qc(col, int(70 + 150 * lv)), max(1.0, px(1.6)))
                pen.setDashPattern([px(3), px(9)])
                pen.setDashOffset(-self.flow * (0.5 + lv))
                q.setPen(pen)
                q.drawLine(npt, rp)

    def _draw_heat(self, q):
        img = self._heat_image()
        if img is None:
            return
        rect = QtCore.QRectF(self.p(0, 0), self.p(self.room.width, self.room.height))
        q.save()
        q.setOpacity(0.80)
        q.drawImage(rect, img)
        q.restore()

    def _draw_sweep(self, q):
        rect = QtCore.QRectF(self.p(0, 0), self.p(self.room.width, self.room.height))
        c = rect.center()
        R = math.hypot(rect.width(), rect.height()) / 2
        q.save()
        q.setClipRect(rect)
        g = QtGui.QConicalGradient(c, -self.sweep)
        col = qc(TEAL if not self.armed else CYAN)
        g.setColorAt(0.00, qc(col.name(), 60))
        g.setColorAt(0.06, qc(col.name(), 16))
        g.setColorAt(0.16, qc(col.name(), 0))
        g.setColorAt(1.00, qc(col.name(), 0))
        q.setBrush(QtGui.QBrush(g))
        q.setPen(QtCore.Qt.NoPen)
        q.drawEllipse(c, R, R)
        a = math.radians(self.sweep)
        q.setPen(QtGui.QPen(qc(col.name(), 90), max(1.0, px(1.2))))
        q.drawLine(c, QtCore.QPointF(c.x() + R * math.cos(a), c.y() - R * math.sin(a)))
        q.restore()

    def _draw_nodes(self, q):
        for idx, n in self.room.nodes().items():
            st = self.node_state.get(idx, {})
            self.draw_object(q, n, level=st)
            p = self.p(*n.center())
            state = st.get("state", "OFFLINE")
            col = qc(STATE_COLORS.get(state, DIM))
            q.setPen(QtGui.QPen(col))
            q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
            q.drawText(QtCore.QRectF(p.x() - px(46), p.y() + px(15), px(92), px(14)),
                       QtCore.Qt.AlignCenter,
                       "N%d  %s" % (idx, STATE_SHORT.get(state, state)))
        r = self.room.router()
        if r is not None:
            self.draw_object(q, r)
            p = self.p(*r.center())
            q.setPen(QtGui.QPen(qc(AMBER, 190)))
            q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
            q.drawText(QtCore.QRectF(p.x() - px(46), p.y() + px(17), px(92), px(14)),
                       QtCore.Qt.AlignCenter, "AP")

    def _draw_sim_truth(self, q):
        if not self.sim_truth:
            return
        x, y, moving = self.sim_truth
        if x < 0:
            return
        p = self.p(x, y)
        col = qc(PURPLE, 190)
        q.setPen(QtGui.QPen(col, max(1.0, px(1.4)), QtCore.Qt.DashLine))
        q.setBrush(QtCore.Qt.NoBrush)
        rr = px(13)
        q.drawEllipse(p, rr, rr)
        q.drawLine(QtCore.QPointF(p.x() - rr - px(5), p.y()),
                   QtCore.QPointF(p.x() + rr + px(5), p.y()))
        q.drawLine(QtCore.QPointF(p.x(), p.y() - rr - px(5)),
                   QtCore.QPointF(p.x(), p.y() + rr + px(5)))
        q.setPen(QtGui.QPen(col))
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(p.x() - px(60), p.y() - px(30), px(120), px(14)),
                   QtCore.Qt.AlignCenter, "SIM TRUTH")

    def _draw_alt(self, q):
        sol = self.solution or {}
        if not sol.get("ambiguous") or not sol.get("alt"):
            return
        p = self.p(*sol["alt"])
        col = qc(AMBER, 160)
        q.setPen(QtGui.QPen(col, max(1.0, px(1.4)), QtCore.Qt.DotLine))
        q.setBrush(QtCore.Qt.NoBrush)
        rr = px(20)
        q.drawEllipse(p, rr, rr)
        q.setPen(QtGui.QPen(col))
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(p.x() - px(70), p.y() + rr + px(2), px(140), px(14)),
                   QtCore.Qt.AlignCenter, "MIRROR SOLUTION?")

    def _draw_trail(self, q):
        if not self.cfg.get("show_trail", True):
            return
        tr = self.track.get("trail") or []
        if len(tr) < 2:
            return
        now = time.time()
        for i in range(1, len(tr)):
            t0, x0, y0 = tr[i - 1]
            t1, x1, y1 = tr[i]
            age = now - t1
            a = int(clamp(150 * (1.0 - age / 12.0), 0, 150))
            if a <= 3:
                continue
            q.setPen(QtGui.QPen(qc(CYAN, a), max(1.0, px(1.6))))
            q.drawLine(self.p(x0, y0), self.p(x1, y1))
        # breadcrumb dots every few points
        q.setBrush(qc(CYAN, 90))
        q.setPen(QtCore.Qt.NoPen)
        for i in range(0, len(tr), 6):
            _, x, y = tr[i]
            q.drawEllipse(self.p(x, y), px(2), px(2))

    def _draw_target(self, q):
        tk = self.track
        if not tk.get("active"):
            return
        x, y = tk["pos"]
        p = self.p(x, y)
        state = tk.get("state", "LOCKED")
        col = qc(CYAN if state == "LOCKED" else (AMBER if state == "COASTING" else TEAL))
        rad_m = tk.get("radius") or 1.0
        rr = max(px(14), rad_m * self.scale)

        # ---- uncertainty ring: THIS is the honest answer ----
        q.setBrush(QtCore.Qt.NoBrush)
        halo = QtGui.QRadialGradient(p, rr)
        halo.setColorAt(0.0, qc(col.name(), 42))
        halo.setColorAt(0.75, qc(col.name(), 14))
        halo.setColorAt(1.0, qc(col.name(), 0))
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(QtGui.QBrush(halo))
        q.drawEllipse(p, rr, rr)
        q.setBrush(QtCore.Qt.NoBrush)
        pen = QtGui.QPen(qc(col.name(), 150), max(1.0, px(1.4)))
        pen.setDashPattern([px(4), px(5)])
        pen.setDashOffset(self.flow * 0.4)
        q.setPen(pen)
        q.drawEllipse(p, rr, rr)

        # expanding pulse
        ph = (math.sin(self.pulse) + 1) / 2
        pr = rr * (0.55 + 0.5 * ph)
        q.setPen(QtGui.QPen(qc(col.name(), int(70 * (1 - ph))), max(1.0, px(1.6))))
        q.drawEllipse(p, pr, pr)

        # ---- the core marker: thick enough to read, small enough to be honest ----
        core = max(px(7.0), min(px(15.0), 0.16 * self.scale))
        g = QtGui.QRadialGradient(p, core * 1.9)
        g.setColorAt(0.0, qc("#ffffff", 235))
        g.setColorAt(0.45, qc(col.name(), 225))
        g.setColorAt(1.0, qc(col.name(), 0))
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(QtGui.QBrush(g))
        q.drawEllipse(p, core * 1.9, core * 1.9)
        q.setBrush(qc(col.name(), 255))
        q.drawEllipse(p, core, core)
        q.setPen(QtGui.QPen(qc("#04070a"), max(1.0, px(1.4))))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawEllipse(p, core, core)

        # crosshair reticle
        q.setPen(QtGui.QPen(qc(col.name(), 200), max(1.0, px(1.4))))
        gap = core + px(4)
        arm = px(11)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q.drawLine(QtCore.QPointF(p.x() + dx * gap, p.y() + dy * gap),
                       QtCore.QPointF(p.x() + dx * (gap + arm), p.y() + dy * (gap + arm)))

        # velocity vector
        vx, vy = tk.get("vel", (0, 0))
        sp = tk.get("speed", 0)
        if sp > 0.10:
            L = clamp(sp, 0.2, 2.5) * self.scale * 0.55
            ang = math.atan2(vy, vx)
            tip = QtCore.QPointF(p.x() + math.cos(ang) * L, p.y() + math.sin(ang) * L)
            q.setPen(QtGui.QPen(qc(col.name(), 210), max(1.5, px(2.2))))
            q.drawLine(p, tip)
            for s in (-1, 1):
                a2 = ang + s * math.radians(150)
                q.drawLine(tip, QtCore.QPointF(tip.x() + math.cos(a2) * px(9),
                                               tip.y() + math.sin(a2) * px(9)))

    # ---- HUD text ----
    def _draw_hud(self, q):
        q.setFont(QtGui.QFont("Consolas", fs(10), QtGui.QFont.Bold))
        top = px(10)
        # left: system state
        col = qc(STATE_COLORS.get(self.system_state, DIM))
        q.setPen(QtGui.QPen(col))
        q.drawText(QtCore.QPointF(px(52), top + px(12)), self.system_state)
        q.setPen(QtGui.QPen(qc(MUTED)))
        q.setFont(QtGui.QFont("Consolas", fs(9)))
        q.drawText(QtCore.QPointF(px(52), top + px(26)),
                   "NODES %d/%d USABLE   %d ACTIVE" % (self.usable, NODE_COUNT, self.active))

        # right: armed chip
        txt = "● ARMED" if self.armed else "○ DISARMED"
        acol = qc(RED if self.armed else DIM)
        q.setFont(QtGui.QFont("Consolas", fs(11), QtGui.QFont.Bold))
        fm = QtGui.QFontMetrics(q.font())
        adv = getattr(fm, "horizontalAdvance", None)
        w = (adv(txt) if adv else fm.width(txt)) + px(18)
        r = QtCore.QRectF(self.width() - w - px(14), top, w, px(22))
        q.setPen(QtGui.QPen(acol, 1))
        q.setBrush(qc(acol.name(), 34))
        q.drawRoundedRect(r, px(4), px(4))
        q.setPen(QtGui.QPen(acol))
        q.drawText(r, QtCore.Qt.AlignCenter, txt)

        # bottom-left: the standing honesty line
        q.setFont(QtGui.QFont("Consolas", fs(9)))
        q.setPen(QtGui.QPen(qc(DIM)))
        q.drawText(QtCore.QPointF(px(14), self.height() - px(10)),
                   "COARSE LIKELIHOOD ZONE — NOT A POSITION FIX · ring = measured uncertainty")

        # centre: reason, when there is nothing to draw
        sol = self.solution or {}
        if not self.track.get("active") and sol.get("reason"):
            q.setFont(QtGui.QFont("Consolas", fs(15), QtGui.QFont.Bold))
            q.setPen(QtGui.QPen(qc(DIM)))
            q.drawText(self.rect(), QtCore.Qt.AlignCenter, sol["reason"])

    # ---- the target dossier ----
    def _draw_dossier(self, q):
        tk = self.track
        if not tk.get("active"):
            return
        sol = self.solution or {}
        x, y = tk["pos"]
        anchor = self.p(x, y)

        W = px(268)
        rows = [
            ("ZONE", tk.get("zone", "—"), TEAL),
            ("EST. POSITION", "%.2f, %.2f m" % (x, y), TEXT),
            ("UNCERTAINTY", "± %.1f m" % (tk.get("radius") or 0), AMBER),
            ("MOVEMENT", tk.get("class", "—"), tk.get("class_color", TEXT)),
            ("SPEED", "%.2f m/s" % tk.get("speed", 0), TEXT),
            ("HEADING", ("%03d°  %s" % (tk.get("heading", 0), compass(tk.get("heading", 0))))
             if tk.get("speed", 0) > 0.08 else "—", TEXT),
            ("DWELL HERE", fmt_dur(tk.get("zone_dwell", 0)), TEXT),
            ("TRACK AGE", fmt_dur(tk.get("age", 0)), TEXT),
            ("PATH LENGTH", "%.1f m" % tk.get("travelled", 0), TEXT),
        ]
        if tk.get("near"):
            rows.append(("NEAREST MAPPED", "%s  %.1f m" % (tk["near"], tk.get("near_d", 0)), MUTED))
        rows.append(("EVIDENCE", "%d of %d nodes crossed" % (tk.get("evidence", 0),
                                                             max(1, tk.get("usable", 0))), MUTED))
        if tk.get("state") == "COASTING":
            rows.append(("FIX AGE", "%.1f s stale" % tk.get("stale", 0), AMBER))
        else:
            rows.append(("SOLUTION QUALITY", "%d%%" % int(100 * sol.get("quality", 0)),
                         GREEN if sol.get("quality", 0) > 0.6 else AMBER))

        head_h = px(30)
        row_h = px(16)
        foot_h = px(58)
        H = head_h + row_h * len(rows) + foot_h + px(12)

        # place it clear of the target, inside the widget
        bx = anchor.x() + px(34)
        if bx + W > self.width() - px(10):
            bx = anchor.x() - W - px(34)
        bx = clamp(bx, px(8), max(px(8), self.width() - W - px(8)))
        by = clamp(anchor.y() - H / 2, px(34), max(px(34), self.height() - H - px(12)))
        box = QtCore.QRectF(bx, by, W, H)

        # connector
        q.setPen(QtGui.QPen(qc(CYAN, 120), 1, QtCore.Qt.DashLine))
        side = box.left() if bx > anchor.x() else box.right()
        q.drawLine(anchor, QtCore.QPointF(side, box.center().y()))

        # panel
        q.setPen(QtGui.QPen(qc(CYAN, 130), max(1.0, px(1.2))))
        q.setBrush(qc("#050a10", 232))
        q.drawRoundedRect(box, px(6), px(6))
        # header strip
        hdr = QtCore.QRectF(box.left(), box.top(), W, head_h)
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(qc(CYAN, 26))
        q.drawRoundedRect(hdr, px(6), px(6))
        q.setBrush(qc(CYAN, 26))
        q.drawRect(QtCore.QRectF(box.left(), box.top() + head_h - px(6), W, px(6)))

        state = tk.get("state", "LOCKED")
        scol = CYAN if state == "LOCKED" else (AMBER if state == "COASTING" else TEAL)
        q.setPen(QtGui.QPen(qc(CYAN)))
        q.setFont(QtGui.QFont("Consolas", fs(12), QtGui.QFont.Bold))
        q.drawText(hdr.adjusted(px(10), 0, -px(10), 0),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   "TRACK %02d" % tk.get("id", 0))
        q.setPen(QtGui.QPen(qc(scol)))
        q.drawText(hdr.adjusted(px(10), 0, -px(10), 0),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, state)

        # rows
        yy = box.top() + head_h + px(4)
        for lab, val, col in rows:
            q.setFont(QtGui.QFont("Consolas", fs(9)))
            q.setPen(QtGui.QPen(qc(DIM)))
            q.drawText(QtCore.QRectF(box.left() + px(10), yy, px(112), row_h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, lab)
            q.setFont(QtGui.QFont("Consolas", fs(10), QtGui.QFont.Bold))
            q.setPen(QtGui.QPen(qc(col)))
            q.drawText(QtCore.QRectF(box.left() + px(120), yy, W - px(130), row_h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, str(val))
            yy += row_h

        # ---- the block that keeps you credible ----
        yy += px(4)
        q.setPen(QtGui.QPen(qc(BORDER_HI), 1))
        q.drawLine(QtCore.QPointF(box.left() + px(10), yy),
                   QtCore.QPointF(box.right() - px(10), yy))
        yy += px(5)
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.setPen(QtGui.QPen(qc(RED, 200)))
        q.drawText(QtCore.QRectF(box.left() + px(10), yy, W - px(20), px(13)),
                   QtCore.Qt.AlignLeft, "NOT MEASURABLE BY THIS SENSOR")
        yy += px(13)
        q.setFont(QtGui.QFont("Consolas", fs(9)))
        q.setPen(QtGui.QPen(qc(MUTED)))
        for line in ("identity · biometrics · height · build",
                     "headcount · carried devices · imagery"):
            q.drawText(QtCore.QRectF(box.left() + px(10), yy, W - px(20), px(13)),
                       QtCore.Qt.AlignLeft, line)
            yy += px(12)


class SparkLine(QtWidgets.QWidget):
    """Rolling energy trace for one node. Bigger and clearer than v3."""

    def __init__(self, engine, height=42):
        super().__init__()
        self.engine = engine
        self.setFixedHeight(px(height))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        q.fillRect(self.rect(), qc(PANEL_2))
        e = self.engine
        # grid
        q.setPen(QtGui.QPen(qc(GRID), 1))
        for i in range(1, 4):
            y = h * i / 4.0
            q.drawLine(0, int(y), w, int(y))
        # threshold + clear lines
        thr_y = h - h * clamp(e.threshold / 100.0, 0, 1)
        clr_y = h - h * clamp(e.threshold * e.cfg["exit_ratio"] / 100.0, 0, 1)
        q.setPen(QtGui.QPen(qc(MUTED, 90), 1, QtCore.Qt.DotLine))
        q.drawLine(0, int(clr_y), w, int(clr_y))
        q.setPen(QtGui.QPen(qc(RED, 130), max(1, px(1)), QtCore.Qt.DashLine))
        q.drawLine(0, int(thr_y), w, int(thr_y))
        # trace
        data = list(e.trace)
        if len(data) > 1:
            col = qc(STATE_COLORS.get(e.state, DIM))
            path = QtGui.QPainterPath()
            fillp = QtGui.QPainterPath()
            n = len(data)
            for i, v in enumerate(data):
                x = w * i / (n - 1)
                y = h - h * clamp(v / 100.0, 0, 1)
                if i == 0:
                    path.moveTo(x, y)
                    fillp.moveTo(x, h)
                    fillp.lineTo(x, y)
                else:
                    path.lineTo(x, y)
                    fillp.lineTo(x, y)
            fillp.lineTo(w, h)
            fillp.closeSubpath()
            g = QtGui.QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, qc(col.name(), 90))
            g.setColorAt(1.0, qc(col.name(), 6))
            q.setPen(QtCore.Qt.NoPen)
            q.setBrush(QtGui.QBrush(g))
            q.drawPath(fillp)
            q.setPen(QtGui.QPen(col, max(1.0, px(1.6))))
            q.setBrush(QtCore.Qt.NoBrush)
            q.drawPath(path)
            # head dot
            hx = w
            hy = h - h * clamp(data[-1] / 100.0, 0, 1)
            q.setBrush(col)
            q.setPen(QtCore.Qt.NoPen)
            q.drawEllipse(QtCore.QPointF(hx - px(2), hy), px(2.5), px(2.5))
        q.setPen(QtGui.QPen(qc(BORDER), 1))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawRect(0, 0, w - 1, h - 1)
        q.end()



# ==========================================================================
# ROOM PLANNER
# ==========================================================================
class PlannerView(RoomCanvas):
    selection_changed = QtCore.pyqtSignal(object)
    room_changed = QtCore.pyqtSignal()

    HANDLE = 9

    def __init__(self, room):
        super().__init__(room)
        self.selected = None
        self.mode = None            # move | resize | rotate
        self.handle = None
        self.grab_dx = 0.0
        self.grab_dy = 0.0
        self.snap = 0.10
        self.cursor_m = (0, 0)
        self.show_legend = True
        self._undo = collections.deque(maxlen=40)
        self._redo = collections.deque(maxlen=40)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    # ---- undo ----
    def snapshot(self):
        self._undo.append(json.dumps(self.room.to_dict()))
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(json.dumps(self.room.to_dict()))
        self.room.load_dict(json.loads(self._undo.pop()))
        self.select(None)
        self.room_changed.emit()
        self.update()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(json.dumps(self.room.to_dict()))
        self.room.load_dict(json.loads(self._redo.pop()))
        self.select(None)
        self.room_changed.emit()
        self.update()
        return True

    # ---- helpers ----
    def _hit(self, mx, my):
        for o in reversed(self.room.objects):
            if o.contains(mx, my):
                return o
        return None

    def _handle_rects(self):
        if not self.selected:
            return {}
        o = self.selected
        pts = o.corners()
        out = {}
        H = px(self.HANDLE)
        for name, pt in zip(("nw", "ne", "se", "sw"), pts):
            p = self.p(*pt)
            out[name] = QtCore.QRectF(p.x() - H, p.y() - H, 2 * H, 2 * H)
        cx, cy = o.center()
        a = math.radians(o.rot)
        rx = cx + (-(o.h / 2 + 0.40)) * math.sin(a)
        ry = cy - (o.h / 2 + 0.40) * math.cos(a)
        p = self.p(rx, ry)
        out["rot"] = QtCore.QRectF(p.x() - H, p.y() - H, 2 * H, 2 * H)
        return out

    def _snap(self, v):
        if self.snap <= 0:
            return round(v, 3)
        return round(round(v / self.snap) * self.snap, 3)

    def select(self, obj):
        self.selected = obj
        self.selection_changed.emit(obj)
        self.update()

    def add(self, kind):
        used = {o.node_index for o in self.room.objects if o.kind == "node"}
        idx = 0
        if kind == "node":
            for i in range(1, NODE_COUNT + 1):
                if i not in used:
                    idx = i
                    break
            if idx == 0:
                return None
        if kind == "router" and self.room.router() is not None:
            return None
        self.snapshot()
        spec = OBJ_TYPES[kind]
        n = sum(1 for x in self.room.objects if x.kind not in ("node", "router"))
        casc = 0.18 * (n % 6)
        o = RoomObject(kind, self.room.width / 2 - spec[1] / 2 + casc,
                       self.room.height / 2 - spec[2] / 2 + casc, node_index=idx,
                       label=("NODE %d" % idx) if kind == "node" else "")
        self.room.objects.append(o)
        self.select(o)
        self.room_changed.emit()
        return o

    def delete_selected(self):
        if self.selected in self.room.objects:
            self.snapshot()
            self.room.objects.remove(self.selected)
            self.select(None)
            self.room_changed.emit()

    def duplicate_selected(self):
        o = self.selected
        if not o or o.kind in ("node", "router"):
            return
        self.snapshot()
        n = RoomObject(o.kind, o.x + 0.25, o.y + 0.25, o.w, o.h, o.rot, o.label,
                       o.metal, 0)
        self.room.objects.append(n)
        self.select(n)
        self.room_changed.emit()

    # ---- mouse ----
    def mousePressEvent(self, ev):
        mx, my = self.m(ev.x(), ev.y())
        if self.selected:
            for name, rect in self._handle_rects().items():
                if rect.contains(ev.pos()):
                    self.snapshot()
                    self.mode = "rotate" if name == "rot" else "resize"
                    self.handle = name
                    return
        o = self._hit(mx, my)
        self.select(o)
        if o:
            self.snapshot()
            self.mode = "move"
            self.grab_dx = mx - o.x
            self.grab_dy = my - o.y

    def mouseMoveEvent(self, ev):
        mx, my = self.m(ev.x(), ev.y())
        self.cursor_m = (mx, my)
        o = self.selected
        if o and self.mode == "move":
            o.x = self._snap(mx - self.grab_dx)
            o.y = self._snap(my - self.grab_dy)
            o.x = max(-0.5, min(self.room.width - 0.05, o.x))
            o.y = max(-0.5, min(self.room.height - 0.05, o.y))
            self.room_changed.emit()
        elif o and self.mode == "resize":
            if self.handle in ("nw", "sw"):
                right = o.x + o.w
                o.x = min(self._snap(mx), right - 0.1)
                o.w = round(right - o.x, 3)
            else:
                o.w = max(0.1, self._snap(mx - o.x))
            if self.handle in ("nw", "ne"):
                bottom = o.y + o.h
                o.y = min(self._snap(my), bottom - 0.1)
                o.h = round(bottom - o.y, 3)
            else:
                o.h = max(0.1, self._snap(my - o.y))
            self.room_changed.emit()
        elif o and self.mode == "rotate":
            cx, cy = o.center()
            ang = math.degrees(math.atan2(mx - cx, -(my - cy)))
            if not (ev.modifiers() & QtCore.Qt.ShiftModifier):
                ang = round(ang / 15.0) * 15.0
            o.rot = round(ang, 1)
            self.room_changed.emit()
        self.update()

    def mouseReleaseEvent(self, _):
        self.mode = None
        self.handle = None

    def keyPressEvent(self, ev):
        o = self.selected
        if ev.key() == QtCore.Qt.Key_Delete and o:
            self.delete_selected()
            return
        if ev.key() == QtCore.Qt.Key_Z and (ev.modifiers() & QtCore.Qt.ControlModifier):
            self.undo()
            return
        if ev.key() == QtCore.Qt.Key_Y and (ev.modifiers() & QtCore.Qt.ControlModifier):
            self.redo()
            return
        if o:
            step = 0.01 if (ev.modifiers() & QtCore.Qt.ShiftModifier) else self.snap
            dx = {QtCore.Qt.Key_Left: -step, QtCore.Qt.Key_Right: step}.get(ev.key(), 0)
            dy = {QtCore.Qt.Key_Up: -step, QtCore.Qt.Key_Down: step}.get(ev.key(), 0)
            if dx or dy:
                o.x = round(o.x + dx, 3)
                o.y = round(o.y + dy, 3)
                self.room_changed.emit()
                self.update()
                return
        super().keyPressEvent(ev)

    # ---- paint ----
    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.draw_backdrop(q)
        self.fit()
        self.draw_grid(q, labels=True)
        self.draw_room_outline(q)

        # dimension callouts
        q.setPen(QtGui.QPen(qc(TEAL, 170), 1))
        q.setFont(QtGui.QFont("Consolas", fs(11), QtGui.QFont.Bold))
        top = self.p(self.room.width / 2, 0) + QtCore.QPointF(0, -px(22))
        q.drawText(QtCore.QRectF(top.x() - px(70), top.y() - px(9), px(140), px(18)),
                   QtCore.Qt.AlignCenter, "%.2f m" % self.room.width)
        q.save()
        left = self.p(0, self.room.height / 2) + QtCore.QPointF(-px(34), 0)
        q.translate(left)
        q.rotate(-90)
        q.drawText(QtCore.QRectF(-px(70), -px(9), px(140), px(18)),
                   QtCore.Qt.AlignCenter, "%.2f m" % self.room.height)
        q.restore()

        # node -> router reference lines so geometry problems are visible
        r = self.room.router()
        if r is not None:
            rp = self.p(*r.center())
            q.setPen(QtGui.QPen(qc(AMBER, 55), 1, QtCore.Qt.DashLine))
            for idx, n in self.room.nodes().items():
                np_ = self.p(*n.center())
                q.drawLine(np_, rp)
                mid = QtCore.QPointF((np_.x() + rp.x()) / 2, (np_.y() + rp.y()) / 2)
                d = math.hypot(n.center()[0] - r.center()[0], n.center()[1] - r.center()[1])
                q.setPen(QtGui.QPen(qc(AMBER, 120)))
                q.setFont(QtGui.QFont("Consolas", fs(9)))
                q.drawText(QtCore.QRectF(mid.x() - px(28), mid.y() - px(8), px(56), px(15)),
                           QtCore.Qt.AlignCenter, "%.2fm" % d)
                q.setPen(QtGui.QPen(qc(AMBER, 55), 1, QtCore.Qt.DashLine))

        for o in self.room.objects:
            self.draw_object(q, o, selected=(o is self.selected))

        # alignment guides while dragging
        if self.selected and self.mode == "move":
            o = self.selected
            cx, cy = o.center()
            q.setPen(QtGui.QPen(qc(PURPLE, 90), 1, QtCore.Qt.DashLine))
            for other in self.room.objects:
                if other is o:
                    continue
                ox, oy = other.center()
                if abs(ox - cx) < 0.06:
                    q.drawLine(self.p(ox, 0), self.p(ox, self.room.height))
                if abs(oy - cy) < 0.06:
                    q.drawLine(self.p(0, oy), self.p(self.room.width, oy))

        # handles
        if self.selected:
            for name, rect in self._handle_rects().items():
                col = PURPLE if name == "rot" else TEAL
                q.setPen(QtGui.QPen(qc(col), max(1.0, px(1.6))))
                q.setBrush(qc(VOID))
                if name == "rot":
                    q.drawEllipse(rect)
                else:
                    q.drawRect(rect)

        if self.show_legend:
            self._draw_legend(q)

        # cursor readout bar
        bar = QtCore.QRectF(0, self.height() - px(24), self.width(), px(24))
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(qc("#050a10", 200))
        q.drawRect(bar)
        q.setPen(QtGui.QPen(qc(TEAL)))
        q.setFont(QtGui.QFont("Consolas", fs(10)))
        q.drawText(bar.adjusted(px(10), 0, -px(10), 0),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   "cursor  x %.2f m   y %.2f m   ·   snap %.0f cm" %
                   (self.cursor_m[0], self.cursor_m[1], self.snap * 100))
        q.setPen(QtGui.QPen(qc(DIM)))
        q.drawText(bar.adjusted(px(10), 0, -px(10), 0),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                   "drag move · corner resize · circle rotate · arrows nudge · "
                   "Del remove · Ctrl+Z undo")
        self.draw_corner_brackets(q)
        q.end()

    def _legend_spot(self, w, h):
        """Place the legend where it covers the fewest map elements.

        Nodes usually sit in the room's corners, so a fixed corner position
        would hide one of them. Try each corner and the side midpoints, and
        keep the first candidate with the least overlap."""
        m, W, H = px(12), self.width(), self.height()
        bottom = H - h - px(32)                       # clear the cursor bar
        mid = (H - h) / 2.0
        spots = [(W - w - m, m), (W - w - m, bottom), (m, m), (m, bottom),
                 (W - w - m, mid), (m, mid)]
        pad = px(26)                                  # node markers extend past their box
        marks = []
        for o in self.room.objects:
            a = self.p(o.x, o.y)
            b = self.p(o.x + o.w, o.y + o.h)
            marks.append(QtCore.QRectF(a, b).normalized().adjusted(-pad, -pad, pad, pad))
        best, best_cost = None, None
        for x, y in spots:
            box = QtCore.QRectF(x, y, w, h)
            cost = 0.0
            for r in marks:
                hit = box.intersected(r)
                if not hit.isEmpty():
                    cost += hit.width() * hit.height()
            if best_cost is None or cost < best_cost:
                best, best_cost = box, cost
        return best

    def _draw_legend(self, q):
        items = [("SENSOR NODE", TEAL), ("ROUTER", AMBER), ("WOOD", "#c08b4a"),
                 ("FABRIC", "#5f96c4"), ("METAL / ELECTRONIC", "#9fb2c4"),
                 ("GLASS", "#57b6e8"), ("GREENERY", "#4fbf6a")]
        w = px(168)
        h = px(16) * len(items) + px(26)
        box = self._legend_spot(w, h)
        q.setPen(QtGui.QPen(qc(BORDER_HI), 1))
        q.setBrush(qc("#050a10", 205))
        q.drawRoundedRect(box, px(5), px(5))
        q.setPen(QtGui.QPen(qc(MUTED)))
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(box.left() + px(9), box.top() + px(5), w, px(14)),
                   QtCore.Qt.AlignLeft, "MATERIAL KEY")
        yy = box.top() + px(22)
        for name, col in items:
            q.setPen(QtCore.Qt.NoPen)
            q.setBrush(qc(col))
            q.drawRect(QtCore.QRectF(box.left() + px(9), yy + px(4), px(10), px(8)))
            q.setPen(QtGui.QPen(qc(TEXT_2)))
            q.setFont(QtGui.QFont("Consolas", fs(9)))
            q.drawText(QtCore.QRectF(box.left() + px(25), yy, w - px(30), px(15)),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)
            yy += px(16)


# ==========================================================================
# NODE STRIP  --  one per node, on the operations page
# ==========================================================================
class NodeStrip(QtWidgets.QFrame):
    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self.setStyleSheet(card_css())
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(px(11), px(9), px(11), px(10))
        v.setSpacing(px(5))

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(px(7))
        self.name = QtWidgets.QLabel("NODE %d" % engine.index)
        self.name.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                                "border:none;background:none;font-family:%s;"
                                % (TEXT, fs(14), max(1, px(2)), SANS))
        self.chip = Chip("OFFLINE", DIM, size=11)
        self.lvl = QtWidgets.QLabel("0")
        self.lvl.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;border:none;"
                               "background:none;font-family:%s;" % (MUTED, fs(21), MONO))
        top.addWidget(self.name)
        top.addWidget(self.chip)
        top.addStretch()
        top.addWidget(self.lvl)
        v.addLayout(top)

        self.meter = Meter(15)
        v.addWidget(self.meter)
        self.spark = SparkLine(engine, 44)
        v.addWidget(self.spark)

        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(px(9))
        self.info = QtWidgets.QLabel("—")
        self.info.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                                "font-family:%s;" % (DIM, fs(11), MONO))
        self.conf = QtWidgets.QLabel("")
        self.conf.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                                "font-family:%s;font-weight:700;" % (MUTED, fs(11), MONO))
        bot.addWidget(self.info)
        bot.addStretch()
        bot.addWidget(self.conf)
        v.addLayout(bot)

        self.flags = QtWidgets.QLabel("")
        self.flags.setWordWrap(True)
        self.flags.setStyleSheet("border:none;background:none;font-size:%dpx;" % fs(10))
        v.addWidget(self.flags)

    def refresh(self):
        e = self.e
        col = STATE_COLORS.get(e.state, DIM)
        self.chip.set(STATE_SHORT.get(e.state, e.state), col)
        self.lvl.setText("%d" % int(e.level))
        self.lvl.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;border:none;"
                               "background:none;font-family:%s;" % (col, fs(21), MONO))
        self.meter.set(e.level, e.threshold, e.threshold * e.cfg["exit_ratio"], col)
        if e.state == "WARMING UP":
            self.info.setText("warming %d%%   trig %.0f" % (int(100 * e.warm_frac()), e.threshold))
        else:
            rssi = ("%ddBm" % e.rssi) if e.rssi is not None else "—"
            self.info.setText("%2d pkt/s  %s  %dsc  trig %.0f  %s"
                              % (e.rate, rssi, e.S, e.threshold, e.dynamics()))
        self.conf.setText(("conf %d%%" % e.confidence) if e.confidence else "")
        if e.flags:
            self.flags.setText("  ".join(
                "<span style='color:%s;'>▲ %s</span>" % (c, t) for t, c in e.flags))
            self.flags.setVisible(True)
        else:
            self.flags.setVisible(False)
        self.spark.update()


# ==========================================================================
# OPTICAL PANEL  --  plain webcam, operator aid only
# ==========================================================================
class CameraPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.image = None
        self.status = "OPTICAL FEED OFF"
        self.recording = False
        self.frames = 0
        self.t0 = time.time()
        self.setMinimumHeight(px(190))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_frame(self, img):
        self.image = img
        self.frames += 1
        self.update()

    def set_status(self, text):
        self.status = text
        self.update()

    def clear(self):
        self.image = None
        self.update()

    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        q.fillRect(self.rect(), qc(VOID))
        r = self.rect().adjusted(px(2), px(2), -px(2), -px(2))
        if self.image is not None and not self.image.isNull():
            iw, ih = self.image.width(), self.image.height()
            sc = min(r.width() / iw, r.height() / ih)
            dw, dh = iw * sc, ih * sc
            target = QtCore.QRectF(r.left() + (r.width() - dw) / 2,
                                   r.top() + (r.height() - dh) / 2, dw, dh)
            q.drawImage(target, self.image)
            # scanlines + vignette so it reads as a monitored feed
            q.setPen(QtGui.QPen(qc("#000000", 34), 1))
            y = int(target.top())
            while y < target.bottom():
                q.drawLine(int(target.left()), y, int(target.right()), y)
                y += px(3)
            g = QtGui.QRadialGradient(target.center(), max(dw, dh) * 0.72)
            g.setColorAt(0.55, qc("#000000", 0))
            g.setColorAt(1.0, qc("#000000", 150))
            q.fillRect(target, QtGui.QBrush(g))
            # reticle
            q.setPen(QtGui.QPen(qc(TEAL, 70), 1))
            q.drawLine(QtCore.QPointF(target.center().x(), target.top()),
                       QtCore.QPointF(target.center().x(), target.bottom()))
            q.drawLine(QtCore.QPointF(target.left(), target.center().y()),
                       QtCore.QPointF(target.right(), target.center().y()))
        else:
            q.setPen(QtGui.QPen(qc(DIM)))
            q.setFont(QtGui.QFont("Consolas", fs(12), QtGui.QFont.Bold))
            q.drawText(self.rect(), QtCore.Qt.AlignCenter, self.status)

        # HUD chrome
        q.setPen(QtGui.QPen(qc(BORDER_HI), max(1, px(1))))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawRect(self.rect().adjusted(0, 0, -1, -1))
        q.setPen(QtGui.QPen(qc(TEAL, 190)))
        q.setFont(QtGui.QFont("Consolas", fs(9), QtGui.QFont.Bold))
        q.drawText(QtCore.QRectF(px(8), px(5), self.width() - px(16), px(14)),
                   QtCore.Qt.AlignLeft, "OPTICAL · OPERATOR AID · NOT A SENSING INPUT")
        q.setPen(QtGui.QPen(qc(MUTED)))
        q.drawText(QtCore.QRectF(px(8), self.height() - px(18),
                                 self.width() - px(16), px(14)),
                   QtCore.Qt.AlignLeft, time.strftime("%Y-%m-%d %H:%M:%S"))
        if self.image is not None:
            q.setPen(QtGui.QPen(qc(RED)))
            q.drawText(QtCore.QRectF(px(8), self.height() - px(18),
                                     self.width() - px(16), px(14)),
                       QtCore.Qt.AlignRight, "● LIVE")
        q.end()



# ==========================================================================
# BOOT SEQUENCE
#   Real work, animated. Each line runs an actual initialisation step, so the
#   bar is not theatre -- if something fails, it fails visibly on its own line.
# ==========================================================================
def logo_pixmap(size=None):
    """Render the Ghost-Vision emblem to a transparent pixmap."""
    s = int(size or 150)
    pm = QtGui.QPixmap(s, s)
    pm.fill(QtCore.Qt.transparent)
    q = QtGui.QPainter(pm)
    q.setRenderHint(QtGui.QPainter.Antialiasing, True)
    draw_fallback_emblem(q, s / 2.0, s / 2.0, s * 0.46, TEAL, 255)
    q.end()
    return pm


def draw_fallback_emblem(q, cx, cy, r, color=TEAL, alpha=255):
    """Ghost-Vision emblem: octagon frame, a sensing node, radio waves either side."""
    col = qc(color, alpha)
    poly = QtGui.QPolygonF()
    for k in range(8):
        a = math.radians(45 * k - 22.5)
        poly.append(QtCore.QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    q.setPen(QtGui.QPen(col, max(1.5, r * 0.045)))
    q.setBrush(qc(VOID, min(alpha, 210)))
    q.drawPolygon(poly)
    q.setPen(QtGui.QPen(qc(color, int(alpha * 0.45)), max(1.0, r * 0.02)))
    q.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx + (p.x() - cx) * 0.80,
                                                  cy + (p.y() - cy) * 0.80)
                                   for p in poly]))
    q.setBrush(QtCore.Qt.NoBrush)
    q.setPen(QtGui.QPen(col, max(1.5, r * 0.07), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    for k, rr in enumerate((r * 0.30, r * 0.50)):
        rect = QtCore.QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
        q.drawArc(rect, -40 * 16, 80 * 16)      # waves to the right
        q.drawArc(rect, 140 * 16, 80 * 16)      # waves to the left
    q.setPen(QtCore.Qt.NoPen)
    q.setBrush(col)
    q.drawEllipse(QtCore.QPointF(cx, cy), r * 0.11, r * 0.11)


class BootSplash(QtWidgets.QWidget):
    finished = QtCore.pyqtSignal()

    LINE_DT = 0.26

    def __init__(self, steps, sound=None, parent=None):
        super().__init__(parent, QtCore.Qt.FramelessWindowHint |
                         QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Dialog)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("GHOST-VISION")
        self.steps = steps                    # [(label, callable|None), ...]
        self.results = []                     # ["OK"|"FAIL: ..."]
        self.sound = sound
        self.logo = logo_pixmap(px(150))
        self.t0 = time.time()
        self.done_at = None
        self.fade = 1.0
        self.closing = False
        self._ran = 0

        self.resize(px(760), px(470))
        self.setCursor(QtCore.Qt.BlankCursor)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        if self.sound:
            self.sound.play("boot_rise")

    # ---- timing ----
    @property
    def t(self):
        return time.time() - self.t0

    def line_start(self, i):
        return 2.05 + i * self.LINE_DT

    def total(self):
        return self.line_start(len(self.steps)) + 0.55

    def skip(self):
        while self._ran < len(self.steps):
            self._run_step(self._ran)
            self._ran += 1
        self._begin_close()

    def _run_step(self, i):
        label, fn = self.steps[i]
        if fn is None:
            self.results.append("OK")
            return
        try:
            r = fn()
            self.results.append("OK" if r is None or r is True else str(r))
        except Exception as e:
            self.results.append("FAIL: %s" % e)

    def _begin_close(self):
        if self.closing:
            return
        self.closing = True
        self.done_at = time.time()
        if self.sound:
            self.sound.play("boot_done")

    def _tick(self):
        t = self.t
        while self._ran < len(self.steps) and t >= self.line_start(self._ran):
            self._run_step(self._ran)
            if self.sound:
                self.sound.play("boot_step")
            self._ran += 1
        if not self.closing and t >= self.total():
            self._begin_close()
        if self.closing:
            el = time.time() - self.done_at
            self.fade = clamp(1.0 - el / 0.45, 0.0, 1.0)
            if self.fade <= 0.01:
                self.timer.stop()
                self.finished.emit()
                self.close()
                return
        self.update()

    # ---- input ----
    def mousePressEvent(self, _):
        self.skip()

    def keyPressEvent(self, _):
        self.skip()

    # ---- paint ----
    def paintEvent(self, _):
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        q.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        q.setOpacity(self.fade)
        W, H = self.width(), self.height()
        t = self.t

        # backdrop
        g = QtGui.QRadialGradient(QtCore.QPointF(W / 2, H * 0.42), W * 0.8)
        g.setColorAt(0.0, qc("#0b141d"))
        g.setColorAt(1.0, qc(VOID))
        q.fillRect(self.rect(), QtGui.QBrush(g))

        # scanning grid, fading in
        ga = clamp(t / 0.8, 0, 1) * 30
        q.setPen(QtGui.QPen(qc(TEAL, int(ga)), 1))
        step = px(26)
        x = 0
        while x < W:
            q.drawLine(x, 0, x, H)
            x += step
        y = 0
        while y < H:
            q.drawLine(0, y, W, y)
            y += step

        # travelling scan bar
        if t < self.total():
            sy = (t * 0.42 % 1.0) * H
            lg = QtGui.QLinearGradient(0, sy - px(40), 0, sy + px(40))
            lg.setColorAt(0.0, qc(TEAL, 0))
            lg.setColorAt(0.5, qc(TEAL, 26))
            lg.setColorAt(1.0, qc(TEAL, 0))
            q.fillRect(QtCore.QRectF(0, sy - px(40), W, px(80)), QtGui.QBrush(lg))

        # ---- logo ----
        lt = clamp((t - 0.10) / 0.75, 0, 1)
        ease = 1 - (1 - lt) ** 3
        cx, cy = W / 2, H * 0.30
        if ease > 0.01:
            r = px(74) * (0.72 + 0.28 * ease)
            # halo
            hg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r * 1.9)
            hg.setColorAt(0.0, qc(TEAL, int(40 * ease)))
            hg.setColorAt(1.0, qc(TEAL, 0))
            q.setPen(QtCore.Qt.NoPen)
            q.setBrush(QtGui.QBrush(hg))
            q.drawEllipse(QtCore.QPointF(cx, cy), r * 1.9, r * 1.9)
            # rotating reticle rings
            q.setBrush(QtCore.Qt.NoBrush)
            for k, (rr, sp, al) in enumerate(((r * 1.30, 34, 70), (r * 1.52, -22, 42))):
                q.setPen(QtGui.QPen(qc(TEAL, int(al * ease)), max(1.0, px(1.2))))
                span = 70 * 16
                for seg in range(3):
                    start = int((t * sp + seg * 120) * 16)
                    q.drawArc(QtCore.QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), start, span)
            if self.logo is not None:
                q.setOpacity(self.fade * ease)
                sz = int(px(150) * (0.72 + 0.28 * ease))
                pm = self.logo.scaled(sz, sz, QtCore.Qt.KeepAspectRatio,
                                      QtCore.Qt.SmoothTransformation)
                q.drawPixmap(int(cx - pm.width() / 2), int(cy - pm.height() / 2), pm)
                q.setOpacity(self.fade)
            else:
                draw_fallback_emblem(q, cx, cy, r * 0.75, TEAL, int(255 * ease))

        # ---- PRODUCT NAME ----
        name = "G H O S T - V I S I O N"
        nt = clamp((t - 0.85) / 0.55, 0, 1)
        shown = int(len(name) * nt)
        q.setFont(QtGui.QFont("Consolas", fs(24), QtGui.QFont.Bold))
        q.setPen(QtGui.QPen(qc(TEXT, int(255 * clamp(nt * 1.6, 0, 1)))))
        q.drawText(QtCore.QRectF(0, H * 0.46, W, px(38)),
                   QtCore.Qt.AlignCenter, name[:shown])

        # ---- SUBTITLE ----
        title = "WiFi CSI MOTION & PRESENCE SENSING"
        tt = clamp((t - 1.35) / 0.60, 0, 1)
        st = int(len(title) * tt)
        q.setFont(QtGui.QFont("Consolas", fs(15), QtGui.QFont.Bold))
        q.setPen(QtGui.QPen(qc(TEAL)))
        txt = title[:st]
        if tt < 1.0 and int(t * 6) % 2 == 0:
            txt += "_"
        q.drawText(QtCore.QRectF(0, H * 0.555, W, px(26)), QtCore.Qt.AlignCenter, txt)

        if tt >= 1.0:
            q.setFont(QtGui.QFont("Consolas", fs(10)))
            q.setPen(QtGui.QPen(qc(DIM)))
            q.drawText(QtCore.QRectF(0, H * 0.605, W, px(20)), QtCore.Qt.AlignCenter,
                       "v%s   ·   M. KALANTARY" % APP_VER)

        # ---- boot lines ----
        y0 = H * 0.68
        q.setFont(QtGui.QFont("Consolas", fs(10)))
        x0 = W * 0.20
        for i in range(min(self._ran, len(self.steps))):
            label = self.steps[i][0]
            res = self.results[i] if i < len(self.results) else ""
            ok = res.startswith("OK")
            age = t - self.line_start(i)
            a = int(255 * clamp(age / 0.18, 0, 1))
            q.setPen(QtGui.QPen(qc(MUTED, a)))
            q.drawText(QtCore.QPointF(x0, y0 + i * px(15)), label)
            q.setPen(QtGui.QPen(qc(GREEN if ok else AMBER, a)))
            q.drawText(QtCore.QRectF(x0, y0 + i * px(15) - px(11), W * 0.60, px(14)),
                       QtCore.Qt.AlignRight, ("[ OK ]" if ok else "[ " + res[:22] + " ]"))

        # ---- progress ----
        prog = clamp(t / max(0.1, self.total()), 0, 1)
        bar = QtCore.QRectF(W * 0.20, H - px(42), W * 0.60, px(5))
        q.setPen(QtCore.Qt.NoPen)
        q.setBrush(qc(PANEL_2))
        q.drawRoundedRect(bar, px(3), px(3))
        fillr = QtCore.QRectF(bar.left(), bar.top(), bar.width() * prog, bar.height())
        lg = QtGui.QLinearGradient(bar.left(), 0, bar.right(), 0)
        lg.setColorAt(0.0, qc(TEAL_DK))
        lg.setColorAt(1.0, qc(TEAL))
        q.setBrush(QtGui.QBrush(lg))
        q.drawRoundedRect(fillr, px(3), px(3))
        q.setPen(QtGui.QPen(qc(DIM)))
        q.setFont(QtGui.QFont("Consolas", fs(9)))
        q.drawText(QtCore.QRectF(0, H - px(28), W, px(16)), QtCore.Qt.AlignCenter,
                   "INITIALISING  %d%%    ·    click or press any key to skip" % int(prog * 100))

        # frame
        q.setPen(QtGui.QPen(qc(BORDER_HI), max(1, px(1))))
        q.setBrush(QtCore.Qt.NoBrush)
        q.drawRect(self.rect().adjusted(0, 0, -1, -1))
        s = px(26)
        q.setPen(QtGui.QPen(qc(TEAL, 170), px(2)))
        for ax, ay, dx, dy in ((px(6), px(6), 1, 1), (W - px(6), px(6), -1, 1),
                               (px(6), H - px(6), 1, -1), (W - px(6), H - px(6), -1, -1)):
            q.drawLine(int(ax), int(ay), int(ax + dx * s), int(ay))
            q.drawLine(int(ax), int(ay), int(ax), int(ay + dy * s))
        q.end()



# ==========================================================================
# MAIN WINDOW
# ==========================================================================
class OpsConsole(QtWidgets.QWidget):
    def __init__(self, sim=False):
        super().__init__()
        self.cfg = load_settings()
        self.room = Room()
        if not self.room.load():
            self.room.save()

        self.engines = {i: Engine(self.cfg, i) for i in range(1, NODE_COUNT + 1)}

        # Sixteen path engines. The four router entries ARE the node engines --
        # the same objects, not copies -- so every existing display, solver
        # call and calibration path keeps working untouched, and the twelve
        # sibling engines are simply added alongside.
        self.path_engines = {}
        for rx in range(1, NODE_COUNT + 1):
            self.path_engines[(0, rx)] = self.engines[rx]
            for tx in range(1, NODE_COUNT + 1):
                if tx != rx:
                    self.path_engines[(tx, rx)] = Engine(self.cfg, rx, tx)
        self.active_paths = []
        self.parsers = {i: CSIParser() for i in range(1, NODE_COUNT + 1)}
        self.workers = {}
        self.udp_worker = None
        self.sim_worker = None
        self.cam_worker = None
        self.sim_mode = bool(sim)
        self.sim_truth = None

        self.solver = ZoneSolver()
        self.tracker = TargetTracker(self.cfg)
        self.sound = SoundBank(self.cfg)

        self.events = []
        self.session_t0 = time.time()
        self.recording = False
        self.rec_file = None
        self.rec_writer = None
        self.rec_path = ""
        self.last_system = "OFFLINE"
        self.last_track_active = False
        self.cal_stage = None
        self.cal_deadline = 0.0
        self._last_frame_t = time.time()
        self._alerted_at = 0.0

        self._build()
        self._apply_cfg_to_widgets()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(50)                 # 20 Hz
        self.paint_timer = QtCore.QTimer(self)
        self.paint_timer.timeout.connect(self._animate)
        self.paint_timer.start(33)           # ~30 fps for the tactical display

        self.log("Console ready — v%s." % APP_VER, TEAL)
        self.log("Scope: motion and presence via WiFi channel disturbance. "
                 "No imagery, no biometrics, no device detection.", DIM)
        if self.sim_mode:
            QtCore.QTimer.singleShot(400, self.start_sim)

    # ======================================================================
    # BUILD
    # ======================================================================
    def _build(self):
        self.setWindowTitle("GHOST-VISION  v%s" % APP_VER)
        self.setStyleSheet("QWidget{background:%s;color:%s;font-family:%s;}"
                           "QToolTip{background:%s;color:%s;border:1px solid %s;padding:%dpx;"
                           "font-size:%dpx;}"
                           % (BG, TEXT, SANS, PANEL, TEXT, BORDER_HI, px(6), fs(12)))
        self.resize(px(1560), px(950))
        self.setMinimumSize(px(1180), px(720))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(px(12), px(10), px(12), px(10))
        root.setSpacing(px(9))

        self.header = self._header()
        root.addWidget(self.header)

        self.flagrow = self._flagrow()
        root.addWidget(self.flagrow)

        self.stack = QtWidgets.QStackedWidget()
        self.page_ops = self._page_ops()
        self.page_planner = self._page_planner()
        self.page_setup = self._page_setup()
        for p in (self.page_ops, self.page_planner, self.page_setup):
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)

        self.statusbar = self._statusbar()
        root.addWidget(self.statusbar)

        self.switch_tab(0, silent=True)
        self._install_shortcuts()

    # ---------- header ----------
    def _header(self):
        f = QtWidgets.QFrame()
        f.setStyleSheet(card_css(PANEL, 10))
        f.setFixedHeight(px(78))
        h = QtWidgets.QHBoxLayout(f)
        h.setContentsMargins(px(14), px(8), px(14), px(8))
        h.setSpacing(px(14))

        lg = QtWidgets.QLabel()
        pm = logo_pixmap(px(50))
        if pm is not None:
            lg.setPixmap(pm)
        else:
            lg.setText("GV")
            lg.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;border:none;"
                             % (TEAL, fs(24)))
        lg.setStyleSheet(lg.styleSheet() + "background:none;border:none;")
        h.addWidget(lg)

        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(0)
        t1 = QtWidgets.QLabel("GHOST-VISION")
        t1.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                         "border:none;background:none;font-family:%s;"
                         % (TEXT, fs(24), max(2, px(4)), COND))
        self.sub = QtWidgets.QLabel("")
        self.sub.setStyleSheet("color:%s;font-size:%dpx;letter-spacing:%dpx;border:none;"
                               "background:none;font-family:%s;"
                               % (MUTED, fs(11), max(1, px(1)), MONO))
        titles.addWidget(t1)
        titles.addWidget(self.sub)
        h.addLayout(titles)
        h.addStretch()

        # tab buttons
        self.tabs = []
        tabbox = QtWidgets.QHBoxLayout()
        tabbox.setSpacing(px(4))
        for i, (name, tip) in enumerate((
                ("OPERATIONS", "Live display, node array, event log   (F1)"),
                ("ROOM PLANNER", "Measure and lay out the room   (F2)"),
                ("SETUP / CONTROL", "Ports, calibration, tuning, sounds, camera   (F3)"))):
            b = QtWidgets.QPushButton(name)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setMinimumHeight(px(38))
            b.setMinimumWidth(px(148))
            b.clicked.connect(lambda _, k=i: self.switch_tab(k))
            self.tabs.append(b)
            tabbox.addWidget(b)
        h.addLayout(tabbox)
        h.addStretch()

        self.clock = QtWidgets.QLabel("")
        self.clock.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.clock.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                                 "font-family:%s;" % (MUTED, fs(12), MONO))
        h.addWidget(self.clock)

        self.badge = QtWidgets.QLabel("OFFLINE")
        self.badge.setAlignment(QtCore.Qt.AlignCenter)
        self.badge.setFixedWidth(px(230))
        self.badge.setFixedHeight(px(50))
        self._set_badge("OFFLINE")
        h.addWidget(self.badge)

        self.b_arm = button("ARM SYSTEM", PANEL_3, TEXT, bold=True, height=50, big=True,
                            tip="Arming makes detections count: alert sounds, log entries, "
                                "and the ARMED marker on screen.   (Ctrl+A)")
        self.b_arm.setFixedWidth(px(160))
        self.b_arm.clicked.connect(self.toggle_arm)
        h.addWidget(self.b_arm)
        return f

    def _flagrow(self):
        f = QtWidgets.QFrame()
        f.setStyleSheet("background:none;border:none;")
        f.setFixedHeight(px(26))
        self.flag_layout = QtWidgets.QHBoxLayout(f)
        self.flag_layout.setContentsMargins(px(2), 0, px(2), 0)
        self.flag_layout.setSpacing(px(6))
        self.flag_layout.addStretch()
        return f

    def _statusbar(self):
        f = QtWidgets.QFrame()
        f.setStyleSheet(card_css(PANEL_2, 8))
        f.setFixedHeight(px(30))
        h = QtWidgets.QHBoxLayout(f)
        h.setContentsMargins(px(12), px(3), px(12), px(3))
        h.setSpacing(px(16))
        self.st_left = QtWidgets.QLabel("")
        self.st_left.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;border:none;"
                                   "background:none;" % (MUTED, fs(11), MONO))
        self.st_right = QtWidgets.QLabel("")
        self.st_right.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;border:none;"
                                    "background:none;" % (DIM, fs(11), MONO))
        h.addWidget(self.st_left)
        h.addStretch()
        h.addWidget(self.st_right)
        return f

    def _install_shortcuts(self):
        def sc(seq, fn):
            s = QtWidgets.QShortcut(QtGui.QKeySequence(seq), self)
            s.activated.connect(fn)
        sc("F1", lambda: self.switch_tab(0))
        sc("F2", lambda: self.switch_tab(1))
        sc("F3", lambda: self.switch_tab(2))
        sc("Ctrl+A", self.toggle_arm)
        sc("Ctrl+K", self.start_cal_sequence)
        sc("Ctrl+R", self.toggle_record)
        sc("Ctrl+S", self.save_cfg)
        sc("Ctrl+E", self.export_report)

    def switch_tab(self, k, silent=False):
        self.stack.setCurrentIndex(k)
        for i, b in enumerate(self.tabs):
            on = (i == k)
            b.setStyleSheet(
                "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
                "font-size:%dpx;font-weight:800;letter-spacing:%dpx;font-family:%s;}"
                "QPushButton:hover{border:1px solid %s;color:%s;}"
                % (TEAL_DK if on else PANEL_2, TEAL if on else MUTED,
                   TEAL if on else BORDER, px(7), fs(12), max(1, px(2)), SANS,
                   BORDER_HOT, TEXT))
        if not silent:
            self.sound.ui("ui_tab")

    def _update_subtitle(self):
        site = self.cfg.get("site_name") or "UNASSIGNED SITE"
        op = self.cfg.get("operator") or "—"
        ref = self.cfg.get("case_ref") or "—"
        self.sub.setText("%s   ·   OPERATOR %s   ·   REF %s   ·   %.2f × %.2f m"
                         % (site.upper(), op.upper(), ref.upper(),
                            self.room.width, self.room.height))

    # ---------- page 1: operations ----------
    def _page_ops(self):
        page = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(px(10))

        # ---- left: tactical + metric cards ----
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(px(9))
        self.tactical = TacticalView(self.room, self.cfg)
        wrap = QtWidgets.QFrame()
        wrap.setStyleSheet(card_css())
        wl = QtWidgets.QVBoxLayout(wrap)
        wl.setContentsMargins(px(9), px(7), px(9), px(9))
        wl.setSpacing(px(5))
        caprow = QtWidgets.QHBoxLayout()
        cap = QtWidgets.QLabel("TACTICAL DISPLAY")
        cap.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                          "border:none;background:none;" % (TEAL, fs(12), max(1, px(2))))
        cap2 = QtWidgets.QLabel("live sensing paths · tracked coarse zone · uncertainty ring")
        cap2.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                           % (DIM, fs(11)))
        caprow.addWidget(cap)
        caprow.addWidget(cap2)
        caprow.addStretch()
        self.view_toggles = {}
        for key, label in (("show_heat", "HEAT"), ("show_paths", "PATHS"),
                           ("show_trail", "TRAIL"), ("show_dossier", "DOSSIER"),
                           ("show_scan", "SWEEP"), ("show_grid", "GRID")):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setChecked(bool(self.cfg.get(key, True)))
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setFixedHeight(px(24))
            b.clicked.connect(lambda _, k=key: self._toggle_view(k))
            self.view_toggles[key] = b
            self._style_toggle(b)
            caprow.addWidget(b)
        wl.addLayout(caprow)
        wl.addWidget(self.tactical, 1)
        left.addWidget(wrap, 1)

        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(px(8))
        self.c_state = Card("SYSTEM STATE", "OFFLINE", DIM, big=18)
        self.c_zone = Card("TRACKED ZONE", "—", MUTED, big=18)
        self.c_move = Card("MOVEMENT", "—", MUTED, big=18)
        self.c_active = Card("NODES ACTIVE", "0/4", MUTED, big=22)
        self.c_events = Card("EVENTS", "0", MUTED, big=22)
        self.c_up = Card("SESSION", "0s", MUTED, big=20)
        for c in (self.c_state, self.c_zone, self.c_move, self.c_active,
                  self.c_events, self.c_up):
            cards.addWidget(c)
        left.addLayout(cards)
        h.addLayout(left, 1)

        # ---- right: optical + node array + assessment + log ----
        rw = QtWidgets.QWidget()
        rw.setFixedWidth(px(452))
        right = QtWidgets.QVBoxLayout(rw)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(px(7))

        right.addWidget(section("OPTICAL FEED", TEAL))
        camwrap = QtWidgets.QFrame()
        camwrap.setStyleSheet(card_css())
        cwl = QtWidgets.QVBoxLayout(camwrap)
        cwl.setContentsMargins(px(7), px(7), px(7), px(7))
        cwl.setSpacing(px(6))
        self.campanel = CameraPanel()
        self.campanel.setFixedHeight(px(222))
        cwl.addWidget(self.campanel)
        crow = QtWidgets.QHBoxLayout()
        crow.setSpacing(px(6))
        self.b_cam = button("START CAMERA", PANEL_3, TEAL, height=28,
                            tip="Plain webcam. Nothing from this feed touches the CSI "
                                "detection chain — it is there so you can see what the "
                                "sensor reacted to.")
        self.b_cam.clicked.connect(self.toggle_camera)
        self.b_cam_snap = button("SNAPSHOT", PANEL_3, MUTED, height=28)
        self.b_cam_snap.clicked.connect(self.camera_snapshot)
        crow.addWidget(self.b_cam, 1)
        crow.addWidget(self.b_cam_snap)
        cwl.addLayout(crow)
        right.addWidget(camwrap)

        na_row = QtWidgets.QHBoxLayout()
        na_row.setSpacing(px(6))
        na_row.addWidget(section("NODE ARRAY"), 1)
        self.b_strip_mode = button("STACKED", PANEL_3, MUTED, height=22,
                                   tip="Switch between a 2x2 grid (all four visible "
                                       "at once) and a single stacked column.")
        self.b_strip_mode.clicked.connect(self.toggle_strip_layout)
        na_row.addWidget(self.b_strip_mode)
        right.addLayout(na_row)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:none;border:none;}"
            "QScrollBar:vertical{background:%s;width:%dpx;border-radius:%dpx;}"
            "QScrollBar::handle:vertical{background:%s;border-radius:%dpx;min-height:%dpx;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
            % (PANEL_2, px(9), px(4), BORDER_HI, px(4), px(30)))
        inner = QtWidgets.QWidget()
        self.strip_grid = QtWidgets.QGridLayout(inner)
        self.strip_grid.setContentsMargins(0, 0, px(6), 0)
        self.strip_grid.setHorizontalSpacing(px(7))
        self.strip_grid.setVerticalSpacing(px(7))
        self.strips = {}
        for i in range(1, NODE_COUNT + 1):
            s = NodeStrip(self.engines[i])
            self.strips[i] = s
        self._layout_strips()
        scroll.setWidget(inner)
        right.addWidget(scroll, 1)

        right.addWidget(section("SYSTEM ASSESSMENT"))
        af = QtWidgets.QFrame()
        af.setStyleSheet(card_css())
        av = QtWidgets.QVBoxLayout(af)
        av.setContentsMargins(px(12), px(10), px(12), px(11))
        self.assess = QtWidgets.QLabel("Waiting for nodes.")
        self.assess.setWordWrap(True)
        self.assess.setAlignment(QtCore.Qt.AlignTop)
        self.assess.setMinimumHeight(px(96))
        self.assess.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                                  % (TEXT_2, fs(12)))
        av.addWidget(self.assess)
        right.addWidget(af)

        right.addWidget(section("EVENT LOG"))
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(px(120))
        self.log_box.setStyleSheet(
            "QPlainTextEdit{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
            "font-family:%s;font-size:%dpx;padding:%dpx;}"
            % (PANEL_2, MUTED, BORDER, px(8), MONO, fs(11), px(8)))
        right.addWidget(self.log_box)
        h.addWidget(rw)
        return page

    def _style_toggle(self, b):
        b.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx %dpx;font-size:%dpx;font-weight:700;letter-spacing:%dpx;}"
            "QPushButton:checked{background:%s;color:%s;border:1px solid %s;}"
            % (PANEL_2, DIM, BORDER, px(4), px(2), px(7), fs(10), max(1, px(1)),
               TEAL_DK, TEAL, TEAL))

    def _toggle_view(self, key):
        self.cfg[key] = bool(self.view_toggles[key].isChecked())
        self.sound.ui("ui_toggle")
        self.tactical.update()


    # ---------- page 2: room planner ----------
    def _page_planner(self):
        page = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(px(10))

        # ---- palette ----
        palw = QtWidgets.QWidget()
        palw.setFixedWidth(px(212))
        palv = QtWidgets.QVBoxLayout(palw)
        palv.setContentsMargins(0, 0, 0, 0)
        palv.setSpacing(px(5))
        palv.addWidget(section("PLACE AN ELEMENT", TEAL))
        pscroll = QtWidgets.QScrollArea()
        pscroll.setWidgetResizable(True)
        pscroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        pscroll.setStyleSheet("QScrollArea{background:none;border:none;}"
                              "QScrollBar:vertical{background:%s;width:%dpx;}"
                              "QScrollBar::handle:vertical{background:%s;border-radius:%dpx;}"
                              "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
                              % (PANEL_2, px(8), BORDER_HI, px(4)))
        pin = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(pin)
        pv.setContentsMargins(0, 0, px(6), 0)
        pv.setSpacing(px(3))
        for group, kinds in PALETTE_GROUPS:
            pv.addWidget(section(group))
            for kind in kinds:
                spec = OBJ_TYPES[kind]
                b = button("＋  " + spec[0], PANEL_2, TEXT_2, height=28,
                           accent=spec[8], tip="%s — default %.2f × %.2f m%s"
                           % (spec[0], spec[1], spec[2],
                              "  ·  flagged metal" if spec[5] else ""))
                b.clicked.connect(lambda _, k=kind: self.add_object(k))
                pv.addWidget(b)
        pv.addStretch()
        pscroll.setWidget(pin)
        palv.addWidget(pscroll, 1)
        h.addWidget(palw)

        # ---- canvas ----
        cwrap = QtWidgets.QFrame()
        cwrap.setStyleSheet(card_css())
        cv = QtWidgets.QVBoxLayout(cwrap)
        cv.setContentsMargins(px(9), px(7), px(9), px(9))
        cv.setSpacing(px(5))
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("ROOM PLANNER")
        lab.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                          "border:none;background:none;" % (TEAL, fs(12), max(1, px(2))))
        lab2 = QtWidgets.QLabel("map accuracy IS measurement accuracy — measure with a tape")
        lab2.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                           % (AMBER, fs(11)))
        row.addWidget(lab)
        row.addWidget(lab2)
        row.addStretch()
        b_undo = button("↶ UNDO", PANEL_2, MUTED, height=24)
        b_undo.clicked.connect(lambda: self.planner.undo())
        b_redo = button("↷ REDO", PANEL_2, MUTED, height=24)
        b_redo.clicked.connect(lambda: self.planner.redo())
        row.addWidget(b_undo)
        row.addWidget(b_redo)
        cv.addLayout(row)
        self.planner = PlannerView(self.room)
        self.planner.selection_changed.connect(self.on_select)
        self.planner.room_changed.connect(self.on_room_changed)
        cv.addWidget(self.planner, 1)
        h.addWidget(cwrap, 1)

        # ---- right column ----
        rw = QtWidgets.QWidget()
        rw.setFixedWidth(px(330))
        rv = QtWidgets.QVBoxLayout(rw)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(px(6))

        rv.addWidget(section("ROOM DIMENSIONS", TEAL))
        gf = QtWidgets.QFrame()
        gf.setStyleSheet(card_css())
        gl = QtWidgets.QGridLayout(gf)
        gl.setContentsMargins(px(11), px(9), px(11), px(10))
        gl.setSpacing(px(6))

        def lab_(t):
            l = QtWidgets.QLabel(t)
            l.setStyleSheet("color:%s;font-size:%dpx;border:none;background:none;"
                            % (MUTED, fs(12)))
            return l

        self.sp_w = spin(1.0, 40.0, self.room.width)
        self.sp_h = spin(1.0, 40.0, self.room.height)
        self.sp_w.valueChanged.connect(self.on_room_size)
        self.sp_h.valueChanged.connect(self.on_room_size)
        gl.addWidget(lab_("Width  (X →)"), 0, 0)
        gl.addWidget(self.sp_w, 0, 1)
        gl.addWidget(lab_("Depth  (Y ↓)"), 1, 0)
        gl.addWidget(self.sp_h, 1, 1)
        self.sp_snap = spin(0.0, 1.0, 0.10, 0.05, 2, " m")
        self.sp_snap.valueChanged.connect(
            lambda v: setattr(self.planner, "snap", float(v)))
        gl.addWidget(lab_("Grid snap"), 2, 0)
        gl.addWidget(self.sp_snap, 2, 1)
        rv.addWidget(gf)

        rv.addWidget(section("SELECTED ELEMENT"))
        pf = QtWidgets.QFrame()
        pf.setStyleSheet(card_css())
        pl = QtWidgets.QGridLayout(pf)
        pl.setContentsMargins(px(11), px(9), px(11), px(10))
        pl.setSpacing(px(6))
        self.sel_name = QtWidgets.QLabel("nothing selected")
        self.sel_name.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;border:none;"
                                    "background:none;letter-spacing:%dpx;"
                                    % (TEXT, fs(13), max(1, px(1))))
        pl.addWidget(self.sel_name, 0, 0, 1, 2)
        self.sp_x = spin(-5, 40, 0)
        self.sp_y = spin(-5, 40, 0)
        self.sp_ow = spin(0.05, 20, 1)
        self.sp_oh = spin(0.05, 20, 1)
        self.sp_rot = spin(-180, 180, 0, 15, 0, "°")
        for w in (self.sp_x, self.sp_y, self.sp_ow, self.sp_oh, self.sp_rot):
            w.valueChanged.connect(self.on_prop)
        pl.addWidget(lab_("X"), 1, 0)
        pl.addWidget(self.sp_x, 1, 1)
        pl.addWidget(lab_("Y"), 2, 0)
        pl.addWidget(self.sp_y, 2, 1)
        pl.addWidget(lab_("Width"), 3, 0)
        pl.addWidget(self.sp_ow, 3, 1)
        pl.addWidget(lab_("Depth"), 4, 0)
        pl.addWidget(self.sp_oh, 4, 1)
        pl.addWidget(lab_("Rotation"), 5, 0)
        pl.addWidget(self.sp_rot, 5, 1)
        self.e_label = QtWidgets.QLineEdit()
        self.e_label.setPlaceholderText("label on the map")
        self.e_label.setStyleSheet(edit_css())
        self.e_label.textChanged.connect(self.on_prop)
        pl.addWidget(self.e_label, 6, 0, 1, 2)
        self.chk_metal = QtWidgets.QCheckBox("Reflective / metal — warn if a node is close")
        self.chk_metal.setStyleSheet(check_css())
        self.chk_metal.toggled.connect(self.on_prop)
        pl.addWidget(self.chk_metal, 7, 0, 1, 2)
        brow = QtWidgets.QHBoxLayout()
        b_dup = button("DUPLICATE", PANEL_2, MUTED, height=26)
        b_dup.clicked.connect(lambda: self.planner.duplicate_selected())
        b_del = button("DELETE", RED_DK, RED, height=26)
        b_del.clicked.connect(lambda: self.planner.delete_selected())
        brow.addWidget(b_dup)
        brow.addWidget(b_del)
        pl.addLayout(brow, 8, 0, 1, 2)
        rv.addWidget(pf)

        rv.addWidget(section("PLACEMENT CHECK", AMBER))
        self.geo_chip = Chip("GEOMETRY —", DIM, size=11)
        rv.addWidget(self.geo_chip)
        self.warn_box = QtWidgets.QLabel("")
        self.warn_box.setWordWrap(True)
        self.warn_box.setAlignment(QtCore.Qt.AlignTop)
        self.warn_box.setStyleSheet(
            "color:%s;font-size:%dpx;background:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx;" % (AMBER, fs(11), PANEL_2, BORDER, px(8), px(9)))
        rv.addWidget(self.warn_box, 1)

        brow2 = QtWidgets.QHBoxLayout()
        b_save = button("SAVE LAYOUT", TEAL_DK, TEAL, bold=True, height=32)
        b_save.clicked.connect(self.save_room)
        b_rel = button("RELOAD", PANEL_2, MUTED, height=32)
        b_rel.clicked.connect(self.reload_room)
        brow2.addWidget(b_save, 1)
        brow2.addWidget(b_rel)
        rv.addLayout(brow2)
        b_reset = button("Reset to default four-corner layout", PANEL_2, DIM, height=26)
        b_reset.clicked.connect(self.reset_room)
        rv.addWidget(b_reset)
        h.addWidget(rw)
        return page

    # ---------- page 3: setup / control ----------
    def _col(self, width):
        w = QtWidgets.QWidget()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setFixedWidth(width)
        scroll.setStyleSheet("QScrollArea{background:none;border:none;}"
                             "QScrollBar:vertical{background:%s;width:%dpx;}"
                             "QScrollBar::handle:vertical{background:%s;border-radius:%dpx;}"
                             "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
                             % (PANEL_2, px(8), BORDER_HI, px(4)))
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, px(8), 0)
        v.setSpacing(px(6))
        scroll.setWidget(w)
        return scroll, v

    def _page_setup(self):
        page = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(px(10))

        # ================= column 1: links + calibration =================
        s1, c1 = self._col(px(360))
        c1.addWidget(section("NODE CONNECTIONS", TEAL))

        self.link_combo = QtWidgets.QComboBox()
        self.link_combo.setStyleSheet(combo_css())
        self.link_combo.addItem("WIRELESS  —  nodes stream over WiFi (UDP)", "udp")
        self.link_combo.addItem("USB SERIAL  —  one COM port per node", "serial")
        _lm = self.link_combo.findData(self.cfg.get("link_mode", "udp"))
        self.link_combo.setCurrentIndex(_lm if _lm >= 0 else 0)
        self.link_combo.currentIndexChanged.connect(self._on_link_mode)
        c1.addWidget(self.link_combo)

        self.port_combos = {}
        for i in range(1, NODE_COUNT + 1):
            f = QtWidgets.QFrame()
            f.setStyleSheet(card_css())
            fv = QtWidgets.QVBoxLayout(f)
            fv.setContentsMargins(px(10), px(8), px(10), px(9))
            fv.setSpacing(px(5))
            top = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel("NODE %d" % i)
            lab.setStyleSheet("color:%s;font-size:%dpx;font-weight:800;letter-spacing:%dpx;"
                              "border:none;background:none;" % (TEXT, fs(13), max(1, px(2))))
            top.addWidget(lab)
            top.addStretch()
            fv.addLayout(top)
            cb = QtWidgets.QComboBox()
            cb.setStyleSheet(combo_css())
            self.port_combos[i] = cb
            fv.addWidget(cb)
            c1.addWidget(f)

        row = QtWidgets.QHBoxLayout()
        b_ref = button("Refresh ports", PANEL_2, MUTED, height=30)
        b_ref.clicked.connect(self.refresh_ports)
        self.b_refresh_ports = b_ref
        self.b_conn = button("CONNECT ALL", TEAL_DK, TEAL, bold=True, height=40, big=True)
        self.b_conn.clicked.connect(self.toggle_connect)
        row.addWidget(b_ref)
        row.addWidget(self.b_conn, 1)
        c1.addLayout(row)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.setStyleSheet(combo_css())
        for b in (115200, 230400, 460800, 921600, 1500000):
            self.baud_combo.addItem("%d baud" % b, b)
        self.baud_combo.setCurrentIndex(max(0, self.baud_combo.findData(int(self.cfg["baud"]))))
        self.baud_combo.currentIndexChanged.connect(
            lambda: self.cfg.update(baud=int(self.baud_combo.currentData())))
        c1.addWidget(self.baud_combo)

        self.chk_sim = QtWidgets.QCheckBox("Simulation mode — virtual nodes and walker")
        self.chk_sim.setStyleSheet(check_css())
        self.chk_sim.setChecked(self.sim_mode)
        self.chk_sim.toggled.connect(self.toggle_sim)
        c1.addWidget(self.chk_sim)

        c1.addWidget(section("CALIBRATION", PURPLE))
        self.b_cal = button("CALIBRATE ALL NODES", "#1c1233", PURPLE, bold=True,
                            height=42, big=True, accent=PURPLE,
                            tip="Measures each node's own noise floor in an EMPTY room and "
                                "sets its trigger from that. It refuses to save if the room "
                                "wasn't quiet.   (Ctrl+K)")
        self.b_cal.clicked.connect(self.start_cal_sequence)
        c1.addWidget(self.b_cal)
        self.s_delay = Slider("Exit delay before it starts", 0, 120,
                              self.cfg["cal_delay"], 0, " s",
                              tip="Time to walk out of the room. Ticks audibly.")
        self.s_delay.changed.connect(lambda x: self.cfg.update(cal_delay=int(x)))
        c1.addWidget(self.s_delay)
        self.s_cal = Slider("Calibration length", 15, 180, self.cfg["cal_secs"], 0, " s")
        self.s_cal.changed.connect(lambda x: self.cfg.update(cal_secs=int(x)))
        c1.addWidget(self.s_cal)
        self.cal_lab = QtWidgets.QLabel()
        self.cal_lab.setWordWrap(True)
        self.cal_lab.setStyleSheet("color:%s;font-size:%dpx;font-family:%s;" % (MUTED, fs(11), MONO))
        c1.addWidget(self.cal_lab)

        row = QtWidgets.QHBoxLayout()
        self.chk_freeze = QtWidgets.QCheckBox("Freeze baselines")
        self.chk_freeze.setStyleSheet(check_css())
        self.chk_freeze.toggled.connect(self.on_freeze)
        self.chk_adapt = QtWidgets.QCheckBox("Keep learning")
        self.chk_adapt.setStyleSheet(check_css())
        self.chk_adapt.setChecked(bool(self.cfg["adapt"]))
        self.chk_adapt.toggled.connect(lambda s: self.cfg.update(adapt=s))
        row.addWidget(self.chk_freeze)
        row.addWidget(self.chk_adapt)
        c1.addLayout(row)
        c1.addStretch()
        h.addWidget(s1)

        # ================= column 2: sensitivity + solver + tracker =================
        s2, c2 = self._col(px(330))
        c2.addWidget(section("DETECTION SENSITIVITY  (all nodes)", TEAL))
        self.s_thr = Slider("Trigger level", 3, 90, self.cfg["threshold"], 1,
                            tip="Calibration normally sets this per node. Moving it here "
                                "overrides all four.")
        self.s_thr.changed.connect(self.on_threshold)
        c2.addWidget(self.s_thr)
        self.s_exit = Slider("Clear level  (× trigger)", 0.2, 0.95, self.cfg["exit_ratio"], 2)
        self.s_exit.changed.connect(lambda x: self.cfg.update(exit_ratio=x))
        c2.addWidget(self.s_exit)
        self.s_gain = Slider("Gain", 0.2, 20, self.cfg["gain"], 1)
        self.s_gain.changed.connect(lambda x: self.cfg.update(gain=x))
        c2.addWidget(self.s_gain)
        self.s_win = Slider("Window  (packets)", 8, 60, self.cfg["window"])
        self.s_win.changed.connect(self.on_window)
        c2.addWidget(self.s_win)
        self.s_smo = Slider("Smoothing", 1, 25, self.cfg["smooth"])
        self.s_smo.changed.connect(self.on_smooth)
        c2.addWidget(self.s_smo)
        self.s_hits = Slider("Hits to trigger", 1, 15, self.cfg["hits_needed"])
        self.s_hits.changed.connect(lambda x: self.cfg.update(hits_needed=int(x)))
        c2.addWidget(self.s_hits)
        self.s_clr = Slider("Ticks to clear", 2, 60, self.cfg["clear_needed"])
        self.s_clr.changed.connect(lambda x: self.cfg.update(clear_needed=int(x)))
        c2.addWidget(self.s_clr)
        self.s_pres = Slider("Presence window", 5, 90, self.cfg["presence_secs"], 0, " s")
        self.s_pres.changed.connect(lambda x: self.cfg.update(presence_secs=x))
        c2.addWidget(self.s_pres)

        c2.addWidget(section("ZONE SOLVER", TEAL))
        self.s_sigma = Slider("Sensing band width", 0.3, 2.5,
                              self.cfg["fresnel_sigma"], 2, " m")
        self.s_sigma.changed.connect(lambda x: self.cfg.update(fresnel_sigma=x))
        c2.addWidget(self.s_sigma)
        self.s_grid = Slider("Solver grid", 4, 20, self.cfg["grid_cm"], 0, " cm",
                             tip="Finer grid = smoother field, more CPU. 8 cm is a good "
                                 "balance on a laptop.")
        self.s_grid.changed.connect(lambda x: self.cfg.update(grid_cm=int(x)))
        c2.addWidget(self.s_grid)
        c2.addWidget(caption(
            "The band width is how far from each node→router line still counts as 'near'. "
            "It shapes the likelihood field. It is a model parameter, not a measurement."))

        c2.addWidget(section("TARGET TRACKER", CYAN))
        self.chk_track = QtWidgets.QCheckBox("Track the zone estimate over time")
        self.chk_track.setStyleSheet(check_css())
        self.chk_track.setChecked(bool(self.cfg["track_enable"]))
        self.chk_track.toggled.connect(lambda s: self.cfg.update(track_enable=s))
        c2.addWidget(self.chk_track)
        self.s_tq = Slider("Responsiveness", 0.1, 2.0, self.cfg["track_q"], 2,
                           tip="Higher = the marker follows fast movement more closely "
                               "but jitters more.")
        self.s_tq.changed.connect(lambda x: self.cfg.update(track_q=x))
        c2.addWidget(self.s_tq)
        self.s_tr = Slider("Smoothing", 0.1, 2.5, self.cfg["track_r"], 2,
                           tip="Higher = smoother, laggier. Smoothing cannot add accuracy — "
                               "it only makes the same estimate easier to read.")
        self.s_tr.changed.connect(lambda x: self.cfg.update(track_r=x))
        c2.addWidget(self.s_tr)
        self.s_th = Slider("Coast time before dropping", 0.5, 10.0,
                           self.cfg["track_hold"], 1, " s")
        self.s_th.changed.connect(lambda x: self.cfg.update(track_hold=x))
        c2.addWidget(self.s_th)
        c2.addStretch()
        h.addWidget(s2)

        # ================= column 3: identity, audio, display, camera =================
        s3, c3 = self._col(px(340))
        c3.addWidget(section("SITE IDENTITY", TEAL))
        self.e_site = QtWidgets.QLineEdit(self.cfg["site_name"])
        self.e_site.setPlaceholderText("Site / room")
        self.e_site.setStyleSheet(edit_css())
        self.e_site.textChanged.connect(self.on_ident)
        self.e_op = QtWidgets.QLineEdit(self.cfg["operator"])
        self.e_op.setPlaceholderText("Operator name")
        self.e_op.setStyleSheet(edit_css())
        self.e_op.textChanged.connect(self.on_ident)
        self.e_ref = QtWidgets.QLineEdit(self.cfg.get("case_ref", ""))
        self.e_ref.setPlaceholderText("Job / case reference")
        self.e_ref.setStyleSheet(edit_css())
        self.e_ref.textChanged.connect(self.on_ident)
        for w in (self.e_site, self.e_op, self.e_ref):
            c3.addWidget(w)

        c3.addWidget(section("AUDIO", AMBER))
        self.snd_combo = QtWidgets.QComboBox()
        self.snd_combo.setStyleSheet(combo_css())
        self.snd_combo.addItems(ALERT_SOUNDS)
        self.snd_combo.setCurrentIndex(max(0, self.snd_combo.findText(
            str(self.cfg.get("sound_kind", "Off")))))
        self.snd_combo.currentTextChanged.connect(self.on_sound_pick)
        c3.addWidget(caption("Detection alert tone", MUTED, 12))
        c3.addWidget(self.snd_combo)
        self.s_vol = Slider("Volume", 0.0, 1.0, self.cfg.get("volume", 0.7), 2)
        self.s_vol.changed.connect(lambda x: self.cfg.update(volume=x))
        c3.addWidget(self.s_vol)
        row = QtWidgets.QHBoxLayout()
        b_test = button("Test alert", PANEL_2, AMBER, height=28)
        b_test.clicked.connect(lambda: self.sound.play(self.snd_combo.currentText()))
        b_arm_t = button("Test arm", PANEL_2, MUTED, height=28)
        b_arm_t.clicked.connect(lambda: self.sound.play("arm"))
        b_lock_t = button("Test lock", PANEL_2, MUTED, height=28)
        b_lock_t.clicked.connect(lambda: self.sound.play("lock"))
        for b in (b_test, b_arm_t, b_lock_t):
            row.addWidget(b)
        c3.addLayout(row)
        self.chk_clear_snd = QtWidgets.QCheckBox("All-clear tone when the room settles")
        self.chk_clear_snd.setStyleSheet(check_css())
        self.chk_clear_snd.setChecked(bool(self.cfg.get("sound_clear", False)))
        self.chk_clear_snd.toggled.connect(lambda x: self.cfg.update(sound_clear=x))
        c3.addWidget(self.chk_clear_snd)
        self.chk_snd_states = QtWidgets.QCheckBox("System sounds — boot, arm, calibrate, lock")
        self.chk_snd_states.setStyleSheet(check_css())
        self.chk_snd_states.setChecked(bool(self.cfg.get("sound_states", True)))
        self.chk_snd_states.toggled.connect(lambda x: self.cfg.update(sound_states=x))
        c3.addWidget(self.chk_snd_states)
        self.chk_snd_ui = QtWidgets.QCheckBox("Interface clicks")
        self.chk_snd_ui.setStyleSheet(check_css())
        self.chk_snd_ui.setChecked(bool(self.cfg.get("sound_ui", True)))
        self.chk_snd_ui.toggled.connect(lambda x: self.cfg.update(sound_ui=x))
        c3.addWidget(self.chk_snd_ui)

        c3.addWidget(section("DISPLAY", TEAL))
        self.s_scale = Slider("Interface size", 0.8, 1.6, self.cfg.get("ui_scale", 1.0), 2, "×",
                              tip="Applies on the next start. Use this if text is too small "
                                  "on your screen.")
        self.s_scale.changed.connect(lambda x: self.cfg.update(ui_scale=x))
        c3.addWidget(self.s_scale)
        c3.addWidget(caption("Interface size takes effect the next time you start the console."))
        self.chk_boot = QtWidgets.QCheckBox("Play the boot sequence at startup")
        self.chk_boot.setStyleSheet(check_css())
        self.chk_boot.setChecked(bool(self.cfg.get("boot_anim", True)))
        self.chk_boot.toggled.connect(lambda x: self.cfg.update(boot_anim=x))
        c3.addWidget(self.chk_boot)

        c3.addWidget(section("OPTICAL / CAMERA", MUTED))
        self.cam_combo = QtWidgets.QComboBox()
        self.cam_combo.setStyleSheet(combo_css())
        for i in range(5):
            self.cam_combo.addItem("Camera %d" % i, i)
        self.cam_combo.setCurrentIndex(int(clamp(self.cfg.get("cam_index", 0), 0, 4)))
        self.cam_combo.currentIndexChanged.connect(
            lambda: self.cfg.update(cam_index=int(self.cam_combo.currentData())))
        c3.addWidget(self.cam_combo)
        self.chk_mirror = QtWidgets.QCheckBox("Mirror the image")
        self.chk_mirror.setStyleSheet(check_css())
        self.chk_mirror.setChecked(bool(self.cfg.get("cam_mirror", True)))
        self.chk_mirror.toggled.connect(lambda x: self.cfg.update(cam_mirror=x))
        c3.addWidget(self.chk_mirror)
        c3.addWidget(caption(
            "The webcam is an operator aid for checking what the sensor reacted to. "
            "It is NOT part of the sensing chain and it does not feed detection. "
            "If someone else is watching this screen, tell them — the camera-free design is about "
            "the sensor, not this panel."
            + ("" if HAVE_CV2 else
               "\n\nopencv-python is not installed, so the panel cannot open a camera. "
               "Install it with:  pip install opencv-python"),
            AMBER if not HAVE_CV2 else DIM))
        c3.addStretch()
        h.addWidget(s3)

        # ================= column 4: output + diagnostics =================
        s4, c4 = self._col(px(400))
        c4.addWidget(section("OUTPUT", TEAL))
        self.b_rec = button("START RECORDING", PANEL_3, TEXT, height=34,
                            tip="Writes a CSV of every node level, state and the tracked "
                                "zone, once per tick.   (Ctrl+R)")
        self.b_rec.clicked.connect(self.toggle_record)
        c4.addWidget(self.b_rec)
        b_rep = button("Export install / session report", PANEL_2, MUTED, height=32)
        b_rep.clicked.connect(self.export_report)
        c4.addWidget(b_rep)
        row = QtWidgets.QHBoxLayout()
        b_savec = button("Save settings", PANEL_2, MUTED, height=30)
        b_savec.clicked.connect(self.save_cfg)
        b_resets = button("Reset session", PANEL_2, MUTED, height=30)
        b_resets.clicked.connect(self.reset_session)
        row.addWidget(b_savec)
        row.addWidget(b_resets)
        c4.addLayout(row)

        c4.addWidget(section("DIAGNOSTICS"))
        self.diag = QtWidgets.QLabel()
        self.diag.setWordWrap(False)
        self.diag.setTextFormat(QtCore.Qt.RichText)
        self.diag.setAlignment(QtCore.Qt.AlignTop)
        self.diag.setStyleSheet(
            "color:%s;font-size:%dpx;font-family:%s;background:%s;border:1px solid %s;"
            "border-radius:%dpx;padding:%dpx;" % (MUTED, fs(11), MONO, PANEL_2, BORDER,
                                                  px(8), px(10)))
        c4.addWidget(self.diag)

        c4.addWidget(section("SCOPE — WHAT THIS SYSTEM CAN AND CANNOT DO", RED))
        scope = QtWidgets.QLabel(
            "Ghost-Vision detects HUMAN MOTION and PRESENCE by measuring how a body "
            "disturbs the WiFi channel between four sensors and a dedicated access point.\n\n"
            "It does not produce an image, outline or skeleton.\n"
            "It does not measure identity, biometrics, height, build, age or sex.\n"
            "It cannot count people — two bodies read as one blob between them.\n"
            "It cannot tell whether anyone is carrying a phone or any other device.\n"
            "It cannot find planted bugs or hidden transmitters — that is TSCM work with "
            "different equipment.\n"
            "Zone output is a coarse likelihood area of roughly a metre, not a coordinate.\n\n"
            "Everything above is enforced in this software on purpose. If a feature would "
            "need data the sensor cannot produce, it is not here.")
        scope.setWordWrap(True)
        scope.setStyleSheet(
            "color:%s;font-size:%dpx;background:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:%dpx;" % (TEXT_2, fs(11), "#160a0d", RED_DK, px(8), px(11)))
        c4.addWidget(scope)
        c4.addStretch()
        h.addWidget(s4, 1)
        return page


    # ======================================================================
    # SETUP HELPERS
    # ======================================================================
    def _apply_cfg_to_widgets(self):
        self.refresh_ports()
        self._apply_link_mode()
        self.on_room_changed()
        self.sync_controls()
        self._update_arm(initial=True)
        self._update_subtitle()

    def add_object(self, kind):
        o = self.planner.add(kind)
        self.sound.ui()
        if o is None:
            if kind == "node":
                self.log("All %d nodes are already on the map." % NODE_COUNT, AMBER)
            elif kind == "router":
                self.log("There is already a router on the map.", AMBER)

    def log(self, msg, color=None):
        ts = time.strftime("%H:%M:%S")
        self.events.append((ts, msg))
        self.log_box.setPlainText("%s  %s\n%s" % (ts, msg, self.log_box.toPlainText()))

    def sync_controls(self):
        self.cal_lab.setText(self._cal_summary())
        warns = self.room.warnings()
        self.warn_box.setText("\n".join("·  " + w for w in warns) if warns
                              else "·  layout looks sane")
        score, _ = self.room.geometry_score()
        col = GREEN if score >= 65 else (AMBER if score >= 35 else RED)
        self.geo_chip.set("GEOMETRY  %d%%" % score, col)
        self._update_subtitle()

    def _cal_summary(self):
        out = []
        for i in range(1, NODE_COUNT + 1):
            t = self.cfg["node_cal_time"][i - 1]
            thr = self.cfg["node_thresholds"][i - 1]
            out.append("N%d  trig %5.1f   %s" % (i, thr, t or "never calibrated"))
        return "\n".join(out)

    # ======================================================================
    # CONNECTIONS
    # ======================================================================
    def _on_link_mode(self):
        mode = self.link_combo.currentData() or "udp"
        self.cfg["link_mode"] = mode
        if self.workers or self.udp_worker:
            self.stop_all(quiet=True)
        self._apply_link_mode()
        if mode == "udp":
            self.log("Link mode: WIRELESS. Node identity comes from the packet, "
                     "so COM ports and USB sockets no longer matter.", TEAL)
        else:
            self.log("Link mode: USB SERIAL. Assign one COM port per node.", TEAL)

    def _layout_strips(self):
        """
        Four nodes stacked in one column do not fit on a 452 px panel without
        scrolling, which defeats the point of an at-a-glance array. 2x2 shows
        all four at once. Single column stays available for narrow windows.
        """
        grid = bool(self.cfg.get("strips_grid", True))
        while self.strip_grid.count():
            self.strip_grid.takeAt(0)
        for i in sorted(self.strips):
            w = self.strips[i]
            if grid:
                self.strip_grid.addWidget(w, (i - 1) // 2, (i - 1) % 2)
            else:
                self.strip_grid.addWidget(w, i - 1, 0, 1, 2)
        self.strip_grid.setRowStretch(self.strip_grid.rowCount(), 1)
        if hasattr(self, "b_strip_mode"):
            self.b_strip_mode.setText("STACKED" if grid else "2 x 2")

    def toggle_strip_layout(self):
        self.cfg["strips_grid"] = not bool(self.cfg.get("strips_grid", True))
        self._layout_strips()

    def _apply_link_mode(self):
        """Serial controls are meaningless in wireless mode. Grey them out."""
        serial_mode = self.cfg.get("link_mode", "udp") == "serial"
        for cb in getattr(self, "port_combos", {}).values():
            cb.setEnabled(serial_mode)
        if hasattr(self, "baud_combo"):
            self.baud_combo.setEnabled(serial_mode)
        if hasattr(self, "b_refresh_ports"):
            self.b_refresh_ports.setEnabled(serial_mode)

    def refresh_ports(self):
        ports = []
        if list_ports is not None:
            try:
                ports = [(p.device, "%s  %s" % (p.device, (p.description or "")[:34]))
                         for p in list_ports.comports()]
            except Exception:
                ports = []
        for i, cb in self.port_combos.items():
            cur = self.cfg["ports"][i - 1]
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("— not assigned —", "")
            for dev, label in ports:
                cb.addItem(label, dev)
            idx = cb.findData(cur)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        if not ports and self.cfg.get("link_mode", "udp") == "serial":
            self.log("No serial ports found. Check the USB hub and the CH343 driver.", AMBER)

    def toggle_connect(self):
        if self.workers or self.sim_worker or self.udp_worker:
            self.stop_all()
        else:
            self.connect_all()

    def connect_all(self):
        if self.sim_mode:
            self.start_sim()
            return
        if self.cfg.get("link_mode", "udp") == "udp":
            self.start_udp()
            return
        if serial is None:
            self.log("pyserial is not installed.  pip install pyserial", RED)
            self.sound.state("error")
            return
        chosen = {}
        for i, cb in self.port_combos.items():
            dev = cb.currentData() or ""
            self.cfg["ports"][i - 1] = dev
            if dev:
                chosen[i] = dev
        if not chosen:
            self.log("Assign at least one COM port first.", AMBER)
            self.sound.state("error")
            return
        dupes = [d for d in set(chosen.values()) if list(chosen.values()).count(d) > 1]
        if dupes:
            self.log("The same port is assigned to more than one node: %s"
                     % ", ".join(dupes), RED)
            self.sound.state("error")
            return
        for i, dev in chosen.items():
            self.parsers[i] = CSIParser()
            w = SerialWorker(i, dev, int(self.cfg["baud"]), self.parsers[i])
            w.batch.connect(self.on_batch)
            w.failed.connect(self.on_fail)
            w.opened.connect(self.on_open)
            self.workers[i] = w
            w.start()
        self.b_conn.setText("DISCONNECT ALL")
        self.sound.state("connect")
        self.log("Opening %d port(s) at %d baud." % (len(chosen), int(self.cfg["baud"])), TEAL)

    def start_udp(self):
        self.stop_all(quiet=True)
        self.udp_worker = UdpWorker(self.cfg.get("udp_data_port", 8266),
                                    self.cfg.get("udp_disc_port", 8267),
                                    self.cfg.get("udp_router_ip", "10.8.0.1"))
        self.udp_worker.batch.connect(self.on_batch)
        self.udp_worker.pbatch.connect(self.on_pbatch)
        self.udp_worker.failed.connect(self.on_fail)
        self.udp_worker.opened.connect(self.on_open)
        self.udp_worker.lost.connect(self.on_lost)
        self.udp_worker.start()
        self.b_conn.setText("DISCONNECT ALL")
        self.sound.state("connect")
        self.log("Listening for wireless nodes on UDP %d, announcing on %d. "
                 "Nodes appear as they report in."
                 % (int(self.cfg.get("udp_data_port", 8266)),
                    int(self.cfg.get("udp_disc_port", 8267))), TEAL)

    def start_sim(self):
        self.stop_all(quiet=True)
        self.sim_worker = SimWorker(self.room, float(self.cfg["fresnel_sigma"]))
        self.sim_worker.batch.connect(self.on_batch)
        self.sim_worker.walker.connect(self.on_walker)
        for e in self.engines.values():
            e.connected = True
            e.port = "SIM"
        for (tx, rx), e in self.path_engines.items():
            if tx:
                e.connected = False       # simulation drives router paths only
        self.sim_worker.start()
        self.b_conn.setText("STOP SIMULATION")
        self.sound.state("connect")
        self.log("SIMULATION running — four virtual nodes and a virtual occupant. "
                 "This proves the display and the maths, not the physics.", PURPLE)

    def stop_all(self, quiet=False):
        for w in list(self.workers.values()):
            w.stop()
            w.wait(700)
        self.workers.clear()
        if self.udp_worker:
            self.udp_worker.stop()
            self.udp_worker.wait(700)
            self.udp_worker = None
        if self.sim_worker:
            self.sim_worker.stop()
            self.sim_worker.wait(700)
            self.sim_worker = None
        for e in self.path_engines.values():
            e.connected = False
            e.cancel_cal()
        self.sim_truth = None
        self.tracker.reset()
        self.b_conn.setText("CONNECT ALL")
        if not quiet:
            self.sound.state("disconnect")
            self.log("All links closed.", MUTED)

    def toggle_sim(self, on):
        self.sim_mode = bool(on)
        if self.workers or self.sim_worker or self.udp_worker:
            self.stop_all(quiet=True)
        self.log("Simulation mode %s." % ("ON" if on else "OFF"), PURPLE if on else MUTED)

    def on_open(self, idx, port):
        self.engines[idx].connected = True
        self.engines[idx].port = port
        self.log("Node %d connected on %s." % (idx, port), TEAL)

    def on_lost(self, idx):
        """A wireless node has gone quiet. Silence is the only symptom we get."""
        e = self.engines.get(idx)
        if e:
            e.connected = False
        self.log("Node %d has stopped reporting. Check its power — on battery "
                 "there is no serial console to ask." % idx, AMBER)
        self.sound.state("error")

    def on_fail(self, idx, msg):
        if idx == -1:
            self.log(msg, TEAL)          # informational, not a failure
            return
        e = self.engines.get(idx)
        if e:
            e.connected = False
            self.log("Node %d failed: %s" % (idx, msg), RED)
        else:
            # idx 0 means the link itself failed, not one node.
            self.log("Link failure: %s" % msg, RED)
            if self.udp_worker:
                self.udp_worker.stop()
                self.udp_worker = None
        self.sound.state("error")
        w = self.workers.pop(idx, None)
        if w:
            w.stop()
        if not self.workers and not self.sim_worker and not self.udp_worker:
            self.b_conn.setText("CONNECT ALL")

    def on_batch(self, idx, packets):
        """Serial and simulation sources have no link id -- treat as router."""
        e = self.engines.get(idx)
        if e:
            e.push(packets)

    def on_pbatch(self, tx, rx, packets):
        """Wireless source, protocol 2: records tagged with their transmitter."""
        e = self.path_engines.get((tx, rx))
        if e:
            e.push(packets)

    def on_walker(self, x, y, moving):
        self.sim_truth = (x, y, moving) if x >= 0 else None

    # ======================================================================
    # TUNING HANDLERS
    # ======================================================================
    def on_threshold(self, v):
        self.cfg["threshold"] = v
        for i, e in self.engines.items():
            e.threshold = v
            self.cfg["node_thresholds"][i - 1] = v

    def on_window(self, v):
        self.cfg["window"] = int(v)
        for e in self.path_engines.values():
            e.set_window(int(v))

    def on_smooth(self, v):
        self.cfg["smooth"] = int(v)
        for e in self.path_engines.values():
            e.set_smooth(int(v))

    def on_freeze(self, s):
        for e in self.path_engines.values():
            e.frozen = bool(s)
        self.log("Baselines %s." % ("frozen" if s else "learning again"), PURPLE)

    def on_ident(self):
        self.cfg["site_name"] = self.e_site.text()
        self.cfg["operator"] = self.e_op.text()
        self.cfg["case_ref"] = self.e_ref.text()
        self._update_subtitle()

    def on_sound_pick(self, kind):
        self.cfg["sound_kind"] = kind
        self.sound.play(kind)

    # ======================================================================
    # ARM / DISARM
    # ======================================================================
    def toggle_arm(self):
        self.cfg["armed"] = not bool(self.cfg.get("armed"))
        self._update_arm()

    def _update_arm(self, initial=False):
        armed = bool(self.cfg.get("armed"))
        self.tactical.armed = armed
        if armed:
            self.b_arm.setText("DISARM")
            self.b_arm.setStyleSheet(
                "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
                "font-size:%dpx;font-weight:800;letter-spacing:%dpx;}"
                "QPushButton:hover{background:%s;}"
                % (RED_DK, RED, RED, px(7), fs(14), max(1, px(2)), "#3a1219"))
            if not initial:
                self.sound.state("arm")
                self.log("SYSTEM ARMED — detections will alert and be logged as events.", RED)
        else:
            self.b_arm.setText("ARM SYSTEM")
            self.b_arm.setStyleSheet(
                "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
                "font-size:%dpx;font-weight:800;letter-spacing:%dpx;}"
                "QPushButton:hover{background:%s;}"
                % (PANEL_3, TEXT, BORDER_HI, px(7), fs(14), max(1, px(2)), PANEL_2))
            if not initial:
                self.sound.state("disarm")
                self.log("System disarmed — still measuring, not alerting.", MUTED)

    def _set_badge(self, state):
        col = STATE_COLORS.get(state, DIM)
        c = QtGui.QColor(col)
        self.badge.setText(state)
        self.badge.setStyleSheet(
            "color:%s;background:rgba(%d,%d,%d,30);border:%dpx solid %s;border-radius:%dpx;"
            "font-size:%dpx;font-weight:800;letter-spacing:%dpx;font-family:%s;"
            % (col, c.red(), c.green(), c.blue(), max(1, px(2)), col, px(8),
               fs(15), max(1, px(2)), SANS))

    # ======================================================================
    # ROOM
    # ======================================================================
    def on_room_size(self):
        self.room.width = float(self.sp_w.value())
        self.room.height = float(self.sp_h.value())
        self.on_room_changed()

    def on_room_changed(self):
        self.sync_controls()
        self.planner.update()
        self.tactical.update()
        if self.sim_worker:
            self.sim_worker.room = self.room

    def on_select(self, obj, from_canvas=False):
        for w in (self.sp_x, self.sp_y, self.sp_ow, self.sp_oh, self.sp_rot,
                  self.e_label, self.chk_metal):
            w.blockSignals(True)
        if obj is None:
            self.sel_name.setText("nothing selected")
            self.e_label.setText("")
            self.chk_metal.setChecked(False)
        else:
            spec = OBJ_TYPES.get(obj.kind, OBJ_TYPES["table"])
            self.sel_name.setText(spec[0] + (("  #%d" % obj.node_index)
                                             if obj.kind == "node" else ""))
            self.sp_x.setValue(obj.x)
            self.sp_y.setValue(obj.y)
            self.sp_ow.setValue(obj.w)
            self.sp_oh.setValue(obj.h)
            self.sp_rot.setValue(obj.rot)
            self.e_label.setText(obj.label)
            self.chk_metal.setChecked(bool(obj.metal))
        for w in (self.sp_x, self.sp_y, self.sp_ow, self.sp_oh, self.sp_rot,
                  self.e_label, self.chk_metal):
            w.blockSignals(False)

    def on_prop(self):
        o = self.planner.selected
        if not o:
            return
        o.x = float(self.sp_x.value())
        o.y = float(self.sp_y.value())
        o.w = max(0.05, float(self.sp_ow.value()))
        o.h = max(0.05, float(self.sp_oh.value()))
        o.rot = float(self.sp_rot.value())
        o.label = self.e_label.text()
        o.metal = bool(self.chk_metal.isChecked())
        self.on_room_changed()

    def save_room(self):
        ok = self.room.save()
        self.sound.ui("ui_toggle")
        self.log("Room layout saved to %s" % os.path.basename(ROOM_PATH) if ok
                 else "Could not save the room layout.", TEAL if ok else RED)

    def reload_room(self):
        if self.room.load():
            self.sp_w.blockSignals(True)
            self.sp_h.blockSignals(True)
            self.sp_w.setValue(self.room.width)
            self.sp_h.setValue(self.room.height)
            self.sp_w.blockSignals(False)
            self.sp_h.blockSignals(False)
            self.planner.select(None)
            self.on_room_changed()
            self.log("Room layout reloaded from disk.", TEAL)
        else:
            self.log("No saved layout found.", AMBER)

    def reset_room(self):
        self.planner.snapshot()
        self.room.default_layout()
        self.planner.select(None)
        self.on_room_changed()
        self.log("Room reset to the default four-corner layout.", AMBER)

    # ======================================================================
    # CALIBRATION
    # ======================================================================
    def start_cal_sequence(self):
        live = [e for e in self.engines.values() if e.connected]
        if not live:
            self.log("Connect at least one node before calibrating.", AMBER)
            self.sound.state("error")
            return
        if self.cal_stage:
            self.cancel_cal()
            return
        delay = int(self.cfg["cal_delay"])
        if delay <= 0:
            self._begin_cal()
            return
        self.cal_stage = "delay"
        self.cal_deadline = time.time() + delay
        self.b_cal.setText("CANCEL  (walk out: %ds)" % delay)
        self.sound.state("cal_start")
        self.log("Calibration starts in %ds. LEAVE THE ROOM — it measures the empty room."
                 % delay, PURPLE)

    def _begin_cal(self):
        self.cal_stage = "running"
        secs = int(self.cfg["cal_secs"])
        self.cal_deadline = time.time() + secs + 0.5
        # Calibrate every path, not just the four router paths. Measured
        # wobble across the sixteen paths in one quiet room ranged 2.2 to 11.5
        # -- a 5x spread from geometry alone -- so a shared threshold would
        # leave quiet paths deaf and lively ones screaming.
        # DEFECT FOUND AND FIXED: this forced connected=True on every path,
        # including ones receiving nothing. A silent path then calibrated on
        # no data and kept a meaningless threshold. Only calibrate paths that
        # have actually delivered records recently.
        now_t = time.time()
        for (tx, rx), e in self.path_engines.items():
            alive = (e.last_pkt_t and (now_t - e.last_pkt_t) < 3.0)
            if tx == 0:
                alive = self.engines[rx].connected
            if alive:
                e.connected = True
                e.cal_result = None
                e.start_cal()
            elif tx != 0:
                e.connected = False
        self.b_cal.setText("CANCEL CALIBRATION")
        self.sound.state("go")
        self.log("Calibrating for %ds. Every node measures its own noise floor." % secs,
                 PURPLE)

    def cancel_cal(self):
        self.cal_stage = None
        for e in self.path_engines.values():
            e.cancel_cal()
        self.b_cal.setText("CALIBRATE ALL NODES")
        self.log("Calibration cancelled — nothing was changed.", AMBER)

    def _cal_tick(self):
        if not self.cal_stage:
            return
        now = time.time()
        if self.cal_stage == "delay":
            left = self.cal_deadline - now
            if left <= 0:
                self._begin_cal()
                return
            self.b_cal.setText("CANCEL  (walk out: %ds)" % int(math.ceil(left)))
            n = int(math.ceil(left))
            if n != getattr(self, "_last_cd", None):
                self._last_cd = n
                self.sound.state("cal_last" if n <= 3 else "cal_tick")
        elif self.cal_stage == "running":
            left = max(0, self.cal_deadline - now)
            self.b_cal.setText("CANCEL  (measuring: %ds)" % int(math.ceil(left)))
            if all(not e.cal_active for e in self.engines.values() if e.connected):
                self._collect_cal()

    def _collect_cal(self):
        self.cal_stage = None
        self.b_cal.setText("CALIBRATE ALL NODES")
        good, bad = [], []
        for i, e in self.engines.items():
            if not e.connected or e.cal_result is None:
                continue
            thr, note = e.cal_result
            if thr is None:
                bad.append("N%d: %s" % (i, note))
            else:
                good.append("N%d trigger %.1f — %s" % (i, thr, note))
            e.cal_result = None
        for line in good:
            self.log(line, GREEN)
        for line in bad:
            self.log(line, RED)
        if good and not bad:
            self.sound.state("cal_done")
            self.log("Calibration complete for %d node(s). Settings saved." % len(good), GREEN)
        elif good:
            self.sound.state("cal_fail")
            self.log("Partial calibration: %d ok, %d refused. Recalibrate the refused nodes "
                     "with the room genuinely empty." % (len(good), len(bad)), AMBER)
        else:
            self.sound.state("cal_fail")
            self.log("Calibration refused on every node — the room was not quiet. "
                     "Nothing was changed.", RED)
        save_settings(self.cfg)
        self.sync_controls()

    # ======================================================================
    # RECORDING / REPORT / SETTINGS
    # ======================================================================
    def toggle_record(self):
        if self.recording:
            self.recording = False
            try:
                if self.rec_file:
                    self.rec_file.close()
            except Exception:
                pass
            self.rec_file = self.rec_writer = None
            self.b_rec.setText("START RECORDING")
            self.sound.state("record_stop")
            self.log("Recording stopped: %s" % os.path.basename(self.rec_path), MUTED)
            return
        self.rec_path = os.path.join(HERE, "ghostvision_%s.csv"
                                     % time.strftime("%Y%m%d_%H%M%S"))
        try:
            self.rec_file = open(self.rec_path, "w", newline="", encoding="utf-8")
        except Exception as e:
            self.log("Cannot open the recording file: %s" % e, RED)
            self.sound.state("error")
            return
        self.rec_writer = csv.writer(self.rec_file)
        head = ["time", "epoch", "system_state", "armed"]
        for i in range(1, NODE_COUNT + 1):
            head += ["n%d_state" % i, "n%d_level" % i, "n%d_thr" % i,
                     "n%d_rate" % i, "n%d_rssi" % i, "n%d_conf" % i]
        head += ["zone", "track_state", "track_x", "track_y", "track_r",
                 "track_speed", "track_heading", "solution_quality", "ambiguous"]
        self.rec_writer.writerow(head)
        self.recording = True
        self.b_rec.setText("STOP RECORDING")
        self.sound.state("record_start")
        self.log("Recording to %s" % os.path.basename(self.rec_path), TEAL)

    def _rec_row(self, system, sol, tk):
        if not self.recording or not self.rec_writer:
            return
        row = [time.strftime("%H:%M:%S"), "%.3f" % time.time(), system,
               int(bool(self.cfg.get("armed")))]
        for i in range(1, NODE_COUNT + 1):
            e = self.engines[i]
            row += [e.state, "%.2f" % e.level, "%.1f" % e.threshold, e.rate,
                    e.rssi if e.rssi is not None else "", e.confidence]
        if tk.get("active"):
            row += [tk.get("zone", ""), tk.get("state", ""),
                    "%.3f" % tk["pos"][0], "%.3f" % tk["pos"][1],
                    "%.2f" % (tk.get("radius") or 0), "%.3f" % tk.get("speed", 0),
                    "%.0f" % tk.get("heading", 0)]
        else:
            row += [(sol or {}).get("reason", ""), "IDLE", "", "", "", "", ""]
        row += ["%.2f" % (sol or {}).get("quality", 0),
                int(bool((sol or {}).get("ambiguous")))]
        try:
            self.rec_writer.writerow(row)
        except Exception:
            pass

    def export_report(self):
        path = os.path.join(HERE, "ghostvision_report_%s.txt"
                            % time.strftime("%Y%m%d_%H%M%S"))
        score, geo_notes = self.room.geometry_score()
        L = []
        L.append("GHOST-VISION  v%s  —  INSTALL / SESSION REPORT" % APP_VER)
        L.append("=" * 74)
        L.append("Generated      : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        L.append("Site           : %s" % (self.cfg.get("site_name") or "—"))
        L.append("Operator       : %s" % (self.cfg.get("operator") or "—"))
        L.append("Job reference  : %s" % (self.cfg.get("case_ref") or "—"))
        L.append("Session length : %s" % fmt_dur(time.time() - self.session_t0))
        L.append("Mode           : %s" % ("SIMULATION" if self.sim_worker else "LIVE HARDWARE"))
        L.append("")
        L.append("ROOM")
        L.append("-" * 74)
        L.append("Dimensions     : %.2f m wide  ×  %.2f m deep" % (self.room.width, self.room.height))
        r = self.room.router()
        L.append("Router         : %s" % ("%.2f, %.2f m" % r.center() if r else "NOT PLACED"))
        for i, n in sorted(self.room.nodes().items()):
            c = n.center()
            d = math.hypot(c[0] - r.center()[0], c[1] - r.center()[1]) if r else 0
            L.append("Node %d         : %.2f, %.2f m    %.2f m from router" % (i, c[0], c[1], d))
        L.append("Geometry score : %d%%  (spread of node→router paths)" % score)
        L.append("Furniture      : %d mapped items" % len(self.room.furniture()))
        L.append("")
        L.append("PLACEMENT CHECK")
        L.append("-" * 74)
        ws = self.room.warnings()
        L.extend(["  · " + w for w in ws] if ws else ["  · no issues flagged"])
        L.append("")
        L.append("NODE CONFIGURATION")
        L.append("-" * 74)
        L.append("  node   port        trigger   calibrated          pkt/s  rssi   state")
        for i in range(1, NODE_COUNT + 1):
            e = self.engines[i]
            L.append("  N%-4d  %-10s  %6.1f   %-18s  %4d   %-5s  %s"
                     % (i, e.port or "—", e.threshold,
                        self.cfg["node_cal_time"][i - 1] or "never",
                        e.rate, e.rssi if e.rssi is not None else "—", e.state))
        L.append("")
        L.append("TUNING")
        L.append("-" * 74)
        for k in ("link_mode", "udp_data_port", "udp_disc_port",
                  "timing_tol", "rate_guard", "common_mode",
                  "baud", "window", "smooth", "gain", "exit_ratio", "hits_needed",
                  "clear_needed", "presence_secs", "fresnel_sigma", "grid_cm",
                  "track_q", "track_r", "track_hold", "adapt"):
            L.append("  %-16s %s" % (k, self.cfg.get(k)))
        L.append("")
        L.append("EVENT LOG  (%d entries)" % len(self.events))
        L.append("-" * 74)
        for ts, msg in self.events[-260:]:
            L.append("  %s  %s" % (ts, msg))
        L.append("")
        L.append("SCOPE OF THE SYSTEM — what it can and cannot do")
        L.append("-" * 74)
        L.append("  Ghost-Vision detects human MOTION and PRESENCE from the disturbance a")
        L.append("  body causes in the WiFi channel between four sensors and a dedicated")
        L.append("  access point. It works in darkness and through furniture.")
        L.append("")
        L.append("  It does NOT produce an image, outline or skeleton.")
        L.append("  It does NOT measure identity, biometrics, height, build, age or sex.")
        L.append("  It does NOT count people — two bodies read as one blob between them.")
        L.append("  It does NOT detect carried phones or any other device.")
        L.append("  It does NOT find planted bugs or hidden transmitters (that is TSCM).")
        L.append("  Zone output is a coarse likelihood area of roughly one metre, with a")
        L.append("  drawn uncertainty ring. It is not a position fix.")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(L))
            self.log("Report written: %s" % os.path.basename(path), TEAL)
        except Exception as e:
            self.log("Could not write the report: %s" % e, RED)

    def save_cfg(self):
        ok = save_settings(self.cfg)
        self.sound.ui("ui_toggle")
        self.log("Settings saved." if ok else "Could not save settings.", TEAL if ok else RED)

    def reset_session(self):
        self.session_t0 = time.time()
        self.events = []
        self.log_box.setPlainText("")
        self.tracker.reset()
        for e in self.path_engines.values():
            e.reset_session()
        self.log("Session counters reset.", MUTED)

    # ======================================================================
    # CAMERA
    # ======================================================================
    def toggle_camera(self):
        if self.cam_worker:
            self.cam_worker.stop()
            self.cam_worker.wait(800)
            self.cam_worker = None
            self.campanel.clear()
            self.campanel.set_status("OPTICAL FEED OFF")
            self.b_cam.setText("START CAMERA")
            self.cfg["cam_enable"] = False
            self.log("Optical feed stopped.", MUTED)
            return
        if not HAVE_CV2:
            self.campanel.set_status("opencv-python not installed")
            self.log("The optical panel needs opencv-python:  pip install opencv-python", AMBER)
            self.sound.state("error")
            return
        self.campanel.set_status("OPENING CAMERA…")
        self.cam_worker = CameraWorker(int(self.cfg.get("cam_index", 0)),
                                       int(self.cfg.get("cam_fps", 15)),
                                       bool(self.cfg.get("cam_mirror", True)))
        self.cam_worker.frame.connect(self.campanel.set_frame)
        self.cam_worker.failed.connect(self.on_cam_fail)
        self.cam_worker.info.connect(lambda m: self.log("Optical: %s" % m, MUTED))
        self.cam_worker.start()
        self.b_cam.setText("STOP CAMERA")
        self.cfg["cam_enable"] = True
        self.log("Optical feed started — operator aid only, not a sensing input.", TEAL)

    def on_cam_fail(self, msg):
        self.campanel.set_status("CAMERA UNAVAILABLE")
        self.log("Optical: %s" % msg, AMBER)
        self.sound.state("error")
        if self.cam_worker:
            self.cam_worker.stop()
            self.cam_worker = None
        self.b_cam.setText("START CAMERA")

    def camera_snapshot(self):
        img = self.campanel.image
        if img is None:
            self.log("No optical frame to save.", AMBER)
            return
        p = os.path.join(HERE, "ghostvision_snap_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
        if img.save(p):
            self.log("Snapshot saved: %s" % os.path.basename(p), TEAL)
        else:
            self.log("Could not save the snapshot.", RED)

    # ======================================================================
    # ANIMATION + MAIN TICK
    # ======================================================================
    def _animate(self):
        now = time.time()
        dt = clamp(now - self._last_frame_t, 0.001, 0.2)
        self._last_frame_t = now
        if self.stack.currentIndex() == 0:
            self.tactical.advance(dt)
            self.tactical.update()
        self.clock.setText(time.strftime("%H:%M:%S") + "\n" +
                           time.strftime("%Y-%m-%d"))


    def on_tick(self):
        now = time.time()
        self._cal_tick()

        for e in self.path_engines.values():
            e.tick()

        node_state = {i: {"state": e.state, "level": e.level, "conf": e.confidence}
                      for i, e in self.engines.items()}
        system = "OFFLINE"
        for e in self.engines.values():
            if STATE_RANK.get(e.state, 0) > STATE_RANK.get(system, 0):
                system = e.state

        # ---- corroboration across all sixteen paths -----------------------
        # Two things happen here, and both raise accuracy:
        #
        #  MORE COVERAGE. The four router paths all converge on one point, so
        #  a corner they don't cross is a blind spot. The twelve sibling paths
        #  criss-cross the room, so a body has far more chances to be seen.
        #
        #  FEWER PHANTOMS. A single path firing alone is the phantom signature
        #  we chased for days -- a rate dip, a reflection, a passing neighbour.
        #  A real body in the room blocks several crossing paths at once.
        #  Requiring path_confirm paths to agree keeps the extra sensitivity
        #  without inheriting the extra false alarms.
        self.active_paths = [e.label for e in self.path_engines.values()
                             if e.connected and e.detected]
        if self.cfg.get("use_paths", True):
            need = max(1, int(self.cfg.get("path_confirm", 2)))
            warmed = [e for e in self.path_engines.values()
                      if e.connected and e.state not in ("OFFLINE", "WARMING UP")]
            if len(self.active_paths) >= need and len(warmed) >= need:
                if STATE_RANK.get("MOTION DETECTED", 0) > STATE_RANK.get(system, 0):
                    system = "MOTION DETECTED"

        # ---- zone solve + track at 10 Hz ----
        if not hasattr(self, "_solve_flip"):
            self._solve_flip = False
        self._solve_flip = not self._solve_flip
        if self._solve_flip:
            self.solution = self.solver.compute(
                self.room,
                self.path_engines if self.cfg.get("use_paths", True)
                else self.engines,
                float(self.cfg["fresnel_sigma"]),
                int(self.cfg["grid_cm"]),
                common_mode=float(self.cfg.get("common_mode", 0.85)),
                use_all_paths=(self.cfg.get("solver_paths", "router") == "all"))
            if self.cfg.get("track_enable", True):
                self.track = self.tracker.step(self.solution, self.room)
            else:
                self.tracker.reset_soft()
                self.track = {"active": False, "state": "OFF"}
        sol = getattr(self, "solution", None) or {}
        tk = getattr(self, "track", None) or {"active": False, "state": "IDLE"}

        # ---- tactical ----
        self.tactical.sim_truth = self.sim_truth
        self.tactical.set_solution(sol, tk, node_state, system)

        # ---- strips ----
        for s in self.strips.values():
            s.refresh()

        # ---- headline cards ----
        self._set_badge(system)
        scol = STATE_COLORS.get(system, DIM)
        self.c_state.set(system, scol,
                         "armed" if self.cfg.get("armed") else "measuring only")
        if tk.get("active"):
            self.c_zone.set(tk.get("zone", "—"), CYAN,
                            "± %.1f m  ·  %s" % (tk.get("radius") or 0, tk.get("state")))
            self.c_move.set(tk.get("class", "—"), tk.get("class_color", MUTED),
                            "%.2f m/s  %s" % (tk.get("speed", 0),
                                              compass(tk.get("heading", 0))
                                              if tk.get("speed", 0) > 0.08 else ""))
        else:
            self.c_zone.set(sol.get("reason") or "—", MUTED,
                            "no tracked target" if not sol.get("reason") else "")
            self.c_move.set("—", MUTED, "")
        live = sum(1 for e in self.engines.values()
                   if e.connected and e.state not in ("OFFLINE", "NO SIGNAL"))
        self.c_active.set("%d/%d" % (live, NODE_COUNT),
                          GREEN if live == NODE_COUNT else (AMBER if live else DIM),
                          "%d contributing" % sol.get("active", 0))
        total_ev = sum(e.events for e in self.engines.values())
        self.c_events.set(total_ev, TEXT if total_ev else MUTED,
                          "since session start")
        self.c_up.set(fmt_dur(now - self.session_t0), MUTED,
                      "REC" if self.recording else "")

        # ---- transitions, sounds, log ----
        if system != self.last_system:
            if system == "MOTION DETECTED":
                self.log("MOTION DETECTED%s" % (" — ARMED" if self.cfg.get("armed") else ""),
                         RED)
                if self.cfg.get("armed"):
                    self.sound.play(self.cfg.get("sound_kind", "Off"), throttle=2.0)
            elif system == "AREA CLEAR" and self.last_system in (
                    "MOTION DETECTED", "PRESENCE (STILL)", "UNSETTLED"):
                self.log("Area clear.", GREEN)
                if self.cfg.get("sound_clear"):
                    self.sound.play("clear", throttle=2.0)
            elif system == "PRESENCE (STILL)":
                self.log("Presence, still — EXPERIMENTAL reading.", AMBER)
            elif system == "WARMING UP" and self.last_system == "OFFLINE":
                self.log("Warming up — nothing can trigger until the baseline is learned.",
                         BLUE)
            elif system == "AREA CLEAR" and self.last_system == "WARMING UP":
                self.sound.state("warm_done")
                self.log("Baseline learned. Detection is live.", GREEN)
            self.last_system = system

        track_active = bool(tk.get("active"))
        if track_active != self.last_track_active:
            if track_active:
                self.sound.state("lock")
                self.log("Track %02d acquired in %s (± %.1f m, %d/%d nodes)."
                         % (tk.get("id", 0), tk.get("zone", "?"), tk.get("radius") or 0,
                            sol.get("active", 0), max(1, sol.get("usable", 0))), CYAN)
            else:
                self.sound.state("lost")
                self.log("Track lost.", MUTED)
            self.last_track_active = track_active

        if sol.get("ambiguous") and now - getattr(self, "_amb_log", 0) > 30:
            self._amb_log = now
            self.log("Solution is AMBIGUOUS — a second, nearly-as-good answer exists "
                     "elsewhere in the room. Treat the zone as unreliable until more "
                     "nodes contribute.", AMBER)

        # ---- assessment, flags, diagnostics, status ----
        self.assess.setText(self._assessment(system, sol, tk))
        flags = []
        for i, e in self.engines.items():
            for txt, col in e.flags:
                flags.append(("N%d %s" % (i, txt), col))
        if sol.get("ambiguous"):
            flags.append(("AMBIGUOUS SOLUTION", AMBER))
        if self.sim_worker:
            flags.append(("SIMULATION — NOT REAL DATA", PURPLE))
        if self.recording:
            flags.append(("RECORDING", RED))
        self._render_flags(flags)
        self._update_diag()
        self._update_status(system, sol, tk)
        self._rec_row(system, sol, tk)

    # ---- plain-language assessment ----
    def _assessment(self, system, sol, tk):
        parts = []
        offline = sum(1 for e in self.engines.values() if not e.connected)
        if offline == NODE_COUNT:
            return ("No nodes connected. Go to SETUP / CONTROL, pick a COM port for each "
                    "node and press CONNECT ALL — or tick simulation mode to rehearse "
                    "without hardware.")
        warming = [i for i, e in self.engines.items() if e.state == "WARMING UP"]
        if warming:
            frac = min(self.engines[i].warm_frac() for i in warming)
            parts.append("Nodes %s are still learning the room (%d%%). Nothing can trigger "
                         "until that finishes — this is deliberate, it is what stops the "
                         "system latching into a false alarm at startup."
                         % (", ".join("N%d" % i for i in warming), int(100 * frac)))
        nosig = [i for i, e in self.engines.items() if e.state == "NO SIGNAL"]
        if nosig:
            parts.append("Nodes %s have an open port but no CSI arriving. That is firmware, "
                         "the sensing WiFi, or the baud rate — not the detection settings."
                         % ", ".join("N%d" % i for i in nosig))

        if system == "MOTION DETECTED":
            # Sixteen path names in one sentence is unreadable. Report the
            # count, name only the loudest few, and say what it does NOT mean.
            hot = sorted(((e.label, e.level) for e in self.path_engines.values()
                          if e.detected), key=lambda t: -t[1])
            top = ", ".join(n for n, _ in hot[:3])
            more = (" and %d more" % (len(hot) - 3)) if len(hot) > 3 else ""
            parts.append("Motion is being detected on %d of %d sensing paths — loudest: "
                         "%s%s. Something is moving where those paths cross. It does not "
                         "say who, how many, or what they are doing."
                         % (len(hot), len(self.path_engines), top, more))
            rs = sol.get("region_state") or ""
            if rs and rs not in ("NO ACTIVITY", "ONE ACTIVITY REGION"):
                parts.append(sol.get("region_note") or rs)
        elif system == "PRESENCE (STILL)":
            parts.append("The channel has not returned to the empty-room baseline, with no "
                         "strong movement now. That reads as someone present and still. "
                         "This is the EXPERIMENTAL state — a fan, an open window or a heating "
                         "system can look identical. Treat it as a hint, not a certainty.")
        elif system == "UNSETTLED":
            parts.append("Energy is above room noise but below the trigger. A pet, a fan, or "
                         "movement in the next room looks like this. It is not reported as a "
                         "detection.")
        elif system == "AREA CLEAR":
            parts.append("All warmed nodes are below their clear lines. As far as this system "
                         "can tell, the room is empty of moving people.")

        if tk.get("active"):
            near = ("  Nearest mapped item: %s at about %.1f m."
                    % (tk["near"], tk.get("near_d", 0))) if tk.get("near") else ""
            parts.append("Tracked zone: %s, roughly %.2f, %.2f m with an uncertainty of about "
                         "%.1f m, from %d of %d usable nodes. Movement reads as %s at %.2f m/s."
                         "%s  This is a likelihood area, not a position fix."
                         % (tk.get("zone"), tk["pos"][0], tk["pos"][1],
                            tk.get("radius") or 0, tk.get("evidence", 0),
                            max(1, tk.get("usable", 0)), tk.get("class", "—"),
                            tk.get("speed", 0), near))
            if tk.get("state") == "COASTING":
                parts.append("No fresh crossing right now — the marker is coasting on a fix "
                             "%.1f s old and will be dropped shortly if nothing confirms it."
                             % tk.get("stale", 0))
            if sol.get("reason") == "LOW EVIDENCE — 1 NODE":
                parts.append("Only one node is currently above its clear line. A single "
                             "sensing band cannot fix a position on its own — anywhere along "
                             "it fits equally well — so no new zone is being computed.")
            if sol.get("ambiguous"):
                parts.append("WARNING: the solver found a second, nearly as good answer "
                             "elsewhere. With this geometry the marked side of the room may "
                             "be wrong. Add active nodes or move a node off the shared line "
                             "before trusting it.")
        elif sol.get("reason") == "INSUFFICIENT DATA":
            parts.append("Zone estimate needs at least two warmed nodes and a router placed "
                         "on the map.")
        elif sol.get("reason") in ("NO ROUTER PLACED", "NO NODES PLACED"):
            parts.append("Zone estimate is disabled: %s. Fix it in ROOM PLANNER."
                         % sol["reason"].lower())

        if not self.cfg.get("armed") and system in ("MOTION DETECTED", "PRESENCE (STILL)"):
            parts.append("The system is DISARMED, so this is being measured but not alerted.")
        return "  ".join(parts) if parts else "Measuring."

    def _render_flags(self, flags):
        while self.flag_layout.count():
            it = self.flag_layout.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for txt, col in flags[:8]:
            self.flag_layout.addWidget(Chip("▲ " + txt, col, size=11))
        self.flag_layout.addStretch()

    def _update_diag(self):
        rows = ["<pre style='margin:0'>",
                "node  port        state             lvl   trig   pkt/s  rssi   sc   conf  qual"]
        for i in range(1, NODE_COUNT + 1):
            e = self.engines[i]
            rows.append(" N%-3d %-11s %-17s %5.1f %6.1f %6d  %-5s %4d %5d%% %5.2f"
                        % (i, (e.port or "—")[:11], e.state[:17], e.level, e.threshold,
                           e.rate, e.rssi if e.rssi is not None else "—", e.S,
                           e.confidence, e.quality))
        sol = getattr(self, "solution", None) or {}
        tk = getattr(self, "track", None) or {}
        rows.append("")
        rows.append("solver   usable %d   active %d   quality %.2f   ambiguous %s"
                    % (sol.get("usable", 0), sol.get("active", 0),
                       sol.get("quality", 0), "yes" if sol.get("ambiguous") else "no"))
        if tk.get("active"):
            rows.append("track    %-9s id %02d   %.2f, %.2f m  ± %.2f   %.2f m/s  %03d°"
                        % (tk.get("state"), tk.get("id", 0), tk["pos"][0], tk["pos"][1],
                           tk.get("radius") or 0, tk.get("speed", 0), tk.get("heading", 0)))
        else:
            rows.append("track    idle")
        rows.append("")
        rows.append("audio    %s   opencv %s   pyserial %s"
                    % ("on" if self.cfg.get("volume", 0.7) > 0 else "muted",
                       "yes" if HAVE_CV2 else "no", "yes" if serial else "no"))
        rows.append("</pre>")
        self.diag.setText("\n".join(rows))

    def _update_status(self, system, sol, tk):
        if self.sim_worker:
            mode = "SIMULATION"
        elif self.udp_worker:
            mode = "LIVE · WIRELESS"
        elif self.workers:
            mode = "LIVE · USB"
        else:
            mode = "IDLE"
        self.st_left.setText(
            "%s   ·   %s   ·   nodes %d/%d   ·   solver %d cm grid   ·   %s"
            % (mode, system, sum(1 for e in self.engines.values() if e.connected),
               NODE_COUNT, int(self.cfg["grid_cm"]),
               "ARMED" if self.cfg.get("armed") else "disarmed"))
        self.st_right.setText(
            "F1 ops · F2 planner · F3 setup · Ctrl+A arm · Ctrl+K calibrate · "
            "Ctrl+R record · Ctrl+E report")

    # ======================================================================
    def closeEvent(self, ev):
        try:
            if self.recording:
                self.toggle_record()
        except Exception:
            pass
        try:
            if self.cam_worker:
                self.cam_worker.stop()
                self.cam_worker.wait(600)
        except Exception:
            pass
        self.stop_all(quiet=True)
        save_settings(self.cfg)
        self.room.save()
        try:
            self.sound.play("shutdown")
        except Exception:
            pass
        try:
            self.sound.cleanup()
        except Exception:
            pass
        ev.accept()

    # ---- staggered entrance ----
    def play_entrance(self):
        Reveal.one(self.header, delay=0, dur=380, rise=10)
        d = 140
        groups = [self.tactical.parentWidget(),
                  self.c_state, self.c_zone, self.c_move,
                  self.c_active, self.c_events, self.c_up]
        d = Reveal.stagger(groups, start=d, step=48, dur=320, rise=12)
        Reveal.stagger([self.campanel.parentWidget()] +
                       [self.strips[i] for i in sorted(self.strips)] +
                       [self.assess.parentWidget(), self.log_box],
                       start=d - 60, step=44, dur=320, rise=12)
        Reveal.one(self.statusbar, delay=d + 220, dur=320, rise=8)


# ==========================================================================
# ENTRY POINT
# ==========================================================================
def main():
    ap = argparse.ArgumentParser(description="Ghost-Vision Ops Console")
    ap.add_argument("--sim", action="store_true",
                    help="simulation mode: four virtual nodes and a virtual occupant")
    ap.add_argument("--noboot", action="store_true", help="skip the boot sequence")
    ap.add_argument("--scale", type=float, default=None,
                    help="interface size multiplier, e.g. 1.2")
    args = ap.parse_args()

    global UI_SCALE
    pre = load_settings()
    UI_SCALE = float(clamp(args.scale if args.scale else pre.get("ui_scale", 1.0), 0.7, 2.0))

    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Ghost-Vision")
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(BG))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(PANEL_2))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor(PANEL))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(TEAL))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(VOID))
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(PANEL))
    pal.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(TEXT))
    app.setPalette(pal)

    holder = {}

    def build_console():
        holder["win"] = OpsConsole(sim=args.sim)
        return True

    sound = SoundBank(pre)
    steps = [
        ("audio synthesis engine", lambda: sound.prewarm(
            ["boot_step", "boot_done", "ui_click", "lock", "arm"])),
        ("room geometry", None),
        ("site settings", None),
        ("detection engines  ×4", None),
        ("zone solver grid", None),
        ("target tracker", None),
        ("optical subsystem", lambda: True if HAVE_CV2 else "no opencv"),
        ("operator console", build_console),
    ]

    if args.noboot or not pre.get("boot_anim", True):
        win = OpsConsole(sim=args.sim)
        win.show()
        win.play_entrance()
        return app.exec_()

    splash = BootSplash(steps, sound=sound)
    scr = app.primaryScreen().geometry()
    splash.move(scr.center() - splash.rect().center())
    splash.show()

    def go():
        win = holder.get("win")
        if win is None:
            win = OpsConsole(sim=args.sim)
            holder["win"] = win
        win.show()
        win.play_entrance()

    splash.finished.connect(go)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

