"""Shared base class and enums for all data-source connectors."""
from __future__ import annotations

import abc
from enum import Enum
from typing import Any

import pandas as pd


class DataSource(str, Enum):
    EXCEL = "excel"
    SHAREPOINT = "sharepoint"
    ONEDRIVE = "onedrive"
    AZURE = "azure"


class BaseConnector(abc.ABC):
    """Every connector must implement these three methods."""

    source: DataSource

    @abc.abstractmethod
    def authenticate(self) -> None:
        """Prompt for / validate credentials. Sets internal state."""

    @abc.abstractmethod
    def list_available(self) -> list[dict[str, Any]]:
        """
        Return a list of items the authenticated user can pick from.
        Each item is a dict with at least 'id' and 'name' keys.
        """

    @abc.abstractmethod
    def load(self, item: dict[str, Any]) -> pd.DataFrame:
        """Load the selected item into a pandas DataFrame."""
