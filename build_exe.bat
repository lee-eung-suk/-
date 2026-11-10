@echo off
rem 학교 위젯을 단일 실행 파일(dist\SchoolWidget.exe)로 만듭니다.
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name SchoolWidget school_widget.py || goto :err
echo.
echo 완료! dist\SchoolWidget.exe 를 원하는 폴더에 복사해서 실행하세요.
pause
exit /b 0
:err
echo 빌드에 실패했습니다. Python 과 인터넷 연결을 확인하세요.
pause
exit /b 1
