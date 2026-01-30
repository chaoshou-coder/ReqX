from __future__ import annotations

from .cli.main import main

__all__ = ["main"]


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
