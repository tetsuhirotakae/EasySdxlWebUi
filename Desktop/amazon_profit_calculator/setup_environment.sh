#!/bin/bash

# プロジェクトディレクトリに移動
cd "$(dirname "$0")"

# 仮想環境が存在しない場合は作成
if [ ! -d "amazon_profit_env" ]; then
    echo "Creating virtual environment..."
    python3 -m venv amazon_profit_env
fi

# 仮想環境を活性化
source amazon_profit_env/bin/activate

# 依存関係をインストール
echo "Installing dependencies..."
pip install -r requirements.txt

# 環境変数を設定
set -a
source config.env
set +a

# 必要なディレクトリとファイルを作成
mkdir -p src tests
touch src/__init__.py tests/__init__.py

echo "Environment setup complete."
echo "To activate the virtual environment, run: source amazon_profit_env/bin/activate"