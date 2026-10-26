"""Package-wide defaults. Per-site settings live at the top of each scraper."""

# Sources that scrape_all() runs when none are given. Remove a name to switch
# a site off (for example "rootcode").
DEFAULT_SOURCES = ("itpro", "linkedin", "rooster", "rootcode")
