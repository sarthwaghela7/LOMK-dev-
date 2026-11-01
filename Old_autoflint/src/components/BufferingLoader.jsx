import "../styles/buffering-loader.css";

export function BufferingLoader({ text = "Loading", size = "md" }) {
  return (
    <div className={`buffering-loader buffering-loader-${size}`}>
      <div className="buffering-spinner">
        <div className="buffering-ring"></div>
        <div className="buffering-ring"></div>
        <div className="buffering-ring"></div>
      </div>
      {text && <p className="buffering-text">{text}</p>}
    </div>
  );
}

export function BufferingOverlay({ loading, text = "Loading" }) {
  if (!loading) return null;
  
  return (
    <div className="buffering-overlay">
      <BufferingLoader text={text} size="lg" />
    </div>
  );
}
