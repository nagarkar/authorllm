"""Pins llmlm's price cache to an empty one that never reaches the network.

llmlm prices a reply from its machine-wide cache (`~/.llmlm/prices.json`, refreshed weekly from
litellm's published map). A suite must neither read that file nor fetch the map, so this replaces
the default with a cache whose fetch returns nothing: every model prices to null, which is what
the hermetic tests assert on. Import it for the side effect, before any LLMClient is built.
"""

import tempfile
from pathlib import Path

from llmlm import prices

_PATH = Path(tempfile.mkdtemp(prefix="authorlm-prices-")) / "prices.json"
prices.CACHE_PATH = _PATH
prices._default = prices.Prices(_PATH, fetch=lambda: {})
