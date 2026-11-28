# Security policy

Crit Overlay runs on lab and classroom workstations as a standard user. Security reports are welcome.

## Supported versions

Fixes go into the latest stable release and the current release candidate. Older versions get no backported fixes; download the newest build from the [releases page](https://github.com/vult-systems/crit-overlay/releases).

## Reporting a vulnerability

Report privately through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**. Don't open a public issue, pull request or discussion for a vulnerability.

Include the version (or commit), what's affected, how to reproduce it, and the impact. Leave out real credentials and personal data, and don't attach screenshots or logs from other people's sessions.

You'll get an acknowledgement within 7 days. Once a fix is ready, it ships as a release candidate and the advisory is published, with credit if you'd like it.

## What counts

In scope:
- Ways to make the app, `launcher.py` or the shipped `.bat` files load or run code from outside the app folder.
- Writes outside the documented locations (`%APPDATA%\CritOverlay`, `%LOCALAPPDATA%\CritOverlay`, `Pictures\CritOverlay`, `%TEMP%`, the Startup-folder shortcut, or `CritOverlay-data\` in portable mode), registry writes, network access, or anything that needs elevation.
- Release zips whose contents don't match `SHA256SUMS.txt` or the CI build.
- Secrets or personal data committed to the repository.

Out of scope: vulnerabilities in Qt, PySide6, Python or Windows themselves (report those to their maintainers), and changes you make in your own fork. The release executables aren't code-signed yet (#6); that's a known limitation.

## Safeguards built in

- Standard user only: no installer, no administrator rights, no registry writes and no network access. Start with Windows is a shortcut in the user's Startup folder.
- Release builds come from CI, built from the tagged commit with a hash-locked toolchain, and ship with `SHA256SUMS.txt`. Locally built zips are never uploaded.
