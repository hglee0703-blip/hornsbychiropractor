# Blog Automation — Every 3 Days

GitHub Actions가 매일 발행 시점을 확인하고, 마지막 글로부터 3일이 지난 날에 OpenAI로 블로그 글을
작성하고 삽화 2장을 만들어 hornsbychiropractor.com에 자동 발행합니다. 이미지는 컴퓨터의
ComfyUI를 먼저 사용하며, 컴퓨터 꺼짐·절전·ComfyUI 미실행·GPU 사용 중·생성 오류·시간 초과 시
기존 OpenAI 이미지 API로 자동 전환합니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `.github/workflows/daily-blog.yml` | 매일 간격 확인 + 3일 간격 발행 + 수동 실행용 workflow_dispatch |
| `scripts/generate_blog.py` | 전체 파이프라인 (주제 선정 → 글 생성 → 이미지 → 발행 → 텔레그램 알림) |
| `scripts/blog_images.py` | ComfyUI 우선 호출, OpenAI fallback, 이미지 검증 및 압축 |
| `scripts/comfy_bridge.py` | 로컬 ComfyUI와 GitHub를 연결하는 인증된 이미지 생성 브리지 |
| `scripts/comfy_workflow.json` | 설치된 Z-Image Turbo용 API 워크플로우 (1152×768, 8 steps) |
| `scripts/setup_comfy_bridge.py` | Windows 로그인 자동 실행 및 GitHub 연결 설정 |
| `scripts/requirements.txt` | Python 의존성 (`requests`, `tzdata`, `Pillow`) |

## 동작 방식

1. **주제 선정** — workflow 입력에 topic이 없으면 기존 `blog/` 폴더 목록을 읽어
   중복되지 않는 새 주제를 OpenAI에게 추천받습니다.
2. **글 생성** — `gpt-5.6`이 1100~1450단어 영문 글을 JSON으로 반환합니다. 핵심 검색어를
   제목·짧은 URL·메타 설명·첫 문단·관련 H2에 자연스럽게 배치하고, 기존 글 전체에서 주제와
   가까운 내부 링크 후보를 골라 연결합니다. AI 상투어,
   과장된 공감 문구, 꾸며낸 환자 사례와 임상 경험, 지나치게 정돈된 문장을 금지하며 발행 전
   문체·키워드 배치·메타 길이·링크 검사를 통과해야 합니다. 의학적 사실에는 신뢰 가능한 출처
   링크가 필요합니다.
3. **이미지** — 인증된 브리지에서 로컬 ComfyUI로 글에 맞는 손그림 2D 삽화를 생성합니다.
   로컬 생성이 불가능하면 해당 이미지부터 `gpt-image-2`로 전환하며, 다음 블로그 글에서는
   ComfyUI를 다시 확인합니다. 브리지 설정이 없으면 기존처럼 OpenAI를 사용합니다.
   결과를 실제 이미지로 검증한 뒤 압축 WebP(quality 82)로
   `assets/blog-images/{slug}-illustration-{n}.webp`에 저장하며 실제 크기를 HTML에 기록합니다.
   광택 있는 3D 렌더링,
   부자연스러운 신체, 글자·로고·워터마크·과장된 통증 효과를 프롬프트에서 금지합니다.
4. **발행** — `blog/{slug}/index.html` 생성(기존 포스트의 헤더/nav/모바일메뉴/footer 마크업 재사용),
   관련 글 3개 연결, `blog/index.html` 목록과 `Blog` 구조화 데이터 갱신, `sitemap.xml` 갱신
   (없으면 생성).
5. **알림** — 성공/실패 리포트를 Telegram으로 전송합니다.

## GitHub Secrets 설정

저장소 Settings → Secrets and variables → Actions → New repository secret:

| Secret 이름 | 값 |
|---|---|
| `HORNSBYCHIROPRACTORBLOGPOSTANDIMAGE` | OpenAI Platform에서 발급한 API 키 |
| `TELEGRAM_BOT_TOKEN` | BotFather에게 받은 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 채팅 ID |
| `COMFYUI_BRIDGE_URL` | 로컬 브리지의 HTTPS 주소 (설치 프로그램이 자동 갱신) |
| `COMFYUI_BRIDGE_TOKEN` | 브리지 인증 토큰 (설치 프로그램이 생성·등록) |

## ComfyUI 연결 (Windows, 최초 한 번)

ComfyUI, 설치된 Z-Image Turbo 모델 3개(`z_image_turbo_bf16.safetensors`,
`qwen_3_4b.safetensors`, `ae.safetensors`), Python, 로그인된 GitHub CLI(`gh`),
`cloudflared`가 필요합니다. ComfyUI는 `http://127.0.0.1:8188`에서 실행합니다.

```powershell
python -m pip install -r scripts/requirements.txt
python scripts/setup_comfy_bridge.py --repo hglee0703-blip/hornsbychiropractor
```

설치 프로그램은 `%LOCALAPPDATA%\HornsbyBlog\ComfyBridge`에 실행 파일·워크플로우·
개인 설정을 저장하고 현재 사용자와 SYSTEM만 해당 폴더에 접근하도록 권한을 설정합니다.
Windows 로그인 시 창 없이 브리지를 실행하고 GitHub Secrets를 등록합니다.
ComfyUI 자체는 자동으로 시작하지 않으므로 평소처럼 ComfyUI를 실행해 두면 됩니다.
포트가 다르면 `--comfy-url http://127.0.0.1:다른포트`로 설치합니다.

브리지는 ComfyUI 화면과 전체 API를 공개하지 않습니다. 토큰으로 인증된 블로그 이미지 요청만
고정 워크플로우로 처리합니다. GPU가 이미 다른 작업을 수행 중이면 OpenAI로 전환하며,
시간 초과 시 해당 블로그의 대기 작업만 제거합니다. 실행 중인 다른 작업은 중단하지 않습니다.
이미 실행 중인 블로그 작업은 GPU 실행이 끝날 수 있으나 그 결과를 발행하지 않습니다.

Cloudflare Quick Tunnel을 사용하므로 별도 도메인 설정 없이 연결됩니다. 주소는 재시작 시
바뀌며 브리지가 자동으로 GitHub Secret을 갱신합니다. Quick Tunnel은 가용성을 보장하지
않으므로 터널 장애도 OpenAI fallback으로 처리합니다.
[Cloudflare 문서](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

기본 생성 제한 시간은 이미지당 300초입니다. GitHub Actions **Variables**에서
`COMFYUI_GENERATION_TIMEOUT`을 1~900초로 변경할 수 있습니다. 연결 불가 확인은 최대
연결 5초·응답 10초를 사용하며, 첫 실패 이후 같은 글의 나머지 이미지는 OpenAI를 바로 사용합니다.
연결 상태와 오류 로그는 설치 폴더의 `bridge.log`에서 확인할 수 있습니다.

자동 실행을 해제하려면 PowerShell에서 다음을 실행하고, 실행 중인 브리지 프로세스를 종료합니다.

```powershell
Remove-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'HornsbyBlogComfyBridge'
```

GitHub의 `COMFYUI_BRIDGE_URL` Secret도 삭제하면 모든 이미지는 OpenAI를 사용합니다.
토큰이나 개인 설정 파일을 저장소에 올리지 마세요.

실제 이미지 API 비용 없이 성공·꺼진 컴퓨터·인증 오류·생성 실패·시간 초과·이미지 손상·
대체 API 실패·브리지 접근 제한을 확인하는 테스트:

```powershell
python -m unittest discover -s scripts -p test_blog_images.py -v
```

## 수동 실행 (workflow_dispatch)

GitHub 저장소 → Actions 탭 → "Blog post every 3 days" → Run workflow:

- **topic** (선택): 비우면 AI가 새 주제를 선정합니다.
- **force** (기본 true): 유사 주제가 있어도 강제 발행.

CLI로도 가능: `gh workflow run daily-blog.yml -f topic="best desk setup for neck pain"`

## 모델 교체

기본값은 글 `gpt-5.6`, 이미지 `gpt-image-2`입니다. 바꾸려면:

- 저장소 Settings → Secrets and variables → Actions → **Variables** 탭에
  `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_SIZE`, `OPENAI_IMAGE_QUALITY`,
  `OPENAI_IMAGE_FORMAT`, `OPENAI_IMAGE_COMPRESSION` 변수를 추가하면 워크플로우가 그 값을 사용합니다.
- 또는 `.github/workflows/daily-blog.yml`의 기본값을 수정합니다.

## 로컬 테스트 (네트워크 호출 없음)

```bash
pip install -r scripts/requirements.txt
python scripts/generate_blog.py --dry-run
```

샘플 데이터로 템플릿 조립·파일 쓰기·sitemap 갱신 로직을 검증하고,
검증 후 생성된 임시 파일은 자동 삭제됩니다.

기존 글의 관련 글 블록과 블로그 목록 구조화 데이터만 다시 계산하려면 API 키 없이 실행합니다:

```bash
python scripts/generate_blog.py --refresh-seo
```

실제 발행 로컬 테스트:

```bash
export OPENAI_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python scripts/generate_blog.py
```

## 주의

- `worker.js`, `index.html`(홈), 기존 서비스 페이지 등 사이트 코드는 이 자동화가 절대 수정하지 않습니다.
  변경되는 파일: `blog/{new-slug}/`, `blog/index.html`, `sitemap.xml`, `assets/blog-images/`.
