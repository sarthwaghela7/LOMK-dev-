import React from "react";

const styles = {
  page: {
    minHeight: "100vh",
    width: "100%",
    backgroundColor: "#0a0a0f",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontFamily:
      "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    padding: "24px",
  },
  card: {
    width: "100%",
    maxWidth: "460px",
    backgroundColor: "#121218",
    border: "1px solid #232330",
    borderRadius: "16px",
    padding: "48px 40px",
    textAlign: "center",
    boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
  },
  iconWrap: {
    width: "72px",
    height: "72px",
    margin: "0 auto 24px",
    borderRadius: "50%",
    backgroundColor: "rgba(245, 158, 11, 0.1)",
    border: "1px solid rgba(245, 158, 11, 0.25)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  brand: {
    fontSize: "28px",
    fontWeight: 800,
    marginBottom: "8px",
    background: "linear-gradient(90deg, #ffffff, #a1a1aa)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    letterSpacing: "-0.5px",
  },
  title: {
    fontSize: "20px",
    fontWeight: 700,
    color: "#f4f4f5",
    marginBottom: "10px",
  },
  subtitle: {
    fontSize: "14px",
    color: "#9b9ba5",
    lineHeight: 1.6,
    marginBottom: "32px",
  },
  statusRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    fontSize: "12px",
    color: "#71717a",
    backgroundColor: "#1a1a22",
    border: "1px solid #232330",
    borderRadius: "8px",
    padding: "10px 14px",
    marginBottom: "28px",
  },
  dot: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    backgroundColor: "#f59e0b",
    boxShadow: "0 0 8px rgba(245, 158, 11, 0.8)",
  },
  buttonRow: {
    display: "flex",
    gap: "12px",
  },
  primaryButton: {
    flex: 1,
    backgroundColor: "#f4f4f5",
    color: "#0a0a0f",
    border: "none",
    borderRadius: "10px",
    padding: "13px 20px",
    fontSize: "14px",
    fontWeight: 700,
    cursor: "pointer",
  },
  secondaryButton: {
    flex: 1,
    backgroundColor: "transparent",
    color: "#d4d4d8",
    border: "1px solid #2b2b38",
    borderRadius: "10px",
    padding: "13px 20px",
    fontSize: "14px",
    fontWeight: 600,
    cursor: "pointer",
  },
  primaryButtonFull: {
    width: "100%",
    backgroundColor: "#f4f4f5",
    color: "#0a0a0f",
    border: "none",
    borderRadius: "10px",
    padding: "13px 20px",
    fontSize: "14px",
    fontWeight: 700,
    cursor: "pointer",
  },
  footer: {
    marginTop: "24px",
    fontSize: "12px",
    color: "#52525b",
  },
};

export default function UnderMaintenance({ onRetry }) {
  const handleRetry = () => {
    if (onRetry) {
      onRetry();
    } else {
      window.location.reload();
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.iconWrap}>
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#f59e0b"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </div>

        <div style={styles.brand}>AutoFlint</div>
        <div style={styles.title}>We're making things better</div>
        <div style={styles.subtitle}>
          AutoFlint is currently undergoing scheduled maintenance. We're
          rolling out updates and improvements. The site will be back
          shortly — thanks for your patience.
        </div>

        <div style={styles.statusRow}>
          <span style={styles.dot} />
          Status: Under Maintenance
        </div>

        <div style={styles.buttonRow}>
          <button style={styles.primaryButtonFull} onClick={handleRetry}>
            Check Again
          </button>
        </div>

        <div style={styles.footer}>
          Expected back online soon. Follow updates or contact your
          administrator for details.
        </div>
      </div>
    </div>
  );
}