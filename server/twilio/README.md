# Twilio Elastic SIP / Voice (Example)

This folder contains an example **TwiML** to start **Media Streams** so you can bridge audio to your
Realtime model. High-level steps:

1. Create a Twilio Voice webhook (or Function) that returns `connect_stream.twiml`.
2. Point your Twilio phone number / SIP Domain to that webhook.
3. Implement a **WebSocket media bridge** at `wss://YOUR_DOMAIN.example/streams/twilio` that:
   - Receives base64-encoded audio frames from Twilio.
   - Streams audio into the Realtime session.
   - Sends synthesized audio back to Twilio as `media` messages.

> This repo does **not** ship a full media bridge. Use this as a starting point.
