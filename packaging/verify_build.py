"""Verify a built dist\\CritOverlay folder before it ships.

    python packaging/verify_build.py dist/CritOverlay [--no-run]

1. Required files are present, and stripped ones really are gone.
2. Static dependency audit (pefile): every DLL that any bundled .exe/.dll/
   .pyd imports is either in the bundle or is a DLL that ships with every
   Windows 10 1809+ install. Catches the classic "works on my machine
   because I happen to have the VC++ redistributable installed" failure,
   without needing a clean VM.
3. Runs CritOverlay-console.exe --selftest:
   a. with a scrubbed environment (minimal PATH, no Python, no Qt vars), as
      on a fresh lab machine;
   b. with a polluted environment (QT_PLUGIN_PATH etc. pointing at junk,
      junk on PATH), as on a lab image with DCC apps installed;
   c. from a copy in a folder whose name has spaces and non-ASCII
      characters, as with many user profile names.
   Each run shows the overlay for a few seconds, visiting every monitor.

Exits non-zero if anything fails.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED = [
    "CritOverlay.exe",
    "CritOverlay-console.exe",
    "_internal/build_info.json",
    "_internal/PySide6/Qt6Core.dll",
    "_internal/PySide6/Qt6Gui.dll",
    "_internal/PySide6/Qt6Widgets.dll",
    "_internal/PySide6/plugins/platforms/qwindows.dll",
    "_internal/PySide6/plugins/imageformats/qico.dll",
    # MSVC runtime. Bundled so no redistributable install is needed.
    "_internal/VCRUNTIME140.dll",
    "_internal/VCRUNTIME140_1.dll",
    "_internal/PySide6/MSVCP140.dll",
]

MUST_NOT_SHIP = [
    "opengl32sw.dll",
    "qt6network.dll",
    "qtuiotouchplugin.dll",
    "qt6qml.dll",
    "qt6quick.dll",
]

# DLLs present on every supported Windows (10 1809+, 11; Home/Pro/
# Education/Enterprise/LTSC, including N editions). Anything imported that
# is neither bundled nor listed here fails the audit and needs a look.
OS_DLLS = {
    "advapi32.dll", "authz.dll", "bcrypt.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll", "d2d1.dll",
    "d3d11.dll", "d3d12.dll", "d3d9.dll", "dcomp.dll", "dnsapi.dll", "dwmapi.dll", "dwrite.dll", "dxgi.dll",
    "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "mpr.dll", "msvcrt.dll", "ncrypt.dll",
    "netapi32.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll", "powrprof.dll", "rpcrt4.dll", "secur32.dll",
    "setupapi.dll", "shcore.dll", "shell32.dll", "shlwapi.dll", "ucrtbase.dll", "uiautomationcore.dll",
    "user32.dll", "userenv.dll", "uxtheme.dll", "version.dll", "winhttp.dll", "winmm.dll", "winspool.drv",
    "ws2_32.dll", "wtsapi32.dll", "opengl32.dll", "glu32.dll", "dbghelp.dll", "psapi.dll", "wintrust.dll",
    "cfgmgr32.dll", "hid.dll", "propsys.dll", "oleacc.dll", "msimg32.dll", "usp10.dll", "windowscodecs.dll",
    "d3dcompiler_47.dll", "wininet.dll", "urlmon.dll", "sspicli.dll", "normaliz.dll", "credui.dll",
    "dxva2.dll", "mf.dll", "mfplat.dll", "mfreadwrite.dll", "evr.dll", "wevtapi.dll", "mswsock.dll",
}

# Redistributables that must be bundled, never assumed.
MUST_BE_BUNDLED_PREFIXES = ("msvcp140", "vcruntime140", "concrt140", "vcomp140", "python3", "libcrypto", "libssl",
                            "libffi", "qt6", "pyside6", "shiboken6")


def check_files(root, failures):
    interpreters = sorted(p.name for p in (root / "_internal").glob("python3*.dll") if p.name != "python3.dll")
    if not interpreters:
        failures.append("no bundled Python DLL (python3XY.dll) in _internal")
    else:
        print(f"  bundled interpreter: {', '.join(interpreters)}")
    for rel in REQUIRED:
        if not (root / rel).exists():
            failures.append(f"missing required file {rel}")
    shipped = {p.name.lower() for p in root.rglob("*") if p.is_file()}
    for name in MUST_NOT_SHIP:
        if name in shipped:
            failures.append(f"{name} should have been stripped (see _STRIP_BINARIES in the spec)")


def audit_imports(root, failures):
    try:
        import pefile
    except ImportError:
        failures.append("pefile not installed (it comes with PyInstaller); can't audit DLL imports")
        return
    bundled = {p.name.lower() for p in root.rglob("*") if p.is_file()}
    problems = {}
    count = 0
    for path in root.rglob("*"):
        if path.suffix.lower() not in (".exe", ".dll", ".pyd"):
            continue
        count += 1
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ])
        except Exception as exc:
            failures.append(f"couldn't parse {path.relative_to(root)}: {exc}")
            continue
        if pe.FILE_HEADER.Machine != 0x8664:
            failures.append(f"{path.relative_to(root)} is not x64 (machine {hex(pe.FILE_HEADER.Machine)})")
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for imp in getattr(pe, attr, []):
                dll = imp.dll.decode(errors="replace").lower()
                if dll in bundled or dll.startswith(("api-ms-win-", "ext-ms-")):
                    continue
                if dll in OS_DLLS and not dll.startswith(MUST_BE_BUNDLED_PREFIXES):
                    continue
                problems.setdefault(dll, set()).add(str(path.relative_to(root)))
        pe.close()
    for dll, users in sorted(problems.items()):
        failures.append(f"{dll} is imported but neither bundled nor part of Windows (by {sorted(users)[:3]})")
    print(f"  audited imports of {count} binaries")


def clean_env():
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    keep = ("SystemRoot", "windir", "SystemDrive", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
            "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH", "COMPUTERNAME", "ProgramData", "PUBLIC",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "ComSpec", "PATHEXT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = os.pathsep.join([rf"{sysroot}\System32", sysroot, rf"{sysroot}\System32\Wbem"])
    return env


def polluted_env(junk_dir):
    env = clean_env()
    env.update({
        "QT_PLUGIN_PATH": str(junk_dir),
        "QT_QPA_PLATFORM_PLUGIN_PATH": str(junk_dir),
        "QT_QPA_PLATFORM": "xcb",
        "QT_SCALE_FACTOR": "1.37",
        "QT_STYLE_OVERRIDE": "nonexistent",
        "PYTHONHOME": str(junk_dir),
        "PYTHONPATH": str(junk_dir),
    })
    env["PATH"] = str(junk_dir) + os.pathsep + env["PATH"]
    return env


def run_selftest(exe, env, label, failures, timeout=90):
    print(f"  self-test ({label})...", flush=True)
    try:
        proc = subprocess.run([str(exe), "--selftest"], env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        failures.append(f"self-test ({label}) timed out after {timeout}s")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith(("[FAIL]", "[WARN]", "Crit Overlay")) or "Full report" in line:
            print(f"    {line}")
    if proc.returncode != 0:
        failures.append(f"self-test ({label}) exited {proc.returncode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dist", type=Path)
    ap.add_argument("--no-run", action="store_true", help="static checks only (no window shown)")
    args = ap.parse_args()
    root = args.dist.resolve()
    failures = []

    print(f"Verifying {root}")
    check_files(root, failures)
    audit_imports(root, failures)

    if not args.no_run and not failures:
        exe = root / "CritOverlay-console.exe"
        run_selftest(exe, clean_env(), "clean environment", failures)
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "junk"
            junk.mkdir()
            # A fake, broken "Qt platform plugin" and Python DLL where a
            # careless loader would find them first.
            (junk / "platforms").mkdir()
            (junk / "platforms" / "qwindows.dll").write_bytes(b"MZ not really a dll")
            for dll in (root / "_internal").glob("python3*.dll"):
                (junk / dll.name).write_bytes(b"MZ not really a dll")
            run_selftest(exe, polluted_env(junk), "polluted Qt/Python environment", failures)

            odd = Path(tmp) / "Crit Övërlay tëst (ünïcode)"
            shutil.copytree(root, odd)
            run_selftest(odd / "CritOverlay-console.exe", clean_env(), "non-ASCII path with spaces", failures)

    if failures:
        print("\nVERIFY FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nVerify OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
