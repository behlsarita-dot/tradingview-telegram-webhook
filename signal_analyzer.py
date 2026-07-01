#!/usr/bin/env python3
"""Signal Analyzer - Paper Trading System v7.0"""

import sys, os
from datetime import datetime

def main():
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "NIFTY"
    print(f"\n=== SIGNAL ANALYZER v7.0 ===")
    print(f"Symbol: {symbol} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        from enhanced_strategy import quick_analysis
        result = quick_analysis(symbol)
    except Exception as e:
        print(f"Analysis failed: {e}")
        return

    signal  = result.get("signal", "NONE")
    quality = result.get("quality", "LOW")
    score   = result.get("confluence_score", 0)
    price   = result.get("entry_price", 0)
    sl      = result.get("stop_loss")
    tp      = result.get("take_profit")
    adx     = result.get("adx", 0)

    print(f"Signal:     {signal}")
    print(f"Quality:    {quality}")
    print(f"Confluence: {score}/5")
    print(f"ADX:        {adx:.1f}")
    print(f"Price:      Rs.{price:,.2f}")
    if sl: print(f"Stop Loss:  Rs.{sl:,.2f}")
    if tp: print(f"Take Profit:Rs.{tp:,.2f}")

    if result.get("fallback"):
        print("\nNote: Using fallback data (yfinance unavailable)")

    print("\nFactors:")
    for r in result.get("reasons", []):
        print(f"  - {r}")

    try:
        from database import DatabaseManager
        from risk_manager import create_risk_manager
        from config import INITIAL_CAPITAL, TRADING_MODE
        db = DatabaseManager()
        rm = create_risk_manager(db, INITIAL_CAPITAL)
        is_valid, details = rm.validate_new_trade(symbol, price, sl or 0, tp or 0, TRADING_MODE)
        print(f"\nRisk Check: {'APPROVED' if is_valid else 'REJECTED'}")
        print(f"  {details.get('message', '')}")
        if is_valid and sl:
            size = rm.calculate_position_size(price, sl, TRADING_MODE)
            print(f"  Recommended size: {size} units")
    except Exception as e:
        print(f"\nRisk check unavailable: {e}")

if __name__ == "__main__":
    main()
