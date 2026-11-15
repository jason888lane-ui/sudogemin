export default {
  async fetch(request, env) {
    const cors = corsHeaders();
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    if (url.pathname === "/health" && !env.BACKEND_URL) {
      return Response.json({
        status: "ok",
        application: "Sudogemin",
        service: "optional API proxy",
        backend_configured: false
      }, { headers: cors });
    }

    if (!env.BACKEND_URL) {
      return Response.json({
        error: "backend_url_not_configured",
        message: "Set BACKEND_URL to the HTTPS URL of the FastAPI service."
      }, { status: 503, headers: cors });
    }

    let backend;
    try {
      backend = new URL(env.BACKEND_URL);
      if (backend.protocol !== "https:" && backend.hostname !== "localhost" && backend.hostname !== "127.0.0.1") {
        throw new Error("BACKEND_URL must use HTTPS except for localhost development.");
      }
    } catch (error) {
      return Response.json({
        error: "invalid_backend_url",
        detail: error instanceof Error ? error.message : "BACKEND_URL must be a valid URL."
      }, { status: 500, headers: cors });
    }

    const target = new URL(url.pathname + url.search, backend);
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("authorization");

    try {
      const upstream = await fetch(target.toString(), {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "manual"
      });
      const responseHeaders = new Headers(upstream.headers);
      for (const [key, value] of Object.entries(cors)) responseHeaders.set(key, value);
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders
      });
    } catch (error) {
      return Response.json({
        error: "backend_proxy_failed",
        detail: error instanceof Error ? error.message : "Unknown proxy error"
      }, { status: 502, headers: cors });
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
