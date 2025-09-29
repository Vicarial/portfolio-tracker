from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from decimal import Decimal
import yfinance as yf
import pandas as pd
import json
import os
from datetime import datetime, timedelta
import threading
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

class PortfolioMonitor:
    def __init__(self, config_file='config.json'):
        # Allow overriding config path via environment variable
        env_config = os.environ.get('CONFIG_PATH')
        self.config_file = env_config if env_config else config_file
        self.config = self.load_config()
        self.monitoring = False
        self.monitor_thread = None
        self.last_scan_results = []
        self.last_scan_time = None
        
    def load_config(self):
        """Load configuration from JSON file"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f)
        else:
            default_config = {
                "stocks": [],
                "alert_threshold": 0.05,
                "lookback_days": 30,
                "scan_interval_minutes": 30,
                "tradingview_url": "",
                "thesis_entries": [],
                "email_settings": {
                    "enabled": False,
                    "smtp_server": "smtp.gmail.com",
                    "smtp_port": 587,
                    "sender_email": "",
                    "sender_password": "",
                    "recipient_email": ""
                }
            }
            self.save_config(default_config)
            return default_config

    def compute_rsi(self, close_series, period=14):
        """Compute RSI from a pandas Series of close prices. Returns last RSI as float or None."""
        try:
            if close_series is None or len(close_series) < period + 1:
                return None
            delta = close_series.diff()
            gains = delta.clip(lower=0)
            losses = -delta.clip(upper=0)
            avg_gain = gains.rolling(window=period, min_periods=period).mean()
            avg_loss = losses.rolling(window=period, min_periods=period).mean()
            rs = avg_gain / avg_loss.replace(0, pd.NA)
            rsi = 100 - (100 / (1 + rs))
            last_rsi = rsi.iloc[-1]
            return round(float(last_rsi), 2) if pd.notna(last_rsi) else None
        except Exception as e:
            print(f"Error computing RSI: {e}")
            return None
    
    def save_config(self, config=None):
        """Save configuration to JSON file"""
        if config:
            self.config = config
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)
    
    def get_stock_data(self, symbol):
        """Get stock data for the specified lookback period"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.config.get('lookback_days', 30))
            
            stock = yf.Ticker(symbol)
            data = stock.history(start=start_date, end=end_date)
            
            if data.empty:
                return None
                
            return data
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return None
    
    def check_stock_alert(self, symbol):
        """Check if stock is below threshold from recent high"""
        data = self.get_stock_data(symbol)
        if data is None:
            return None
        
        current_price = data['Close'].iloc[-1]
        recent_high = data['High'].max()
        # alert_threshold is a positive fraction (e.g., 0.05 means alert when 5% below recent high)
        alert_threshold = abs(self.config.get('alert_threshold', 0.05))
        # Compute RSI using recent closes, default 14 period
        rsi_value = self.compute_rsi(data['Close'], period=14)
        
        pct_change = (current_price - recent_high) / recent_high
        # pct_change is negative when below the high; trigger when drop >= alert_threshold
        is_alert = pct_change <= -alert_threshold
        
        return {
            'symbol': symbol,
            'current_price': round(current_price, 2),
            'recent_high': round(recent_high, 2),
            'pct_from_high': round(pct_change * 100, 2),
            'is_alert': is_alert,
            'date_of_high': data.loc[data['High'] == recent_high].index[-1].strftime('%Y-%m-%d'),
            'status': '🚨 ALERT' if is_alert else '✅ OK',
            'rsi': rsi_value
        }
    
    def scan_portfolio(self):
        """Scan all stocks in portfolio"""
        results = []
        alerts = []
        
        for symbol in self.config.get('stocks', []):
            result = self.check_stock_alert(symbol)
            if result:
                results.append(result)
                if result['is_alert']:
                    alerts.append(result)
        
        self.last_scan_results = results
        self.last_scan_time = datetime.now()
        
        # Send email if alerts and email enabled
        if alerts and self.config.get('email_settings', {}).get('enabled', False):
            self.send_email_alert(alerts)
        
        return alerts, results
    
    def send_email_alert(self, alerts):
        """Send email alert for stocks below threshold"""
        email_config = self.config.get('email_settings', {})
        if not email_config.get('enabled') or not all(key in email_config for key in ['sender_email', 'sender_password', 'recipient_email']):
            return False
        
        try:
            msg = MIMEMultipart()
            msg['From'] = email_config['sender_email']
            msg['To'] = email_config['recipient_email']
            msg['Subject'] = f"Portfolio Alert: {len(alerts)} stocks triggered"
            
            body = "The following stocks are below your alert threshold:\n\n"
            for alert in alerts:
                body += f"• {alert['symbol']}: ${alert['current_price']} ({alert['pct_from_high']:.1f}% from ${alert['recent_high']} high on {alert['date_of_high']})\n"
            
            body += f"\nAlert generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(email_config.get('smtp_server', 'smtp.gmail.com'), 
                                email_config.get('smtp_port', 587))
            server.starttls()
            server.login(email_config['sender_email'], email_config['sender_password'])
            server.sendmail(email_config['sender_email'], email_config['recipient_email'], msg.as_string())
            server.quit()
            
            return True
        except Exception as e:
            print(f"Error sending email: {e}")
            return False
    
    def start_monitoring(self):
        """Start continuous monitoring in background thread"""
        if self.monitoring:
            return False
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        return True
    
    def stop_monitoring(self):
        """Stop continuous monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
        return True
    
    def _monitor_loop(self):
        """Background monitoring loop"""
        while self.monitoring:
            try:
                self.scan_portfolio()
                interval = self.config.get('scan_interval_minutes', 30) * 60
                time.sleep(interval)
            except Exception as e:
                print(f"Error in monitor loop: {e}")
                time.sleep(60)  # Wait 1 minute on error

# Initialize monitor (will use CONFIG_PATH if provided)
monitor = PortfolioMonitor()

@app.route('/')
def dashboard():
    """Main dashboard showing portfolio status"""
    alerts, results = monitor.scan_portfolio()
    return render_template('dashboard.html', 
                         results=results, 
                         alerts=alerts,
                         last_scan_time=monitor.last_scan_time,
                         monitoring=monitor.monitoring,
                         config=monitor.config,
                         config_stocks=monitor.config.get('stocks', []))

@app.route('/config')
def config_page():
    """Configuration page"""
    return render_template('config.html', config=monitor.config)

@app.route('/update_stocks', methods=['POST'])
def update_stocks():
    """Update stock list"""
    stocks_text = request.form.get('stocks', '')
    stocks = [s.strip().upper() for s in stocks_text.replace(',', '\n').split('\n') if s.strip()]
    
    monitor.config['stocks'] = stocks
    monitor.save_config()
    
    flash(f'Updated portfolio with {len(stocks)} stocks', 'success')
    return redirect(url_for('config_page'))

@app.route('/update_settings', methods=['POST'])
def update_settings():
    """Update monitoring settings"""
    try:
        # Parse as Decimal to avoid float artifacts (e.g., 5.049999...) then quantize
        alert_percent_str = request.form.get('alert_threshold', '5.0').strip()
        alert_percent_dec = Decimal(alert_percent_str)
        alert_fraction = (alert_percent_dec / Decimal('100')).quantize(Decimal('0.0001'))
        monitor.config['alert_threshold'] = float(alert_fraction)
        monitor.config['lookback_days'] = int(request.form.get('lookback_days', 30))
        monitor.config['scan_interval_minutes'] = int(request.form.get('scan_interval_minutes', 30))
        # TradingView URL (optional)
        monitor.config['tradingview_url'] = request.form.get('tradingview_url', '').strip()
        
        # Email settings
        email_settings = monitor.config.get('email_settings', {})
        email_settings['enabled'] = request.form.get('email_enabled') == 'on'
        email_settings['sender_email'] = request.form.get('sender_email', '')
        email_settings['sender_password'] = request.form.get('sender_password', '')
        email_settings['recipient_email'] = request.form.get('recipient_email', '')
        email_settings['smtp_server'] = request.form.get('smtp_server', 'smtp.gmail.com')
        email_settings['smtp_port'] = int(request.form.get('smtp_port', 587))
        
        monitor.config['email_settings'] = email_settings
        monitor.save_config()
        
        flash('Settings updated successfully', 'success')
    except Exception as e:
        flash(f'Error updating settings: {str(e)}', 'error')
    
    return redirect(url_for('config_page'))

@app.route('/start_monitoring', methods=['POST'])
def start_monitoring():
    """Start continuous monitoring"""
    if monitor.start_monitoring():
        flash('Continuous monitoring started', 'success')
    else:
        flash('Monitoring is already running', 'info')
    return redirect(url_for('dashboard'))

# Thesis routes
@app.route('/thesis')
def thesis_page():
    """Page for managing trade theses"""
    entries = monitor.config.get('thesis_entries', [])
    # Optional edit index to render edit form prefilled
    edit_param = request.args.get('edit')
    edit_index = None
    edit_entry = None
    try:
        if edit_param is not None:
            idx = int(edit_param)
            if 0 <= idx < len(entries):
                edit_index = idx
                edit_entry = entries[idx]
    except Exception:
        edit_index = None
        edit_entry = None
    return render_template('thesis.html', entries=entries, config=monitor.config, edit_index=edit_index, edit_entry=edit_entry)

@app.route('/add_thesis', methods=['POST'])
def add_thesis():
    """Add a new thesis entry"""
    try:
        ticker = request.form.get('ticker', '').strip().upper()
        thesis_text = request.form.get('thesis', '').strip()
        trigger_text = request.form.get('trigger', '').strip()
        if not ticker:
            flash('Ticker is required', 'error')
            return redirect(url_for('thesis_page'))
        entry = {
            'ticker': ticker,
            'thesis': thesis_text,
            'trigger': trigger_text,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        entries = monitor.config.get('thesis_entries', [])
        entries.append(entry)
        monitor.config['thesis_entries'] = entries
        monitor.save_config()
        flash('Thesis saved', 'success')
    except Exception as e:
        flash(f'Error saving thesis: {str(e)}', 'error')
    return redirect(url_for('thesis_page'))

@app.route('/update_thesis/<int:index>', methods=['POST'])
def update_thesis(index):
    """Update a thesis entry"""
    try:
        ticker = request.form.get('ticker', '').strip().upper()
        thesis_text = request.form.get('thesis', '').strip()
        trigger_text = request.form.get('trigger', '').strip()
        if not ticker:
            flash('Ticker is required', 'error')
            return redirect(url_for('thesis_page', edit=index))
        entries = monitor.config.get('thesis_entries', [])
        if 0 <= index < len(entries):
            entries[index]['ticker'] = ticker
            entries[index]['thesis'] = thesis_text
            entries[index]['trigger'] = trigger_text
            monitor.config['thesis_entries'] = entries
            monitor.save_config()
            flash('Thesis updated', 'success')
        else:
            flash('Invalid entry index', 'error')
    except Exception as e:
        flash(f'Error updating thesis: {str(e)}', 'error')
    return redirect(url_for('thesis_page'))

@app.route('/delete_thesis/<int:index>', methods=['POST'])
def delete_thesis(index):
    """Delete a thesis entry by index"""
    try:
        entries = monitor.config.get('thesis_entries', [])
        if 0 <= index < len(entries):
            removed = entries.pop(index)
            monitor.config['thesis_entries'] = entries
            monitor.save_config()
            flash(f"Removed thesis for {removed.get('ticker','?')}", 'info')
        else:
            flash('Invalid entry index', 'error')
    except Exception as e:
        flash(f'Error deleting thesis: {str(e)}', 'error')
    return redirect(url_for('thesis_page'))

@app.route('/stop_monitoring', methods=['POST'])
def stop_monitoring():
    """Stop continuous monitoring"""
    if monitor.stop_monitoring():
        flash('Continuous monitoring stopped', 'info')
    return redirect(url_for('dashboard'))

@app.route('/scan_now', methods=['POST'])
def scan_now():
    """Run manual scan"""
    alerts, results = monitor.scan_portfolio()
    
    # Create a message with current prices
    if results:
        price_info = []
        for stock in results:
            ticker = stock.get('symbol', '')
            price = stock.get('current_price', 'N/A')
            if ticker and price != 'N/A':
                price_info.append(f"{ticker}: ${price:.2f}")
        
        if price_info:
            price_message = "Current prices: " + ", ".join(price_info)
            flash(price_message, 'info')
    
    flash(f'Scan complete: {len(alerts)} alerts from {len(results)} stocks', 'info')
    return redirect(url_for('dashboard'))

@app.route('/api/stock_prices')
def api_stock_prices():
    """API endpoint to get current stock prices"""
    if not hasattr(monitor, 'config'):
        print("Error: Monitor config not available")
        return jsonify({'error': 'Configuration not loaded'})
    
    stocks = monitor.config.get('stocks', [])
    print(f"Fetching prices for stocks: {stocks}")
    
    if not stocks:
        print("No stocks configured")
        return jsonify({'error': 'No stocks configured'})
    
    prices = {}
    
    for symbol in stocks:
        try:
            print(f"Fetching data for {symbol}...")
            stock = yf.Ticker(symbol)
            # Intraday: use 1m data for current price where available
            intraday = stock.history(period='1d', interval='1m')
            # Daily history for RSI and previous close reference
            daily = stock.history(period='30d')

            if (intraday is None or intraday.empty) and (daily is None or daily.empty):
                print(f"No data returned for {symbol}")
                prices[symbol] = {'error': 'No data available'}
                continue

            # Determine current price
            if intraday is not None and not intraday.empty:
                current_price = float(intraday['Close'].iloc[-1])
            else:
                current_price = float(daily['Close'].iloc[-1])

            # Determine change vs previous close
            change_pct = 0.0
            if daily is not None and len(daily) >= 2:
                prev_close = float(daily['Close'].iloc[-2])
                if prev_close:
                    change_pct = round(((current_price / prev_close) - 1) * 100, 2)

            # RSI from daily closes
            rsi_val = None
            if daily is not None and not daily.empty:
                rsi_val = monitor.compute_rsi(daily['Close'], period=14)

            prices[symbol] = {
                'price': round(current_price, 2),
                'change': change_pct,
                'rsi': rsi_val
            }

            print(f"Processed {symbol}: {prices[symbol]}")

        except Exception as e:
            error_msg = f"Error fetching price for {symbol}: {str(e)}"
            print(error_msg)
            prices[symbol] = {'error': error_msg}
    
    print("Final prices:", prices)
    return jsonify(prices)

@app.route('/api/status')
def api_status():
    """API endpoint for current status"""
    return jsonify({
        'monitoring': monitor.monitoring,
        'last_scan_time': monitor.last_scan_time.isoformat() if monitor.last_scan_time else None,
        'stock_count': len(monitor.config.get('stocks', [])),
        'alert_count': len([r for r in monitor.last_scan_results if r.get('is_alert')])
    })

if __name__ == '__main__':
    # Create templates directory and files if they don't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create dashboard template
    dashboard_html = '''<!DOCTYPE html>
<html>
<head>
    <title>Portfolio Monitor Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .status-card { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stock-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }
        .stock-card { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .alert { border-left: 4px solid #e74c3c; }
        .ok { border-left: 4px solid #27ae60; }
        .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn-primary { background: #3498db; color: white; }
        .btn-success { background: #27ae60; color: white; }
        .btn-danger { background: #e74c3c; color: white; }
        .btn-secondary { background: #95a5a6; color: white; }
        .flash { padding: 10px; margin: 10px 0; border-radius: 4px; }
        .flash-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .flash-info { background: #cce7ff; color: #004085; border: 1px solid #b3d7ff; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: white; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-number { font-size: 2em; font-weight: bold; color: #3498db; }
        .nav { margin-bottom: 20px; }
        .nav a { margin-right: 15px; color: #3498db; text-decoration: none; }
    </style>
    <script>
        function refreshPage() {
            location.reload();
        }
        
        function updateStockPrices() {
            console.log('Fetching stock prices...');
            fetch('/api/stock_prices')
                .then(response => {
                    console.log('API Response status:', response.status);
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    console.log('Received stock data:', data);
                    if (Object.keys(data).length === 0) {
                        console.warn('No stock data received');
                        return;
                    }
                    
                    for (const [symbol, info] of Object.entries(data)) {
                        console.log(`Processing ${symbol}:`, info);
                        // Update monitored stock tiles (when present)
                        const tile = document.getElementById(`stock-${symbol}`);
                        if (tile) {
                            if (info.error) {
                                tile.innerHTML = `
                                    <h4>${symbol}</h4>
                                    <p class="price-down">Error: ${info.error}</p>
                                `;
                            } else {
                                const changeClass = info.change >= 0 ? 'price-up' : 'price-down';
                                const rsiText = (info.rsi === null || info.rsi === undefined) ? 'N/A' : Number(info.rsi).toFixed(2);
                                tile.innerHTML = `
                                    <h4>${symbol}</h4>
                                    <p>$${Number(info.price).toLocaleString()}</p>
                                    <p class="${changeClass}">${info.change >= 0 ? '+' : ''}${info.change}%</p>
                                    <p><strong>RSI:</strong> ${rsiText}</p>
                                `;
                            }
                        }

                        // Also update portfolio status cards (when present)
                        if (!info.error) {
                            const priceSpan = document.getElementById(`price-${symbol}`);
                            if (priceSpan) {
                                priceSpan.textContent = `$${Number(info.price).toLocaleString()}`;
                            }
                            const rsiSpan = document.getElementById(`rsi-${symbol}`);
                            if (rsiSpan) {
                                rsiSpan.textContent = (info.rsi === null || info.rsi === undefined) ? 'N/A' : Number(info.rsi).toFixed(2);
                            }
                        }
                    }
                })
                .catch(error => {
                    console.error('Error in updateStockPrices:', error);
                    // Show error in the first stock card if available
                    const firstStock = document.querySelector('.stock-card');
                    if (firstStock) {
                        firstStock.innerHTML += `<p class="price-down">Error: ${error.message}</p>`;
                    }
                });
        }

        // Update status and stock prices on page load
        document.addEventListener('DOMContentLoaded', function() {
            // Update status
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('status-indicator').textContent = 
                        data.monitoring ? '🟢 Monitoring Active' : '🔴 Monitoring Stopped';
                });
            
            // Update stock prices immediately and then every 30 seconds
            updateStockPrices();
            setInterval(updateStockPrices, 30000);
        });
    </script>
    <style>
        .price-up { color: #27ae60; }
        .price-down { color: #e74c3c; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 Portfolio Monitor Dashboard</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/config">Configuration</a>
                <a href="/thesis">Thesis</a>
                {% if config.tradingview_url %}
                <a href="{{ config.tradingview_url }}" target="_blank">TradingView ↗</a>
                {% endif %}
            </div>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash flash-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{{ results|length }}</div>
                <div>Total Stocks</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" style="color: #e74c3c;">{{ alerts|length }}</div>
                <div>Active Alerts</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" style="color: #27ae60;">{{ (results|length) - (alerts|length) }}</div>
                <div>Stocks OK</div>
            </div>
            <div class="stat-card">
                <div style="font-size: 1.2em;" id="status-indicator">
                    {% if monitoring %}🟢 Monitoring Active{% else %}🔴 Monitoring Stopped{% endif %}
                </div>
                <div>Status</div>
            </div>
        </div>
        
        <div class="status-card">
            <h3>Controls</h3>
            <form method="post" style="display: inline;">
                <button formaction="/scan_now" class="btn btn-primary">🔍 Scan Now</button>
                {% if monitoring %}
                    <button formaction="/stop_monitoring" class="btn btn-danger">⏹️ Stop Monitoring</button>
                {% else %}
                    <button formaction="/start_monitoring" class="btn btn-success">▶️ Start Monitoring</button>
                {% endif %}
                <button type="button" onclick="refreshPage()" class="btn btn-secondary">🔄 Refresh</button>
            </form>
            {% if last_scan_time %}
                <p><strong>Last Scan:</strong> {{ last_scan_time.strftime('%Y-%m-%d %H:%M:%S') }}</p>
            {% endif %}
            <p><strong>Alert Threshold:</strong> {{ "%.1f"|format(config.alert_threshold * 100) }}% below recent high</p>
            <p><strong>Lookback Period:</strong> {{ config.lookback_days }} days</p>
        </div>
        
        {% if results %}
        <div class="status-card">
            <h3>Portfolio Status</h3>
            <div class="stock-grid">
                {% for result in results %}
                <div class="stock-card {{ 'alert' if result.is_alert else 'ok' }}">
                    <h4>{{ result.symbol }} {{ result.status }}</h4>
                    <p><strong>Current:</strong> <span id="price-{{ result.symbol }}">${{ result.current_price }}</span></p>
                    <p><strong>RSI:</strong> <span id="rsi-{{ result.symbol }}">{{ '%.2f'|format(result.rsi) if result.rsi is not none else 'N/A' }}</span></p>
                    <p><strong>Recent High:</strong> ${{ result.recent_high }} ({{ result.date_of_high }})</p>
                    <p><strong>From High:</strong> 
                        <span style="color: {{ '#e74c3c' if result.pct_from_high < -5 else '#f39c12' if result.pct_from_high < 0 else '#27ae60' }};">
                            {{ result.pct_from_high }}%
                        </span>
                    </p>
                </div>
                {% endfor %}
            </div>
        </div>
        {% else %}
        {% if config_stocks %}
        <div class="status-card">
            <h3>📊 Monitored Stocks</h3>
            <div class="stock-grid">
                {% for stock in config_stocks %}
                <div class="stock-card" id="stock-{{ stock }}">
                    <h4>{{ stock }}</h4>
                    <p>Loading price data...</p>
                </div>
                {% endfor %}
            </div>
        </div>
        {% else %}
        <div class="status-card">
            <h3>No Stocks Configured</h3>
            <p>Go to <a href="/config">Configuration</a> to add stocks to monitor.</p>
        </div>
        {% endif %}
        {% endif %}
    </div>
</body>
</html>'''
    
    with open('templates/dashboard.html', 'w') as f:
        f.write(dashboard_html)
    
    # Create config template
    config_html = '''<!DOCTYPE html>
<html>
<head>
    <title>Portfolio Monitor Configuration</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; }
        .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .config-section { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, textarea, select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        textarea { height: 120px; font-family: monospace; }
        .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn-primary { background: #3498db; color: white; }
        .btn-success { background: #27ae60; color: white; }
        .btn-secondary { background: #95a5a6; color: white; }
        .flash { padding: 10px; margin: 10px 0; border-radius: 4px; }
        .flash-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .nav { margin-bottom: 20px; }
        .nav a { margin-right: 15px; color: #3498db; text-decoration: none; }
        .checkbox-wrapper { display: flex; align-items: center; }
        .checkbox-wrapper input[type="checkbox"] { width: auto; margin-right: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚙️ Portfolio Monitor Configuration</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/config">Configuration</a>
                <a href="/thesis">Thesis</a>
            </div>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash flash-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>
        
        <div class="config-section">
            <h3>📊 Stock Portfolio</h3>
            <form method="post" action="/update_stocks">
                <div class="form-group">
                    <label>Stock Symbols (one per line or comma-separated):</label>
                    <textarea name="stocks" placeholder="AAPL&#10;MSFT&#10;GOOGL&#10;TSLA">{{ config.stocks|join('\n') }}</textarea>
                    <small>Enter stock ticker symbols. You can use new lines or commas to separate them.</small>
                </div>
                <button type="submit" class="btn btn-success">💾 Update Portfolio</button>
            </form>
        </div>
        
        <div class="config-section">
            <h3>⚙️ Monitoring Settings</h3>
            <form method="post" action="/update_settings">
                <div class="form-group">
                    <label>Alert Threshold (%):</label>
                    <input type="number" name="alert_threshold" step="0.01" value="{{ '%.2f'|format(config.alert_threshold * 100) }}" required>
                    <small>Trigger alert when stock falls this percentage below recent high (e.g., 5.0 for 5%)</small>
                </div>
                
                <div class="form-group">
                    <label>Lookback Period (days):</label>
                    <input type="number" name="lookback_days" value="{{ config.lookback_days }}" min="1" max="365" required>
                    <small>Number of days to look back for the recent high</small>
                </div>
                
                <div class="form-group">
                    <label>Scan Interval (minutes):</label>
                    <input type="number" name="scan_interval_minutes" value="{{ config.scan_interval_minutes }}" min="1" max="1440" required>
                    <small>How often to scan when continuous monitoring is enabled</small>
                </div>
                
                <h4>📧 Email Alerts</h4>
                
                <div class="form-group">
                    <div class="checkbox-wrapper">
                        <input type="checkbox" name="email_enabled" {{ 'checked' if config.email_settings.enabled }}>
                        <label>Enable Email Alerts</label>
                    </div>
                </div>
                
                <div class="form-group">
                    <label>Sender Email:</label>
                    <input type="email" name="sender_email" value="{{ config.email_settings.sender_email }}">
                </div>
                
                <div class="form-group">
                    <label>Email App Password:</label>
                    <input type="password" name="sender_password" value="{{ config.email_settings.sender_password }}">
                    <small>For Gmail, use an App Password, not your regular password</small>
                </div>
                
                <div class="form-group">
                    <label>Recipient Email:</label>
                    <input type="email" name="recipient_email" value="{{ config.email_settings.recipient_email }}">
                </div>
                
                <div class="form-group">
                    <label>SMTP Server:</label>
                    <input type="text" name="smtp_server" value="{{ config.email_settings.smtp_server }}">
                </div>
                
                <div class="form-group">
                    <label>SMTP Port:</label>
                    <input type="number" name="smtp_port" value="{{ config.email_settings.smtp_port }}">
                </div>
                
                <button type="submit" class="btn btn-success">💾 Save Settings</button>
            </form>
        </div>

        <div class="config-section">
            <h3>🔗 Integrations</h3>
            <form method="post" action="/update_settings">
                <div class="form-group">
                    <label>TradingView URL:</label>
                    <input type="url" name="tradingview_url" placeholder="https://www.tradingview.com/chart/..." value="{{ config.tradingview_url if config.tradingview_url is defined else '' }}">
                    <small>Paste a link to your preferred TradingView chart or workspace. A link will appear in the dashboard header.</small>
                </div>
                <button type="submit" class="btn btn-success">💾 Save Integrations</button>
            </form>
        </div>
        
        <div class="config-section">
            <h3>📋 Current Configuration</h3>
            <p><strong>Stocks:</strong> {{ config.stocks|length }} symbols</p>
            <p><strong>Alert Threshold:</strong> {{ "%.1f"|format(config.alert_threshold * 100) }}%</p>
            <p><strong>Lookback:</strong> {{ config.lookback_days }} days</p>
            <p><strong>Scan Interval:</strong> {{ config.scan_interval_minutes }} minutes</p>
            <p><strong>Email Alerts:</strong> {{ '✅ Enabled' if config.email_settings.enabled else '❌ Disabled' }}</p>
        </div>
    </div>
</body>
</html>'''
    
    with open('templates/config.html', 'w') as f:
        f.write(config_html)
    
    # Create thesis template
    thesis_html = '''<!DOCTYPE html>
<html>
<head>
    <title>Trade Thesis</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1000px; margin: 0 auto; }
        .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .nav a { margin-right: 15px; color: #3498db; text-decoration: none; }
        .section { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .form-group { margin-bottom: 12px; }
        label { display: block; margin-bottom: 6px; font-weight: bold; }
        input[type="text"], textarea { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        textarea { height: 100px; font-family: sans-serif; }
        .btn { padding: 10px 16px; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn-success { background: #27ae60; color: white; }
        .btn-danger { background: #e74c3c; color: white; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; border-bottom: 1px solid #eee; text-align: left; vertical-align: top; }
        .ticker { font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📝 Trade Thesis</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/config">Configuration</a>
                <a href="/thesis">Thesis</a>
                {% if config.tradingview_url %}
                <a href="{{ config.tradingview_url }}" target="_blank">TradingView ↗</a>
                {% endif %}
            </div>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash flash-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>

        <div class="section">
            <h3>Add Thesis</h3>
            <form method="post" action="/add_thesis">
                <div class="form-group">
                    <label>Ticker</label>
                    <input type="text" name="ticker" placeholder="e.g., MSFT" required>
                </div>
                <div class="form-group">
                    <label>Thesis</label>
                    <textarea name="thesis" placeholder="Why this trade? Key catalysts, valuation, risks..."></textarea>
                </div>
                <div class="form-group">
                    <label>Trigger (what needs to happen?)</label>
                    <textarea name="trigger" placeholder="Price level, RSI condition, earnings reaction, etc."></textarea>
                </div>
                <button type="submit" class="btn btn-success">➕ Add Thesis</button>
            </form>
        </div>

        {% if edit_index is not none and edit_entry is not none %}
        <div class="section" style="border-left: 4px solid #3498db;">
            <h3>Edit Thesis ({{ edit_entry.ticker }})</h3>
            <form method="post" action="/update_thesis/{{ edit_index }}">
                <div class="form-group">
                    <label>Ticker</label>
                    <input type="text" name="ticker" value="{{ edit_entry.ticker }}" required>
                </div>
                <div class="form-group">
                    <label>Thesis</label>
                    <textarea name="thesis">{{ edit_entry.thesis }}</textarea>
                </div>
                <div class="form-group">
                    <label>Trigger (what needs to happen?)</label>
                    <textarea name="trigger">{{ edit_entry.trigger }}</textarea>
                </div>
                <button type="submit" class="btn btn-success">💾 Save Changes</button>
                <a href="/thesis" class="btn">Cancel</a>
            </form>
        </div>
        {% endif %}

        <div class="section">
            <h3>Saved Theses ({{ entries|length }})</h3>
            {% if entries %}
            <table>
                <thead>
                    <tr>
                        <th style="width: 120px;">Ticker</th>
                        <th>Thesis</th>
                        <th>Trigger</th>
                        <th style="width: 140px;">Created</th>
                        <th style="width: 160px;"></th>
                    </tr>
                </thead>
                <tbody>
                    {% for e in entries %}
                    <tr>
                        <td class="ticker">
                            {% if config.tradingview_url %}
                                <a href="{{ config.tradingview_url }}{% if '?' in config.tradingview_url %}&{% else %}?{% endif %}symbol={{ e.ticker }}" target="_blank" rel="noopener noreferrer">{{ e.ticker }}</a>
                            {% else %}
                                <a href="https://www.tradingview.com/chart/?symbol={{ e.ticker }}" target="_blank" rel="noopener noreferrer">{{ e.ticker }}</a>
                            {% endif %}
                        </td>
                        <td>{{ e.thesis }}</td>
                        <td>{{ e.trigger }}</td>
                        <td>{{ e.created_at }}</td>
                        <td>
                            <a class="btn" href="/thesis?edit={{ loop.index0 }}">✏️ Edit</a>
                            <form method="post" action="/delete_thesis/{{ loop.index0 }}" onsubmit="return confirm('Delete this thesis?');">
                                <button class="btn btn-danger">🗑️ Delete</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p>No theses saved yet.</p>
            {% endif %}
        </div>
    </div>
</body>
</html>'''

    with open('templates/thesis.html', 'w') as f:
        f.write(thesis_html)
    
    print("🚀 Starting Portfolio Monitor Web App...")
    print("📱 Open your browser and go to: http://localhost:5001")
    print("⚙️  Go to Configuration to add your stocks")
    print("🔍 Use Dashboard to monitor and control scanning")
    
    app.run(debug=True, host='0.0.0.0', port=5001)