import http from "node:http";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { isIP } from "node:net";

const publicRoot = fileURLToPath(new URL("./public/", import.meta.url));
const maxBodyBytes = 8192;
const windowMs = 10 * 60 * 1000;
const maxLeadsPerWindow = 5;
const consentVersion = "2026-09-24";
const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function sendJson(res, status, value, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers });
  res.end(JSON.stringify(value));
}

function cleanText(value, maxLength) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, maxLength + 1);
}

export function validateLead(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const name = cleanText(body.name, 100);
  const contact = cleanText(body.contact, 150);
  const message = cleanText(body.message, 3000);
  if (name.length < 2 || name.length > 100 || contact.length < 3 || contact.length > 150 ||
      message.length < 10 || message.length > 3000 || body.consent !== true) return null;
  const utm = {};
  for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) {
    const value = cleanText(body.utm?.[key], 120);
    if (value) utm[key] = value;
  }
  return { name, contact, message, utm, page: cleanText(body.page, 200), website: cleanText(body.website, 200) };
}

export function formatLead(lead) {
  const lines = [
    "Новая заявка с jaxgo.ru",
    `Получена: ${new Date().toISOString()}`,
    `Согласие на обработку: подтверждено, редакция ${consentVersion}`,
    `Имя: ${lead.name}`,
    `Контакт: ${lead.contact}`,
    `Задача: ${lead.message}`,
  ];
  if (lead.page) lines.push(`Страница: ${lead.page}`);
  for (const [key, value] of Object.entries(lead.utm)) lines.push(`${key}: ${value}`);
  return lines.join("\n");
}

async function deliverTelegram(env, message, telegramFetch) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const response = await telegramFetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: message, disable_web_page_preview: true }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Telegram request failed");
  const result = await response.json();
  if (!result.ok) throw new Error("Telegram delivery failed");
  return true;
}

async function deliverMax(env, message, maxFetch) {
  if (!env.MAX_BOT_TOKEN || !/^-?\d+$/.test(String(env.MAX_CHAT_ID || ""))) return false;
  const url = new URL("https://platform-api2.max.ru/messages");
  url.searchParams.set("chat_id", String(env.MAX_CHAT_ID));
  const response = await maxFetch(url.toString(), {
    method: "POST",
    headers: { Authorization: env.MAX_BOT_TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ text: message.slice(0, 4000), disable_link_preview: true }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("MAX request failed");
  const result = await response.json();
  if (!result.message) throw new Error("MAX delivery failed");
  return true;
}

async function readJson(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maxBodyBytes) {
      const error = new Error("Payload too large");
      error.status = 413;
      throw error;
    }
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    const error = new Error("Invalid JSON");
    error.status = 400;
    throw error;
  }
}

async function sendFile(req, res, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    sendJson(res, 400, { error: "Invalid path" });
    return;
  }
  const legacyArticle = decoded.match(/^\/articles\/([a-z0-9-]+)\.html$/);
  let articleDirectory;
  if (legacyArticle) {
    articleDirectory = legacyArticle[1] === "index" ? "/articles/" : `/articles/${legacyArticle[1]}/`;
  } else if (/^\/articles(?:\/[a-z0-9-]+|\/page\/[0-9]+)?(?:\/index\.html)?$/.test(decoded)) {
    articleDirectory = decoded.replace(/(?:\/index\.html)?$/, "/");
  }
  if (articleDirectory) {
    const index = path.join(publicRoot, articleDirectory, "index.html");
    if ((await stat(index).catch(() => null))?.isFile()) {
      const query = new URL(req.url, "http://localhost").search;
      res.writeHead(301, { Location: articleDirectory + query });
      res.end();
      return;
    }
  }
  if (decoded.endsWith("/")) decoded += "index.html";
  if (path.extname(decoded).toLowerCase() === ".php") {
    sendJson(res, 404, { error: "Not found" });
    return;
  }
  const target = path.resolve(publicRoot, "." + decoded);
  if (!target.startsWith(publicRoot) || decoded.split("/").some((segment) => segment.startsWith("."))) {
    sendJson(res, 404, { error: "Not found" });
    return;
  }
  try {
    const info = await stat(target);
    if (!info.isFile()) throw new Error("Not a file");
    const content = await readFile(target);
    const contentType = mimeTypes[path.extname(target).toLowerCase()] || "application/octet-stream";
    const cacheControl = target.endsWith("index.html") || target.endsWith("site-config.js")
      ? "no-cache"
      : "public, max-age=86400";
    res.writeHead(200, {
      "Content-Type": contentType,
      "Content-Length": content.length,
      "Cache-Control": cacheControl,
    });
    res.end(req.method === "HEAD" ? undefined : content);
  } catch {
    sendJson(res, 404, { error: "Not found" });
  }
}

function clientAddress(req, trustProxy) {
  if (trustProxy) {
    const fromProxy = String(req.headers["x-real-ip"] || "").trim();
    if (isIP(fromProxy)) return fromProxy;
  }
  return req.socket.remoteAddress || "unknown";
}

export function createServer({ env = process.env, telegramFetch = fetch, maxFetch = fetch } = {}) {
  const attempts = new Map();
  const trustProxy = env.TRUST_PROXY === "1";

  return http.createServer(async (req, res) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
    res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    res.setHeader("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: https://mc.yandex.ru; script-src 'self' https://mc.yandex.ru https://yastatic.net; style-src 'self' 'unsafe-inline'; connect-src 'self' https://mc.yandex.ru");

    let url;
    try {
      url = new URL(req.url, "http://localhost");
    } catch {
      sendJson(res, 400, { error: "Invalid URL" });
      return;
    }

    if (url.pathname === "/api/health" && req.method === "GET") {
      sendJson(res, 200, { status: "ok" });
      return;
    }

    if (url.pathname === "/api/lead" || url.pathname === "/api/lead.php") {
      if (req.method !== "POST") {
        sendJson(res, 405, { error: "Method not allowed" }, { Allow: "POST" });
        return;
      }
      if (!String(req.headers["content-type"] || "").startsWith("application/json")) {
        sendJson(res, 415, { error: "JSON required" });
        return;
      }
      const allowedOrigins = String(env.SITE_ORIGIN || "").split(",").map((value) => value.trim()).filter(Boolean);
      if (allowedOrigins.length && req.headers.origin && !allowedOrigins.includes(req.headers.origin)) {
        sendJson(res, 403, { error: "Origin not allowed" });
        return;
      }

      const address = clientAddress(req, trustProxy);
      const now = Date.now();
      for (const [key, value] of attempts) {
        if (value.reset <= now) attempts.delete(key);
      }
      const limit = attempts.get(address) || { count: 0, reset: now + windowMs };
      if (limit.count >= maxLeadsPerWindow) {
        sendJson(res, 429, { error: "Too many requests" }, { "Retry-After": String(Math.ceil((limit.reset - now) / 1000)) });
        return;
      }

      try {
        const lead = validateLead(await readJson(req));
        if (!lead) {
          sendJson(res, 400, { error: "Check the form fields" });
          return;
        }
        limit.count += 1;
        attempts.set(address, limit);
        // Give bots a neutral response without delivering their hidden field content.
        if (lead.website) {
          sendJson(res, 200, { ok: true });
          return;
        }
        const channels = new Set(String(env.LEAD_CHANNELS || "telegram").split(",").map((value) => value.trim().toLowerCase()));
        const message = formatLead(lead);
        const sends = [];
        if (channels.has("telegram") && env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
          sends.push(deliverTelegram(env, message, telegramFetch));
        }
        if (channels.has("max") && env.MAX_BOT_TOKEN && /^-?\d+$/.test(String(env.MAX_CHAT_ID || ""))) {
          sends.push(deliverMax(env, message, maxFetch));
        }
        if (!sends.length) {
          sendJson(res, 503, { error: "Delivery unavailable" });
          return;
        }
        const outcomes = await Promise.allSettled(sends);
        if (!outcomes.some((outcome) => outcome.status === "fulfilled" && outcome.value === true)) {
          throw new Error("All delivery channels failed");
        }
        sendJson(res, 200, { ok: true });
      } catch (error) {
        if (error.status) sendJson(res, error.status, { error: error.message });
        else sendJson(res, 502, { error: "Delivery unavailable" });
      }
      return;
    }

    if (req.method === "GET" || req.method === "HEAD") {
      await sendFile(req, res, url.pathname);
      return;
    }
    sendJson(res, 405, { error: "Method not allowed" }, { Allow: "GET, HEAD" });
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 3000);
  const host = process.env.HOST || "127.0.0.1";
  createServer().listen(port, host, () => {
    console.log(`JAXGO listening on http://${host}:${port}`);
  });
}
