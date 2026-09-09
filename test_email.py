from app import app
from compliance_alerts import send_rmk_trading_test_email

with app.app_context():
    # The user said: "ONLY the configured Brixen test Gmail address."
    # I don't know the exact test Gmail, but if I leave recipient_override=None,
    # it uses compliance_alert_settings().get('test_recipient').
    res = send_rmk_trading_test_email()
    print("Result:")
    print(res)
