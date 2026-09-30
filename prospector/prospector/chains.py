"""National chains to skip. The live list is read from the Chain Exclusions tab;
this fallback is only used for dry runs without sheet access."""

FALLBACK_CHAINS = [
    "Howdens", "Magnet", "Wren", "Wickes", "B&Q", "Benchmarx", "IKEA", "Homebase",
    "Harvey Jones", "Neptune", "John Lewis of Hungerford", "Tom Howley",
    "Kutchenhaus", "DIY Kitchens", "Jewson", "Travis Perkins", "Selco",
]


def is_chain(name: str, website: str, chains) -> bool:
    """Same rule as the sheet's Chain check column: case-insensitive
    substring of name + website."""
    haystack = f"{name} {website}".lower()
    return any(c and c.lower() in haystack for c in chains)
