"""One module per website.

Contract: a module defines NAME (str) and `async def fetch(**options) -> list[Job]`.
It must not write files or run code at import time. To add a site, create the
module and add it to the tuple below.
"""
from . import itpro, linkedin, rooster, rootcode

SCRAPERS = {m.NAME: m.fetch for m in (itpro, linkedin, rooster, rootcode)}
