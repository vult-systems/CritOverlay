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
RECENT_COLORS = 10
FAVORITE_COLORS = 10
# Neutral UI palette: selection is a lighter surface, not a colored outline.
TEXT = "#e7e7ea"
TEXT_DIM = "#8b8b93"
ICON_SIZE = 20
TOAST_GAP = 10
TOAST_DURATION_MS = 1600
STARTUP_HINT_MS = 3200

TOOLBAR_STYLE = f"""
    QFrame#Toolbar, QFrame#Toolbar QWidget {{
        font-family: "Segoe UI", "IBM Plex Sans";
        font-size: 12px;
        color: {TEXT};
    }}
    QFrame#Toolbar {{
        background: rgba(30,30,33,244);
        border: 1px solid rgba(255,255,255,22);
        border-radius: 12px;
    }}
    QToolButton {{
        color: {TEXT};
        background: transparent;
        border: none;
        border-radius: 7px;
        padding: 6px;
    }}
    QToolButton:hover {{ background: rgba(255,255,255,18); }}
    QToolButton:pressed {{ background: rgba(255,255,255,10); }}
    QToolButton:checked {{ background: rgba(255,255,255,40); }}
    QToolButton:checked:hover {{ background: rgba(255,255,255,48); }}
    QToolButton:disabled {{ color: #5f5f66; }}
    QToolButton#Chip {{
        background: rgba(255,255,255,16);
        padding: 3px 8px;
        text-align: left;
    }}
    QToolButton#Chip:hover {{ background: rgba(255,255,255,28); }}
    QToolButton#Seg {{ padding: 4px 10px; }}
    QToolButton#Swatch {{ padding: 3px; }}
    QToolButton#Tile {{ padding: 6px; border-radius: 9px; }}
    QToolButton#Link {{ color: {TEXT_DIM}; padding: 3px 6px; }}
    QToolButton#Disclosure {{ color: {TEXT_DIM}; padding: 4px 2px; font-weight: 600; }}
    QToolButton#Disclosure:checked {{ background: transparent; }}
    QToolButton#Disclosure:hover {{ background: rgba(255,255,255,12); }}
    QLabel#SliderCaption, QLabel#MenuHeading {{
        color: {TEXT_DIM};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
        padding: 2px 4px;
    }}
    QLabel#SliderValue {{ color: {TEXT_DIM}; font-size: 11px; min-width: 32px; }}
    QLabel#Hint {{ color: {TEXT_DIM}; font-size: 12px; padding: 0 6px; }}
    QFrame#Sep {{ background: rgba(255,255,255,20); }}
    QFrame#Rule {{ background: rgba(255,255,255,14); }}
    QSlider {{ min-height: 24px; }}
    QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,26); border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: rgba(255,255,255,150); border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #f4f4f5;
    }}
    QToolButton:checked:disabled {{ background: rgba(255,255,255,12); }}
    QSlider::sub-page:horizontal:disabled {{ background: rgba(255,255,255,40); }}
    QSlider::handle:horizontal:disabled {{ background: #5c5c62; }}
    QLabel#SliderCaption:disabled, QLabel#SliderValue:disabled {{ color: #55555b; }}
"""

MENU_STYLE = f"""
    QPushButton#MenuRow {{
        color: {TEXT};
        background: transparent;
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        text-align: left;
    }}
    QPushButton#MenuRow:hover {{ background: rgba(255,255,255,18); }}
    QPushButton#MenuRow:checked {{ background: rgba(255,255,255,34); }}
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
    QLineEdit { color: #eeeeef; background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,26);
                border-radius: 6px; padding: 3px 6px; selection-background-color: #4a4a52; }
    QLineEdit:focus { border: 1px solid rgba(255,255,255,80); }
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
