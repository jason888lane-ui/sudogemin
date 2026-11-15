const BACKEND_URL = "https://03cip71vx7.execute-api.us-east-2.amazonaws.com";
const AWS_REGION = "us-east-2";
const AWS_SERVICE = "execute-api";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (url.pathname === "/health") {
      const configured = Boolean(env.AWS_ACCESS_KEY_ID && env.AWS_SECRET_ACCESS_KEY);
      return Response.json({
        status: "ok",
        application: "Sudogemin",
        service: "Cloudflare AWS gateway",
        backend_configured: configured,
        backend: "AWS API Gateway",
        region: AWS_REGION
      }, { headers: corsHeaders() });
    }

    if (!env.AWS_ACCESS_KEY_ID || !env.AWS_SECRET_ACCESS_KEY) {
      return Response.json({
        error: "aws_credentials_not_configured",
        message: "Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY as encrypted Worker secrets.",
        application: "Sudogemin"
      }, { status: 503, headers: corsHeaders() });
    }

    const target = new URL(url.pathname + url.search, BACKEND_URL);
    try {
      const method = request.method.toUpperCase();
      const body = ["GET", "HEAD"].includes(method) ? new ArrayBuffer(0) : await request.arrayBuffer();
      const payloadHash = await sha256Hex(body);
      const now = new Date();
      const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
      const dateStamp = amzDate.slice(0, 8);
      const canonicalUri = canonicalRequestPath(target.pathname);
      const canonicalQuery = canonicalQueryString(target.searchParams);

      const headers = new Headers(request.headers);
      headers.delete("host");
      headers.delete("authorization");
      headers.delete("content-length");
      headers.delete("x-amz-date");
      headers.delete("x-amz-content-sha256");
      headers.set("x-amz-date", amzDate);
      headers.set("x-amz-content-sha256", payloadHash);
      headers.set("host", target.host);

      if (env.AWS_SESSION_TOKEN) {
        headers.set("x-amz-security-token", env.AWS_SESSION_TOKEN);
      }

      const signedHeaderNames = [...new Set([...headers.keys()].map(name => name.toLowerCase()))]
        .filter(name => ["host", "x-amz-content-sha256", "x-amz-date", "x-amz-security-token"].includes(name))
        .sort();
      const canonicalHeaders = signedHeaderNames.map(name => `${name}:${canonicalHeaderValue(headers.get(name))}\n`).join("");
      const signedHeaders = signedHeaderNames.join(";");
      const canonicalRequest = [method, canonicalUri, canonicalQuery, canonicalHeaders, signedHeaders, payloadHash].join("\n");
      const credentialScope = dateStamp + "/" + AWS_REGION + "/" + AWS_SERVICE + "/aws4_request";
      const stringToSign = "AWS4-HMAC-SHA256\n" + amzDate + "\n" + credentialScope + "\n" + await sha256Hex(new TextEncoder().encode(canonicalRequest));
      const signingKey = await getSigningKey(env.AWS_SECRET_ACCESS_KEY, dateStamp, AWS_REGION, AWS_SERVICE);
      const signature = await hmacHex(signingKey, stringToSign);
      headers.set("Authorization", "AWS4-HMAC-SHA256 Credential=" + env.AWS_ACCESS_KEY_ID + "/" + credentialScope + ", SignedHeaders=" + signedHeaders + ", Signature=" + signature);

      const upstream = await fetch(target.toString(), {
        method,
        headers,
        body: ["GET", "HEAD"].includes(method) ? undefined : body,
        redirect: "manual"
      });
      const responseHeaders = new Headers(upstream.headers);
      for (const [key, value] of Object.entries(corsHeaders())) responseHeaders.set(key, value);
      return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
    } catch (error) {
      return Response.json({
        error: "backend_proxy_failed",
        detail: error instanceof Error ? error.message : "Unknown gateway error"
      }, { status: 502, headers: corsHeaders() });
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

function canonicalRequestPath(pathname) {
  return "/" + pathname.split("/").slice(1).map(segment => encodeRFC3986(decodeURIComponent(segment))).join("/") || "/";
}

function canonicalQueryString(searchParams) {
  const pairs = [];
  for (const [key, value] of searchParams.entries()) {
    pairs.push([encodeRFC3986(key), encodeRFC3986(value)]);
  }
  pairs.sort(([leftKey, leftValue], [rightKey, rightValue]) =>
    leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue));
  return pairs.map(([key, value]) => `${key}=${value}`).join("&");
}

function canonicalHeaderValue(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function encodeRFC3986(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

async function sha256Hex(value) {
  const bytes = value instanceof ArrayBuffer ? value : value instanceof Uint8Array ? value : new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function hmac(key, value) {
  const cryptoKey = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(value)));
}

async function hmacHex(key, value) {
  return [...await hmac(key, value)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function getSigningKey(secret, dateStamp, region, service) {
  const kDate = await hmac(new TextEncoder().encode("AWS4" + secret), dateStamp);
  const kRegion = await hmac(kDate, region);
  const kService = await hmac(kRegion, service);
  return hmac(kService, "aws4_request");
}
