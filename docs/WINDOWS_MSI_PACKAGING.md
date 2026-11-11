# Windows packaging (NSIS and MSI)

**Verification status: see IMPLEMENTATION_PROGRESS.md**

Neither installer is claimed here to have been built or tested. This page documents `installer/electron-builder.yml` and the npm scripts that use it.

## Commands

| Command | Target | Output |
|---|---|---|
| `npm run package:win` | `electron-builder --win nsis --config installer/electron-builder.yml` | `dist-installer/Jarvis-Setup-<version>.exe` |
| `npm run package:msi` | `electron-builder --win msi --config installer/electron-builder.yml` | `dist-installer/Jarvis-<version>.msi` |

Both run `npm run build` first. `<version>` comes from `package.json` (currently `0.1.0`). `win.target` lists only `nsis`; the `--win msi` flag on the command line selects the MSI target.

## Common settings

| Key | Value |
|---|---|
| `appId` | `com.escan.jarvis` (also set as the AppUserModelID in `main.ts`) |
| `productName` | `Jarvis`; `win.executableName: Jarvis` |
| `directories.output` | `dist-installer` |
| `directories.buildResources` | `resources/icons` |
| `win.icon` | `resources/icons/icon.ico` |
| `asar` | `true` |
| `extraResources` | `build/backend/jarvis-backend` → `backend`, `resources/models` → `models`, `resources/icons` → `icons` |
| `npmRebuild` | `false` |

## NSIS (`nsis:`)

| Option | Value | Effect |
|---|---|---|
| `oneClick` | `false` | Assisted installer with pages |
| `perMachine` | `false` | Per-user by default, **no admin rights**. The assisted installer shows an install-mode page |
| `allowElevation` | `true` | The user may choose an all-users install, which elevates |
| `allowToChangeInstallationDirectory` | `true` | |
| `createDesktopShortcut` / `createStartMenuShortcut` | `true` / `true`; `shortcutName: Jarvis` | |
| `deleteAppDataOnUninstall` | `false` | `%APPDATA%\Jarvis` is kept on uninstall and upgrade |
| `installerIcon` / `uninstallerIcon` | `resources/icons/icon.ico` | |
| `runAfterFinish` | `true` | |
| artifact name | `Jarvis-Setup-${version}.${ext}` (from `win.artifactName`) | |

## MSI (`msi:`)

electron-builder's `msi` target is **WiX-based**. electron-builder downloads the WiX toolset itself; no separate WiX install is needed.

| Option | Value |
|---|---|
| `artifactName` | `Jarvis-${version}.${ext}` → `Jarvis-<version>.msi` |
| `perMachine` | `false` (per-user) |
| `oneClick` | `true` |
| `createDesktopShortcut` / `createStartMenuShortcut` | `true` / `true`; `shortcutName: Jarvis` |
| `upgradeCode` | `6f1c2b7e-3c55-4a8e-9a43-6a3c1f2e9d10` |

### Upgrades

The `upgradeCode` is a constant. Windows Installer uses it to recognise every Jarvis MSI as the same product family, so installing a newer version performs a **major upgrade**: the older version is removed and the new one installed. Rules for maintainers:

- **Never change `upgradeCode`.** A new value makes Windows treat the next MSI as an unrelated product, and both would end up installed side by side.
- **Bump `package.json` `version`** for every release. MSI version comparison is based on the version number, so rebuilding with the same version will not upgrade.
- The NSIS and MSI installers are separate installation mechanisms. Upgrade with the same installer type you installed with.

User data in `%APPDATA%\Jarvis` and the Credential Manager entries are not part of the MSI's components, so upgrades and uninstalls leave them in place.

## Code signing

**Not configured.** There is no certificate or signing configuration in `electron-builder.yml`, so the installers and `Jarvis.exe` / `jarvis-backend.exe` are unsigned. Expect SmartScreen "unknown publisher" prompts. To add signing, configure electron-builder's Windows signing options (certificate or a signing service) and sign `jarvis-backend.exe` as well. This is listed in [ROADMAP.md](ROADMAP.md).

## Checklist before packaging

1. `npm install` and `npm run setup` (venv and models).
2. Bump `version` in `package.json`. `backend/core/app.py` `VERSION` is a separate constant and must be kept in sync by hand.
3. `npm test`, then `npm run test:e2e`.
4. `npm run package:win` and/or `npm run package:msi`.
