#!/usr/bin/env python3
"""Build the local hybrid RAG index.

Usage:
    python scripts/build_rag_index.py
    python scripts/build_rag_index.py --no-reset
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.rag import RAGIndexBuilder, get_index_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Build hybrid RAG index")
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not delete existing DB before build",
    )
    args = parser.parse_args()

    builder = RAGIndexBuilder()
    stats = builder.build(reset=not args.no_reset)

    print("RAG index build complete")
    print(f"  db_path: {stats.get('db_path')}")
    print(f"  documents: {stats.get('documents')}")
    print(f"  chunks: {stats.get('chunks')}")
    print(f"  nodes: {stats.get('nodes')}")
    print(f"  edges: {stats.get('edges')}")

    status = get_index_status()
    print("Current status:")
    print(f"  exists: {status.get('exists')}")
    print(f"  built_at: {status.get('built_at')}")


if __name__ == "__main__":
    main()
