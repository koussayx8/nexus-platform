"""Entry point: load config and the baked index, then serve. Not ready (exit 1) if the index fails."""

import sys

from . import config
from .retrieval import Retriever
from .service import Reasoner, serve


def main() -> int:
    cfg = config.load()
    try:
        retriever = Retriever.load(cfg.index_path, cfg.score_floor)
    except (OSError, ValueError) as e:
        print(f"index not loaded: {e}", file=sys.stderr)
        return 1
    print(f"nexus-reasoner ready, corpus hash {retriever.corpus_hash}", file=sys.stderr)
    serve(Reasoner(cfg, retriever)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
