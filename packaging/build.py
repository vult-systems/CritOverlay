"""Build, verify and package dist\\CritOverlay. Normally run by build.bat,
which first creates .venv-build and installs packaging/requirements-build.txt
with --require-hashes.

    python packaging/build.py [--skip-verify] [--no-run] [--no-zip]

Steps:
1. Check the toolchain matches the lock file (refuses to build otherwise,
   unless --allow-unlocked), so two builds of one commit use the same
   PySide6/Qt/PyInstaller.
2. Bake the icon and write build/build_info.json (version, commit,
   toolchain), which ships in the bundle and appears in diagnostics.
3. Run PyInstaller with PYTHONHASHSEED=0 and SOURCE_DATE_EPOCH set to the
   commit time, so repeat builds are as close to identical as PyInstaller
   allows.
4. Add deployment extras (README-FIRST.txt, example INI, helper .bat files).
5. verify_build.py: static checks and self-tests.
6. Zip and write SHA256SUMS.txt (IT needs the hash for allow-list rules).
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
WORK = ROOT / "build"
APP_DIST = DIST / "CritOverlay"


def run(cmd, **kw):
    print(f"> {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.check_call([str(c) for c in cmd], cwd=ROOT, **kw)


def git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def app_version():
    text = (ROOT / "crit_overlay" / "constants.py").read_text(encoding="utf-8")
    return re.search(r'^APP_VERSION = "([^"]+)"', text, re.M).group(1)


from venv_setup import SUPPORTED_BUILD_PYTHONS  # noqa: E402  (packaging/ is sys.path[0])


def check_toolchain(allow_unlocked, allow_unsupported_python=False):
    pins = {}
    for line in (PACKAGING / "requirements-build.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)\s*(?:;\s*(.+?))?\s*\\?$", line)
        if m:
            pins[m.group(1)] = (m.group(2), m.group(3))
    mismatches = []
    for name, (version, marker) in pins.items():
        if marker and "3.10" in marker and sys.version_info >= (3, 10):
            continue
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            mismatches.append(f"{name}: locked {version}, installed {installed}")
    if mismatches:
        msg = "Toolchain doesn't match packaging/requirements-build.txt:\n  " + "\n  ".join(mismatches)
        if not allow_unlocked:
            sys.exit(msg + "\nRun packaging\\build.bat (it installs the lock), or pass --allow-unlocked.")
        print("WARNING: " + msg)
    if sys.version_info[:2] not in SUPPORTED_BUILD_PYTHONS:
        supported = ", ".join(f"{a}.{b}" for a, b in SUPPORTED_BUILD_PYTHONS)
        msg = (f"Python {platform.python_version()} isn't a supported build Python ({supported}). "
               "It would be bundled into the app as its runtime.")
        if not allow_unsupported_python:
            sys.exit(msg + " Install Python 3.13 (per-user is fine) or set CRIT_PYTHON; "
                           "--allow-unsupported-python overrides.")
        print("WARNING: " + msg)
    if platform.architecture()[0] != "64bit":
        sys.exit("Build with 64-bit Python; the app ships x64 only.")


def source_date_epoch():
    ts = git("log", "-1", "--format=%ct")
    return int(ts) if ts and ts.isdigit() else int(datetime.now(timezone.utc).timestamp())


def write_build_info(epoch):
    dirty = git("status", "--porcelain")
    info = {
        "version": app_version(),
        "commit": git("rev-parse", "--short=12", "HEAD"),
        "dirty": bool(dirty),
        "source_date": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "python": platform.python_version(),
        "pyside6": metadata.version("PySide6_Essentials"),
        "pyinstaller": metadata.version("pyinstaller"),
        "build_host_windows": platform.version(),
    }
    WORK.mkdir(exist_ok=True)
    (WORK / "build_info.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Build info: {info}")
    if info["dirty"]:
        print("WARNING: building from a working tree with uncommitted changes")
    return info


def add_extras():
    extras = PACKAGING / "dist_extras"
    for item in extras.iterdir():
        shutil.copy2(item, APP_DIST / item.name)
    shutil.copy2(ROOT / "LICENSE", APP_DIST / "LICENSE.txt")


def make_zip(info):
    # Same naming as the v1.4.2 release asset: CritOverlay-v1.4.2-win64.zip
    name = f"CritOverlay-v{info['version']}-win64"
    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    files = sorted(p for p in APP_DIST.rglob("*") if p.is_file())
    # Fixed timestamps and order, so the zip's bytes depend only on content.
    stamp = datetime.fromisoformat(info["source_date"]).timetuple()[:6]
    stamp = max(stamp, (1980, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arc = Path(name) / path.relative_to(APP_DIST)
            zi = zipfile.ZipInfo(arc.as_posix(), date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, path.read_bytes())
    return zip_path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(zip_path):
    lines = [f"{sha256(zip_path)}  {zip_path.name}"]
    for exe in ("CritOverlay.exe", "CritOverlay-console.exe"):
        lines.append(f"{sha256(APP_DIST / exe)}  {APP_DIST.name}/{exe}")
    out = DIST / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))


def ensure_brush_engine():
    """libmypaint is part of the app: build and stage it (pinned sources,
    MSVC) unless crit_overlay/mypaint/ already holds everything."""
    run([sys.executable, ROOT / "third_party" / "libmypaint" / "build_libmypaint.py", "--if-needed"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--no-run", action="store_true", help="verify statically only; don't launch the self-tests")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--allow-unlocked", action="store_true")
    ap.add_argument("--allow-unsupported-python", action="store_true")
    args = ap.parse_args()

    check_toolchain(args.allow_unlocked, args.allow_unsupported_python)
    epoch = source_date_epoch()
    env = dict(os.environ, PYTHONHASHSEED="0", SOURCE_DATE_EPOCH=str(epoch))

    ensure_brush_engine()
    run([sys.executable, PACKAGING / "build_icon.py"], env=env)
    info = write_build_info(epoch)
    run([sys.executable, "-m", "PyInstaller", PACKAGING / "crit_overlay.spec",
         "--distpath", DIST, "--workpath", WORK / "pyinstaller", "--noconfirm", "--clean"], env=env)
    add_extras()

    if not args.skip_verify:
        verify = [sys.executable, PACKAGING / "verify_build.py", APP_DIST]
        if args.no_run:
            verify.append("--no-run")
        run(verify)

    if not args.no_zip:
        zip_path = make_zip(info)
        write_checksums(zip_path)
        print(f"Packaged {zip_path}")
    print(f"Built {APP_DIST / 'CritOverlay.exe'}")


if __name__ == "__main__":
    main()
