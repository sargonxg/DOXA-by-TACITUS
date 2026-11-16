import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="DOXA by TACITUS research workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    os.environ["DOXA_DATA_DIR"] = args.data_dir
    import uvicorn
    uvicorn.run("doxa.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
