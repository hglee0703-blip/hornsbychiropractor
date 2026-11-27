(() => {
  const timesContainer = document.querySelector("#booking-times");
  const status = document.querySelector("#booking-status");
  const dateLabel = document.querySelector("#booking-date");
  const refreshButton = document.querySelector("#refresh-times");
  const form = document.querySelector("#booking-form");
  const selectedTimeLabel = document.querySelector("#selected-time-label");
  const changeTimeButton = document.querySelector("#change-time");
  const formStatus = document.querySelector("#booking-form-status");
  const successPanel = document.querySelector("#booking-success");
  const successTime = document.querySelector("#booking-success-time");
  const waitlistForm = document.querySelector("#waitlist-form");
  const waitlistStatus = document.querySelector("#waitlist-form-status");
  const waitlistSuccess = document.querySelector("#waitlist-success");
  const waitlistSuccessDate = document.querySelector("#waitlist-success-date");
  const waitlistSuccessCopy = document.querySelector("#waitlist-success-copy");

  if (!timesContainer || !form) return;

  // The Korean home page uses the same widget; show its messages in Korean.
  const isKorean = document.documentElement.lang === "ko";
  const locale = isKorean ? "ko-KR" : "en-AU";
  const text = isKorean
    ? {
        loading: "예약 가능한 시간을 불러오는 중...",
        checking: "오늘 일정을 확인하는 중...",
        loadFailed: "예약 가능한 시간을 불러오지 못했습니다. 다시 시도해 주세요.",
        noSlots: '오늘은 온라인으로 예약 가능한 시간이 없습니다. <a href="https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/">다른 날짜 확인하기</a>.',
        joining: "등록 중...",
        joinWaitlist: "오늘 대기 명단 등록",
        waitlistFailed: "대기 명단에 등록하지 못했습니다. 다시 시도하시거나 클리닉에 전화 또는 문자 주세요.",
        alreadyListed: "이미 오늘 대기 명단에 등록되어 있습니다. 자리가 나면 클리닉에서 연락드립니다.",
        timesAvailable: "지금 예약 가능한 시간이 생겼습니다. 아래에서 시간을 선택하세요.",
        select: "시간을 선택하세요:",
        confirming: "확인 중...",
        creating: "시간을 확인하고 예약을 만드는 중...",
        bookFailed: "예약을 확정하지 못했습니다. 다시 시도해 주세요.",
        slotTaken: "선택하신 시간에 예약할 수 없습니다. 방금 마감되었을 수 있으니 다른 시간을 선택해 주세요.",
        invalid: "입력하신 정보를 확인해 주세요.",
        confirm: "예약 확정",
      }
    : {
        loading: "Loading available appointments...",
        checking: "Checking today's calendar...",
        loadFailed: "Available times could not be loaded. Please try again.",
        noSlots: 'There are no online appointments left today. <a href="https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/">Check another date</a>.',
        joining: "Joining...",
        joinWaitlist: "Join today's waitlist",
        waitlistFailed: "You could not be added to the waitlist. Please try again, or call or text the clinic.",
        alreadyListed: "You're already on today's waitlist. The clinic will contact you if a time becomes available.",
        timesAvailable: "A time has just become available. Please choose one below.",
        select: "Select a time:",
        confirming: "Confirming...",
        creating: "Checking the time and creating your appointment...",
        bookFailed: "The appointment could not be confirmed. Please try again.",
        confirm: "Confirm appointment",
      };

  // English pages show the server's own message; Korean pages show a Korean one.
  function errorMessage(response, data, fallback) {
    if (!isKorean) return (data && data.message) || fallback;
    if (response && response.status === 409) return text.slotTaken;
    if (response && response.status === 400) return text.invalid;
    return fallback;
  }

  const timeFormatter = new Intl.DateTimeFormat(locale, {
    timeZone: "Australia/Sydney",
    hour: "numeric",
    minute: "2-digit",
  });
  const dateFormatter = new Intl.DateTimeFormat(locale, {
    timeZone: "Australia/Sydney",
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  let selectedStart = "";

  function setLoading(isLoading) {
    refreshButton.disabled = isLoading;
    refreshButton.classList.toggle("is-loading", isLoading);
  }

  function hideWaitlist() {
    if (waitlistForm) waitlistForm.hidden = true;
    if (waitlistSuccess) waitlistSuccess.hidden = true;
    if (waitlistStatus) waitlistStatus.textContent = "";
  }

  function showTimes() {
    form.hidden = true;
    successPanel.hidden = true;
    hideWaitlist();
    timesContainer.hidden = false;
    status.hidden = false;
    selectedStart = "";
    formStatus.textContent = "";
  }

  function chooseTime(startsAt) {
    selectedStart = startsAt;
    selectedTimeLabel.textContent = `${dateFormatter.format(new Date(startsAt))}, ${timeFormatter.format(new Date(startsAt))}`;
    timesContainer.hidden = true;
    status.hidden = true;
    successPanel.hidden = true;
    form.hidden = false;
    form.querySelector("input").focus();
  }

  async function loadAvailability() {
    setLoading(true);
    showTimes();
    timesContainer.replaceChildren();
    status.textContent = text.loading;
    dateLabel.textContent = text.checking;

    try {
      const response = await fetch("/api/availability", {
        headers: { Accept: "application/json" },
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(errorMessage(response, data, text.loadFailed));
      }

      dateLabel.textContent = isKorean && data.date
        ? dateFormatter.format(new Date(`${data.date}T12:00:00Z`))
        : data.displayDate;

      if (!data.slots.length) {
        status.innerHTML = text.noSlots;
        if (waitlistForm) waitlistForm.hidden = false;
        return;
      }

      status.textContent = text.select;
      data.slots.forEach((slot) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "time-button";
        button.textContent = timeFormatter.format(new Date(slot.startsAt));
        button.addEventListener("click", () => chooseTime(slot.startsAt));
        timesContainer.append(button);
      });
    } catch (error) {
      status.textContent = error.message || text.loadFailed;
    } finally {
      setLoading(false);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedStart || !form.reportValidity()) return;

    const submitButton = form.querySelector(".booking-submit");
    const formData = new FormData(form);
    submitButton.disabled = true;
    submitButton.textContent = text.confirming;
    formStatus.textContent = text.creating;

    try {
      const response = await fetch("/api/book", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          startsAt: selectedStart,
          firstName: formData.get("firstName"),
          lastName: formData.get("lastName"),
          email: formData.get("email"),
          phone: formData.get("phone"),
          consent: formData.get("consent") === "on",
          website: formData.get("website"),
        }),
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(errorMessage(response, data, text.bookFailed));
      }

      form.hidden = true;
      successPanel.hidden = false;
      successTime.textContent = `${dateFormatter.format(new Date(data.startsAt))}, ${timeFormatter.format(new Date(data.startsAt))}`;
      form.reset();
    } catch (error) {
      formStatus.textContent = error.message || text.bookFailed;
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = text.confirm;
    }
  });

  if (waitlistForm) {
    waitlistForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!waitlistForm.reportValidity()) return;

      const submitButton = waitlistForm.querySelector(".waitlist-submit");
      const formData = new FormData(waitlistForm);
      submitButton.disabled = true;
      submitButton.textContent = text.joining;
      waitlistStatus.textContent = "";

      try {
        const response = await fetch("/api/waitlist", {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            firstName: formData.get("firstName"),
            lastName: formData.get("lastName"),
            email: formData.get("email"),
            phone: formData.get("phone"),
            consent: formData.get("consent") === "on",
            website: formData.get("website"),
          }),
        });
        const data = await response.json().catch(() => ({}));

        if (response.status === 409 && data.timesAvailable) {
          await loadAvailability();
          status.textContent = text.timesAvailable;
          return;
        }
        if (!response.ok) {
          throw new Error(isKorean ? text.waitlistFailed : data.message || text.waitlistFailed);
        }

        waitlistForm.hidden = true;
        waitlistSuccess.hidden = false;
        waitlistSuccessDate.textContent = data.date
          ? dateFormatter.format(new Date(`${data.date}T12:00:00Z`))
          : "";
        if (data.alreadyListed) waitlistSuccessCopy.textContent = text.alreadyListed;
        waitlistForm.reset();
      } catch (error) {
        waitlistStatus.textContent = error.message || text.waitlistFailed;
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = text.joinWaitlist;
      }
    });
  }

  changeTimeButton.addEventListener("click", showTimes);
  refreshButton.addEventListener("click", loadAvailability);
  loadAvailability();
})();
