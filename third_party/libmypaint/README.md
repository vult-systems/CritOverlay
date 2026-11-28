# libmypaint (the brush engine)

libmypaint draws every freehand stroke, erasers included. It's a required part of the build: `packaging/build.py` runs `build_libmypaint.py --if-needed`, which builds `mypaint.dll` and stages it with the curated brushes in `crit_overlay/mypaint/` (not committed). The spec refuses to build without it, `verify_build.py` checks the files, and the self-test checks that the engine loads and every brush paints.

- **Sources** (pinned by SHA-256 in the script): libmypaint 1.6.1 (ISC), json-c 0.18 (MIT), mypaint-brushes 1.3.1 (CC0).
- **Build:** MSVC via CMake (Visual Studio 2022 C++ tools and CMake required). json-c is linked statically, glib and gettext are compiled out, and the C runtime is static. The DLL imports only `KERNEL32` and `ADVAPI32`.
- **Glue:** `crit_surface.c` is a sparse, transparent tile surface plus a fast 15-bit to ARGB32 read, compiled into the DLL.
- **Brushes:** the `BRUSHES` list in the script; `crit_overlay/brushes.py` turns them into the presets and erasers.
- **Shipped licenses:** `LICENSE-libmypaint.txt`, `LICENSE-json-c.txt`, `brushes/LICENSE-mypaint-brushes.txt`.
