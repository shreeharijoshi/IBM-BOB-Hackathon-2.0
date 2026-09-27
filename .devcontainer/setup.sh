#!/usr/bin/env bash
set -e

echo "=== Setting up C++ Compiler Diagnostic Helper in Codespaces ==="

# 1. Install Python dependencies
echo "--> Installing Python requirements..."
pip3 install --user -r requirements.txt || pip install -r requirements.txt

# 2. Build VS Code extension
echo "--> Building VS Code extension..."
cd vscode-extension
npm install
npm run compile
npx @vscode/vsce package --no-dependencies
cd ..

# 3. Install the extension in Codespaces
echo "--> Installing extension into VS Code..."
code --install-extension vscode-extension/cpp-diagnostic-*.vsix --force || true

# 4. Start backend API in the background (optional daemon)
echo "--> Starting local backend server on port 8000..."
nohup python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > /tmp/backend.log 2>&1 &

echo "=== Setup complete! Open any file in examples/ to test diagnostics. ==="
