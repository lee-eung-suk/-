@echo off
rem 콘솔 창 없이 학교 위젯 실행
chcp 65001 >nul
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0school_widget.py"
) else (
  where py >nul 2>nul && (start "" pyw "%~dp0school_widget.py") || (
    echo Python 이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치하세요.
    echo 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
  )
)
