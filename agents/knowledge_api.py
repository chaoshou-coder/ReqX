from __future__ import annotations

import argparse
import sys

from .api.knowledge_http_api import KnowledgeHttpApi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reqx-knowledge-api")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--knowledge", dest="knowledge_path", default=None)
    parser.add_argument("--base-dir", default=None)
    parser.add_argument("--token-env", default="REQX_KNOWLEDGE_API_TOKEN")
    parser.add_argument("--token", default=None)
    parser.add_argument("--max-body-bytes", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args(argv)

    api = KnowledgeHttpApi(
        bind=args.bind,
        port=args.port,
        base_dir=args.base_dir,
        default_knowledge_path=args.knowledge_path,
        token_env=args.token_env,
        token_value=args.token,
        max_body_bytes=args.max_body_bytes,
    )
    sys.stderr.write(f"Knowledge API listening on http://{args.bind}:{args.port}\n")
    api.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

