from __future__ import annotations

import sys


def knowledge_api_main(argv: list[str] | None = None) -> int:
    from ..cli.main import main

    argv = list(argv) if argv is not None else sys.argv[1:]
    return main(["knowledge-api", *argv])

