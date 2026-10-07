# Local WhatsApp bridge

This is an experimental local HTTP bridge using `whatsapp-web.js`. It automates
WhatsApp Web rather than using Meta's official API; it may break when WhatsApp
Web changes, and automated use may put the account at risk of restrictions.
Use it only with an account and recipients you are authorized to contact.

## Run

Requires Node.js 18 or newer and npm.

```sh
npm install
openssl rand -hex 32
```

Copy the generated secret, then set it before starting the server:

```sh
export WHATSAPP_BRIDGE_TOKEN="paste-the-generated-secret-here"
npm start
```

On first run, scan the QR code printed in the terminal from WhatsApp's
**Linked devices** screen. The linked session is stored locally in
`.wwebjs_auth/`; do not share or commit that directory. The bridge only listens
on `127.0.0.1` and requires the same secret token on each send request.

In the terminal where you run the agent or `curl`, export the same secret.
Keep it in a password manager or another private place for later runs. Set
`PORT` to change the listening port (default `3000`).

By default, Puppeteer uses its downloaded Chrome. If that download is broken
and Google Chrome is installed on macOS, you can use the installed copy instead:

```sh
export WHATSAPP_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
npm start
```

## Send a message

The local agent can call:

```sh
export WHATSAPP_BRIDGE_TOKEN="paste-the-same-secret-here"
curl http://127.0.0.1:3000/send \
  -H "Authorization: Bearer $WHATSAPP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":"34612345678","message":"Hola"}'
```

Use the recipient's international phone number with country code and no spaces.
The server returns a message ID when WhatsApp accepts the send request. Check
`GET http://127.0.0.1:3000/health` for client readiness.

Run the bridge tests with `npm test`; they use a mock WhatsApp client and do not
send messages.
