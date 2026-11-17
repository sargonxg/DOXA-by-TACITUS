import argparse
import os
from pathlib import Path
import stat


def load_private_environment(filename):
    """Load explicitly selected operator secrets before importing the API."""
    path = Path(filename).expanduser().resolve(strict=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("The pilot environment must be a private regular file (mode 0600)")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("The pilot environment must belong to the current operator")
    from dotenv import load_dotenv
    load_dotenv(path, override=True)


def main():
    parser = argparse.ArgumentParser(description="DOXA by TACITUS research workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--env-file", help="Explicit private pilot.env file; never served to the browser")
    args = parser.parse_args()
    if args.env_file:
        try:
            load_private_environment(args.env_file)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    os.environ["DOXA_DATA_DIR"] = args.data_dir
    import uvicorn
    uvicorn.run("doxa.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
