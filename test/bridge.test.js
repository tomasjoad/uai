import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createBridgeServer } from "../src/bridge.js";

const token = "test-token-that-is-long-enough-for-this-suite";
let ready = true;
let sentMessage;
let server;
let baseUrl;

before(async () => {
  server = createBridgeServer({
    client: {
      async sendMessage(chatId, message) {
        sentMessage = { chatId, message };
        return { id: { _serialized: "test-message-id" } };
      }
    },
    token,
    isReady: () => ready
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve) => server.close(resolve));
});

test("health reports whether WhatsApp is ready", async () => {
  const response = await fetch(`${baseUrl}/health`);
  assert.deepEqual(await response.json(), { ready: true });
});

test("send requires the bridge token", async () => {
  const response = await fetch(`${baseUrl}/send`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ to: "34612345678", message: "Hello" })
  });
  assert.equal(response.status, 401);
});

test("send forwards a validated message to WhatsApp", async () => {
  const response = await fetch(`${baseUrl}/send`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json"
    },
    body: JSON.stringify({ to: "+34612345678", message: "Hello" })
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { messageId: "test-message-id" });
  assert.deepEqual(sentMessage, {
    chatId: "34612345678@c.us",
    message: "Hello"
  });
});

test("send rejects invalid phone numbers", async () => {
  const response = await fetch(`${baseUrl}/send`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json"
    },
    body: JSON.stringify({ to: "not-a-number", message: "Hello" })
  });
  assert.equal(response.status, 400);
});

test("send reports when WhatsApp is not ready", async () => {
  ready = false;
  const response = await fetch(`${baseUrl}/send`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json"
    },
    body: JSON.stringify({ to: "34612345678", message: "Hello" })
  });
  ready = true;
  assert.equal(response.status, 503);
});
