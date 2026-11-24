"""Constants, defaults, and style sheets.

Keeps naming and versioning in one place for config.py and packaging.
"""

APP_NAME = "Crit Overlay"
ORG_NAME = "CritOverlay"
APP_VERSION = "1.5.0-rc.1"

ACCENT = "#ff4b4b"

COLOR_PALETTE = [
    "#ff4b4b",  # red (default)
    "#ff9f43",  # orange
    "#ffd43b",  # yellow
    "#51cf66",  # green
    "#22b8cf",  # cyan
    "#4dabf7",  # blue
    "#9775fa",  # violet
    "#f06595",  # pink
    "#f8f9fa",  # near-white
    "#212529",  # near-black
]

# Brush defaults
DEFAULT_COLOR = ACCENT
DEFAULT_SIZE = 12
MIN_SIZE = 1
MAX_SIZE = 80
DEFAULT_OPACITY = 1.0
DEFAULT_SMOOTHING = 0.0
DEFAULT_PRESSURE = 0.65
DEFAULT_BACKDROP = None  # None | "white" | "black"
DEFAULT_BACKDROP_OPACITY = 0.5

# Past this many strokes the oldest is dropped, so a long session doesn't
# grow memory without limit.
MAX_STROKE_HISTORY = 300

# Minimum distance (px) the cursor must move before a new point is recorded,
# so tiny jitter doesn't bloat the point list. Coarse on purpose: brush.py
# resamples every point through a ~6x Catmull-Rom spline, so denser sampling
# costs per-frame work without looking any smoother.
MIN_POINT_SPACING = 0.6

# Seconds to suppress synthesized mouse events that some Wacom/Windows
# configurations emit right after a real tablet event, so one pen stroke
# can't become two history entries.
TABLET_MOUSE_GUARD_S = 0.35

PANEL_MARGIN_TOP = 16
TOAST_GAP = 10
TOAST_DURATION_MS = 1600
STARTUP_HINT_MS = 3200

TOOLBAR_STYLE = f"""
    QFrame#Toolbar {{
        background: rgba(20,20,23,240);
        border: 1px solid rgba(255,255,255,20);
        border-radius: 14px;
    }}
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 5px;
    }}
    QToolButton:hover {{
        background: rgba(255,255,255,24);
    }}
    QToolButton:pressed {{
        background: rgba(255,255,255,10);
    }}
    QToolButton:checked {{
        background: rgba(255,75,75,32);
        border: 1px solid {ACCENT};
    }}
    QLabel#SliderCaption {{
        color: #8a8a91;
        font-size: 10px;
        font-weight: 600;
    }}
    QLabel#SliderValue {{
        color: #c7c7cc;
        font-size: 11px;
        min-width: 32px;
    }}
    QSlider::groove:horizontal {{
        height: 3px;
        background: rgba(255,255,255,28);
        border-radius: 1px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ACCENT};
        border-radius: 1px;
    }}
    QSlider::handle:horizontal {{
        width: 12px;
        height: 12px;
        margin: -5px 0;
        border-radius: 6px;
        background: #f2f2f3;
    }}
    QFrame#Sep {{
        background: rgba(255,255,255,16);
    }}
"""

TOAST_STYLE = """
    QLabel {
        color: #eeeeef;
        background: rgba(17,17,19,235);
        border: 1px solid #303035;
        border-radius: 6px;
        padding: 6px 10px;
    }
"""

PASS_THROUGH_BADGE_STYLE = f"""
    QLabel {{
        color: #eeeeef;
        background: rgba(17,17,19,242);
        border: 1px solid {ACCENT};
        border-radius: 14px;
        padding: 7px 14px;
        font-weight: 600;
    }}
    QLabel:hover {{
        background: rgba(255,75,75,32);
    }}
"""
