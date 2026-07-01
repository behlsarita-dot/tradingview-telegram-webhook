# Paper Trading System v7.0

Receive TradingView webhook alerts, execute paper trades, send Telegram notifications, and monitor performance via a live dashboard — all running free on Render.

## Architecture
TradingView Alert
|
v
Render (Flask app)
|
+-- Risk Manager (validates trade)
|
+-- Database (SQLite, records position)
|
+-- Telegram (sends notification)
|
v
Dashboard (http://your-app.onrender.com/dashboard)

## Features

- TradingView Pine Script webhook integration
- Telegram notifications for every trade open/close
- Full risk management — drawdown limits, circuit breakers, position sizing
- Multi-timeframe confluence scoring (ADX + EMA + Volume)
- Backtesting engine on historical NIFTY data
- Live dashboard with P&L, positions, risk status
- Options paper trading support
- Zerodha-accurate brokerage charge calculations
- LOT_SIZE=65 (NIFTY standard)
- Initial capital Rs.500,000

## Quick Start (Local)

### 1. Clone and set up environment
```bash
git clone https://github.com/behlsarita-dot/tradingview-telegram-webhook.git
cd tradingview-telegram-webhook
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Create your .env file
```bash
cp .env.example .env
```

Edit .env and fill in:
SECRET_KEY=run: python -c "import secrets; print(secrets.token_hex(32))"
WEBHOOK_SECRET=run: python -c "import secrets; print(secrets.token_hex(32))"
TELEGRAM_BOT_TOKEN=get from @BotFather on Telegram
TELEGRAM_CHAT_ID=get from @userinfobot on Telegram

### 3. Verify setup
```bash
python setup_verification.py
```

All checks should pass before running.

### 4. Start the bot
```bash
python app.py
```

Open browser: http://localhost:5000/dashboard

### 5. Test webhook
```bash
python webhook_tester.py
```

## TradingView Setup

### Step 1 — Get your webhook URL
- Local: `http://your-ngrok-url/api/webhook`
- Production: `https://your-app.onrender.com/api/webhook`

### Step 2 — Create an alert in TradingView
In your Pine Script strategy, set the alert message to:

**BUY signal:**
```json
{
  "webhook_secret": "YOUR_WEBHOOK_SECRET_HERE",
  "symbol": "NIFTY",
  "action": "BUY",
  "price": {{close}},
  "quantity": 65,
  "sl": {{close}} * 0.99,
  "tp": {{close}} * 1.02
}
```

**SELL signal:**
```json
{
  "webhook_secret": "YOUR_WEBHOOK_SECRET_HERE",
  "symbol": "NIFTY",
  "action": "SELL",
  "price": {{close}},
  "quantity": 65,
  "sl": {{close}} * 1.01,
  "tp": {{close}} * 0.98
}
```

**EXIT signal:**
```json
{
  "webhook_secret": "YOUR_WEBHOOK_SECRET_HERE",
  "symbol": "NIFTY",
  "action": "EXIT",
  "price": {{close}}
}
```

### Supported actions
| Action | Effect |
|---|---|
| BUY or LONG | Open long position |
| SELL or SHORT | Open short position |
| EXIT | Close long position |
| EXIT_SHORT | Close short position |
| BUY_OPTION | Open options position |
| EXIT_OPTION | Close options position |

## Risk Management

The system has multiple layers of protection:

| Protection | Default | Description |
|---|---|---|
| Max Drawdown | 15% | Halts trading if drawdown exceeds this |
| Daily Loss Limit | Rs.15,000 | Halts trading for the day |
| Consecutive Losses | 3 | Halts after N losing trades in a row |
| Portfolio Heat | 20% | Max % of capital at risk simultaneously |
| Max Open Positions | 5 | Hard cap on simultaneous positions |
| Max Trades/Day | 20 | Circuit breaker on trade frequency |
| R:R Minimum | 1.5 | Rejects trades with poor risk/reward |

All settings are configurable in .env — see CONFIGURATION.md for full reference.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| /health | GET | System health check |
| /api/portfolio | GET | Account summary and P&L |
| /api/positions | GET | All positions (add ?status=OPEN or CLOSED) |
| /api/webhook | POST | Receive TradingView signals |
| /api/risk/report | GET | Real-time risk status |
| /api/risk/validate | POST | Pre-validate a trade |
| /api/analyze/NIFTY | GET | Signal analysis for symbol |
| /api/backtest | POST | Run backtest |
| /api/telegram/test | GET | Send test Telegram message |
| /api/market/status | GET | NSE market open/closed status |
| /dashboard | GET | Dashboard UI |
| /analysis | GET | Analysis UI |
| /backtesting | GET | Backtesting UI |
| /options | GET | Options UI |

## Utility Scripts

| Script | Usage | Description |
|---|---|---|
| setup_verification.py | python setup_verification.py | Check all files, packages, env vars |
| webhook_tester.py | python webhook_tester.py | Test all webhook endpoints |
| monitor_trades.py | python monitor_trades.py | View portfolio and trades |
| signal_analyzer.py | python signal_analyzer.py NIFTY | Analyse current signal |
| db_diagnostic.py | python db_diagnostic.py | Check database state |
| db_viewer.py | python db_viewer.py | Browse all DB tables |
| emergency_close.py | python emergency_close.py | Manually close stuck positions |

## Deployment on Render

### Step 1 — Push to GitHub
```bash
git add .
git commit -m "deploy v7.0"
git push origin main
```

### Step 2 — Create Render web service
1. Go to https://render.com
2. New → Web Service
3. Connect your GitHub repo
4. Render auto-detects render.yaml

### Step 3 — Set environment variables in Render
In Render dashboard → Environment, add:
SECRET_KEY          = your_secret_key
WEBHOOK_SECRET      = your_webhook_secret
TELEGRAM_BOT_TOKEN  = your_bot_token
TELEGRAM_CHAT_ID    = your_chat_id

### Step 4 — Deploy
Click Deploy. After 2-3 minutes your app is live at:
https://tradingview-telegram-webhook-XXXX.onrender.com

Health check: https://your-app.onrender.com/health

### Render free tier notes
- App sleeps after 15 minutes of inactivity
- First request after sleep takes 30-60 seconds (cold start)
- Use UptimeRobot to ping /health every 14 minutes to keep it awake
- SQLite database resets on each deploy (use /tmp/trading_bot.db path)

## Project Structure
tradingview-telegram-webhook/
├── app.py                  # Main Flask application
├── config.py               # All configuration from .env
├── database.py             # SQLite database manager
├── risk_manager.py         # Risk validation and circuit breakers
├── portfolio.py            # P&L and charge calculations
├── enhanced_strategy.py    # Multi-timeframe signal analysis
├── backtester.py           # Historical backtesting engine
├── telegram_notifier.py    # Telegram notifications
├── exceptions.py           # Custom exception classes
├── gunicorn.conf.py        # Production server config
├── render.yaml             # Render deployment config
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── templates/
│   ├── dashboard.html      # Main dashboard
│   ├── analysis.html       # Signal analysis page
│   ├── backtesting.html    # Backtesting lab
│   └── options.html        # Options trading page
├── static/
│   ├── css/style.css       # Dark theme stylesheet
│   └── js/
│       ├── utils.js        # Shared JS utilities
│       ├── dashboard.js    # Dashboard logic
│       ├── analysis.js     # Analysis page logic
│       └── backtesting.js  # Backtesting page logic
└── scripts/
├── setup_verification.py
├── webhook_tester.py
├── monitor_trades.py
├── signal_analyzer.py
├── db_diagnostic.py
├── db_viewer.py
└── emergency_close.py

## Configuration

Key settings in .env:

```env
# Trading
TRADING_MODE=PAPER
INITIAL_CAPITAL=500000
LOT_SIZE=65

# Risk
MAX_DRAWDOWN_PCT=15.0
MAX_DAILY_LOSS=15000
MAX_CONSECUTIVE_LOSSES=3
RISK_PER_TRADE_PERCENT=1.0

# Strategy
MIN_CONFLUENCE_SCORE=3
ADX_THRESHOLD=25
```

Full reference: see CONFIGURATION.md

## Troubleshooting

**Bot not receiving TradingView alerts**
- Check WEBHOOK_SECRET matches exactly in .env and TradingView alert
- Verify app is running: GET /health
- Check logs for authentication errors

**Telegram not sending messages**
- Run: python -c "from telegram_notifier import test_connection; print(test_connection())"
- Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env

**yfinance data unavailable**
- Normal outside Indian market hours (9:15am-3:15pm IST, Mon-Fri)
- Fallback prices used automatically — bot continues working

**Position not closing**
- Use emergency_close.py to manually close stuck positions
- Check /api/positions?status=OPEN to see what is open

## Version History

- v7.0 — Complete rebuild: clean architecture, risk manager, enhanced strategy, full dashboard UI
- v6.x — Legacy version (deprecated)

## License

MIT License — free to use and modify.
