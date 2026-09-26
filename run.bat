@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Первый запуск: готовлю окружение, это займёт несколько минут...
  where python >nul 2>nul || (
    echo Не найден Python. Установите Python 3.11 или новее с python.org и запустите снова.
    pause
    exit /b 1
  )
  python -m venv .venv || (pause ^& exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause ^& exit /b 1)
)
start "" ".venv\Scripts\pythonw.exe" -m gantt %*
