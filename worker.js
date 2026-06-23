const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

const SYDNEY_TIME_ZONE = "Australia/Sydney";
const MAX_BODY_BYTES = 12_000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/availability" && request.method === "GET") {
      return getAvailability(env);
    }

    if (url.pathname === "/api/book" && request.method === "POST") {
      return createBooking(request, env);
    }

    if (url.pathname.startsWith("/api/")) {
      return json({ message: "Not found." }, 404);
    }

    return env.ASSETS.fetch(request);
  },
};

async function getAvailability(env) {
  try {
    assertConfiguration(env);
    const date = dateInSydney();
    const slots = await fetchAvailableTimes(env, date);

    return json({
      date,
      displayDate: formatSydneyDate(date),
      slots: slots.map((startsAt) => ({ startsAt })),
    });
  } catch (error) {
    console.error("Availability error", safeError(error));
    return json(
      { message: "Today's available times could not be loaded. Please try again shortly." },
      502,
    );
  }
}

async function createBooking(request, env) {
  try {
    assertConfiguration(env);

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

    const availableTimes = await fetchAvailableTimes(env, bookingDate);
    if (!availableTimes.includes(validation.startsAt)) {
      return json(
        { message: "That time is no longer available. Please choose another appointment." },
        409,
      );
    }

    const patientId = await findOrCreatePatient(env, validation);
    const appointment = await clinikoRequest(env, "/individual_appointments", {
      method: "POST",
      body: JSON.stringify({
        appointment_type_id: env.CLINIKO_APPOINTMENT_TYPE_ID,
        business_id: env.CLINIKO_BUSINESS_ID,
        patient_id: patientId,
        practitioner_id: env.CLINIKO_PRACTITIONER_ID,
        starts_at: validation.startsAt,
        notes: "Booked via the Hornsby Chiropractor website.",
      }),
    });

    return json(
      {
        success: true,
        appointmentId: appointment.id,
        startsAt: appointment.starts_at || validation.startsAt,
      },
      201,
    );
  } catch (error) {
    console.error("Booking error", safeError(error));

    if (error instanceof ClinikoError && error.status === 422) {
      return json(
        { message: "That time could not be booked. It may have just been taken; please choose another time." },
        409,
      );
    }

    if (error instanceof SyntaxError) {
      return json({ message: "The booking details were not valid." }, 400);
    }

    return json(
      { message: "The appointment could not be confirmed. Please try again or use the Cliniko booking page." },
      502,
    );
  }
}

async function fetchAvailableTimes(env, date) {
  const path =
    `/businesses/${env.CLINIKO_BUSINESS_ID}` +
    `/practitioners/${env.CLINIKO_PRACTITIONER_ID}` +
    `/appointment_types/${env.CLINIKO_APPOINTMENT_TYPE_ID}` +
    `/available_times?from=${date}&to=${date}`;
  const data = await clinikoRequest(env, path);
  const now = Date.now();

  return (data.available_times || [])
    .map((slot) => slot.appointment_start)
    .filter((startsAt) => startsAt && new Date(startsAt).getTime() > now);
}

async function findOrCreatePatient(env, patient) {
  const query = new URLSearchParams({
    per_page: "5",
    "q[]": `email:=${patient.email}`,
  });
  const result = await clinikoRequest(env, `/patients?${query.toString()}`);
  const exactMatch = (result.patients || []).find(
    (entry) => String(entry.email || "").toLowerCase() === patient.email,
  );

  if (exactMatch) return exactMatch.id;

  const created = await clinikoRequest(env, "/patients", {
    method: "POST",
    body: JSON.stringify({
      first_name: patient.firstName,
      last_name: patient.lastName,
      email: patient.email,
      patient_phone_numbers: [
        {
          phone_type: "Mobile",
          number: patient.phone,
        },
      ],
    }),
  });

  return created.id;
}

async function clinikoRequest(env, path, options = {}) {
  const response = await fetch(`https://api.${env.CLINIKO_SHARD}.cliniko.com/v1${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      Authorization: `Basic ${btoa(`${env.CLINIKO_API_KEY}:`)}`,
      "Content-Type": "application/json",
      "User-Agent": "Hornsby Chiropractor website (hornsbychiroandy@gmail.com)",
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = {};
    }
  }

  if (!response.ok) {
    throw new ClinikoError(response.status, data);
  }

  return data;
}

function validateBooking(payload) {
  const clean = {
    startsAt: String(payload?.startsAt || "").trim(),
    firstName: cleanText(payload?.firstName, 80),
    lastName: cleanText(payload?.lastName, 80),
    email: String(payload?.email || "").trim().toLowerCase(),
    phone: String(payload?.phone || "").trim(),
    consent: payload?.consent === true,
    website: String(payload?.website || "").trim(),
  };

  if (!clean.startsAt || Number.isNaN(new Date(clean.startsAt).getTime())) {
    return { message: "Please choose an available appointment time." };
  }
  if (!clean.firstName || !clean.lastName) {
    return { message: "Please enter your first and last name." };
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean.email) || clean.email.length > 160) {
    return { message: "Please enter a valid email address." };
  }
  if (!/^[+()\d\s-]{8,30}$/.test(clean.phone)) {
    return { message: "Please enter a valid mobile number." };
  }
  if (!clean.consent) {
    return { message: "Please agree to the booking consent before continuing." };
  }

  return clean;
}

function cleanText(value, maxLength) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, maxLength);
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

function formatSydneyDate(date) {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("en-AU", {
    timeZone: SYDNEY_TIME_ZONE,
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(Date.UTC(year, month - 1, day, 12)));
}

function assertConfiguration(env) {
  const required = [
    "CLINIKO_API_KEY",
    "CLINIKO_SHARD",
    "CLINIKO_BUSINESS_ID",
    "CLINIKO_PRACTITIONER_ID",
    "CLINIKO_APPOINTMENT_TYPE_ID",
  ];
  if (required.some((key) => !env[key])) {
    throw new Error("Cliniko configuration is incomplete.");
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: JSON_HEADERS,
  });
}

function safeError(error) {
  if (error instanceof ClinikoError) {
    return { name: error.name, status: error.status };
  }
  return { name: error?.name || "Error", message: error?.message || "Unknown error" };
}

class ClinikoError extends Error {
  constructor(status, data) {
    super(`Cliniko request failed with status ${status}`);
    this.name = "ClinikoError";
    this.status = status;
    this.data = data;
  }
}
