import { useState } from "react";
import { loginFD } from "../data/api.js";
import mkLogo from "../images/mk-logo.png";
import loginIllustration from "../images/login-globe.png";

export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("Please enter your username and password.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await loginFD(username.trim(), password);
      onLogin(data);
    } catch (err) {
      setError(err.message || "Invalid credentials.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <img src={mkLogo} alt="Law Offices of Mary Kennedy" className="login-firm-logo" />
      <div className="login-card">
        <div className="login-illustration">
          <div className="login-logo">
            <span><span className="login-logo-flint">Flint</span><b>Desk</b></span>
          </div>
          <img src={loginIllustration} alt="World map with an airplane route, representing international immigration work" className="login-illustration-image" />
        </div>

        <div className="login-form-panel">
          <h1>Login</h1>
          <p className="login-subtitle">Sign in to access your FlintDesk workspace.</p>

          <form onSubmit={handleSubmit} className="login-form">
            <label className="login-field">
              <div className="date-input">
                <i className="fa-regular fa-user" />
                <input
                  type="text"
                  placeholder="Username"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                />
              </div>
            </label>

            <label className="login-field">
              <div className="date-input">
                <i className="fa-solid fa-lock" />
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder="Password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="login-eye-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  <i className={`fa-regular ${showPassword ? "fa-eye-slash" : "fa-eye"}`} />
                </button>
              </div>
            </label>

            {error && <p className="login-error">{error}</p>}

            <button type="submit" className="primary-button login-submit" disabled={loading}>
              {loading ? "Signing in..." : "Login"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
