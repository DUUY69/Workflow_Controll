@echo off
setlocal

REM Create virtual environment if not exists
if not exist .venv (
	python -m venv .venv
)

call .venv\Scripts\activate.bat

pip install --upgrade pip >NUL
pip install -r requirements.txt

REM Start server
python server.py
