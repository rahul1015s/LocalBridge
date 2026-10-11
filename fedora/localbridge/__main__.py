"""Entry point: picks the best UI for this machine (GTK4 on Linux, Tk elsewhere, CLI anywhere)."""

import sys


def _gtk() -> int | None:
    try:
        from .app import main as gtk_main
    except (ImportError, ValueError):  # no PyGObject / GTK4 / Libadwaita
        return None
    return gtk_main()


def _tk() -> int | None:
    try:
        from .ui_tk import main as tk_main
    except ImportError:  # tkinter not installed
        return None
    return tk_main()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in args:
        args.remove("--cli")
        from .cli import main as cli_main

        return cli_main(args)
    order = {"--gtk": [_gtk], "--tk": [_tk]}.get(args[0] if args else "", None)
    if order is None:
        order = [_gtk, _tk] if sys.platform.startswith("linux") else [_tk, _gtk]
    for ui in order:
        rc = ui()
        if rc is not None:
            return rc
    print(
        "No graphical toolkit available. Use the terminal receiver: python -m localbridge --cli\n"
        "(Linux GUI: install python3-gobject gtk4 libadwaita · Windows/macOS GUI needs tkinter.)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
