"""Shared project paths and .env loading for all components."""
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def env_file_path():
    return os.path.join(PROJECT_ROOT, '.env')


def load_env():
    from dotenv import load_dotenv

    load_dotenv(env_file_path())
