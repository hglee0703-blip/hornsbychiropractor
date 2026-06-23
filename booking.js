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

  if (!timesContainer || !form) return;

  const timeFormatter = new Intl.DateTimeFormat("en-AU", {
    timeZone: "Australia/Sydney",
    hour: "numeric",
    minute: "2-digit",
  });
  const dateFormatter = new Intl.DateTimeFormat("en-AU", {
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

  function showTimes() {
    form.hidden = true;
    successPanel.hidden = true;
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
    status.textContent = "Loading available appointments...";
    dateLabel.textContent = "Checking today's calendar...";

    try {
      const response = await fetch("/api/availability", {
        headers: { Accept: "application/json" },
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.message || "Available times could not be loaded.");
      }

      dateLabel.textContent = data.displayDate;

      if (!data.slots.length) {
        status.innerHTML = 'There are no online appointments left today. <a href="https://hornsby-chiropractor.au4.cliniko.com/bookings">Check another date in Cliniko</a>.';
        return;
      }

      status.textContent = "Select a time:";
      data.slots.forEach((slot) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "time-button";
        button.textContent = timeFormatter.format(new Date(slot.startsAt));
        button.addEventListener("click", () => chooseTime(slot.startsAt));
        timesContainer.append(button);
      });
    } catch (error) {
      status.textContent = error.message || "Available times could not be loaded. Please try again.";
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
    submitButton.textContent = "Confirming...";
    formStatus.textContent = "Checking the time and creating your appointment...";

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
        throw new Error(data.message || "The appointment could not be confirmed.");
      }

      form.hidden = true;
      successPanel.hidden = false;
      successTime.textContent = `${dateFormatter.format(new Date(data.startsAt))}, ${timeFormatter.format(new Date(data.startsAt))}`;
      form.reset();
    } catch (error) {
      formStatus.textContent = error.message || "The appointment could not be confirmed. Please try again.";
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "Confirm appointment";
    }
  });

  changeTimeButton.addEventListener("click", showTimes);
  refreshButton.addEventListener("click", loadAvailability);
  loadAvailability();
})();
