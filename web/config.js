// Online relay, hosted together with this page, so the page simply uses its own origin as the relay
// address. The relay token is meant to be public: it only lets a browser join channels; commands are
// signed with the device's API key and device messages are checked against it.
// Keep the web server's Content-Security-Policy connect-src in step with the relay address.
export const RELAY_URL = location.origin;
export const RELAY_KEY = 'RYrsXH1Zz-ZA2OGWcBJ7_DY8h7jzPNHT';
