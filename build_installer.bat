@echo off
rem 설치 파일(dist_installer\SchoolWidget-Setup-버전.exe)을 만듭니다.
rem 필요: Python 3.9+  /  Inno Setup 6 (https://jrsoftware.org/isdl.php, 무료)
chcp 65001 >nul
cd /d "%~dp0"
set VERSION=%1
if "%VERSION%"=="" set VERSION=1.0.0

set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo Inno Setup 6 이 설치되어 있지 않습니다.
  echo https://jrsoftware.org/isdl.php 에서 설치한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --clean --onedir --windowed --name SchoolWidget --icon assets\icon.ico --distpath build_onedir school_widget.py || goto :err
"%ISCC%" /DMyAppVersion=%VERSION% installer\SchoolWidget.iss || goto :err
echo.
echo 완료! dist_installer 폴더의 SchoolWidget-Setup-%VERSION%.exe 를 실행해서 설치하세요.
pause
exit /b 0
:err
echo 빌드에 실패했습니다.
pause
exit /b 1
