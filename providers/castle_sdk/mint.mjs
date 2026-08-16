import { JSDOM } from "jsdom";

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i], process.argv[i + 1] || "");
const pk = args.get("--pk");
const pageUrl = args.get("--url") || "https://accounts.x.ai/sign-up";
const userAgent = args.get("--user-agent") || "Mozilla/5.0";
const count = Math.max(1, parseInt(args.get("--count") || "1", 10) || 1);
if (!pk) throw new Error("missing --pk");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: pageUrl,
  pretendToBeVisual: true,
  userAgent,
});
const { window } = dom;
for (const [name, value] of Object.entries({
  window,
  document: window.document,
  navigator: window.navigator,
  location: window.location,
  localStorage: window.localStorage,
  sessionStorage: window.sessionStorage,
  XMLHttpRequest: window.XMLHttpRequest,
})) {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

const imported = await import("@castleio/castle-js");
const Castle = imported.default || imported;
Castle.configure({ pk, window, timeout: 15000, verbose: false });
const tokens = [];
for (let i = 0; i < count; i += 1) {
  const token = await Castle.createRequestToken();
  if (!token) throw new Error("empty castle request token");
  tokens.push(token);
}
process.stdout.write(JSON.stringify({ tokens }) + "\n");
dom.window.close();
