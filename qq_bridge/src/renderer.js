"use strict";

// Deliberately inactive without a verified version-specific message adapter.
// Do not scrape generic DOM containers, contacts, sidebars, or document titles.
// A trusted adapter may report {target, message_id} through reportTarget; the
// main process must resolve the actual account, conversation, and message list.
