#!/bin/bash
# Запуск программы «Остатки склада»
cd "$(dirname "$0")"

# Если DISPLAY не задан — подхватываем графическую сессию (Xwayland)
if [ -z "$DISPLAY" ]; then
    export DISPLAY=:0
fi
if [ -z "$XAUTHORITY" ]; then
    XAUTHORITY=$(pgrep -af Xwayland | grep -oE '\-auth [^ ]+' | head -1 | awk '{print $2}')
    if [ -n "$XAUTHORITY" ]; then
        export XAUTHORITY
    fi
fi

exec .venv314/bin/python app.py
