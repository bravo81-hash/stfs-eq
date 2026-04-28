import os
import re

def patch_backtest():
    if not os.path.exists('backtest.py'): 
        print("⚠️ backtest.py not found.")
        return
        
    with open('backtest.py', 'r', encoding='utf-8') as f: 
        content = f.read()
        
    lines = content.split('\n')
    changed = False
    
    for i, line in enumerate(lines):
        # Dynamically wrap whatever calculates the distance in abs()
        if 'stop_loss_dist =' in line and 'abs(' not in line:
            parts = line.split('=', 1)
            lines[i] = parts[0] + '= abs(' + parts[1].strip() + ')'
            changed = True
            
    if changed:
        with open('backtest.py', 'w', encoding='utf-8') as f: 
            f.write('\n'.join(lines))
        print("✅  Successfully patched backtest.py (Absolute Value Sizing)")
    else:
        print("ℹ️  backtest.py sizing already uses abs() or pattern not found.")

def patch_pine_momentum():
    fname = 'STFS Momentun Panel v3.pine'
    if not os.path.exists(fname): 
        print(f"⚠️ {fname} not found.")
        return
        
    with open(fname, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Super robust replacement: finds the variable and replaces everything after the '='
    if 'ta.atr(60)' not in content:
        content = re.sub(
            r'(bonus_atr_expansion\s*=).*', 
            r'\1 (ta.atr(10) / close * 100) / (ta.atr(60) / close * 100) > 1.10', 
            content
        )
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅  Successfully patched {fname} (ATR Expansion Sync)")
    else:
         print(f"ℹ️  {fname} is already synced.")

def patch_pine_v25():
    fname = 'STFS v2.5.pine'
    if not os.path.exists(fname): 
        print(f"⚠️ {fname} not found.")
        return
        
    with open(fname, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Add SPY Regime Filter if missing
    if 'marketRegimeBullish' not in content and 'longCondition =' in content:
        regime_code = """
// --- REGIME FILTER ---
spyClose = request.security("SPY", "D", close)
spyEma = request.security("SPY", "D", ta.ema(close, 200))
marketRegimeBullish = spyClose > spyEma
"""
        # Inject regime code above longCondition
        content = re.sub(r'(longCondition\s*=)', regime_code + r'\n\1', content)
        
        # Append regime boolean to the actual condition
        content = re.sub(r'(longCondition\s*=\s*[^\n]+)', r'\1 and marketRegimeBullish', content)
        
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅  Successfully patched {fname} (SPY Regime Sync)")
    else:
        print(f"ℹ️  {fname} is already synced.")

if __name__ == '__main__':
    print("🚀 Running Final Math & Pine Sync Patcher...")
    patch_backtest()
    patch_pine_momentum()
    patch_pine_v25()
    print("🎉 All automated syncs complete! You can commit and push.")