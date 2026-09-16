from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal, checkpoint_database
from app.models import User
from app.security import hash_password


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("密码至少需要 12 个字符")
    if not any(character.isalpha() for character in password):
        raise ValueError("密码至少需要一个字母")
    if not any(character.isdigit() for character in password):
        raise ValueError("密码至少需要一个数字")


def reset_password(db: Session, username: str, password: str) -> None:
    validate_password(password)
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or user.role != "admin":
        raise ValueError("管理员账号不存在")
    user.password_hash = hash_password(password)
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="交互式重置本地管理员密码（密码不会显示或写入 Git）")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    first = getpass.getpass("输入新管理员密码（至少12位，含字母和数字）：")
    second = getpass.getpass("再次输入：")
    if first != second:
        raise SystemExit("两次输入的密码不一致")
    with SessionLocal() as db:
        reset_password(db, args.username, first)
    checkpoint_database()
    print(f"管理员 {args.username} 的密码已更新。请只通过安全渠道告知队友。")


if __name__ == "__main__":
    main()
