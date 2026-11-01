export const normalizeRoutingNumber = (value) => {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
};

// Canonical lead groups. 601/603/605 remain routing destinations, but must never
// be presented as selectable extensions in the UI.
export const LEAD_NUMBER_GROUPS = Object.freeze({
  "3128781255": Object.freeze({ routingNumbers: Object.freeze(["601"]), extensions: Object.freeze(["101", "102", "255"]) }),
  "2243374447": Object.freeze({ routingNumbers: Object.freeze(["603"]), extensions: Object.freeze(["201", "202", "4447"]) }),
  "2243939098": Object.freeze({ routingNumbers: Object.freeze(["605"]), extensions: Object.freeze(["203", "204", "205"]) }),
});
export const LEAD_NUMBER_OPTIONS = Object.freeze(["3128781255", "2243374447", "2243939098"]);
// These are valid stored extension values as well as the short internal
// extensions in the routing groups. Keep them selectable without treating a
// lead number as an extension during filtering.
export const SUPPORTED_EXTENSIONS = Object.freeze([
  "101", "102", "201", "202", "203", "204", "205", "3128781255", "2243374447", "2243939098",
]);
export const NON_SELECTABLE_ROUTING_EXTENSIONS = Object.freeze(new Set(["601", "603", "605"]));

// 255/4447 are legacy short aliases rendered as the lead phone numbers. They
// are routing artefacts, not values in calls.extension, so including both
// produced duplicate visible options.
export const isSelectableExtension = (extension) =>
  !NON_SELECTABLE_ROUTING_EXTENSIONS.has(String(extension).trim()) &&
  !new Set(["255", "4447"]).has(String(extension).trim());

// The lead number itself should also be selectable as an "extension" option
// (representing calls on the main line), mapped back to the legacy alias
// actually stored in calls.extension for matching.
export const ALIAS_EXTENSION_FOR_LEAD_NUMBER = Object.freeze({
  "3128781255": "255",
  "2243374447": "4447",
});

export const callLeadNumbers = (call) => [
  call.parentNumber,
  call.raw?.parent_number,
  ...(call.capturedNumbers || []),
].map(normalizeRoutingNumber).filter(Boolean);

export const getOutboundLeadNumberOptions = () => [...LEAD_NUMBER_OPTIONS];

export const getLeadNumberExtensions = (leadNumber) => {
  const normalized = normalizeRoutingNumber(leadNumber);
  const group = LEAD_NUMBER_GROUPS[normalized];
  if (!group) return [];
  const extensions = group.extensions.filter(isSelectableExtension);
  return ALIAS_EXTENSION_FOR_LEAD_NUMBER[normalized]
    ? [...extensions, normalized]
    : extensions;
};

export const getAllLeadExtensions = () =>
  [...new Set([
    ...Object.values(LEAD_NUMBER_GROUPS)
      .flatMap((group) => group.extensions)
      .filter(isSelectableExtension),
    ...SUPPORTED_EXTENSIONS,
  ])].sort();

export const displayExtensionOption = (extension) => String(extension);

export const callMatchesLeadNumber = (call, leadNumber, _allCalls = [], _syncFilters = []) => {
  if (!leadNumber) return true;
  const expected = normalizeRoutingNumber(leadNumber);
  const group = LEAD_NUMBER_GROUPS[expected];
  const source = normalizeRoutingNumber(call.sourceNumber || call.raw?.src);
  const destination = normalizeRoutingNumber(call.destinationNumber || call.raw?.dst);
  return (
    source === expected ||
    destination === expected ||
    Boolean(group?.routingNumbers.includes(destination))
  );
};
