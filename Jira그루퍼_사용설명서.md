# Jira JQL 그루퍼 — 사용 설명서

Jira JQL로 조회한 이슈를 담당자별로 그룹핑해서 Slack DM으로 전송하는 툴.

---

## 파일 구성

| 파일 | 설명 |
|------|------|
| `jira_grouper_gui.py` | 소스 코드 (Python 있는 환경에서 실행) |
| `Jira그루퍼.exe` | 단독 실행 파일 (Python 불필요) |
| `jira_grouper_config.json` | 설정 저장 파일 (첫 실행 후 자동 생성) |

> **다른 사람에게 배포할 때**: `Jira그루퍼.exe`만 전달.  
> `jira_grouper_config.json`은 각자 환경에서 새로 세팅되므로 같이 주지 않아도 됨.

---

## 실행 방법

### exe 사용 (권장)
`Jira그루퍼.exe` 더블클릭.  
작업표시줄 고정: exe 우클릭 → **작업표시줄에 고정**

> `jira_grouper_config.json`은 exe와 **같은 폴더**에 자동 생성됨.  
> exe를 옮길 때 config도 같이 옮기면 설정 유지됨.

### Python으로 실행
```
python jira_grouper_gui.py
```
추가 패키지 설치 불필요 (표준 라이브러리만 사용).

---

## exe 빌드 방법

소스를 수정했거나 exe를 새로 만들어야 할 때.

**1. PyInstaller 설치** (최초 1회)
```
python -m pip install pyinstaller
```

**2. py 파일이 있는 폴더로 이동**
```
cd C:\Users\SUNGKWANGMOON\Downloads
```

**3. 빌드 실행**
```
python -m PyInstaller --onefile --windowed --name "Jira그루퍼" jira_grouper_gui.py
```
1~2분 소요.

**4. 빌드 결과물**
```
Downloads\
  ├── jira_grouper_gui.py
  ├── dist\
  │     └── Jira그루퍼.exe   ← 최종 실행 파일
  ├── build\                  ← 삭제해도 됨
  └── Jira그루퍼.spec         ← 삭제해도 됨
```
`dist\Jira그루퍼.exe`를 원하는 위치로 복사해서 사용.

---

## 초기 설정

### 1. Jira 인증 정보 (조회 탭 → 저장)

| 항목 | 내용 |
|------|------|
| 도메인 | `kongstudios.atlassian.net` |
| 이메일 | Jira 로그인 이메일 |
| API 토큰 | 아래 방법으로 발급 |

**API 토큰 발급:**
1. https://id.atlassian.com/manage-profile/security/api-tokens 접속
2. **API 토큰 만들기** 클릭 → 이름 입력 후 생성
3. 토큰 복사 → 앱 입력란에 붙여넣고 **저장**

---

### 2. Slack 전송 설정 (설정 탭 → 저장)

| 항목 | 내용 |
|------|------|
| Bot Token | `xoxb-` 로 시작하는 토큰 |
| 내 User ID | `U` 로 시작하는 ID |

**Bot Token 발급:**
1. https://api.slack.com/apps 접속
2. **Create New App** → **From scratch**
3. **OAuth & Permissions** → **Scopes** → `chat:write` 추가
4. **Install to Workspace** → **Bot User OAuth Token** 복사

**내 User ID 확인:**
Slack 앱에서 본인 프로필 클릭 → **멤버 ID 복사** (`U012AB3CD` 형태)

---

## 사용 방법

### 조회 및 전송

1. **조회 탭** → JQL 입력
   ```
   project = "ZR" AND status != Done AND assignee is not EMPTY ORDER BY assignee ASC
   ```
2. `Ctrl+Enter` 또는 **▶ 조회** 클릭
3. 결과 확인 후 **✈ Slack 전송** → 내 DM으로 전송

### 출력 형식 선택
| 형식 | 용도 |
|------|------|
| Slack mrkdwn | Slack 전송용 (불렛 리스트 + 링크 + 멘션) |
| 일반 텍스트 | 문서 붙여넣기용 (URL 텍스트 노출) |

---

## 멘션 설정

조회 실행 시 담당자가 **멘션 설정 탭**에 자동 추가됨.

1. **멘션 설정 탭** 이동
2. 각 담당자 오른쪽 Slack User ID 칸에 입력
   - 해당 담당자 Slack 프로필 → **멤버 ID 복사**
3. **저장** 클릭

| 상태 | 전송 시 표시 |
|------|------------|
| UID 입력됨 | `@멘션` (이름 대체) |
| UID 없음 | 평문 이름 |

---

## 전송 결과 예시

```
@강유준
  • ZR-1234: 소환 화면 내 확률 버튼이 미동작하는 이슈
  • ZR-1235: 인벤토리 내 버리기 버튼이 비활성화 상태로 노출되는 이슈

박상현
  • ZR-1236: 영웅 소환 버튼 2종 클릭 시 알림 팝업 노출되는 이슈
```

이슈 키(`ZR-1234`)에 Jira 링크가 걸려 Slack에서 바로 클릭 가능.

---

## 설정 파일

`jira_grouper_config.json` — exe/py와 **같은 폴더**에 자동 생성

```json
{
  "domain": "kongstudios.atlassian.net",
  "email": "your@email.com",
  "api_token": "...",
  "slack_bot_token": "xoxb-...",
  "slack_my_uid": "U012AB3CD",
  "slack_mentions": {
    "강유준 (Yoojun Kang)": "U0306TMUUJV"
  }
}
```

> 인증 정보가 저장되므로 외부 공유 금지.
