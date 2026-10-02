// Serves the static site and sends www.txintrade.com to the main domain.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "www.txintrade.com") {
      url.hostname = "txintrade.com";
      return Response.redirect(url.toString(), 301);
    }
    // Pages pass through unchanged, so Cloudflare Web Analytics (cookieless) can add its beacon.
    return env.ASSETS.fetch(request);
  },
};
