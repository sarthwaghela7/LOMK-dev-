import pytest

from ops.load_test_15_users import validate_users


def test_requires_exactly_15_unique_test_calls():
    users = [{"call_id": str(index)} for index in range(15)]
    validate_users(users)
    with pytest.raises(ValueError, match="Exactly 15"):
        validate_users(users[:14])
    users[-1]["call_id"] = users[0]["call_id"]
    with pytest.raises(ValueError, match="unique"):
        validate_users(users)
