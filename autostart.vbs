' Streamlit 포트폴리오 대시보드를 콘솔창 안 보이게 백그라운드로 실행한다.
' Windows 시작 시 자동 실행되도록 Startup 폴더에 이 파일을 복사해서 사용.
'   복사 위치: C:\Users\ksw96\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
'
' 동작:
' - PC 켜지고 사용자 로그인하면 자동으로 실행됨
' - 콘솔 창은 안 뜨고 (0 = SW_HIDE), 백그라운드로만 동작
' - http://localhost:8765 가 항상 응답 → 언제든 접속 가능
'
' 끄려면 작업관리자에서 "python.exe" 또는 "streamlit" 프로세스 종료.

Set WshShell = CreateObject("WScript.Shell")
projectDir = "C:\Users\ksw96\Desktop\클로드 프로젝트\주식 포폴 플젝"
batPath = projectDir & "\autostart.bat"
WshShell.Run "cmd /c """ & batPath & """", 0, False
