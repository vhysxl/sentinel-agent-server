import sys
import os

# Add current directory to path so python can find the 'app' module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import engine, Base
from app.db import models

from sqlalchemy import text

def run_migrations():
    print("Connecting to database...")
    print("Creating tables (users, vendors, transactions)...")
    Base.metadata.create_all(bind=engine)
    
    # Add password column if not exists (for existing tables)
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password VARCHAR(255) DEFAULT 'default_pass' NOT NULL;"))
            print("Ensured password column exists in users table.")
        except Exception as e:
            print(f"Note on altering table: {e}")
            
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS fullname VARCHAR(100);"))
            print("Ensured fullname column exists in users table.")
        except Exception as e:
            print(f"Note on altering table: {e}")

    print("Tables created successfully!")

if __name__ == "__main__":
    run_migrations()
