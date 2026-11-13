@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto nopy
echo [1/2] 빌드 도구 설치 중...
%PY% -m pip install --disable-pip-version-check --upgrade pyinstaller > build_log.txt 2>&1
echo [2/2] exe 만드는 중... 1~3분 걸려요
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name LeuniTalk --icon assets\leuni.ico --add-data "assets\leuni.ico;." --add-data "addon;addon" --add-data "extension;extension" app\app.py >> build_log.txt 2>&1
if exist "dist\LeuniTalk.exe" goto ok
echo 빌드 실패. build_log.txt 를 확인해 주세요.
pause
exit /b
:ok
echo 완료: dist\LeuniTalk.exe
explorer dist
pause
exit /b
:nopy
echo 파이썬이 없어요.
pause
