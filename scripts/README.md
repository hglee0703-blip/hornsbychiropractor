# Daily Blog Automation

매일 시드니 시간 오후 12시 30분(UTC 02:30)에 GitHub Actions가 OpenAI로 블로그 글과
손그림 2D 애니메이션풍 삽화 2장을 만들어 hornsbychiropractor.com에 자동 발행합니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `.github/workflows/daily-blog.yml` | 매일 cron 실행 + 수동 실행용 workflow_dispatch |
| `scripts/generate_blog.py` | 전체 파이프라인 (주제 선정 → 글 생성 → 이미지 → 발행 → 텔레그램 알림) |
| `scripts/requirements.txt` | Python 의존성 (`requests`, `tzdata`) |

## 동작 방식

1. **주제 선정** — workflow 입력에 topic이 없으면 기존 `blog/` 폴더 목록을 읽어
   중복되지 않는 새 주제를 OpenAI에게 추천받습니다.
2. **글 생성** — `gpt-5.6`이 900~1400단어 영문 글을 JSON으로 반환합니다. AI 상투어,
   과장된 공감 문구, 꾸며낸 환자 사례와 임상 경험, 지나치게 정돈된 문장을 금지하며 발행 전
   금칙어·분량 검사를 통과해야 합니다. 의학적 사실에는 신뢰 가능한 출처 링크가 필요합니다.
3. **이미지** — `gpt-image-2`가 글 내용에 맞는 서로 다른 손그림 2D 편집 삽화 2장을 만들고
   `assets/blog-images/{slug}-illustration-{n}.png`에 저장합니다. 광택 있는 3D 렌더링,
   부자연스러운 신체, 글자·로고·워터마크·과장된 통증 효과를 프롬프트에서 금지합니다.
4. **발행** — `blog/{slug}/index.html` 생성(기존 포스트의 헤더/nav/모바일메뉴/footer 마크업 재사용),
   `blog/index.html` 목록 맨 앞에 새 카드 삽입, `sitemap.xml` 갱신(없으면 생성).
5. **알림** — 성공/실패 리포트를 Telegram으로 전송합니다.

## GitHub Secrets 설정

저장소 Settings → Secrets and variables → Actions → New repository secret:

| Secret 이름 | 값 |
|---|---|
| `HORNSBYCHIROPRACTORBLOGPOSTANDIMAGE` | OpenAI Platform에서 발급한 API 키 |
| `TELEGRAM_BOT_TOKEN` | BotFather에게 받은 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 채팅 ID |

## 수동 실행 (workflow_dispatch)

GitHub 저장소 → Actions 탭 → "Daily blog post" → Run workflow:

- **topic** (선택): 비우면 AI가 새 주제를 선정합니다.
- **force** (기본 true): 유사 주제가 있어도 강제 발행.

CLI로도 가능: `gh workflow run daily-blog.yml -f topic="best desk setup for neck pain"`

## 모델 교체

기본값은 글 `gpt-5.6`, 이미지 `gpt-image-2`입니다. 바꾸려면:

- 저장소 Settings → Secrets and variables → Actions → **Variables** 탭에
  `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_SIZE`,
  `OPENAI_IMAGE_QUALITY` 변수를 추가하면 워크플로우가 그 값을 사용합니다.
- 또는 `.github/workflows/daily-blog.yml`의 기본값을 수정합니다.

## 로컬 테스트 (네트워크 호출 없음)

```bash
pip install -r scripts/requirements.txt
python scripts/generate_blog.py --dry-run
```

샘플 데이터로 템플릿 조립·파일 쓰기·sitemap 갱신 로직을 검증하고,
검증 후 생성된 임시 파일은 자동 삭제됩니다.

실제 발행 로컬 테스트:

```bash
export OPENAI_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python scripts/generate_blog.py
```

## 주의

- `worker.js`, `index.html`(홈), 기존 서비스 페이지 등 사이트 코드는 이 자동화가 절대 수정하지 않습니다.
  변경되는 파일: `blog/{new-slug}/`, `blog/index.html`, `sitemap.xml`, `assets/blog-images/`.
