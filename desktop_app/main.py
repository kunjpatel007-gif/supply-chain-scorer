import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import messagebox

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def check_db_connection(username, password, dsn):
    try:
        import oracledb
        conn = oracledb.connect(user=username, password=password, dsn=dsn)
        conn.close()
        return True
    except Exception as e:
        return str(e)

def setup_gui():
    root = tk.Tk()
    root.title("Oracle Database Setup")
    root.geometry("400x300")
    
    tk.Label(root, text="Welcome to the Supply Chain Scorer!").pack(pady=10)
    tk.Label(root, text="Please enter your Oracle 21c Database credentials:").pack(pady=5)
    
    tk.Label(root, text="Username:").pack()
    user_entry = tk.Entry(root)
    user_entry.pack()
    user_entry.insert(0, "system")
    
    tk.Label(root, text="Password:").pack()
    pass_entry = tk.Entry(root, show="*")
    pass_entry.pack()
    
    tk.Label(root, text="Database DSN (Host:Port/Service):").pack()
    dsn_entry = tk.Entry(root)
    dsn_entry.pack()
    dsn_entry.insert(0, "localhost:1521/XEPDB1")
    
    result = {}
    
    def on_submit():
        u = user_entry.get()
        p = pass_entry.get()
        d = dsn_entry.get()
        
        status = check_db_connection(u, p, d)
        if status is True:
            result['user'] = u
            result['pass'] = p
            result['dsn'] = d
            root.destroy()
        else:
            messagebox.showerror("Connection Failed", f"Could not connect to Oracle:\n{status}")
            
    tk.Button(root, text="Connect & Launch", command=on_submit).pack(pady=20)
    
    root.mainloop()
    return result

def bootstrap_database(creds):
    import oracledb
    conn = oracledb.connect(user=creds['user'], password=creds['pass'], dsn=creds['dsn'])
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'SELLER'")
    count = cursor.fetchone()[0]
    conn.close()
    
    if count == 0:
        print("Empty database detected! Bootstrapping schema...")
        schema_path = os.path.join(get_base_path(), 'schema_setup.sql')
        if not os.path.exists(schema_path):
            print("ERROR: schema_setup.sql not found!")
            return
            
        # Use sqlplus to execute the script reliably
        conn_str = f"{creds['user']}/{creds['pass']}@{creds['dsn']}"
        try:
            # We exit to avoid SQL*Plus hanging if it waits for input
            with open(schema_path, 'a') as f:
                f.write("\nEXIT;\n")
            subprocess.run(['sqlplus', conn_str, f'@{schema_path}'], check=True)
            print("Schema successfully bootstrapped!")
        except Exception as e:
            print(f"Warning: sqlplus execution failed. {e}")

def main():
    creds = setup_gui()
    if not creds:
        print("Setup aborted.")
        sys.exit(0)
        
    env_path = os.path.join(get_base_path(), '.env')
    with open(env_path, 'w') as f:
        f.write(f"DB_USER={creds['user']}\n")
        f.write(f"DB_PASSWORD={creds['pass']}\n")
        f.write(f"DB_DSN={creds['dsn']}\n")
        
    bootstrap_database(creds)
    
    sys.path.insert(0, get_base_path())
    try:
        import cryptography.hazmat.primitives.kdf.pbkdf2
        import cryptography.hazmat.backends
        import webview
        from app import app as flask_app
    except Exception as e:
        messagebox.showerror("Error", f"Failed to load Flask App:\n{e}")
        sys.exit(1)
        
    window = webview.create_window('Supply Chain Vendor Risk Dashboard', flask_app, width=1200, height=800)
    webview.start()

if __name__ == '__main__':
    main()
