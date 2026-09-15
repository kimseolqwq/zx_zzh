from app.security import hash_password, verify_password


def test_password_hash_is_not_plaintext() -> None:
    password = "Strong-Test-Password-2026"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(hashed, password)
    assert not verify_password(hashed, "wrong-password")

