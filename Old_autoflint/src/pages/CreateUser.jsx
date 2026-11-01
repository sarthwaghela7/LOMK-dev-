import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { createUser } from "../data/api";
import { User, Mail, Phone, Lock, Shield, AlertCircle, CheckCircle } from "lucide-react";

function CreateUser() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
    mob_no: "",
    role: "user",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [validationErrors, setValidationErrors] = useState({});


  const validateForm = () => {
    const errors = {};

    if (!formData.name.trim()) {
      errors.name = "Full name is required";
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!formData.email.trim()) {
      errors.email = "Email is required";
    } else if (!emailRegex.test(formData.email)) {
      errors.email = "Please enter a valid email address";
    }

    if (!formData.password) {
      errors.password = "Password is required";
    } else if (formData.password.length < 8) {
      errors.password = "Password must be at least 8 characters";
    }

    if (formData.mob_no && !/^[0-9\s\-\+\(\)]+$/.test(formData.mob_no)) {
      errors.mob_no = "Please enter a valid phone number";
    }

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    // Clear error for this field when user starts typing
    if (validationErrors[name]) {
      setValidationErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[name];
        return newErrors;
      });
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!validateForm()) {
      return;
    }

    setLoading(true);

    try {
      const response = await createUser({
        name: formData.name.trim(),
        email: formData.email.trim(),
        password: formData.password,
        mob_no: formData.mob_no.trim() || null,
        role: formData.role,
      });

      setSuccess(`User ${response.email} created successfully! They can now log in.`);
      
      // Reset form after 2 seconds
      setTimeout(() => {
        navigate("/users");
      }, 2000);
    } catch (err) {
      setError(err.message || "Unable to create user. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const FormField = ({ label, icon: Icon, error, ...inputProps }) => (
    <div className="form-field">
      <label className="form-label">{label}</label>
      <div className="form-input-wrapper">
        {Icon && <Icon size={18} className="form-input-icon" />}
        <input
          {...inputProps}
          className={`form-input ${error ? "input-error" : ""}`}
        />
      </div>
      {error && (
        <div className="form-error">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}
    </div>
  );

  return (
    <div className="create-user-page">
      <div className="page-header">
        <div>
          <h1>Create New User</h1>
          <p>Add a new user account to the system</p>
        </div>
      </div>

      <div className="create-user-container">
        {error && (
          <div className="alert alert-error">
            <AlertCircle size={20} />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div className="alert alert-success">
            <CheckCircle size={20} />
            <span>{success}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="create-user-form">
          <div className="form-section">
            <h2>User Information</h2>
            
            <FormField
              label="Full Name"
              icon={User}
              type="text"
              name="name"
              placeholder="John Doe"
              value={formData.name}
              onChange={handleChange}
              error={validationErrors.name}
              required
            />

            <FormField
              label="Email Address"
              icon={Mail}
              type="email"
              name="email"
              placeholder="john.doe@example.com"
              value={formData.email}
              onChange={handleChange}
              error={validationErrors.email}
              required
            />

            <FormField
              label="Phone Number"
              icon={Phone}
              type="tel"
              name="mob_no"
              placeholder="+1 (555) 123-4567"
              value={formData.mob_no}
              onChange={handleChange}
              error={validationErrors.mob_no}
            />
          </div>

          <div className="form-section">
            <h2>Account Security</h2>
            
            <FormField
              label="Password"
              icon={Lock}
              type="password"
              name="password"
              placeholder="Min. 8 characters"
              value={formData.password}
              onChange={handleChange}
              error={validationErrors.password}
              required
            />
          </div>

          <div className="form-section">
            <h2>User Role</h2>
            
            <div className="role-selection">
              <label className="role-radio">
                <input
                  type="radio"
                  name="role"
                  value="user"
                  checked={formData.role === "user"}
                  onChange={handleChange}
                />
                <div className="role-radio-content">
                  <span className="role-radio-title">Regular User</span>
                  <span className="role-radio-desc">Can view and analyze call records</span>
                </div>
              </label>

              <label className="role-radio">
                <input
                  type="radio"
                  name="role"
                  value="superuser"
                  checked={formData.role === "superuser"}
                  onChange={handleChange}
                />
                <div className="role-radio-content">
                  <Shield size={16} className="role-radio-icon" />
                  <div>
                    <span className="role-radio-title">Super User</span>
                    <span className="role-radio-desc">Full system access and user management</span>
                  </div>
                </div>
              </label>
            </div>
          </div>

          <div className="form-actions">
            <button 
              type="submit" 
              className="btn-primary" 
              disabled={loading}
            >
              {loading ? "Creating User..." : "Create User"}
            </button>
            <button 
              type="button" 
              className="btn-secondary" 
              onClick={() => navigate("/users")}
              disabled={loading}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default CreateUser;
