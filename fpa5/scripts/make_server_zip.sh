#!/usr/bin/env bash
set -euo pipefail

# Run from fpa5 root directory.
if [ ! -f "requirements.txt" ] || [ ! -f "package.json" ] || [ ! -d "scripts" ]; then
  echo "请在 fpa5 根目录运行本脚本。"
  exit 1
fi

OUT=${1:-/mnt/fpa5_upload.zip}
EXCLUDE_FILE=${2:-server_upload_exclude.txt}

if [ ! -f "$EXCLUDE_FILE" ]; then
  cat > "$EXCLUDE_FILE" <<'EXC'
node_modules/*
artifacts/*
cache/*
outputs/*
data/*
.venv/*
venv/*
env/*
__pycache__/*
*/__pycache__/*
.pytest_cache/*
.git/*
.vscode/*
.idea/*
*.zip
*.tar
*.tar.gz
*.7z
*.rar
*.log
*.tmp
*.bak
.DS_Store
Thumbs.db
configs/_archive_old_configs/*
EXC
fi

rm -f "$OUT"
zip -r "$OUT" . -x@"$EXCLUDE_FILE"
echo "已生成：$OUT"
echo "服务器解压后需执行：pip install -r requirements.txt && npm install"
