#!/bin/bash

# Set environment variables
export FLASK_APP=portfolio_web_app.py
export FLASK_ENV=development

# Check if virtual environment exists, if not create it
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "Installing requirements..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Check if requirements are installed
if ! pip freeze | grep -q Flask; then
    echo "Installing requirements..."
    pip install -r requirements.txt
fi

# Create config.json if it doesn't exist
if [ ! -f "config.json" ]; then
    echo "Creating default config.json..."
    cat > config.json <<EOL
{
    "stocks": [],
    "alert_threshold": 0.05,
    "lookback_days": 30,
    "scan_interval_minutes": 30,
    "email_settings": {
        "enabled": false,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "",
        "sender_password": "",
        "recipient_email": ""
    },
    "tradingview_url": ""
}
EOL
fi

# Create templates directory if it doesn't exist
mkdir -p templates

# Run the Flask application
echo "Starting Flask application..."
flask run

# Keep the script running in the foreground
while true; do
    sleep 1
done
