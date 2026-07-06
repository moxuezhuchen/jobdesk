"""Allow: python -m jobdesk_app.agent serve"""
from jobdesk_app.agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
