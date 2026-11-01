const CLIENT_TYPE_DISPLAY_LABELS = Object.freeze({
  "New Client - Organization": "New Client - Organisation",
  "Existing client - Individual": "Existing Client - Individual",
  "Existing client - Organization": "Existing Client - Organisation",
});

export function displayClientType(value) {
  return CLIENT_TYPE_DISPLAY_LABELS[value] || value;
}
