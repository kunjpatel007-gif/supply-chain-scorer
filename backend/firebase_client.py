"""
firebase_client.py
------------------
Single source of truth for the Firebase Admin SDK.
Import `db` from here everywhere else — never initialize Firebase twice.
"""
import os
import firebase_admin
from firebase_admin import credentials, firestore

import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from project_env import load_env

load_env()


def _init_firebase():
    if firebase_admin._apps:
        return  # already initialized

    cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-key.json')

    if os.getenv('FIRESTORE_EMULATOR_HOST'):
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred, {'projectId': 'demo-olist'})
    elif os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred, {'projectId': 'dbms-d424e'})


_init_firebase()
db = firestore.client()
