# MQTT Dashboard

Real-time MQTT monitoring dashboard built with GTK4/Adwaita.

## Features
- Connect to any MQTT broker
- Subscribe to topics with wildcard support
- Widget types: text, gauge, sparkline chart
- Publish messages
- Save/load dashboard layout
- Message log

## Dependencies
```bash
pip install paho-mqtt
```

## Run
```bash
PYTHONPATH=src python3 -c "from mqtt_dashboard.main import main; main()"
```

## License
GPL-3.0-or-later
