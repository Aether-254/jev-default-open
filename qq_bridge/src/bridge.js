"use strict";

const { validateClick, validateContext } = require("./protocol");

function createBridge({ adapter, transport, sourcePid, clock = Date.now }) {
  if (!adapter || typeof adapter.resolveClick !== "function" ||
      !transport || typeof transport.send !== "function" ||
      !Number.isInteger(sourcePid) || sourcePid < 1) throw new TypeError("Invalid bridge dependencies");
  const windows = new Map();
  const pending = new Set();
  const pendingByWindow = new Map();
  const lastRequest = new Map();
  let active = false;
  return {
    start() { active = true; },
    register(window) {
      const contents = window.webContents;
      windows.set(contents.id, contents);
      window.once("closed", () => {
        windows.delete(contents.id);
        lastRequest.delete(contents.id);
        for (const controller of pendingByWindow.get(contents.id) || []) controller.abort();
        pendingByWindow.delete(contents.id);
      });
    },
    async handle(event, payload) {
      if (!active || !event || !event.sender || event.sender.isDestroyed()) return false;
      if (windows.get(event.sender.id) !== event.sender || event.senderFrame !== event.sender.mainFrame) return false;
      const click = validateClick(payload);
      if (!click) return false;
      const now = clock();
      if (now - (lastRequest.get(event.sender.id) ?? -Infinity) < 100) return false;
      lastRequest.set(event.sender.id, now);
      const controller = new AbortController();
      pending.add(controller);
      const windowRequests = pendingByWindow.get(event.sender.id) || new Set();
      windowRequests.add(controller);
      pendingByWindow.set(event.sender.id, windowRequests);
      let timeout;
      try {
        const operation = async () => {
          const raw = await adapter.resolveClick({ contents: event.sender, click, signal: controller.signal });
          if (!active || controller.signal.aborted || windows.get(event.sender.id) !== event.sender ||
              event.sender.isDestroyed() || event.senderFrame !== event.sender.mainFrame) return false;
          const context = validateContext(raw, click, sourcePid, now);
          if (!context || Buffer.byteLength(JSON.stringify(context), "utf8") > 65536) return false;
          await transport.send(context, { signal: controller.signal });
          return active && !controller.signal.aborted;
        };
        return await Promise.race([
          operation(),
          new Promise((resolve) => {
            controller.signal.addEventListener("abort", () => resolve(false), { once: true });
            timeout = setTimeout(() => controller.abort(), 400);
          }),
        ]);
      } catch {
        return false;
      } finally {
        clearTimeout(timeout);
        pending.delete(controller);
        windowRequests.delete(controller);
        if (windowRequests.size === 0 && pendingByWindow.get(event.sender.id) === windowRequests) {
          pendingByWindow.delete(event.sender.id);
        }
      }
    },
    stop() {
      active = false;
      for (const controller of pending) controller.abort();
      pending.clear();
      pendingByWindow.clear();
      windows.clear();
      lastRequest.clear();
      if (typeof transport.close === "function") transport.close();
    },
  };
}

module.exports = { createBridge };
