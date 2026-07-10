# pretty_risk_report.py
import requests
import json

def get_pretty_risk_report():
    url = "https://tradingview-telegram-webhook-dpaj.onrender.com/api/risk/report"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            
            print("=" * 60)
            print("📊 RISK MANAGEMENT REPORT")
            print("=" * 60)
            
            print("\n🔴 TRADING STATUS:")
            print(f"  Can Trade: {'✅ YES' if data.get('can_trade') else '❌ NO'}")
            print(f"  Kill Switch: {'🔴 ACTIVE' if data.get('kill_switch_active') else '🟢 INACTIVE'}")
            print(f"  Circuit Breaker: {'🔴 ACTIVE' if data.get('circuit_breaker_active') else '🟢 INACTIVE'}")
            if data.get('circuit_breaker_reason'):
                print(f"  Reason: {data.get('circuit_breaker_reason')}")
            
            print("\n💰 CAPITAL:")
            print(f"  Current Capital: ₹{data.get('current_capital', 0):,.2f}")
            print(f"  Peak Capital: ₹{data.get('peak_capital', 0):,.2f}")
            print(f"  Current Drawdown: {data.get('current_drawdown', 0):.2f}%")
            print(f"  Max Drawdown: {data.get('max_drawdown', 0)}%")
            
            print("\n📈 PORTFOLIO:")
            print(f"  Portfolio Heat: {data.get('portfolio_heat', 0):.2f}%")
            print(f"  Open Positions: {data.get('open_positions', 0)} / {data.get('max_open_positions', 0)}")
            print(f"  Daily PnL: ₹{data.get('daily_pnl', 0):,.2f}")
            print(f"  Trades Today: {data.get('trades_today', 0)}")
            
            print("\n🛡️  RISK METRICS:")
            print(f"  Consecutive Losses: {data.get('consecutive_losses', 0)}")
            print(f"  Position Size Multiplier: {data.get('position_size_multiplier', 0):.2f}x")
            print(f"  Recovery Mode: {'🔴 ACTIVE' if data.get('recovery_mode') else '🟢 INACTIVE'}")
            print(f"  Sizing Method: {data.get('sizing_method', 'N/A')}")
            
            print("\n" + "=" * 60)
            
            # Show full JSON for reference
            print("\n📄 Full JSON Response:")
            print(json.dumps(data, indent=2))
            
        else:
            print(f"❌ Error: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    get_pretty_risk_report()
    