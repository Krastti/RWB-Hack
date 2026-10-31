"""Create the configured initial user once, without changing existing accounts."""
import os
from uuid import uuid4

from sqlalchemy import create_engine, text
from .services.auth import hash_password


def main():
    login = os.environ.get('PANEL_LOGIN', '').strip()
    password = os.environ.get('PANEL_PASSWORD', '')
    if not login or len(login) > 120 or not password:
        raise ValueError('Set PANEL_LOGIN (1–120 characters) and PANEL_PASSWORD')
    engine = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO users (id, login, password_hash) VALUES (:id, :login, :password_hash) ON CONFLICT (login) DO NOTHING'),
                           {'id': uuid4(), 'login': login, 'password_hash': hash_password(password)})
    engine.dispose()


if __name__ == '__main__':
    main()
