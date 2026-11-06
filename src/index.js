export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders()
      });
    }

    if (url.pathname === "/health") {
      return Response.json({
        status: "ok",
        application: "Sudogemin",
        service: "Cloudflare gateway",
        backend_configured: Boolean(env.BACKEND_URL)
      }, { headers: corsHeaders() });
    }

    if (!env.BACKEND_URL) {
      return Response.json({
        error: "backend_not_configured",
        message: "Set the BACKEND_URL Worker variable to the deployed Sudogemin FastAPI origin.",
        application: "Sudogemin"
      }, { status: 503, headers: corsHeaders() });
    }

    let origin;
    try {
      origin = new URL(env.BACKEND_URL);
      if (origin.protocol !== "https:" && origin.hostname !== "localhost" && origin.hostname !== "127.0.0.1") {
        return Response.json({ error: "invalid_backend_url" }, { status: 500 });
      }
    } catch {
      return Response.json({ error: "invalid_backend_url" }, { status: 500 });
    }

    origin.pathname = url.pathname;
    origin.search = url.search;

    const headers = new Headers(request.headers);
    headers.delete("host");
    const upstreamRequest = new Request(origin.toString(), {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual"
    });

    try {
      const upstream = await fetch(upstreamRequest);
      const responseHeaders = new Headers(upstream.headers);
      for (const [key, value] of Object.entries(corsHeaders())) {
        responseHeaders.set(key, value);
      }
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders
      });
    } catch {
      return Response.json({ error: "backend_unavailable" }, {
        status: 502,
        headers: corsHeaders()
      });
    }
  }
};

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Stripe-Signature",
    "Cache-Control": "no-store"
  };
}
