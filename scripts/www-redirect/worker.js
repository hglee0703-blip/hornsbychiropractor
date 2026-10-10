// Keep the request method intact while consolidating the historical WWW host.
// This service never reads the body or sends a request to the main website.
const CANONICAL_ORIGIN = "https://hornsbychiropractor.com";

export default {
  fetch(request) {
    const url = new URL(request.url);
    return new Response(null, {
      status: 308,
      headers: {
        Location: `${CANONICAL_ORIGIN}${url.pathname}${url.search}`,
      },
    });
  },
};
