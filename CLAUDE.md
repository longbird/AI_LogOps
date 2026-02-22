# AI-LogOps Project Rules

## Project Structure

- `agent/` - 에이전트 (PyInstaller로 빌드, Windows 서비스로 실행)
- `server/` - 서버 (TCP + Dashboard + GUI)
- `shared/` - 공용 프로토콜/모델
- `deploy.py` - 빌드 + 배포 자동화

## Environment

- Python 3.13 (`audioop` 모듈 사용 불가)
- Server: 61.42.53.61 (TCP 9500, Dashboard 8080)
- Agent: PC-DAERIGO (D:\AirSoft\AILogOps-Agent)
- Agent DB: 127.0.0.1:56200/vrms2

---

## Deployment Rules

### 1. Agent Deployment (에이전트 배포)

**버전 관리:**
- 배포 전 반드시 `agent/__init__.py`의 `__version__` 을 올릴 것
- 현재 버전: 확인 후 +0.0.1 증가

**빌드 + 서버 경유 배포 (권장):**
```bash
python deploy.py --server http://61.42.53.61:8080 --agent-id PC-DAERIGO
```
- PyInstaller 빌드 → zip 압축 (config.yaml 제외) → HTTP API 업로드 → TCP로 에이전트 자동 전송
- auth_token: `server/config.yaml` → `connection.token` 또는 환경변수 `TCP_AUTH_TOKEN`

**빌드만 (서버 연결 불가 시):**
```bash
python deploy.py --skip-send
```
- 결과물: `dist/AILogOps-Agent.zip` (39MB~)

**GUI 배포 (서버 관리 프로그램):**
- `run_server_gui.py` 실행 → 배포 탭 → 에이전트 배포 섹션 → zip 파일 선택 → 배포 버튼

**절대 금지:**
- config.yaml을 zip에 포함하지 말 것 (사용자 설정 덮어쓰기 방지)
- 수동 압축 해제 시에도 config.yaml 을 덮어쓰면 안 됨

### 2. Recording Client Deployment (녹취 클라이언트 배포)

**GUI 배포:**
- `run_server_gui.py` 실행 → 배포 탭 → 녹취 클라이언트 배포 섹션
- target = `rec_client` → 서버 API `/api/deploy/upload` 에 `target=rec_client` 으로 업로드

**deploy.py CLI:**
- 현재 녹취 클라이언트 전용 CLI 옵션 없음 → GUI 배포 탭 사용

### 3. Process Deployment (모니터링 프로세스 배포)

```bash
python deploy.py --target process --process-dir "D:\Work\Setup\AirREC\Server" --server http://61.42.53.61:8080
```
- 프로세스 소스 디렉토리를 zip 압축 → 서버 경유 → 에이전트의 ProcessDeployer가 실행

---

## Configuration Rules

- `server/config.yaml` 에 절대 실제 API 키 하드코딩 금지 → `.env` 또는 환경변수 사용
- `agent/config.yaml` 은 배포 zip에 포함하지 않음 (사용자 설정 보호)
- 에이전트의 `recording.enabled` 는 기본적으로 `false`

## Code Rules

- CPU-intensive 작업은 `asyncio.to_thread()` 사용
- Type error suppression (`as any`, `@ts-ignore`) 사용 금지
- 버그 수정 시 최소한으로 수정, 리팩토링 병행 금지
