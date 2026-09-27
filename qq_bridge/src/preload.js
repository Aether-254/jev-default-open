"use strict";

const { contextBridge, ipcRenderer } = require("electron");
const { validateClick } = require("./protocol");

contextBridge.exposeInMainWorld("jevDefaultOpen", Object.freeze({
  reportTarget(value) {
    const click = validateClick(value);
    if (!click) return false;
    ipcRenderer.send("jev-default-open:target", click);
    return true;
  },
}));
