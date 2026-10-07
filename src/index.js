import whatsappWeb from "whatsapp-web.js";
import qrcode from "qrcode-terminal";
import { createBridgeServer } from "./bridge.js";

const { Client, LocalAuth } = whatsappWeb;

const token = process.env.WHATSAPP_BRIDGE_TOKEN;
if (!token || token.length < 32) {
  throw new Error("Set WHATSAPP_BRIDGE_TOKEN to a secret of at least 32 characters");
}

const port = Number(process.env.PORT || 3000);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("PORT must be an integer between 1 and 65535");
}

const puppeteerOptions = process.env.WHATSAPP_CHROME_PATH
  ? { executablePath: process.env.WHATSAPP_CHROME_PATH }
  : {};

const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: process.env.WWEBJS_DATA_PATH || ".wwebjs_auth"
  }),
  puppeteer: puppeteerOptions
});

let ready = false;
const server = createBridgeServer({
  client,
  token,
  isReady: () => ready
});

client.on("qr", (qr) => {
  console.log("Scan this QR code with WhatsApp > Linked devices:");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  ready = true;
  console.log("WhatsApp is ready. Local bridge listening on http://127.0.0.1:" + port);
});

client.on("disconnected", (reason) => {
  ready = false;
  console.error("WhatsApp disconnected:", reason);
});

async function start() {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });

  try {
    await client.initialize();
  } catch (error) {
    server.close();
    throw error;
  }
}

async function stop() {
  ready = false;
  server.close();
  await client.destroy();
}

process.once("SIGINT", () => {
  stop().catch((error) => {
    console.error("Failed to stop cleanly:", error);
    process.exitCode = 1;
  });
});

process.once("SIGTERM", () => {
  stop().catch((error) => {
    console.error("Failed to stop cleanly:", error);
    process.exitCode = 1;
  });
});

start().catch((error) => {
  console.error("Failed to start WhatsApp bridge:", error);
  process.exitCode = 1;
});
