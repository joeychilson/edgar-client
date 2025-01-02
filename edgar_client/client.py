from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from httpx import Client, Response
from pydantic import BaseModel
from ratelimit import limits, sleep_and_retry


class FilerMatch(BaseModel):
    """Represents an entity name to CIK mapping."""

    name: str
    cik: str


class CompanyMatch(BaseModel):
    """Represents a company name to CIK mapping."""

    cik: str
    name: str
    ticker: str
    exchange_name: str


class Filing(BaseModel):
    """Represents a single filing submission."""

    accession_number: str
    form: str
    filing_date: datetime
    report_date: Optional[datetime] = None
    acceptance_time: datetime
    act: Optional[str] = None
    size: int
    items: Optional[List[str]] = None
    is_xbrl: bool
    is_inline_xbrl: bool
    primary_document: str
    primary_document_description: str


class Filer(BaseModel):
    """Represents a filer's profile information."""

    cik: str
    entity_type: str
    sic: Optional[str] = None
    sic_description: Optional[str] = None
    name: str
    tickers: List[str]
    exchanges: List[str]
    ein: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    category: Optional[str] = None
    fiscal_year_end: Optional[str] = None
    state_of_incorporation: Optional[str] = None
    phone_number: Optional[str] = None
    flags: Optional[str] = None


class EDGARClient:
    """Client for interacting with SEC's EDGAR system."""

    BASE_URL = "https://www.sec.gov"
    DATA_URL = "https://data.sec.gov"

    def __init__(
        self, user_agent: str = "CompanyName contact@email.com", timeout: int = 30
    ) -> None:
        """
        Initialize the EDGAR client.

        Args:
            user_agent: User agent string for SEC requests
            timeout: Request timeout in seconds
            rate_limit: Maximum requests per second
        """
        self.user_agent = user_agent
        self.timeout = timeout

        self.client = Client(timeout=timeout, headers={"User-Agent": user_agent})

    def __enter__(self) -> "EDGARClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.client.close()

    @sleep_and_retry
    @limits(calls=10, period=1)
    def _get(self, url: str) -> Response:
        """
        Make a rate-limited GET request.

        Args:
            url: Request URL

        Returns:
            Response object

        Raises:
            httpx.HTTPError: If the request fails
        """
        response = self.client.get(url)
        response.raise_for_status()
        return response

    def search_filers(
        self,
        *,
        contains: Optional[str] = None,
        ciks: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[FilerMatch]:
        """
        Search for filers by name or CIK.

        Args:
            contains: Filter filer names containing this string (case-insensitive)
            ciks: Filter by specific CIK numbers
            limit: Maximum number of results to return

        Returns:
            List of matching filers
        """
        url = urljoin(self.BASE_URL, "/Archives/edgar/cik-lookup-data.txt")
        response = self._get(url)

        matches = []
        for line in response.text.splitlines():
            if not line.strip():
                continue

            fields = line.split(":")
            if len(fields) < 3:
                continue

            name = fields[0].strip()
            cik = f"{int(fields[1]):010d}"

            if ciks and cik not in ciks:
                continue
            if contains and contains.lower() not in name.lower():
                continue

            matches.append(FilerMatch(name=name, cik=cik))

            if limit and len(matches) >= limit:
                break

        return matches

    def search_companies(
        self,
        *,
        tickers: Optional[List[str]] = None,
        ciks: Optional[List[str]] = None,
        exchanges: Optional[List[str]] = None,
        contains: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[CompanyMatch]:
        """
        Search for companies by various criteria.

        Args:
            tickers: Filter by ticker symbols
            ciks: Filter by CIK numbers
            exchanges: Filter by exchange names
            contains: Filter company names containing this string
            limit: Maximum number of results to return

        Returns:
            List of matching companies
        """
        url = urljoin(self.BASE_URL, "/files/company_tickers_exchange.json")
        response = self._get(url)
        data = response.json()

        companies = []
        for row in data["data"]:
            if len(row) != 4:
                continue

            cik = f"{int(row[0]):010d}"
            name = str(row[1])
            ticker = str(row[2])
            exchange = str(row[3])

            if ciks and cik not in ciks:
                continue
            if tickers and ticker not in tickers:
                continue
            if exchanges and exchange not in exchanges:
                continue
            if contains and contains.lower() not in name.lower():
                continue

            companies.append(
                CompanyMatch(cik=cik, name=name, ticker=ticker, exchange_name=exchange)
            )

            if limit and len(companies) >= limit:
                break

        return companies

    def get_filer(self, cik: str) -> Filer:
        """
        Retrieve a filer's profile information.

        Args:
            cik: CIK number

        Returns:
            Filer profile information
        """
        normalized_cik = f"{int(cik):010d}"
        url = urljoin(self.DATA_URL, f"submissions/CIK{normalized_cik}.json")
        response = self._get(url)
        data = response.json()

        return Filer(
            cik=data["cik"],
            entity_type=data["entityType"],
            sic=data.get("sic"),
            sic_description=data.get("sicDescription"),
            name=data["name"],
            tickers=data["tickers"],
            exchanges=data["exchanges"],
            ein=data.get("ein"),
            description=data.get("description"),
            website=data.get("website"),
            category=data.get("category"),
            fiscal_year_end=data.get("fiscalYearEnd"),
            state_of_incorporation=data.get("stateOfIncorporation"),
            phone_number=data.get("phone"),
            flags=data.get("flags"),
        )

    def get_filings(
        self,
        cik: str,
        *,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        forms: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Filing]:
        """
        Retrieve filings for a given CIK.

        Args:
            cik: CIK number
            start_date: Start date for filtering filings
            end_date: End date for filtering filings
            forms: Forms to filter by
            limit: Maximum number of filings to return

        Returns:
            List of filings
        """
        normalized_cik = f"{int(cik):010d}"
        url = urljoin(self.DATA_URL, f"submissions/CIK{normalized_cik}.json")
        response = self._get(url)
        data = response.json()

        all_filings = []

        main_filings = self._parse_filings(data["filings"]["recent"])
        all_filings.extend(main_filings)

        for file in data["filings"]["files"]:
            file_url = urljoin(self.DATA_URL, f"submissions/{file['name']}")
            file_response = self._get(file_url)
            file_data = file_response.json()

            additional_filings = self._parse_filings(file_data)
            all_filings.extend(additional_filings)

        filtered_filings = []
        for filing in all_filings:
            if start_date and filing.filing_date < start_date:
                continue
            if end_date and filing.filing_date > end_date:
                continue
            if forms and filing.form not in forms:
                continue
            filtered_filings.append(filing)

            if limit and len(filtered_filings) >= limit:
                break

        return filtered_filings

    def _parse_filings(self, data: Dict[str, Any]) -> List[Filing]:
        """Parse filings data into Filing objects.

        Args:
            data: Filings data

        Returns:
            List of filings
        """
        filings = []
        num_filings = len(data["accessionNumber"])

        for i in range(num_filings):
            try:
                filing_dict = {
                    "accession_number": data["accessionNumber"][i],
                    "form": data["form"][i],
                    "filing_date": datetime.strptime(data["filingDate"][i], "%Y-%m-%d"),
                    "acceptance_time": datetime.strptime(
                        data["acceptanceDateTime"][i].replace(".000Z", "+0000"),
                        "%Y-%m-%dT%H:%M:%S%z",
                    ),
                    "size": data["size"][i],
                    "is_xbrl": bool(data.get("isXBRL", [0] * num_filings)[i]),
                    "is_inline_xbrl": bool(
                        data.get("isInlineXBRL", [0] * num_filings)[i]
                    ),
                    "primary_document": data.get("primaryDocument", [""] * num_filings)[
                        i
                    ]
                    or "",
                    "primary_document_description": data.get(
                        "primaryDocDescription", [""] * num_filings
                    )[i]
                    or "",
                }

                if (
                    data.get("reportDate")
                    and i < len(data["reportDate"])
                    and data["reportDate"][i]
                ):
                    filing_dict["report_date"] = datetime.strptime(
                        data["reportDate"][i], "%Y-%m-%d"
                    )

                if data.get("act") and i < len(data["act"]) and data["act"][i]:
                    filing_dict["act"] = data["act"][i]

                if data.get("items") and i < len(data["items"]) and data["items"][i]:
                    filing_dict["items"] = [
                        item.strip()
                        for item in data["items"][i].split(",")
                        if item.strip()
                    ]

                filings.append(Filing.model_validate(filing_dict))
            except (KeyError, IndexError, ValueError) as e:
                print(f"Warning: Error parsing filing {i}: {e}")
                continue

        return filings
