import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";

const MAX_BODY_SIZE = 10 * 1024;
const MAX_MESSAGE_LENGTH = 4096;

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

function hasValidToken(header, token) {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) {
    return false;
  }

  const provided = Buffer.from(header.slice(7));
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

async function readJson(request) {
  const chunks = [];
  let size = 0;

  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_SIZE) {
      const error = new Error("Request body too large");
      error.statusCode = 413;
      throw error;
    }
    chunks.push(chunk);
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    const error = new Error("Request body must be valid JSON");
    error.statusCode = 400;
    throw error;
  }
}

function validateMessage(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "Expected a JSON object with 'to' and 'message' fields";
  }

  if (typeof payload.to !== "string" || !/^\+?\d{7,15}$/.test(payload.to)) {
    return "'to' must be an international phone number with 7 to 15 digits";
  }

  if (
    typeof payload.message !== "string" ||
    payload.message.trim().length === 0 ||
    payload.message.length > MAX_MESSAGE_LENGTH
  ) {
    return `'message' must contain 1 to ${MAX_MESSAGE_LENGTH} characters`;
  }

  return null;
}

export function createBridgeServer({ client, token, isReady }) {
  return createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/health") {
      return sendJson(response, 200, { ready: isReady() });
    }

    if (request.method !== "POST" || request.url !== "/send") {
      return sendJson(response, 404, { error: "Not found" });
    }

    if (!hasValidToken(request.headers.authorization, token)) {
      return sendJson(response, 401, { error: "Unauthorized" });
    }

    if (!isReady()) {
      return sendJson(response, 503, { error: "WhatsApp client is not ready" });
    }

    try {
      const payload = await readJson(request);
      const validationError = validateMessage(payload);
      if (validationError) {
        return sendJson(response, 400, { error: validationError });
      }

      const number = payload.to.replace(/^\+/, "");
      const result = await client.sendMessage(`${number}@c.us`, payload.message);
      return sendJson(response, 200, { messageId: result.id._serialized });
    } catch (error) {
      if (error.statusCode) {
        return sendJson(response, error.statusCode, { error: error.message });
      }

      console.error("Failed to send WhatsApp message:", error);
      return sendJson(response, 502, { error: "WhatsApp could not send the message" });
    }
  });
}
