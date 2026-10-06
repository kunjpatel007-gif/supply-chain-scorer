"""Shared project paths and .env loading for all components."""
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def env_file_path():
    return os.path.join(PROJECT_ROOT, '.env')


def load_env():
    from dotenv import load_dotenv

    load_dotenv(env_file_path())
    legacy = os.path.join(PROJECT_ROOT, 'desktop_app', '.env')
    if os.path.exists(legacy):
        load_dotenv(legacy, override=False)


def write_env(user, password, dsn):
    with open(env_file_path(), 'w', encoding='utf-8') as f:
        f.write(f"DB_USER={user}\n")
        f.write(f"DB_PASSWORD={password}\n")
        f.write(f"DB_DSN={dsn}\n")
