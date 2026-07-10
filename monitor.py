# monitor.py (WITH LONGER TIMEOUTS)
import requests
import json
from datetime import datetime
import time

def monitor_trading_system():
    base_url = "https://tradingview-telegram-webhook-dpaj.onrender.com"
    
    # Send a warm-up request first
    print("🔥 Warming up server (this may take 30-60 seconds)...")
    try:
        requests.get(f"{base_url}/health", timeout=60)
        print("✅ Server is awake!\n")
    except requests.exceptions.RequestException:
        # NOTE (fixed 2026-07-10): was a bare `except:`, which also
        # catches KeyboardInterrupt - a Ctrl+C during this warm-up
        # request would get swallowed instead of stopping the script.
        # Narrowed to network/timeout errors only.
        print("⏳ Server is starting up, waiting 30 seconds...")
        time.sleep(30)
    
    while True:
        print("\n" + "=" * 70)
        print(f"📊 TRADING SYSTEM MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        # Get Account Info with longer timeout
        try:
            response = requests.get(f"{base_url}/api/account", timeout=30)  # Increased from 10 to 30
            if response.status_code == 200:
                acc = response.json()
                print(f"\n💰 CAPITAL: ₹{acc.get('current_capital', 0):,.2f} "
                      f"(PnL: ₹{acc.get('total_pnl', 0):,.2f} | "
                      f"Daily: ₹{acc.get('daily_pnl', 0):,.2f})")
                print(f"📊 Trades: {acc.get('total_trades', 0)} total "
                      f"({acc.get('win_rate', 0):.1f}% win rate)")
            else:
                print(f"\n❌ Account error: {response.status_code}")
        except requests.exceptions.Timeout:
            print("\n⏳ Server is waking up (cold start), waiting...")
            time.sleep(20)  # Give it more time
        except Exception as e:
            print(f"\n❌ Account error: {str(e)[:50]}")
        
        # Get Risk Report with longer timeout
        try:
            response = requests.get(f"{base_url}/api/risk/report", timeout=30)  # Increased from 10 to 30
            if response.status_code == 200:
                risk = response.json()
                status = "🟢 TRADING" if risk.get('can_trade') else "🔴 BLOCKED"
                print(f"\n{status} | "
                      f"Drawdown: {risk.get('current_drawdown', 0):.1f}% | "
                      f"Heat: {risk.get('portfolio_heat', 0):.1f}% | "
                      f"Positions: {risk.get('open_positions', 0)}")
                if not risk.get('can_trade') and risk.get('circuit_breaker_reason'):
                    print(f"⚠️  Reason: {risk.get('circuit_breaker_reason')}")
            else:
                print(f"\n❌ Risk error: {response.status_code}")
        except requests.exceptions.Timeout:
            print("\n⏳ Risk data: Server is waking up...")
        except Exception as e:
            print(f"\n❌ Risk error: {str(e)[:50]}")
        
        # Get System Health
        try:
            response = requests.get(f"{base_url}/health", timeout=30)  # Increased from 5 to 30
            if response.status_code == 200:
                health = response.json()
                print(f"\n✅ System: {health.get('status', 'unknown')} | "
                      f"DB: {health.get('database', 'unknown')} | "
                      f"Risk: {health.get('risk_manager', 'unknown')}")
            else:
                print(f"\n❌ Health error: {response.status_code}")
        except requests.exceptions.Timeout:
            print("\n⏳ Health check: Server is waking up...")
        except Exception as e:
            print(f"\n❌ Health error: {str(e)[:50]}")
        
        print("=" * 70)
        print("Press Ctrl+C to stop monitoring")
        
        # Wait longer between checks to reduce cold starts
        time.sleep(60)  # Increased from 30 to 60 seconds

if __name__ == "__main__":
    try:
        monitor_trading_system()
    except KeyboardInterrupt:
        print("\n\n✅ Monitoring stopped")

        