// Serves the static site and sends www.txintrade.com to the main domain.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "www.txintrade.com") {
      url.hostname = "txintrade.com";
      return Response.redirect(url.toString(), 301);
    }
    const response = await env.ASSETS.fetch(request);
    if (!response.headers.get("Content-Type")?.startsWith("text/html")) return response;
    // The site promises no tracking: no-transform keeps Cloudflare from injecting its analytics beacon.
    const page = new Response(response.body, response);
    page.headers.set("Cache-Control", "public, max-age=0, must-revalidate, no-transform");
    return page;
  },
};
