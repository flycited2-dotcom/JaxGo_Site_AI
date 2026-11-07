import test from "node:test";
import assert from "node:assert/strict";
import { createServer, validateLead } from "../server.mjs";

const validLead = {
  name: "Иван",
  contact: "@ivan",
  message: "Нужно автоматизировать подготовку прайса поставщиков",
  consent: true,
  website: "",
  utm: { utm_source: "direct" },
  page: "/",
};

async function withServer(env, telegramFetch, run, maxFetch) {
  const server = createServer({ env, telegramFetch, maxFetch });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    await run(origin);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

async function post(origin, body) {
  return fetch(`${origin}/api/lead.php`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

test("validates all required fields and consent", () => {
  assert.equal(validateLead(validLead).name, "Иван");
  assert.equal(validateLead({ ...validLead, consent: false }), null);
  assert.equal(validateLead({ ...validLead, message: "short" }), null);
});

test("serves canonical article URLs, redirects legacy URLs, and preserves 404", async () => {
  await withServer({}, undefined, async (origin) => {
    const article = "/articles/obedinit-praisy-postavshchikov/";
    const old = await fetch(`${origin}${article.slice(0, -1)}.html?utm_source=test`, { redirect: "manual" });
    assert.equal(old.status, 301);
    assert.equal(old.headers.get("location"), `${article}?utm_source=test`);
    const page = await fetch(origin + article);
    assert.equal(page.status, 200);
    assert.ok((await page.text()).includes(`<link href="https://jaxgo.ru${article}" rel="canonical"`));
    const catalogue = await fetch(`${origin}/articles/index.html`, { redirect: "manual" });
    assert.equal(catalogue.status, 301);
    assert.equal(catalogue.headers.get("location"), "/articles/");
    for (const route of ["/articles/does-not-exist/", "/articles/does-not-exist.html", "/articles/page/99/"]) {
      assert.equal((await fetch(origin + route, { redirect: "manual" })).status, 404);
    }
  });
});

test("serves the site and delivers valid leads to Telegram", async () => {
  let delivered;
  await withServer(
    { TELEGRAM_BOT_TOKEN: "test-token", TELEGRAM_CHAT_ID: "123" },
    async (url, options) => {
      assert.match(url, /^https:\/\/api\.telegram\.org\/bottest-token\/sendMessage$/);
      delivered = JSON.parse(options.body);
      return { ok: true, json: async () => ({ ok: true }) };
    },
    async (origin) => {
      const page = await fetch(origin);
      assert.equal(page.status, 200);
      assert.match(await page.text(), /JAXGO/);
      const response = await post(origin, validLead);
      assert.equal(response.status, 200);
      assert.equal((await response.json()).ok, true);
    }
  );
  assert.equal(delivered.chat_id, "123");
  assert.match(delivered.text, /utm_source: direct/);
  assert.match(delivered.text, /Согласие на обработку: подтверждено, редакция 2026-09-24/);
  assert.match(delivered.text, /Нужно автоматизировать/);
});

test("rejects invalid fields, filters honeypot, and limits repeated sends", async () => {
  let calls = 0;
  await withServer(
    { TELEGRAM_BOT_TOKEN: "test-token", TELEGRAM_CHAT_ID: "123" },
    async () => { calls += 1; return { ok: true, json: async () => ({ ok: true }) }; },
    async (origin) => {
      assert.equal((await post(origin, { ...validLead, consent: false })).status, 400);
      assert.equal((await post(origin, { ...validLead, website: "spam.example" })).status, 200);
      assert.equal(calls, 0);
      for (let i = 0; i < 4; i += 1) assert.equal((await post(origin, validLead)).status, 200);
      assert.equal((await post(origin, validLead)).status, 429);
    }
  );
  assert.equal(calls, 4);
});

test("reports unavailable delivery when credentials are absent", async () => {
  await withServer({}, async () => { throw new Error("should not run"); }, async (origin) => {
    assert.equal((await post(origin, validLead)).status, 503);
  });
});

test("delivers a lead to MAX when that channel is selected", async () => {
  let maxRequest;
  await withServer(
    { LEAD_CHANNELS: "max", MAX_BOT_TOKEN: "max-test-token", MAX_CHAT_ID: "1234567890123" },
    async () => { throw new Error("Telegram must not be called"); },
    async (origin) => {
      const response = await post(origin, validLead);
      assert.equal(response.status, 200);
    },
    async (url, options) => {
      maxRequest = { url, options };
      return { ok: true, json: async () => ({ message: { body: { text: "received" } } }) };
    }
  );
  assert.equal(maxRequest.url, "https://platform-api2.max.ru/messages?chat_id=1234567890123");
  assert.equal(maxRequest.options.headers.Authorization, "max-test-token");
  assert.match(JSON.parse(maxRequest.options.body).text, /Нужно автоматизировать/);
});

test("uses a working channel when another delivery fails", async () => {
  await withServer(
    { LEAD_CHANNELS: "telegram,max", TELEGRAM_BOT_TOKEN: "test-token", TELEGRAM_CHAT_ID: "123", MAX_BOT_TOKEN: "max-test-token", MAX_CHAT_ID: "456" },
    async () => ({ ok: false, json: async () => ({ ok: false }) }),
    async (origin) => {
      const response = await post(origin, validLead);
      assert.equal(response.status, 200);
    },
    async () => ({ ok: true, json: async () => ({ message: { recipient: { chat_id: 456 } } }) })
  );
});
