# edgar-client

A Python client for retrieving filings from the SEC Edgar.

## Installation

```bash
uv add edgar-client
```

## Examples

```python
from datetime import datetime
from edgar_client import EdgarClient


with EdgarClient(user_agent="YourCompany contact@email.com") as client:
    companies = client.search_companies(tickers=["AAPL"], limit=1)

    filings = client.get_filings(
        cik=companies[0].cik,
        forms=["10-K", "10-Q"],
        start_date=datetime(2000, 1, 1),
        end_date=datetime(2001, 12, 31),
        limit=1,
    )

    print(filings)
```
