"""Constants, defaults, and style sheets.

Keeps naming and versioning in one place for config.py and packaging.
"""

APP_NAME = "Crit Overlay"
ORG_NAME = "CritOverlay"
APP_VERSION = "1.5.0-rc.5"

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
MIN_SIZE = 0.5
MAX_SIZE = 160
DEFAULT_ERASER_SIZE = 24
# Pen pressure response: raw pressure is raised to this power before any
# brush sees it. Soft reaches full strength with a light touch; firm needs
# more force.
PRESSURE_CURVES = {"soft": 0.6, "normal": 1.0, "firm": 1.7}
ERASER_STYLES = ("hard", "soft")
DEFAULT_OPACITY = 1.0
DEFAULT_SMOOTHING = 0.0
DEFAULT_PRESSURE = 0.65
DEFAULT_BACKDROP = None  # None | "white" | "black"
DEFAULT_BACKDROP_OPACITY = 0.5

# Drawing region (see region.py). Sizes are logical pixels, so they look the
# same at any display scale.
MIN_REGION_SIZE = 32  # a smaller drag means "full monitor"
MARGIN_MIN_SIZE = 16
MARGIN_MAX_SIZE = 400
DEFAULT_MARGIN_ENABLED = False
DEFAULT_MARGIN_SIZE = 120
DEFAULT_MARGIN_COLOR = "white"  # "white" | "black"
DEFAULT_MARGIN_OPACITY = 1.0

# Text tool (on-screen pixel size, like the brush).
DEFAULT_TEXT_SIZE = 32
MIN_TEXT_SIZE = 8
MAX_TEXT_SIZE = 200
TEXT_ALIGNS = ("left", "center", "right")

# Global show/hide hotkey (hotkeys.py). Configurable as [compat] hotkey or
# --hotkey; "none" turns it off.
DEFAULT_HOTKEY = "Ctrl+Alt+D"

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
ICON_SIZE = 20
TOAST_GAP = 10
TOAST_DURATION_MS = 1600
STARTUP_HINT_MS = 3200

TOOLBAR_STYLE = f"""
    QFrame#Toolbar, QFrame#Toolbar QWidget {{
        font-family: "Segoe UI", "IBM Plex Sans";
        font-size: 12px;
    }}
    QFrame#Toolbar {{
        background: rgba(20,20,23,240);
        border: 1px solid rgba(255,255,255,20);
        border-radius: 14px;
    }}
    QToolButton {{
        color: #e6e6e8;
        background: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px;
    }}
    QToolButton#Chip {{
        border: 1px solid rgba(255,255,255,34);
        padding: 4px 10px;
        font-weight: 600;
    }}
    QLabel#Hint {{
        color: #9a9aa1;
        font-size: 11px;
        padding: 0 4px;
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
    QSlider {{
        min-height: 24px;   /* a comfortable target for a pen */
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -6px 0;
        border-radius: 7px;
        background: #f2f2f3;
    }}
    QFrame#Sep {{
        background: rgba(255,255,255,16);
    }}
    /* Disabled controls (e.g. margin settings before an area is selected)
       must look inactive; the accent colors above read as "live" (#13). */
    QToolButton:checked:disabled {{
        background: transparent;
        border: 1px solid rgba(255,255,255,30);
    }}
    QSlider::sub-page:horizontal:disabled {{
        background: rgba(255,255,255,40);
    }}
    QSlider::handle:horizontal:disabled {{
        background: #5c5c62;
    }}
    QLabel#SliderCaption:disabled, QLabel#SliderValue:disabled {{
        color: #55555b;
    }}
"""

# Numeric fields and the font list in the context bar and menus.
FIELD_STYLE = """
    QAbstractSpinBox {
        color: #eeeeef;
        background: rgba(255,255,255,12);
        border: 1px solid rgba(255,255,255,26);
        border-radius: 6px;
        padding: 2px 6px;
        min-height: 22px;
        selection-background-color: #3a3a40;
    }
    QAbstractSpinBox:focus { border: 1px solid rgba(255,255,255,70); }
    QComboBox { color: #eeeeef; background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,26);
                border-radius: 6px; padding: 3px 8px; min-width: 120px; min-height: 22px; }
    QComboBox QAbstractItemView { color: #eeeeef; background: #1c1c1f; selection-background-color: #3a3a40; }
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
