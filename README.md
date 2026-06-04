# ax-os-icons — courier branch (transient)

Not part of Axiom. Branded AX OS app icons + bundle config. Delete after transfer.

Fixes a real scaffold bug: the committed desktop app referenced
src-tauri/icons/{...} that were never shipped, so tauri-build aborted.

NEW:
- `desktop/src-tauri/icons/` — branded AX OS mark (teal→violet tile, white "A"):
  app-icon.png (1024 source), icon.png, 32x32, 128x128, 128x128@2x, the
  Windows Square*/StoreLogo set, multi-res icon.ico, and icon.icns.
UPDATE:
- `desktop/src-tauri/tauri.conf.json` — bundle.active=true + the standard
  5-icon set (all now present), so `tauri dev` builds and `tauri build`
  produces installers.

Replace any placeholder icons with these. To rebrand: drop a 1024 logo and
run `npm run tauri icon path/to/logo.png`.
