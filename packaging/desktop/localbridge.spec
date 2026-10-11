# PyInstaller spec for the Windows/macOS desktop app (Tk GUI + CLI). NOT yet built or tested on
# those systems; the CI job in .github/workflows/ci.yml will be the first to try.
# Build:  pip install -e fedora pyinstaller && pyinstaller packaging/desktop/localbridge.spec
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["../../fedora/localbridge/__main__.py"],
    pathex=["../../fedora"],
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("zeroconf") + ["localbridge.ui_tk", "localbridge.cli"],
    excludes=["gi"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="LocalBridge", console=False, icon=None)
