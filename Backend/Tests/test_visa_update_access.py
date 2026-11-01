import pytest
from fastapi import HTTPException

from middleware.auth import require_fd_visa_update_editor


@pytest.mark.parametrize("role", ["admin", "team_lead", "team lead", "subject_matter_expert", "sme", "superuser"])
def test_visa_update_editor_accepts_authorized_roles(role):
    user = {"role": role}
    assert require_fd_visa_update_editor(user) is user


@pytest.mark.parametrize("role", ["agent", "", "viewer"])
def test_visa_update_editor_rejects_other_roles(role):
    with pytest.raises(HTTPException) as exception:
        require_fd_visa_update_editor({"role": role})
    assert exception.value.status_code == 403
