"""Resource-bounded parser subprocess; never interprets source instructions."""
import json
import os
import sys
from pathlib import Path


def _cap_resource(resource, kind, soft, hard):
    inherited_soft, inherited_hard = resource.getrlimit(kind)
    if inherited_soft != resource.RLIM_INFINITY:
        soft = min(soft, inherited_soft)
    if inherited_hard != resource.RLIM_INFINITY:
        hard = min(hard, inherited_hard)
    resource.setrlimit(kind, (min(soft, hard), hard))


def main():
    if sys.platform != "win32":
        import resource
        # macOS can reject a ceiling below the worker's reserved address space.
        if sys.platform != "darwin":
            cap = int(os.environ.get("DOXA_PARSER_MEMORY_MB", "4096")) * 1024 * 1024
            _cap_resource(resource, resource.RLIMIT_AS, cap, cap)
        _cap_resource(resource, resource.RLIMIT_CPU, 170, 175)
    from doxa.extraction import extract_document
    result = extract_document(Path(sys.argv[1]), filename=sys.argv[3])
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
