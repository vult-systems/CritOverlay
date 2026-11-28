"""Build libmypaint, the app's brush engine (a required part of the build).

Downloads pinned, hash-checked source releases, builds one self-contained
mypaint.dll with MSVC (json-c static, no glib, no gettext, static CRT) and
stages it with its licenses and the curated brushes in crit_overlay/mypaint/.
packaging/build.py runs this with --if-needed; the output is not committed.

    python third_party/libmypaint/build_libmypaint.py [--check | --if-needed]

Needs CMake and Visual Studio 2022 (C build tools).
"""

import hashlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = HERE / ".cache"
WORK = HERE / ".build"
OUT = ROOT / "crit_overlay" / "mypaint"

SOURCES = {
    "libmypaint": ("https://github.com/mypaint/libmypaint/releases/download/v1.6.1/libmypaint-1.6.1.tar.xz",
                   "741754f293f6b7668f941506da07cd7725629a793108bb31633fb6c3eae5315f", "libmypaint-1.6.1"),
    "json-c": ("https://s3.amazonaws.com/json-c_releases/releases/json-c-0.18.tar.gz",
               "876ab046479166b869afc6896d288183bbc0e5843f141200c677b3e8dfb11724", "json-c-0.18"),
    "brushes": ("https://github.com/mypaint/mypaint-brushes/releases/download/v1.3.1/mypaint-brushes-1.3.1.tar.xz",
                "fef66ffc241b7c5cd29e9c518e933c739618cb51c4ed4d745bf648a1afc3fe70", "mypaint-brushes-1.3.1"),
}

# The curated presets and erasers (crit_overlay/brushes.py names them by
# the staged file name: pack and file joined with "_"). All CC0 / public
# domain.
BRUSHES = [
    "deevad/soft-dip-pen.myb",       # Studio Pen
    "tanda/pencil-2b.myb",           # Pencil
    "kaerhon_v1/inkster_l.myb",      # Ink
    "tanda/marker-01.myb",           # Marker
    "deevad/airbrush.myb",           # Soft Brush (tuned in brushes.py)
    "deevad/large_hard_eraser.myb",  # hard eraser
    "ramon/Soft_Eraser.myb",         # soft eraser
]

CONFIG_H = """\
/* Written by build_libmypaint.py: no glib, no gettext, no OpenMP. */
#define PACKAGE_NAME "libmypaint"
#define PACKAGE_VERSION "1.6.1"
#define HAVE_STDINT_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define MYPAINT_CONFIG_USE_GLIB 0
"""


def fetch(name):
    url, sha, _ = SOURCES[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.exists():
        print("download", url)
        with urllib.request.urlopen(url) as r, open(path.with_suffix(".part"), "wb") as f:
            shutil.copyfileobj(r, f)
        path.with_suffix(".part").replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha:
        path.unlink()
        sys.exit(f"{path.name}: sha256 {digest} does not match the pinned {sha}")
    return path


def extract(name):
    archive = fetch(name)
    dest = WORK / "src"
    target = dest / SOURCES[name][2]
    if target.exists():
        shutil.rmtree(target)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as t:
        # Skip links (json-c's test aliases): Windows needs privileges for them.
        members = [m for m in t.getmembers() if not (m.issym() or m.islnk())]
        t.extractall(dest, members=members, filter="data")
    return target


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    if shutil.which("cmake") is None:
        sys.exit("cmake not found")
    lib = extract("libmypaint")
    jsonc = extract("json-c")
    brushes = extract("brushes")

    # The sources include "config.h" with quotes, so it has to replace the
    # shipped (glib-enabled) one next to them.
    (lib / "config.h").write_text(CONFIG_H, encoding="utf-8")

    jbuild, jinst = WORK / "json-c-build", WORK / "json-c-install"
    run("cmake", "-S", jsonc, "-B", jbuild, "-A", "x64", "-DBUILD_SHARED_LIBS=OFF", "-DBUILD_STATIC_LIBS=ON",
        "-DBUILD_TESTING=OFF", "-DBUILD_APPS=OFF", "-DDISABLE_WERROR=ON",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded", "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
        f"-DCMAKE_INSTALL_PREFIX={jinst}")
    run("cmake", "--build", jbuild, "--config", "Release", "--target", "install")

    mbuild = WORK / "mypaint-build"
    run("cmake", "-S", HERE, "-B", mbuild, "-A", "x64", f"-DLIBMYPAINT_SRC={lib}", f"-DJSONC_DIR={jinst}",
        f"-DCONFIG_DIR={lib}")
    run("cmake", "--build", mbuild, "--config", "Release")

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "brushes").mkdir(parents=True)
    shutil.copy2(mbuild / "Release" / "mypaint.dll", OUT / "mypaint.dll")
    shutil.copy2(lib / "COPYING", OUT / "LICENSE-libmypaint.txt")
    shutil.copy2(jsonc / "COPYING", OUT / "LICENSE-json-c.txt")
    shutil.copy2(brushes / "COPYING", OUT / "brushes" / "LICENSE-mypaint-brushes.txt")
    for rel in BRUSHES:
        shutil.copy2(brushes / "brushes" / rel, OUT / "brushes" / staged_name(rel))
    print("staged", OUT)


def staged_name(rel):
    return rel.replace("/", "_")


def expected_files():
    return ["mypaint.dll", "LICENSE-libmypaint.txt", "LICENSE-json-c.txt", "brushes/LICENSE-mypaint-brushes.txt"] + [
        f"brushes/{staged_name(rel)}" for rel in BRUSHES]


def check():
    """True when crit_overlay/mypaint/ holds everything the app needs."""
    missing = [f for f in expected_files() if not (OUT / f).exists()]
    for f in missing:
        print("missing", OUT / f)
    return not missing


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    if "--if-needed" in sys.argv and check():
        print("libmypaint already staged in", OUT)
        sys.exit(0)
    main()
