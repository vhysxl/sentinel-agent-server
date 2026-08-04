import sys
import os

# Add current directory to path so python can find the 'app' module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import engine, Base
from app.db import models

def run_migrations():
    print("Connecting to database...")
    print("Creating tables (users, vendors, transactions)...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")

if __name__ == "__main__":
    run_migrations()
