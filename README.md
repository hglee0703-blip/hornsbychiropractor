# Hornsby Chiropractor

Test website for GitHub and Cloudflare Pages deployment.

## Cloudflare Pages Settings

- Framework preset: None
- Build command: leave blank
- Build output directory: `/`

After the first deploy, Cloudflare will provide a temporary `*.pages.dev`
address. Use that address to review the site before changing the live domain's
DNS from Bluehost to Cloudflare.

## Automation

### Daily Blog Post
매일 시드니 시간 오전 9시(UTC 23:00)에 GitHub Actions가 Gemini로 블로그 글을 쓰고,
hornsbychiropractor.com에 자동 발행합니다.

- **`.github/workflows/daily-blog.yml`** — 매일 cron 실행 + 수동 실행용 `workflow_dispatch`
- **`scripts/generate_blog.py`** — 전체 파이프라인 (주제 선정 → 글 생성 → 이미지 → 발행 → 텔레그램 알림)
  의존성: `requests` 하나

[블로그 자동화 상세 안내](scripts/README.md)

### Google Business Profile Auto-Post
매일 시드니 시간 오전 9시 15분(UTC 23:15)에 블로그 글을 발간한 직후,
Google Business Profile에 자동으로 지역 게시물(local post)을 등록합니다.

- **`.github/workflows/gbp-post.yml`** — 매일 cron 실행 + 수동 실행용 `workflow_dispatch`
- **`scripts/generate_gbp_post.py`** — OAuth2 refresh → GBP API v4 `localPosts.create` 호출
  의존성: `requests` (블로그와 동일)

[초기 설정 가이드 (OAuth 2.0 토큰 발급)](scripts/gbp_oauth_setup.md)

#### 설정 방법

1. `scripts/gbp_oauth_setup.md`의 **Step 1–3**을 따라 Google Cloud OAuth 클라이언트를
   생성하고, **한 번만** 브라우저에서 Google 계정에 동의하여 refresh token을 획득합니다.
2. 다음 GitHub Secrets을 저장소에 등록합니다:

| Secret | 값 |
|--------|-----|
| `GOOGLE_CLIENT_ID` | OAuth 2.0 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | OAuth 2.0 클라이언트 비밀번호 |
| `GBP_REFRESH_TOKEN` | 한 번 발급받은 refresh token (수명: revoked까지) |
| `GBP_LOCATION_ID` | `accounts/{accountId}/locations/{locationId}` (Full resource name) |
| `GBP_ACCOUNT_ID` | 계정 ID (선택 — GBP_LOCATION_ID가 full resource name이면 불필요) |

> 💡 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHAT_ID`는 기존 블로그 워크플로에서 이미 설정되어 있습니다.

3. GitHub → **Actions** 탭에서 **GBP auto-post** 워크플로우를 수동 실행하면:
   - 블로그 글이 없으면 최신 블로그 글 제목+요약+링크로 자동 포스트 생성
   - `gbp-posts.json`에 결과 기록 (자동 커밋됨)
   - 실패 시 Telegram으로 알림

#### 수동 실행 (workflow_dispatch)
- **post_text**: 커스텀 요약 글자 (기본값: 자동 생성)
- **post_url**: CTA 버튼 URL (기본값: 최신 블로그 URL)
- **cta_type**: `LEARN_MORE` / `BOOK` / `ORDER` / `SHOP` / `SIGN_UP` / `CALL` (기본값: `LEARN_MORE`)
- **media_url**: 포스트 이미지 URL (선택, jpg/png, ≤5MB, ≥400×300)

CLI: `gh workflow run gbp-post.yml -f post_text="Weekly office hours every Tuesday"`