// Floating KakaoTalk channel button for the Korean pages.
(() => {
  if (document.querySelector(".kakao-float")) return;

  const link = document.createElement("a");
  link.className = "whatsapp-float kakao-float";
  link.href = "https://pf.kakao.com/_xfkRxiX";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", "카카오톡 채널로 문의하기 (새 탭에서 열림)");
  link.innerHTML = `
    <svg viewBox="0 0 24 24" width="30" height="30" fill="currentColor" aria-hidden="true" focusable="false">
      <path d="M12 3C6.48 3 2 6.58 2 11c0 2.85 1.86 5.35 4.66 6.77l-1.05 3.85c-.09.33.27.6.56.41l4.48-2.97c.44.05.9.07 1.35.07 5.52 0 10-3.58 10-8.13S17.52 3 12 3z"/>
    </svg>
    <span class="whatsapp-label" aria-hidden="true">카카오톡으로 문의하기</span>
  `;
  document.body.append(link);
})();
