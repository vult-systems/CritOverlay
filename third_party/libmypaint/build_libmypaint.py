"""Build libmypaint for the MyPaint brush engine comparison (prototype).

Downloads pinned, hash-checked source releases, builds one self-contained
mypaint.dll with MSVC (json-c static, no glib, no gettext, static CRT) and
stages it with its licenses and a curated set of brushes in
crit_overlay/mypaint/, which the app picks up when present. Nothing here
runs at app runtime and the output is not committed.

    python third_party/libmypaint/build_libmypaint.py

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

# A small curated set suited to drawing over the screen (no smudge or
# wet-paint brushes: those sample the canvas, and each stroke here paints on
# its own transparent layer). All CC0 / public domain.
BRUSHES = [
    ("deevad/ballpen.myb", "Ballpoint"),
    ("deevad/liner.myb", "Liner"),
    ("tanda/pencil-2b.myb", "Pencil 2B"),
    ("deevad/4H_pencil.myb", "Pencil 4H"),
    ("classic/textured_ink.myb", "Textured Ink"),
    ("classic/calligraphy.myb", "Calligraphy"),
    ("tanda/charcoal-01.myb", "Charcoal"),
    ("tanda/marker-01.myb", "Marker"),
    ("deevad/airbrush.myb", "Airbrush"),
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
    index = []
    for rel, label in BRUSHES:
        src = brushes / "brushes" / rel
        name = rel.replace("/", "_")
        shutil.copy2(src, OUT / "brushes" / name)
        index.append(f"{name}\t{label}")
    (OUT / "brushes" / "index.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print("staged", OUT)


if __name__ == "__main__":
    main()
