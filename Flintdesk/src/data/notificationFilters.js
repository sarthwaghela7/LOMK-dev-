export const NOTIFICATION_FILTERS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "read", label: "Read" },
];

/** The same rule the server applies (read_at set = read); used to keep the open list in step after local changes. */
export function filterNotifications(items, filter) {
  const list = items || [];
  if (filter === "unread") return list.filter((item) => !item.read_at);
  if (filter === "read") return list.filter((item) => item.read_at);
  return list;
}

export function emptyNotificationMessage(filter) {
  if (filter === "unread") return "No unread notifications.";
  if (filter === "read") return "No read notifications.";
  return "No notifications.";
}
