# edgar-client

A Python client for retrieving filings from the SEC Edgar.

## Examples

```python
from datetime import datetime
from edgar_client.client import EDGARClient


with EDGARClient(user_agent="YourCompany contact@email.com") as client:
    companies = client.search_companies(tickers=["AAPL", "MSFT"], limit=5)
    print(companies)

    filings = client.get_filings(
        cik="320193",
        forms=["10-K", "10-Q"],
        start_date=datetime(2000, 1, 1),
        end_date=datetime(2001, 12, 31),
    )
    print(filings)
```
