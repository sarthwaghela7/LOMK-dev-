from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from utils.jwt import verify_token

security = HTTPBearer(auto_error=False)
optional_security = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = credentials.credentials
    payload = verify_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    return payload


def get_optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(optional_security)):
    if not credentials:
        return None
    payload = verify_token(credentials.credentials)
    return payload or None


def require_role(required_role: str):
    def role_checker(user=Depends(get_current_user)):
        user_role = user.get("role", "").lower()
        # Normalize both formats
        if user_role == "super_user":
            user_role = "superuser"
        required_normalized = required_role.lower()
        if required_normalized == "super_user":
            required_normalized = "superuser"
        
        if user_role != required_normalized:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return role_checker


def require_fd_team_lead(user=Depends(get_current_user)):
    """Restrict FlintDesk configuration controls to explicit privileged roles."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    if role not in {"admin", "team_lead", "superuser", "super_user"}:
        raise HTTPException(status_code=403, detail="Team lead access required")
    return user


def require_fd_admin(user=Depends(get_current_user)):
    """Settings changes are admin-only even when reviewers can use the app."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    if role not in {"admin", "superuser", "super_user"}:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_fd_settings_view(user=Depends(get_current_user)):
    """Settings are visible to operational reviewers, but mutations stay admin-only."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    if role not in {"admin", "team_lead", "subject_matter_expert", "sme", "superuser", "super_user"}:
        raise HTTPException(status_code=403, detail="Settings access required")
    return user


def require_fd_dependency_reviewer(user=Depends(get_current_user)):
    """SMEs and Team Leads share persisted dependency-review authority."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    if role not in {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}:
        raise HTTPException(status_code=403, detail="Dependency reviewer access required")
    return user


def require_fd_visa_update_editor(user=Depends(get_current_user)):
    """Allow operational reviewers to publish Visa Updates."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_").replace("-", "_")
    if role not in {"admin", "team_lead", "teamlead", "subject_matter_expert", "subjectmatterexpert", "sme", "superuser", "super_user"}:
        raise HTTPException(status_code=403, detail="Visa update editor access required")
    return user


PRIORITY_CASES_ROLES = {"admin", "team_lead", "teamlead", "subject_matter_expert", "subjectmatterexpert", "sme", "superuser", "super_user"}


def has_priority_cases_access(user) -> bool:
    role = str(user.get("role", "")).strip().lower().replace(" ", "_").replace("-", "_")
    return role in PRIORITY_CASES_ROLES


def require_fd_priority_cases(user=Depends(get_current_user)):
    """Priority Cases: Team Leads, Subject Matter Experts and Admins (same reviewer set as Visa Updates)."""
    if not has_priority_cases_access(user):
        raise HTTPException(status_code=403, detail="Priority Cases access required")
    return user
