"""Название и пути к ресурсам в исходниках и в сборке PyInstaller."""

from pathlib import Path


APP_NAME = "sekret"
ASSETS = Path(__file__).resolve().parent / "assets"
APP_ICON = ASSETS / "app_icon.png"
HEADER_LOGO = ASSETS / "logo_header.png"
