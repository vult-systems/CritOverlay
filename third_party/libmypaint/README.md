# libmypaint (comparison engine, exp/shape-tools prototype)

`python third_party/libmypaint/build_libmypaint.py` builds `mypaint.dll` and stages it in `crit_overlay/mypaint/` (not committed). The app offers the MyPaint brushes in the Brush presets popup only when that folder exists, and `packaging/crit_overlay.spec` bundles it only then.

- **Sources** (pinned by SHA-256 in the script): libmypaint 1.6.1 (ISC), json-c 0.18 (MIT), mypaint-brushes 1.3.1 (CC0).
- **Build:** MSVC via CMake. json-c is linked statically, glib and gettext are compiled out, and the C runtime is static. The DLL imports only `KERNEL32` and `ADVAPI32`.
- **Glue:** `crit_surface.c` is a sparse, transparent tile surface plus a fast 15-bit to ARGB32 read, compiled into the DLL.
- **Shipped licenses:** `LICENSE-libmypaint.txt`, `LICENSE-json-c.txt`, `brushes/LICENSE-mypaint-brushes.txt`.
