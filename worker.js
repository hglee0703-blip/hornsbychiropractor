const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

const SYDNEY_TIME_ZONE = "Australia/Sydney";
const MAX_BODY_BYTES = 12_000;
const ASESCHEDULE_ORIGIN = "https://aseschedule.com";
const ASESCHEDULE_ACCOUNT_ID = "10d766b5-81f6-43b1-9a09-0b7dc8404ce2";
const ASESCHEDULE_BUSINESS_ID = "default";
const ASESCHEDULE_APPOINTMENT_TYPE_ID = "2-standard-30-minutes-appointment";
const ASESCHEDULE_PRACTITIONER = "Andy Lee";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/availability" && request.method === "GET") {
      return getAvailability();
    }

    if (url.pathname === "/api/book" && request.method === "POST") {
      return createBooking(request);
    }

    if (url.pathname === "/api/waitlist" && request.method === "POST") {
      return joinWaitlist(request, env);
    }

    if (url.pathname.startsWith("/api/")) {
      return json({ message: "Not found." }, 404);
    }

    return env.ASSETS.fetch(request);
  },
};

async function getAvailability() {
  try {
    const date = dateInSydney();
    const slots = await fetchAvailableTimes(date);

    return json({
      date,
      displayDate: formatSydneyDate(date),
      slots: slots.map((slot) => ({ startsAt: slot.startsAt })),
    });
  } catch (error) {
    console.error("Availability error", safeError(error));
    return json(
      { message: "Today's available times could not be loaded. Please try again shortly." },
      502,
    );
  }
}

async function createBooking(request) {
  let hold = null;
  let sessionToken = "";

  try {
    const requestUrl = new URL(request.url);
    const origin = request.headers.get("origin");
    if (origin && origin !== requestUrl.origin) {
      return json({ message: "The booking request could not be accepted." }, 403);
    }

    const contentLength = Number(request.headers.get("content-length") || 0);
    if (contentLength > MAX_BODY_BYTES) {
      return json({ message: "The booking request is too large." }, 413);
    }

    const payload = await request.json();
    const validation = validateBooking(payload);
    if (validation.message) {
      return json({ message: validation.message }, 400);
    }
    if (validation.website) {
      return json({ message: "The booking request could not be accepted." }, 400);
    }

    const bookingDate = dateInSydney(new Date(validation.startsAt));
    if (bookingDate !== dateInSydney()) {
      return json({ message: "Only today's appointments can be booked here." }, 400);
    }

    const availableTimes = await fetchAvailableTimes(bookingDate);
    const selectedSlot = availableTimes.find((slot) => slot.startsAt === validation.startsAt);
    if (!selectedSlot) {
      return json(
        { message: "That time is no longer available. Please choose another appointment." },
        409,
      );
    }

    sessionToken = crypto.randomUUID();
    const holdResult = await asescheduleRequest(aseschedulePath("/hold"), {
      method: "POST",
      body: JSON.stringify({
        sessionToken,
        appointmentTypeId: ASESCHEDULE_APPOINTMENT_TYPE_ID,
        practitioner: selectedSlot.practitioner,
        day: selectedSlot.day,
        start: selectedSlot.start,
      }),
    });
    hold = holdResult.hold;
    if (!hold?.id) throw new AsescheduleError(502, { error: "The appointment could not be held." });

    const result = await asescheduleRequest(aseschedulePath(), {
      method: "POST",
      body: JSON.stringify({
        name: `${validation.firstName} ${validation.lastName}`,
        email: validation.email,
        phone: validation.phone,
        newPatientFormSubmission: {},
        appointmentTypeId: ASESCHEDULE_APPOINTMENT_TYPE_ID,
        practitioner: selectedSlot.practitioner,
        day: selectedSlot.day,
        start: selectedSlot.start,
        holdId: hold.id,
        sessionToken,
      }),
    });

    const appointment = result.appointment;
    if (result.booked !== true || !appointment) {
      throw new AsescheduleError(502, { error: "The booking could not be verified." });
    }

    hold = null;
    return json(
      {
        success: true,
        appointmentId:
          appointment.onlineBookingId || appointment.appointmentInstanceId || appointment.id,
        startsAt: validation.startsAt,
      },
      201,
    );
  } catch (error) {
    if (hold?.id && sessionToken) {
      await releaseHold(hold.id, sessionToken).catch(() => null);
    }
    console.error("Booking error", safeError(error));

    if (error instanceof AsescheduleError && [409, 422].includes(error.status)) {
      return json(
        { message: "That time could not be booked. It may have just been taken; please choose another time." },
        409,
      );
    }
    if (error instanceof SyntaxError) {
      return json({ message: "The booking details were not valid." }, 400);
    }

    return json(
      { message: "The appointment could not be confirmed. Please try again or use the Aseschedule booking page." },
      502,
    );
  }
}

// Today's waitlist: when no online times are left today, a patient can ask to be put on the clinic's
// Aseschedule waitlist for a 30-minute appointment today. Uses the clinic API with the FSTER_API_KEY secret,
// which needs the clinic_settings:read, patients:read, patients:write and waitlist:write permissions.
async function joinWaitlist(request, env) {
  try {
    const requestUrl = new URL(request.url);
    const origin = request.headers.get("origin");
    if (origin && origin !== requestUrl.origin) {
      return json({ message: "The waitlist request could not be accepted." }, 403);
    }

    const contentLength = Number(request.headers.get("content-length") || 0);
    if (contentLength > MAX_BODY_BYTES) {
      return json({ message: "The waitlist request is too large." }, 413);
    }

    const payload = await request.json();
    const validation = validateContactDetails(payload, "Please agree to the waitlist consent before continuing.");
    if (validation.message) return json({ message: validation.message }, 400);
    if (validation.website) {
      return json({ message: "The waitlist request could not be accepted." }, 400);
    }

    if (!env?.FSTER_API_KEY) {
      console.error("Waitlist error", { name: "MissingApiKey" });
      return json({ message: "The waitlist is not available right now. Please call or text the clinic." }, 503);
    }

    const today = dateInSydney();
    const openTimes = await fetchAvailableTimes(today);
    if (openTimes.length) {
      return json(
        { message: "Appointments are available today. Please choose a time instead.", timesAvailable: true },
        409,
      );
    }

    const businessId = await clinicBusinessId(env);
    const patientId = await findOrCreatePatient(env, validation);

    try {
      await clinicApiRequest(env, "/api/integration?action=create_waitlist_entry", {
        method: "POST",
        body: JSON.stringify({
          business_id: businessId,
          patient_id: patientId,
          appointment_type_id: ASESCHEDULE_APPOINTMENT_TYPE_ID,
          date_from: today,
          date_to: today,
          expires_on: today,
          duration_minutes: 30,
          contact_consent: true,
          notes: "Joined from the hornsbychiropractor.com booking section (no online times left today).",
        }),
      });
    } catch (error) {
      if (error instanceof AsescheduleError && error.status === 409 && error.data?.code === "waitlist_duplicate") {
        return json({ success: true, alreadyListed: true, date: today });
      }
      throw error;
    }

    return json({ success: true, date: today }, 201);
  } catch (error) {
    console.error("Waitlist error", safeError(error), error?.data?.code || "", error?.data?.missing_scopes || "");
    if (error instanceof SyntaxError) {
      return json({ message: "The waitlist details were not valid." }, 400);
    }
    return json(
      { message: "You could not be added to the waitlist. Please try again, or call or text the clinic." },
      502,
    );
  }
}

let cachedBusinessId = "";

async function clinicBusinessId(env) {
  if (cachedBusinessId) return cachedBusinessId;
  const settings = await clinicApiRequest(env, "/api/integration?resource=clinic_settings");
  const businesses = Array.isArray(settings?.businesses) ? settings.businesses : [];
  const business =
    businesses.find((item) => String(item?.id) === ASESCHEDULE_BUSINESS_ID) ||
    businesses.find((item) => Array.isArray(item?.practitioners) && item.practitioners.includes(ASESCHEDULE_PRACTITIONER)) ||
    businesses[0];
  if (!business?.id) throw new AsescheduleError(404, { error: "Clinic not found." });
  cachedBusinessId = String(business.id);
  return cachedBusinessId;
}

// Reuse an existing patient only when both the email and the mobile number match, so a typed-in email
// alone never attaches someone to another person's record.
async function findOrCreatePatient(env, details) {
  const found = await clinicApiRequest(
    env,
    `/api/integration?resource=patients&email=${encodeURIComponent(details.email)}`,
  );
  const wanted = phoneDigits(details.phone);
  const match = (Array.isArray(found?.patients) ? found.patients : []).find(
    (patient) => patient && patient.archived !== true && phoneDigits(patient.phone) === wanted,
  );
  if (match?.id) return String(match.id);

  const created = await clinicApiRequest(env, "/api/integration?action=create_patient", {
    method: "POST",
    body: JSON.stringify({
      first_name: details.firstName,
      last_name: details.lastName,
      email: details.email,
      phone: details.phone,
    }),
  });
  if (!created?.result?.id) throw new AsescheduleError(502, { error: "The patient could not be created." });
  return String(created.result.id);
}

function phoneDigits(value) {
  return String(value || "").replace(/\D/g, "").slice(-9);
}

function clinicApiRequest(env, path, options = {}) {
  return asescheduleRequest(path, {
    ...options,
    headers: { "x-fster-api-key": env.FSTER_API_KEY, ...(options.headers || {}) },
  });
}

async function fetchAvailableTimes(date) {
  const data = await asescheduleRequest(aseschedulePath());
  const day = (data.days || []).find((entry) => entry.isoDate === date);
  if (!day) return [];

  return (day.slots || [])
    .filter(
      (slot) =>
        slot.appointmentTypeId === ASESCHEDULE_APPOINTMENT_TYPE_ID &&
        slot.practitioner === ASESCHEDULE_PRACTITIONER,
    )
    .map((slot) => ({
      day: day.day,
      start: slot.start,
      practitioner: slot.practitioner,
      startsAt: sydneyIsoDateTime(date, Number(slot.start) * 5),
    }))
    .filter((slot) => new Date(slot.startsAt).getTime() > Date.now());
}

function aseschedulePath(suffix = "") {
  const path = `/api/schedule/${ASESCHEDULE_ACCOUNT_ID}/${ASESCHEDULE_BUSINESS_ID}${suffix}`;
  if (suffix) return path;
  const query = new URLSearchParams({
    type: ASESCHEDULE_APPOINTMENT_TYPE_ID,
    practitioner: ASESCHEDULE_PRACTITIONER,
  });
  return `${path}?${query.toString()}`;
}

async function releaseHold(holdId, sessionToken) {
  return asescheduleRequest(aseschedulePath("/hold"), {
    method: "DELETE",
    body: JSON.stringify({ holdId, sessionToken }),
  });
}

async function asescheduleRequest(path, options = {}) {
  const response = await fetch(`${ASESCHEDULE_ORIGIN}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) throw new AsescheduleError(response.status, data);
  return data;
}

function validateBooking(payload) {
  const startsAt = String(payload?.startsAt || "").trim();
  if (!startsAt || Number.isNaN(new Date(startsAt).getTime())) {
    return { message: "Please choose an available appointment time." };
  }
  const clean = validateContactDetails(payload, "Please agree to the booking consent before continuing.");
  return clean.message ? clean : { startsAt, ...clean };
}

function validateContactDetails(payload, consentMessage) {
  const clean = {
    firstName: cleanText(payload?.firstName, 80),
    lastName: cleanText(payload?.lastName, 80),
    email: String(payload?.email || "").trim().toLowerCase(),
    phone: String(payload?.phone || "").trim(),
    consent: payload?.consent === true,
    website: String(payload?.website || "").trim(),
  };

  if (!clean.firstName || !clean.lastName) return { message: "Please enter your first and last name." };
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean.email) || clean.email.length > 160) {
    return { message: "Please enter a valid email address." };
  }
  if (!/^[+()\d\s-]{8,30}$/.test(clean.phone)) return { message: "Please enter a valid mobile number." };
  if (!clean.consent) return { message: consentMessage };
  return clean;
}

function cleanText(value, maxLength) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, maxLength);
}

function dateInSydney(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: SYDNEY_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function sydneyIsoDateTime(date, minutes) {
  const hours = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mins = String(minutes % 60).padStart(2, "0");
  const utcGuess = new Date(`${date}T${hours}:${mins}:00Z`);
  const offsetName = new Intl.DateTimeFormat("en-AU", {
    timeZone: SYDNEY_TIME_ZONE,
    timeZoneName: "longOffset",
  }).formatToParts(utcGuess).find((part) => part.type === "timeZoneName")?.value || "GMT+10:00";
  const offset = offsetName.replace("GMT", "");
  return `${date}T${hours}:${mins}:00${offset}`;
}

function formatSydneyDate(date) {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("en-AU", {
    timeZone: SYDNEY_TIME_ZONE,
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(Date.UTC(year, month - 1, day, 12)));
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function safeError(error) {
  if (error instanceof AsescheduleError) return { name: error.name, status: error.status };
  return { name: error?.name || "Error", message: error?.message || "Unknown error" };
}

class AsescheduleError extends Error {
  constructor(status, data) {
    super(`Aseschedule request failed with status ${status}`);
    this.name = "AsescheduleError";
    this.status = status;
    this.data = data;
  }
}
