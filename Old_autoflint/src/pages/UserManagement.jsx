import { useState, useEffect } from "react";
import {
  getUsers,
  createUser,
  updateUser,
  deleteUser,
  resetUserPassword,
} from "../data/api";

function UserManagement() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
    is_superuser: false,
  });

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      setUsers(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const payload = {
        ...formData,
        role: formData.is_superuser ? "superuser" : "user",
      };
      if (editingUser) {
        await updateUser(editingUser.id, payload);
      } else {
        await createUser(payload);
      }
      setShowForm(false);
      setEditingUser(null);
      setFormData({ name: "", email: "", password: "", is_superuser: false });
      fetchUsers();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm("Are you sure you want to delete this user?")) return;
    try {
      await deleteUser(id);
      fetchUsers();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleResetPassword = async (id) => {
    const newPassword = prompt("Enter new password:");
    if (!newPassword) return;
    try {
      await resetUserPassword(id, newPassword);
      alert("Password reset successfully");
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="user-management-page">
      <div className="page-header" style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr", alignItems: "center" }}>
        <div /> {/* empty spacer on left */}
        <h1>User Management</h1>
        <button
          onClick={() => {
            setShowForm(true);
            setEditingUser(null);
            setFormData({
              name: "",
              email: "",
              password: "",
              is_superuser: false,
            });
          }}
          className="btn-primary"
          style={{ justifySelf: "end" }}
        >
          Add User
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}

      {showForm && (
        <form onSubmit={handleSubmit} className="user-form">
          <input
            type="text"
            placeholder="Name"
            value={formData.name}
            onChange={(e) =>
              setFormData((d) => ({ ...d, name: e.target.value }))
            }
            required
          />
          <input
            type="email"
            placeholder="Email"
            value={formData.email}
            onChange={(e) =>
              setFormData((d) => ({ ...d, email: e.target.value }))
            }
            required
          />
          {!editingUser && (
            <input
              type="password"
              placeholder="Password"
              value={formData.password}
              onChange={(e) =>
                setFormData((d) => ({ ...d, password: e.target.value }))
              }
              required
            />
          )}
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={formData.is_superuser}
              onChange={(e) =>
                setFormData((d) => ({ ...d, is_superuser: e.target.checked }))
              }
            />
            Super User
          </label>
          <div className="form-actions">
            <button type="submit" className="btn-primary">
              {editingUser ? "Update" : "Create"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="btn-secondary"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="table-wrapper">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>{user.name}</td>
                  <td>{user.email}</td>
                  <td>{user.is_superuser ? "Super User" : "User"}</td>
                  <td className="actions">
                    <button
                      onClick={() => {
                        setEditingUser(user);
                        setFormData(user);
                        setShowForm(true);
                      }}
                      className="btn-icon"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleResetPassword(user.id)}
                      className="btn-icon"
                    >
                      Reset PW
                    </button>
                    <button
                      onClick={() => handleDelete(user.id)}
                      className="btn-icon danger"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {users.length === 0 && !loading && (
                <tr>
                  <td colSpan="4" className="no-data">
                    No users found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default UserManagement;
