# 르니톡 (LeuniTalk)

와우 포에버 안의 작은 채팅창에 입력한 말을, 지금 크롬에서 보고 있는 **숲(SOOP) 르니 방송 채팅**으로 보내 주는 팬 제작 도구예요.

## 설치
1. [최신 LeuniTalk.exe 받기](https://github.com/balohani/leunitalk/releases/latest/download/LeuniTalk.exe) → 실행
   - 와우 포에버 폴더를 찾아서 애드온을 자동 설치해요.
2. 처음 한 번 크롬 확장 설치 안내를 따라 주세요.
3. 와우를 다시 켜면 '르니톡' 창이 생겨요.

업데이트는 르니톡을 켤 때 자동으로 확인해요.

## 개발자용
- `app/` PC 프로그램 (와우 화면 신호 읽기 + 크롬 확장 중계)
- `addon/SoopBridge/` 와우 애드온
- `extension/` 크롬 확장
- 새 버전 배포: `VERSION` 파일 숫자를 올려서 main 에 push → GitHub Actions가 exe를 빌드해 릴리스(v버전)에 올림
