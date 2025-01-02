from importlib.metadata import version

from .client import EdgarError, FilerMatch, CompanyMatch, Filing, Filer, EdgarClient

__all__ = ["EdgarError", "FilerMatch", "CompanyMatch", "Filing", "Filer", "EdgarClient"]
__version__ = version("edgar-client")
