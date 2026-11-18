#!/bin/sh
# Syntax and provider-schema checks only. This command never provisions resources.
set -eu
cd "$(dirname "$0")"
terraform fmt -check -diff
terraform init -backend=false -input=false -no-color
terraform validate -no-color
python3 -c 'import ast,pathlib;ast.parse(pathlib.Path("cloud_store.py").read_text())'
bash -n backup.sh restore.sh startup.sh.tftpl
