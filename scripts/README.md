# Daily Blog Automation

매일 시드니 시간 오전 9시(UTC 23:00)에 GitHub Actions가 Gemini로 블로그 글을 쓰고,
Pollinations.ai 이미지를 붙여서 hornsbychiropractor.com에 자동 발행합니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `.github/workflows/daily-blog.yml` | 매일 cron 실행 + 수동 실행용 workflow_dispatch |
| `scripts/generate_blog.py` | 전체 파이프라인 (주제 선정 → 글 생성 → 이미지 → 발행 → 텔레그램 알림). 의존성: `requests` 하나 |

## 동작 방식

1. **주제 선정** — workflow 입력에 topic이 없으면 기존 `blog/` 폴더 목록을 읽어
   중복되지 않는 새 주제를 Gemini에게 추천받습니다 (호른스비 지역 환자가 검색할 법한 질문형 주제).
2. **글 생성** — Gemini(gemini-3.6-flash)가 사람이 쓴 것 같은 톤으로 900~1400단어 영문
   아티클을 JSON 형태로 반환합니다. 모든 의학적 사실에는 PubMed/Cochrane/Mayo Clinic/
   .gov.au 등 신뢰 가능한 출처의 인라인 링크가 필수입니다. JSON 파싱 실패 시 최대 3회 재요청.
3. **이미지** — Pollinations.ai에서 플랫 일러스트 스타일 삽화를 다운로드해
   `assets/blog-images/{slug}-{n}.png`에 저장합니다. API 키 불필요. 다운로드 실패 시
   해당 위치에 CSS 인포그래픽 카드로 폴백됩니다.
4. **발행** — `blog/{slug}/index.html` 생성(기존 포스트의 헤더/nav/모바일메뉴/footer 마크업 재사용),
   `blog/index.html` 목록 맨 앞에 새 카드 삽입, `sitemap.xml` 갱신(없으면 생성).
5. **알림** — 성공/실패 리포트를 Telegram으로 전송합니다.

## GitHub Secrets 설정

저장소 Settings → Secrets and variables → Actions → New repository secret:

| Secret 이름 | 값 |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio에서 발급한 무료 Gemini API 키 |
| `TELEGRAM_BOT_TOKEN` | BotFather에게 받은 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 채팅 ID |

## 수동 실행 (workflow_dispatch)

GitHub 저장소 → Actions 탭 → "Daily blog post" → Run workflow:

- **topic** (선택): 비우면 AI가 새 주제를 선정합니다.
- **force** (기본 true): 유사 주제가 있어도 강제 발행.

CLI로도 가능: `gh workflow run daily-blog.yml -f topic="best desk setup for neck pain"`

## 모델 교체

기본값은 `gemini-3.6-flash`입니다. 바꾸려면:

- 저장소 Settings → Secrets and variables → Actions → **Variables** 탭에
  `GEMINI_MODEL` 변수를 추가하면 워크플로우가 그 값을 사용합니다.
- 또는 `.github/workflows/daily-blog.yml`의 `GEMINI_MODEL` env를 직접 수정.

> 참고: 신규 Gemini 계정은 gemini-2.5-flash가 404로 차단되므로
> `gemini-3.6-flash` 또는 `gemini-flash-latest`를 사용하세요.

## 로컬 테스트 (네트워크 호출 없음)

```bash
python scripts/generate_blog.py --dry-run
```

샘플 데이터로 템플릿 조립·파일 쓰기·sitemap 갱신 로직을 검증하고,
검증 후 생성된 임시 파일은 자동 삭제됩니다.

실제 발행 로컬 테스트:

```bash
export GEMINI_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python scripts/generate_blog.py
```

## 주의

- `worker.js`, `index.html`(홈), 기존 서비스 페이지 등 사이트 코드는 이 자동화가 절대 수정하지 않습니다.
  변경되는 파일: `blog/{new-slug}/`, `blog/index.html`, `sitemap.xml`, `assets/blog-images/`.
