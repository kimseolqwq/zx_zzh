from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User
from app.security import hash_password, verify_password
from scripts.reset_admin_password import reset_password, validate_password


def test_reset_admin_password_updates_hash() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(username="admin", password_hash=hash_password("OldPassword123"), role="admin"))
        db.commit()
        reset_password(db, "admin", "NewPassword456")
        user = db.query(User).filter_by(username="admin").one()
        assert verify_password(user.password_hash, "NewPassword456")


def test_password_policy_rejects_short_password() -> None:
    try:
        validate_password("short1")
    except ValueError as exc:
        assert "12" in str(exc)
    else:
        raise AssertionError("short password should be rejected")
