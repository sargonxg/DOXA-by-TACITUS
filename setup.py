"""Bundle an already-built UI in release wheels; never fetch Node or models."""
from pathlib import Path
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWorkbench(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent / "frontend" / "dist"
        if not (source / "index.html").is_file():
            if os.environ.get("DOXA_REQUIRE_FRONTEND") == "1":
                raise RuntimeError("Build the frontend before building a release wheel")
            return
        destination = Path(self.build_lib) / "doxa" / "static"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)


setup(cmdclass={"build_py": BuildWorkbench})
