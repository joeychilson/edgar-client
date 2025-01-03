import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import urljoin

from httpx import Client, HTTPError, Response
from pydantic import BaseModel
from ratelimit import limits, sleep_and_retry  # type: ignore

logger = logging.getLogger(__name__)


class EdgarError(Exception):
    """Base exception for EDGAR-related errors."""

    pass


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
    primary_document_url: str


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


class EdgarClient:
    """Client for interacting with SEC's EDGAR system."""

    BASE_URL = "https://www.sec.gov"
    DATA_URL = "https://data.sec.gov"

    def __init__(self, user_agent: str = "CompanyName contact@email.com", timeout: int = 30) -> None:
        """
        Initialize the EDGAR client.

        Args:
            user_agent: User agent string for SEC requests
            timeout: Request timeout in seconds
            rate_limit: Maximum requests per second
        """
        if not user_agent or len(user_agent.split()) < 2:
            raise ValueError("User agent must contain company name and contact email as per SEC requirements")

        self.user_agent = user_agent
        self.timeout = timeout
        self.client = Client(timeout=timeout, headers={"User-Agent": user_agent})

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.client.close()

    @sleep_and_retry
    @limits(calls=10, period=1)
    def get(self, url: str) -> Response:
        """
        Make a rate-limited GET request.

        Args:
            url: Request URL

        Returns:
            Response object

        Raises:
            EdgarError: If the request fails
        """
        try:
            response = self.client.get(url)
            response.raise_for_status()
            return response
        except HTTPError as e:
            raise EdgarError(f"HTTP error occurred: {str(e)}") from e

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

        Raises:
            EdgarError: If the request fails
        """
        url = urljoin(self.BASE_URL, "/Archives/edgar/cik-lookup-data.txt")
        response = self.get(url)

        normalized_ciks = {self._normalize_cik(cik) for cik in (ciks or [])}

        def filer_generator() -> Generator[FilerMatch, None, None]:
            for line in response.text.splitlines():
                if not line.strip():
                    continue

                fields = line.split(":")
                if len(fields) < 3:
                    logger.warning(f"Skipping malformed line: {line}")
                    continue

                try:
                    name = fields[0].strip()
                    cik = self._normalize_cik(fields[1])

                    if normalized_ciks and cik not in normalized_ciks:
                        continue
                    if contains and contains.lower() not in name.lower():
                        continue

                    yield FilerMatch(name=name, cik=cik)
                except ValueError as e:
                    logger.warning(f"Error processing line {line}: {str(e)}")
                    continue

        matches = list(filer_generator())
        return matches[:limit] if limit else matches

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
            tickers: Filter by ticker symbols (case-insensitive)
            ciks: Filter by CIK numbers
            exchanges: Filter by exchange names (case-insensitive)
            contains: Filter company names containing this string (case-insensitive)
            limit: Maximum number of results to return

        Returns:
            List of matching companies

        Raises:
            EdgarError: If the request fails
        """
        url = urljoin(self.BASE_URL, "/files/company_tickers_exchange.json")
        response = self.get(url)
        data = response.json()

        normalized_ciks = {self._normalize_cik(cik) for cik in (ciks or [])}
        normalized_tickers = {t.upper() for t in (tickers or [])}
        normalized_exchanges = {e.upper() for e in (exchanges or [])}

        def company_generator() -> Generator[CompanyMatch, None, None]:
            for row in data.get("data", []):
                try:
                    if len(row) != 4:
                        logger.warning(f"Skipping malformed company data: {row}")
                        continue

                    cik = self._normalize_cik(str(row[0]))
                    name = str(row[1])
                    ticker = str(row[2])
                    exchange = str(row[3])

                    if normalized_ciks and cik not in normalized_ciks:
                        continue
                    if normalized_tickers and ticker.upper() not in normalized_tickers:
                        continue
                    if normalized_exchanges and exchange.upper() not in normalized_exchanges:
                        continue
                    if contains and contains.lower() not in name.lower():
                        continue

                    yield CompanyMatch(cik=cik, name=name, ticker=ticker, exchange_name=exchange)
                except (ValueError, IndexError) as e:
                    logger.warning(f"Error processing company data {row}: {str(e)}")
                    continue

        companies = list(company_generator())
        return companies[:limit] if limit else companies

    @lru_cache(maxsize=100)
    def get_filer(self, cik: str) -> Filer:
        """
        Retrieve a filer's profile information.

        Args:
            cik: CIK number

        Returns:
            Filer profile information

        Raises:
            EdgarError: If the request fails
            ValueError: If CIK is invalid
        """
        normalized_cik = self._normalize_cik(cik)
        url = urljoin(self.DATA_URL, f"submissions/CIK{normalized_cik}.json")
        response = self.get(url)
        try:
            return Filer.model_validate(response.json())
        except ValueError as e:
            raise EdgarError(f"Invalid filer data received: {str(e)}") from e

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
            forms: Forms to filter by (e.g., ["10-K", "10-Q"])
            limit: Maximum number of filings to return

        Returns:
            List of filings

        Raises:
            EDGARError: If the request fails
            ValueError: If CIK is invalid
        """
        normalized_cik = self._normalize_cik(cik)
        url = urljoin(self.DATA_URL, f"submissions/CIK{normalized_cik}.json")
        response = self.get(url)
        data = response.json()

        def filing_generator() -> Generator[Filing, None, None]:
            seen_accession_numbers = set()

            for filing in self._parse_filings(cik, data["filings"]["recent"]):
                if filing.accession_number not in seen_accession_numbers:
                    seen_accession_numbers.add(filing.accession_number)
                    if self._should_include_filing(filing, start_date, end_date, forms):
                        yield filing

            for file in data["filings"].get("files", []):
                try:
                    file_url = urljoin(self.DATA_URL, f"submissions/{file['name']}")
                    file_response = self.get(file_url)
                    file_data = file_response.json()

                    for filing in self._parse_filings(cik, file_data):
                        if filing.accession_number not in seen_accession_numbers:
                            seen_accession_numbers.add(filing.accession_number)
                            if self._should_include_filing(filing, start_date, end_date, forms):
                                yield filing
                except Exception as e:
                    logger.error(f"Error processing filing file {file['name']}: {str(e)}")
                    continue

        filings = list(filing_generator())
        return filings[:limit] if limit else filings

    def _parse_filings(self, cik: str, data: Dict[str, Any]) -> Generator[Filing, None, None]:
        """
        Parse filings data into Filing objects.

        Args:
            data: Filings data

        Yields:
            Filing objects
        """
        if not data.get("accessionNumber"):
            return

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
                    "is_xbrl": bool(data["isXBRL"][i]),
                    "is_inline_xbrl": bool(data["isInlineXBRL"][i]),
                    "primary_document": data["primaryDocument"][i] or "",
                    "primary_document_description": data["primaryDocDescription"][i] or "",
                }

                if "reportDate" in data and i < len(data["reportDate"]) and data["reportDate"][i]:
                    filing_dict["report_date"] = datetime.strptime(data["reportDate"][i], "%Y-%m-%d")

                if "act" in data and i < len(data["act"]) and data["act"][i]:
                    filing_dict["act"] = data["act"][i]

                if "items" in data and i < len(data["items"]) and data["items"][i]:
                    filing_dict["items"] = [item.strip() for item in data["items"][i].split(",") if item.strip()]

                filing_dict["primary_document_url"] = urljoin(
                    self.BASE_URL,
                    f"/Archives/edgar/data/{cik}/{filing_dict['accession_number'].replace('-', '')}/{filing_dict['primary_document']}",
                )

                yield Filing.model_validate(filing_dict)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(f"Error parsing filing {i}: {str(e)}")
                continue

    @staticmethod
    def _normalize_cik(cik: str) -> str:
        """Normalize CIK to 10-digit format."""
        try:
            return f"{int(cik):010d}"
        except ValueError as e:
            raise ValueError(f"Invalid CIK format: {cik}") from e

    @staticmethod
    def _should_include_filing(
        filing: Filing,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        forms: Optional[List[str]],
    ) -> bool:
        """Determine if a filing should be included based on filters."""
        if start_date and filing.filing_date < start_date:
            return False
        if end_date and filing.filing_date > end_date:
            return False
        if forms and filing.form not in forms:
            return False
        return True
