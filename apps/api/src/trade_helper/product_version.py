import re
from importlib.metadata import PackageNotFoundError, version


def product_version() -> str:
    """Expose the installed product version, including SemVer beta notation."""
    try:
        installed = version("tx-trade-api")
    except PackageNotFoundError:
        # A source checkout without installed metadata has no verified build version.
        return "development"
    beta = re.fullmatch(r"(\d+\.\d+\.\d+)b(\d+)", installed)
    return f"{beta[1]}-beta.{beta[2]}" if beta else installed
