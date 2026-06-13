# Contributing

JobDesk is a Windows-first Python project.

Before sending changes, run:

```powershell
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_dev -p no:cacheprovider
```

Keep real server credentials and infrastructure details out of the repository. Use mocked tests by default; real SSH/SFTP integration tests require explicit local environment variables.
