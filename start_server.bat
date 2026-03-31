@echo off
chcp 65001 > nul
title PS Court 스트리밍 서버

echo.
echo  =============================================
echo    PS Court 자동 스트리밍 서버 시작 중...
echo  =============================================
echo.

:: Python 확인
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo  [오류] Python이 설치되지 않았어요.
    echo  https://python.org 에서 다운로드 후 다시 시도하세요.
    pause
    exit /b 1
)

:: 의존성 설치 확인
python -c "import flask" >nul 2>nul
if %errorlevel% neq 0 (
    echo  패키지 설치 중...
    pip install -r requirements.txt
    echo.
)

:: client_secrets.json 확인
if not exist "client_secrets.json" (
    echo  [안내] client_secrets.json 파일이 없어요.
    echo.
    echo  Google Cloud Console에서:
    echo    1. YouTube Data API v3 활성화
    echo    2. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
    echo    3. JSON 다운로드 → client_secrets.json 으로 저장
    echo.
    pause
    exit /b 1
)

:: 서버 실행
python stream_server.py
pause
