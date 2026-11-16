"""Resource-bounded parser subprocess; never interprets source instructions."""
import json
import os
import sys
from pathlib import Path


def main():
    if sys.platform != "win32":
        import resource
        cap = int(os.environ.get("DOXA_PARSER_MEMORY_MB", "4096")) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        resource.setrlimit(resource.RLIMIT_CPU, (170, 175))
    from doxa.extraction import extract_document
    result = extract_document(Path(sys.argv[1]), filename=sys.argv[3])
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
