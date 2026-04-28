import os
import re

def fix_tws_port():
    files_to_check = ['test_tws.py', 'config.py', 'tws_data.py', 'order_server.py']
    fixed_files = []
    
    for filename in files_to_check:
        if not os.path.exists(filename):
            continue
            
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
            
        original_content = content
        
        # Look for explicit PORT = 7497 declarations
        content = re.sub(r'PORT\s*=\s*7497', 'PORT = 7496', content)
        
        # Look for inline connections like ib.connect('127.0.0.1', 7497, ...)
        content = re.sub(r'ib\.connect\((.*?),\s*7497\s*,', r'ib.connect(\1, 7496,', content)
        
        if content != original_content:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            fixed_files.append(filename)
            
    if fixed_files:
        print(f"✅  Successfully updated port to 7496 (Live) in: {', '.join(fixed_files)}")
    else:
        print("ℹ️  No files found with hardcoded port 7497.")

if __name__ == '__main__':
    print("🚀 Running Port Fixer...")
    fix_tws_port()
    print("🎉 Done! Run pytest to verify.")